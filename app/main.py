import hmac
import json
import os
from dataclasses import asdict
from decimal import Decimal
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app import audit, firewall, paypal_client
from app.agent import Proposal, run_agent
from app.catalog import get_product, list_shops
from app.shop_pages import render_shop_page

load_dotenv()

# Where PayPal sends the buyer back after approval; must be reachable by the browser
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

# Demo scenarios: which shops the agent may browse and whether hidden page text reaches it
SCENARIOS = {
    "clean": {"allowed_shops": ["techmart", "gadgethub"], "include_hidden": True},
    "control": {"allowed_shops": None, "include_hidden": False},
    "poisoned": {"allowed_shops": None, "include_hidden": True},
}

app = FastAPI(title="Agent Payment Firewall")


def require_demo_key(x_demo_key: str | None = Header(default=None)):
    """Protect routes that spend money or reveal decisions.

    With no DEMO_ACCESS_KEY configured the routes are refused, never left open.
    """
    expected = os.environ.get("DEMO_ACCESS_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="Purchasing is disabled: no access key is configured")
    # Constant-time comparison so response timing doesn't leak the key
    if not x_demo_key or not hmac.compare_digest(x_demo_key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Missing or invalid X-Demo-Key header")


class PaymentProposal(BaseModel):
    user_instruction: str
    budget: Decimal = Field(gt=0)
    payee_email: str
    amount: Decimal = Field(gt=0)


class Verdict(BaseModel):
    decision: Literal["allow", "hold", "block"]
    reasons: list[str]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/check", response_model=Verdict)
def check(proposal: PaymentProposal) -> Verdict:
    if proposal.amount > proposal.budget:
        return Verdict(
            decision="block",
            reasons=[f"Amount {proposal.amount} exceeds budget {proposal.budget}"],
        )
    return Verdict(decision="allow", reasons=["Within budget"])


@app.get("/shops")
def shops():
    return [{"shop_key": key, "shop_name": name} for key, name in list_shops()]


@app.get("/shops/{shop_key}", response_class=HTMLResponse)
def shop_page(shop_key: str):
    if shop_key not in dict(list_shops()):
        raise HTTPException(status_code=404, detail=f"Unknown shop: {shop_key}")
    return render_shop_page(shop_key)


class PurchaseRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=500)
    budget: Decimal = Field(gt=0, decimal_places=2)
    scenario: Literal["clean", "control", "poisoned"] = "poisoned"


def _block_without_order(instruction, budget, proposal, reason, error_detail=None):
    """Log a block for a purchase that never reached a PayPal authorization."""
    verdict = firewall.Verdict("block", [reason], [], [])
    decision_id = audit.log_decision(instruction, budget, proposal, None, verdict)
    if error_detail:
        audit.log_action(decision_id, "error", error_detail)
    return {"decision_id": decision_id, "decision": "block", "reasons": [reason]}


def _settle(decision_id, shop_key, authorization_id, capture):
    """Capture or void held funds and log the outcome. Returns the action logged."""
    try:
        if capture:
            status = paypal_client.capture_authorization(shop_key, authorization_id)
            audit.log_action(decision_id, "captured", f"Capture status {status}")
            return "captured"
        paypal_client.void_authorization(shop_key, authorization_id)
        audit.log_action(decision_id, "voided", "Authorization voided; held funds released")
        return "voided"
    except Exception as e:
        verb = "capture" if capture else "void"
        audit.log_action(decision_id, "error", f"Could not {verb} authorization: {type(e).__name__}: {e}")
        return "error"


def _decision_response(decision_id):
    row = audit.get_decision_with_actions(decision_id)
    return {
        "decision_id": decision_id,
        "decision": row["decision"],
        "reasons": json.loads(row["reasons_json"]),
        "actions": [{"action": a["action"], "detail": a["detail"]} for a in row["actions"]],
    }


@app.post("/purchase", dependencies=[Depends(require_demo_key)])
def purchase(req: PurchaseRequest):
    """Run the shopping agent and create a PayPal order (AUTHORIZE) for its proposal."""
    try:
        result = run_agent(req.instruction, req.budget, **SCENARIOS[req.scenario])
    except Exception as e:
        partial = getattr(e, "agent_result", None)
        detail = json.dumps(
            {
                "error": f"{type(e).__name__}: {e}",
                "trace": [asdict(s) for s in partial.trace] if partial else [],
            }
        )
        return _block_without_order(
            req.instruction, req.budget, None, f"The shopping assistant failed: {type(e).__name__}", detail
        )
    proposal = result.proposal
    if proposal is None:
        return _block_without_order(
            req.instruction, req.budget, None, "The shopping assistant did not choose a product"
        )

    product = get_product(proposal.product_id)
    try:
        # The order is created with the proposed shop's own credentials, so the
        # payee is whoever owns that shop; the firewall checks it after approval.
        order_id, approve_url = paypal_client.create_order(
            proposal.shop_key,
            proposal.amount,
            product.name if product else proposal.product_id,
            f"{PUBLIC_BASE_URL}/paypal/return",
            f"{PUBLIC_BASE_URL}/paypal/cancel",
        )
    except (ValueError, paypal_client.PayPalError) as e:
        return _block_without_order(
            req.instruction, req.budget, proposal, f"Could not create a PayPal order: {type(e).__name__}"
        )

    purchase_id = audit.log_purchase(req.instruction, req.budget, result, order_id)
    return {
        "purchase_id": purchase_id,
        "order_id": order_id,
        "approve_url": approve_url,
        "proposal": {
            "shop_key": proposal.shop_key,
            "product_id": proposal.product_id,
            "amount": str(proposal.amount),
            "reason": proposal.reason,
        },
    }


@app.get("/paypal/return")
def paypal_return(token: str):
    """After buyer approval: authorize, read the order back, run the firewall, settle."""
    order_id = token
    # Idempotent: a reload or replayed redirect returns the existing decision
    existing = audit.get_decision_id_by_order(order_id)
    if existing is not None:
        return _decision_response(existing)

    row = audit.get_purchase_by_order(order_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown order")
    instruction, budget, shop_key = row["instruction"], Decimal(row["budget"]), row["shop_key"]
    proposal = Proposal(row["product_id"], shop_key, Decimal(row["agent_claimed_amount"]), row["agent_reason"])

    try:
        auth = paypal_client.authorize_order(shop_key, order_id)
    except paypal_client.PayPalError as e:
        verdict = firewall.Verdict("block", [f"PayPal could not hold the funds: {e.name}"], [], [])
        decision_id = audit.log_decision(instruction, budget, proposal, None, verdict)
        audit.log_action(decision_id, "error", f"Authorize failed: HTTP {e.status_code} {e.name} (debug_id={e.debug_id})")
        return _decision_response(decision_id)

    # Funds are now held. Anything that stops the firewall from reaching a
    # verdict must release them (fail-safe block), never capture.
    order = None
    try:
        order = paypal_client.get_order(shop_key, order_id)
        verdict = firewall.evaluate(instruction, budget, proposal, order, json.loads(row["pages_read_json"]))
    except Exception as e:
        verdict = firewall.Verdict("block", [f"The payment could not be checked: {type(e).__name__}"], [], [])

    decision_id = audit.log_decision(instruction, budget, proposal, order, verdict, auth.authorization_id)
    if verdict.decision == "allow":
        _settle(decision_id, shop_key, auth.authorization_id, capture=True)
    elif verdict.decision == "block":
        _settle(decision_id, shop_key, auth.authorization_id, capture=False)
    else:
        audit.log_action(decision_id, "held", "Funds held; waiting for the user to approve or reject")
    return _decision_response(decision_id)


@app.get("/paypal/cancel")
def paypal_cancel(token: str | None = None):
    """Buyer cancelled on PayPal: nothing was authorized, so no money moved."""
    return {"order_id": token, "status": "cancelled", "detail": "You cancelled the payment on PayPal."}


def _resolve_hold(decision_id, approve):
    row = audit.get_decision_with_actions(decision_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown decision")
    if row["decision"] != "hold":
        raise HTTPException(status_code=409, detail=f"Decision is '{row['decision']}', not on hold")
    # Claim the resolution first. A unique index allows only one user action per
    # decision, so of two concurrent requests exactly one gets past this line
    # and only that one touches PayPal.
    try:
        audit.log_action(
            decision_id,
            "user_approved" if approve else "user_rejected",
            "User approved the held payment" if approve else "User rejected the held payment",
        )
    except audit.AlreadyResolved:
        raise HTTPException(status_code=409, detail="This payment has already been resolved")
    _settle(decision_id, row["shop_key"], row["authorization_id"], capture=approve)
    return _decision_response(decision_id)


@app.get("/decisions/{decision_id}", dependencies=[Depends(require_demo_key)])
def get_decision(decision_id: int):
    """Return a decision with its evidence and every action taken on it."""
    row = audit.get_decision_with_actions(decision_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown decision")
    row["reasons"] = json.loads(row.pop("reasons_json"))
    row["findings"] = json.loads(row.pop("findings_json"))
    return row


@app.post("/decisions/{decision_id}/approve", dependencies=[Depends(require_demo_key)])
def approve_decision(decision_id: int):
    """Capture a held payment after the user approves it."""
    return _resolve_hold(decision_id, approve=True)


@app.post("/decisions/{decision_id}/reject", dependencies=[Depends(require_demo_key)])
def reject_decision(decision_id: int):
    """Void a held payment after the user rejects it."""
    return _resolve_hold(decision_id, approve=False)

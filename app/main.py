import hmac
import json
import os
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints, field_validator

from app import audit, firewall, paypal_client
from app.agent import Proposal, run_agent
from app.catalog import PRODUCTS, get_product, list_shops
from app.lab import run_lab
from app.rate_limit import PurchaseLimiter, client_ip
from app.result_page import render_result_page
from app.security_page import load_summary, render_security_page
from app.shop_pages import page_text_for_agent, render_shop_page

load_dotenv()

STATIC_DIR = Path(__file__).parent / "static"

# HTML pages may only run scripts from our own origin: no inline scripts, so
# even if untrusted text ever slipped through unescaped it could not execute.
# Inline styles stay allowed because shop pages hide seller text with style="display:none".
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)

# Where PayPal sends the buyer back after approval; must be reachable by the browser
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

# Demo scenarios: which shops the agent may browse and whether hidden page text reaches it
SCENARIOS = {
    "clean": {"allowed_shops": ["techmart", "gadgethub"], "include_hidden": True},
    "control": {"allowed_shops": None, "include_hidden": False},
    "poisoned": {"allowed_shops": None, "include_hidden": True},
}

app = FastAPI(title="Agent Payment Firewall")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def content_security_policy(request: Request, call_next):
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    return response


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


# Protects the free LLM quota: 10 purchases and 10 lab runs per hour per client IP, 100 per day overall
purchase_limiter = PurchaseLimiter()


def limit_purchases(request: Request):
    # Runs after the key check, so requests without a valid key never use up the quota
    refusal = purchase_limiter.check(client_ip(request))
    if refusal:
        raise HTTPException(status_code=429, detail=refusal)


def limit_lab_runs(request: Request):
    # Own per-IP bucket; shares the overall daily cap with purchases
    refusal = purchase_limiter.check(client_ip(request), bucket="lab")
    if refusal:
        raise HTTPException(status_code=429, detail=refusal)


class PaymentProposal(BaseModel):
    user_instruction: str
    budget: Decimal = Field(gt=0)
    payee_email: str
    amount: Decimal = Field(gt=0)


class Verdict(BaseModel):
    decision: Literal["allow", "hold", "block"]
    reasons: list[str]


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


# Built once at startup from results/security_summary.json; the page is static after that
SECURITY_PAGE = render_security_page(load_summary())


@app.get("/security", include_in_schema=False, response_class=HTMLResponse)
def security_page():
    """Public page: layered defence, evidence and limitations. No access key needed."""
    return SECURITY_PAGE


@app.get("/lab", include_in_schema=False)
def lab_page():
    return FileResponse(STATIC_DIR / "lab.html", media_type="text/html")


class LabRequest(BaseModel):
    target_shop: Literal["techmart", "gadgethub", "techmartdeals"]
    injected_text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    visibility: Literal["hidden", "visible"] = "hidden"
    instruction: str = Field(default="Buy me a phone case for my 6.1-inch phone.", min_length=1, max_length=500)
    budget: Decimal = Field(default=Decimal("20.00"), gt=0, decimal_places=2)

    @field_validator("budget")
    @classmethod
    def two_decimal_places(cls, v: Decimal) -> Decimal:
        return v.quantize(Decimal("0.01"))


@app.post("/lab/run", dependencies=[Depends(require_demo_key)])
def lab_run(req: LabRequest, request: Request):
    """Attack Lab: the agent and the firewall against the visitor's text. No PayPal order, nothing stored."""
    # Checked here, after the body is validated, so malformed requests don't use up the quota
    limit_lab_runs(request)
    return run_lab(req.target_shop, req.injected_text, req.visibility == "hidden", req.instruction, req.budget)


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


@app.get("/catalog")
def catalog():
    """Platform-controlled product fields only (no seller-written description), for UI labels."""
    return [
        {"product_id": p.product_id, "shop_key": p.shop_key, "shop_name": p.shop_name, "name": p.name, "price": str(p.price)}
        for p in PRODUCTS
    ]


@app.get("/shops/{shop_key}", response_class=HTMLResponse)
def shop_page(shop_key: str):
    if shop_key not in dict(list_shops()):
        raise HTTPException(status_code=404, detail=f"Unknown shop: {shop_key}")
    return render_shop_page(shop_key)


@app.get("/shops/{shop_key}/views")
def shop_views(shop_key: str):
    """A shop page as a human sees it (hidden text removed) and as a scraper reads it."""
    if shop_key not in dict(list_shops()):
        raise HTTPException(status_code=404, detail=f"Unknown shop: {shop_key}")
    return {
        "visible": page_text_for_agent(shop_key, include_hidden=False),
        "full": page_text_for_agent(shop_key, include_hidden=True),
    }


class PurchaseRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=500)
    budget: Decimal = Field(gt=0, decimal_places=2)
    scenario: Literal["clean", "control", "poisoned"] = "poisoned"

    @field_validator("budget")
    @classmethod
    def two_decimal_places(cls, v: Decimal) -> Decimal:
        # Store all money in one format ("20" -> "20.00"); exact, since at most 2 places are allowed
        return v.quantize(Decimal("0.01"))


def _agent_view(agent_result):
    """The agent's trace and final message, for display (all of it is untrusted text)."""
    if agent_result is None:
        return {"trace": [], "final_message": None}
    return {
        "trace": [asdict(s) for s in agent_result.trace],
        "final_message": agent_result.final_message,
    }


def _block_without_order(instruction, budget, proposal, reason, error_detail=None, agent_result=None):
    """Log a block for a purchase that never reached a PayPal authorization."""
    verdict = firewall.Verdict("block", [reason], [], [])
    decision_id = audit.log_decision(instruction, budget, proposal, None, verdict)
    if error_detail:
        audit.log_action(decision_id, "error", error_detail)
    return {"decision_id": decision_id, "decision": "block", "reasons": [reason], **_agent_view(agent_result)}


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


@app.post("/purchase", dependencies=[Depends(require_demo_key), Depends(limit_purchases)])
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
            req.instruction,
            req.budget,
            None,
            f"The shopping assistant failed: {type(e).__name__}",
            detail,
            agent_result=partial,
        )
    proposal = result.proposal
    if proposal is None:
        return _block_without_order(
            req.instruction,
            req.budget,
            None,
            "The shopping assistant did not choose a product",
            agent_result=result,
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
            req.instruction,
            req.budget,
            proposal,
            f"Could not create a PayPal order: {type(e).__name__}",
            agent_result=result,
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
        **_agent_view(result),
    }


def _return_response(request, decision_id):
    """HTML result page for browsers (PayPal's redirect), JSON for everything else."""
    if "text/html" in request.headers.get("accept", ""):
        return HTMLResponse(render_result_page(audit.get_decision_with_actions(decision_id)))
    return _decision_response(decision_id)


@app.get("/paypal/return")
def paypal_return(token: str, request: Request):
    """After buyer approval: authorize, read the order back, run the firewall, settle.

    Reachable only via the unguessable PayPal order token, never by decision id.
    """
    order_id = token
    # Idempotent: a reload or replayed redirect returns the existing decision
    existing = audit.get_decision_id_by_order(order_id)
    if existing is not None:
        return _return_response(request, existing)

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
        return _return_response(request, decision_id)

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
    return _return_response(request, decision_id)


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

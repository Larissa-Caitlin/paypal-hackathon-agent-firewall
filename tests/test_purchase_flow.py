"""End-to-end tests of the purchase routes with the agent, LLM and PayPal mocked."""

import threading
import time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import app.firewall as firewall_module
from app import main, paypal_client
from app.agent import AgentResult, Proposal
from app.firewall import Verdict
from app.paypal_client import AuthInfo, OrderInfo, PayPalError

GADGETHUB_ID = "Q6EAZLAJEAUKC"
TECHMARTDEALS_ID = "NBTHNLSYURVNN"
INSTRUCTION = "Buy me a phone case for my 6.1-inch phone."
TEST_KEY = "test-demo-key"


class FakePayPal:
    """Records PayPal calls; the order it returns is configurable per test."""

    def __init__(self, monkeypatch, order):
        self.order = order
        self.calls = []
        self.authorize_error = None
        self.get_order_error = None
        for name in ("create_order", "authorize_order", "get_order", "capture_authorization", "void_authorization"):
            monkeypatch.setattr(paypal_client, name, getattr(self, name))

    def create_order(self, shop, amount, description, return_url, cancel_url):
        self.calls.append(("create_order", shop, amount))
        return self.order.order_id, f"https://paypal.example/approve?token={self.order.order_id}"

    def authorize_order(self, shop, order_id):
        self.calls.append(("authorize_order", shop, order_id))
        if self.authorize_error:
            raise self.authorize_error
        return AuthInfo("AUTH1", "CREATED")

    def get_order(self, shop, order_id):
        self.calls.append(("get_order", shop, order_id))
        if self.get_order_error:
            raise self.get_order_error
        return self.order

    def capture_authorization(self, shop, auth_id):
        time.sleep(self.settle_delay)
        self.calls.append(("capture", shop, auth_id))
        return "COMPLETED"

    settle_delay = 0

    def void_authorization(self, shop, auth_id):
        time.sleep(self.settle_delay)
        self.calls.append(("void", shop, auth_id))

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "flow_test.db"))
    monkeypatch.setenv("DEMO_ACCESS_KEY", TEST_KEY)


@pytest.fixture
def client():
    return TestClient(main.app, headers={"X-Demo-Key": TEST_KEY})


def agent_proposing(monkeypatch, product_id, shop_key, amount):
    """Fake agent; returns the kwargs list it was called with, for scenario checks."""
    proposal = Proposal(product_id, shop_key, Decimal(amount), "Fits 6.1-inch phones")
    result = AgentResult(proposal=proposal, pages_read={shop_key: "page text"})
    seen = []

    def fake_run_agent(instruction, budget, **kwargs):
        seen.append(kwargs)
        return result

    monkeypatch.setattr(main, "run_agent", fake_run_agent)
    return seen


def gadgethub_order():
    return OrderInfo("ORD-GH", "COMPLETED", Decimal("15.00"), "USD", None, GADGETHUB_ID, "Slim Phone Case")


def stub_verdict(monkeypatch, decision):
    reasons = {"allow": ["All checks passed"], "hold": ["Needs a human"], "block": ["Bad"]}[decision]
    monkeypatch.setattr(firewall_module, "evaluate", lambda *a: Verdict(decision, reasons, [], []))


def start_purchase(client):
    resp = client.post("/purchase", json={"instruction": INSTRUCTION, "budget": "20.00"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_purchase_creates_order_with_proposed_shop(client, monkeypatch):
    agent_proposing(monkeypatch, "gh-case-01", "gadgethub", "15.00")
    pp = FakePayPal(monkeypatch, gadgethub_order())
    body = start_purchase(client)
    assert body["order_id"] == "ORD-GH"
    assert body["approve_url"].endswith("ORD-GH")
    assert pp.calls == [("create_order", "gadgethub", Decimal("15.00"))]


def test_allow_captures(client, monkeypatch):
    agent_proposing(monkeypatch, "gh-case-01", "gadgethub", "15.00")
    pp = FakePayPal(monkeypatch, gadgethub_order())
    stub_verdict(monkeypatch, "allow")
    start_purchase(client)
    body = client.get("/paypal/return", params={"token": "ORD-GH", "PayerID": "X"}).json()
    assert body["decision"] == "allow"
    assert [a["action"] for a in body["actions"]] == ["captured"]
    assert "void" not in pp.names()


def test_real_attack_is_blocked_and_voided(client, monkeypatch):
    # Real rule checks; AI checks stubbed out so no LLM call is made
    monkeypatch.setattr(firewall_module, "run_ai_checks", lambda *a: [])
    agent_proposing(monkeypatch, "tmd-case-01", "techmartdeals", "12.00")
    order = OrderInfo("ORD-TMD", "COMPLETED", Decimal("12.00"), "USD", None, TECHMARTDEALS_ID, "Phone Case")
    pp = FakePayPal(monkeypatch, order)
    start_purchase(client)
    body = client.get("/paypal/return", params={"token": "ORD-TMD"}).json()
    assert body["decision"] == "block"
    assert "TechMart Deals is not an approved shop" in body["reasons"]
    assert [a["action"] for a in body["actions"]] == ["voided"]
    assert ("void", "techmartdeals", "AUTH1") in pp.calls
    assert "capture" not in pp.names()


def test_hold_then_approve_captures_once(client, monkeypatch):
    agent_proposing(monkeypatch, "gh-case-01", "gadgethub", "15.00")
    pp = FakePayPal(monkeypatch, gadgethub_order())
    stub_verdict(monkeypatch, "hold")
    start_purchase(client)
    body = client.get("/paypal/return", params={"token": "ORD-GH"}).json()
    assert body["decision"] == "hold"
    assert [a["action"] for a in body["actions"]] == ["held"]
    assert "capture" not in pp.names() and "void" not in pp.names()

    approved = client.post(f"/decisions/{body['decision_id']}/approve").json()
    assert [a["action"] for a in approved["actions"]] == ["held", "user_approved", "captured"]
    again = client.post(f"/decisions/{body['decision_id']}/approve")
    assert again.status_code == 409
    assert pp.names().count("capture") == 1


def test_hold_then_reject_voids(client, monkeypatch):
    agent_proposing(monkeypatch, "gh-case-01", "gadgethub", "15.00")
    pp = FakePayPal(monkeypatch, gadgethub_order())
    stub_verdict(monkeypatch, "hold")
    start_purchase(client)
    decision_id = client.get("/paypal/return", params={"token": "ORD-GH"}).json()["decision_id"]
    rejected = client.post(f"/decisions/{decision_id}/reject").json()
    assert [a["action"] for a in rejected["actions"]] == ["held", "user_rejected", "voided"]
    assert "capture" not in pp.names()


def test_approve_on_non_held_decision_is_rejected(client, monkeypatch):
    agent_proposing(monkeypatch, "gh-case-01", "gadgethub", "15.00")
    FakePayPal(monkeypatch, gadgethub_order())
    stub_verdict(monkeypatch, "block")
    start_purchase(client)
    decision_id = client.get("/paypal/return", params={"token": "ORD-GH"}).json()["decision_id"]
    assert client.post(f"/decisions/{decision_id}/approve").status_code == 409


def test_replayed_return_does_not_authorize_twice(client, monkeypatch):
    agent_proposing(monkeypatch, "gh-case-01", "gadgethub", "15.00")
    pp = FakePayPal(monkeypatch, gadgethub_order())
    stub_verdict(monkeypatch, "allow")
    start_purchase(client)
    first = client.get("/paypal/return", params={"token": "ORD-GH"}).json()
    second = client.get("/paypal/return", params={"token": "ORD-GH"}).json()
    assert second["decision_id"] == first["decision_id"]
    assert pp.names().count("authorize_order") == 1
    assert pp.names().count("capture") == 1


def test_unknown_order_token_is_404_without_paypal_calls(client, monkeypatch):
    pp = FakePayPal(monkeypatch, gadgethub_order())
    assert client.get("/paypal/return", params={"token": "NOT-OURS"}).status_code == 404
    assert pp.calls == []


def test_failure_after_authorize_voids(client, monkeypatch):
    agent_proposing(monkeypatch, "gh-case-01", "gadgethub", "15.00")
    pp = FakePayPal(monkeypatch, gadgethub_order())
    pp.get_order_error = PayPalError(500, "INTERNAL_SERVER_ERROR", "boom", "dbg")
    start_purchase(client)
    body = client.get("/paypal/return", params={"token": "ORD-GH"}).json()
    assert body["decision"] == "block"
    assert body["reasons"] == ["The payment could not be checked: PayPalError"]
    assert ("void", "gadgethub", "AUTH1") in pp.calls
    assert "capture" not in pp.names()


def test_authorize_failure_is_logged_as_block(client, monkeypatch):
    agent_proposing(monkeypatch, "gh-case-01", "gadgethub", "15.00")
    pp = FakePayPal(monkeypatch, gadgethub_order())
    pp.authorize_error = PayPalError(422, "ORDER_NOT_APPROVED", "not approved", "dbg")
    start_purchase(client)
    body = client.get("/paypal/return", params={"token": "ORD-GH"}).json()
    assert body["decision"] == "block"
    assert [a["action"] for a in body["actions"]] == ["error"]
    assert "capture" not in pp.names() and "void" not in pp.names()


def test_no_proposal_is_blocked_without_creating_order(client, monkeypatch):
    monkeypatch.setattr(main, "run_agent", lambda instruction, budget, **kw: AgentResult(proposal=None))
    pp = FakePayPal(monkeypatch, gadgethub_order())
    body = start_purchase(client)
    assert body["decision"] == "block"
    assert body["reasons"] == ["The shopping assistant did not choose a product"]
    assert pp.calls == []


def test_agent_llm_failure_is_logged_with_trace(client, monkeypatch):
    import app.agent as agent_module

    def failing_chat(messages, tools=None, model=None):
        raise TimeoutError("LLM timed out")

    monkeypatch.setattr(agent_module, "chat", failing_chat)
    pp = FakePayPal(monkeypatch, gadgethub_order())
    body = start_purchase(client)
    assert body["decision"] == "block"
    assert body["reasons"] == ["The shopping assistant failed: TimeoutError"]
    decision = client.get(f"/decisions/{body['decision_id']}").json()
    [error] = decision["actions"]
    assert error["action"] == "error"
    assert "TimeoutError: LLM timed out" in error["detail"]
    assert '"step_type": "error"' in error["detail"]
    assert pp.calls == []


# --- Scenarios ---


@pytest.mark.parametrize(
    "scenario, allowed_shops, include_hidden",
    [
        ("clean", ["techmart", "gadgethub"], True),
        ("control", None, False),
        ("poisoned", None, True),
        (None, None, True),  # default is poisoned
    ],
)
def test_scenario_sets_agent_options(client, monkeypatch, scenario, allowed_shops, include_hidden):
    seen = agent_proposing(monkeypatch, "gh-case-01", "gadgethub", "15.00")
    FakePayPal(monkeypatch, gadgethub_order())
    payload = {"instruction": INSTRUCTION, "budget": "20.00"}
    if scenario:
        payload["scenario"] = scenario
    assert client.post("/purchase", json=payload).status_code == 200
    assert seen == [{"allowed_shops": allowed_shops, "include_hidden": include_hidden}]


def test_unknown_scenario_is_rejected(client, monkeypatch):
    seen = agent_proposing(monkeypatch, "gh-case-01", "gadgethub", "15.00")
    pp = FakePayPal(monkeypatch, gadgethub_order())
    resp = client.post("/purchase", json={"instruction": INSTRUCTION, "budget": "20.00", "scenario": "evil"})
    assert resp.status_code == 422
    assert seen == [] and pp.calls == []


# --- Access key ---

PROTECTED = [
    ("post", "/purchase", {"instruction": INSTRUCTION, "budget": "20.00"}),
    ("get", "/decisions/1", None),
    ("post", "/decisions/1/approve", None),
    ("post", "/decisions/1/reject", None),
]


@pytest.mark.parametrize("method, path, payload", PROTECTED)
@pytest.mark.parametrize("headers", [{}, {"X-Demo-Key": "wrong-key"}], ids=["missing", "wrong"])
def test_protected_routes_require_key(monkeypatch, method, path, payload, headers):
    seen = agent_proposing(monkeypatch, "gh-case-01", "gadgethub", "15.00")
    pp = FakePayPal(monkeypatch, gadgethub_order())
    resp = TestClient(main.app).request(method, path, json=payload, headers=headers)
    assert resp.status_code == 401
    assert seen == [] and pp.calls == []


@pytest.mark.parametrize("method, path, payload", PROTECTED)
def test_protected_routes_refused_when_key_not_configured(monkeypatch, method, path, payload):
    monkeypatch.delenv("DEMO_ACCESS_KEY")
    seen = agent_proposing(monkeypatch, "gh-case-01", "gadgethub", "15.00")
    pp = FakePayPal(monkeypatch, gadgethub_order())
    resp = TestClient(main.app).request(method, path, json=payload, headers={"X-Demo-Key": ""})
    assert resp.status_code == 503
    assert seen == [] and pp.calls == []


def test_get_decision_returns_findings_and_actions(client, monkeypatch):
    agent_proposing(monkeypatch, "gh-case-01", "gadgethub", "15.00")
    FakePayPal(monkeypatch, gadgethub_order())
    stub_verdict(monkeypatch, "allow")
    start_purchase(client)
    decision_id = client.get("/paypal/return", params={"token": "ORD-GH"}).json()["decision_id"]
    body = client.get(f"/decisions/{decision_id}").json()
    assert body["decision"] == "allow"
    assert body["reasons"] == ["All checks passed"]
    assert body["findings"] == {"rules": [], "ai": []}
    assert [a["action"] for a in body["actions"]] == ["captured"]
    assert client.get("/decisions/9999").status_code == 404


# --- Race on approve/reject ---


def held_decision(client, monkeypatch):
    agent_proposing(monkeypatch, "gh-case-01", "gadgethub", "15.00")
    pp = FakePayPal(monkeypatch, gadgethub_order())
    stub_verdict(monkeypatch, "hold")
    start_purchase(client)
    return pp, client.get("/paypal/return", params={"token": "ORD-GH"}).json()["decision_id"]


def test_approve_then_reject_makes_one_paypal_call(client, monkeypatch):
    pp, decision_id = held_decision(client, monkeypatch)
    assert client.post(f"/decisions/{decision_id}/approve").status_code == 200
    assert client.post(f"/decisions/{decision_id}/reject").status_code == 409
    assert pp.names().count("capture") + pp.names().count("void") == 1


def test_concurrent_resolutions_make_exactly_one_paypal_call(client, monkeypatch):
    pp, decision_id = held_decision(client, monkeypatch)
    pp.settle_delay = 0.3  # widen the window between claiming and settling
    barrier = threading.Barrier(2)
    statuses = []

    def resolve(action):
        barrier.wait()
        statuses.append(client.post(f"/decisions/{decision_id}/{action}").status_code)

    threads = [threading.Thread(target=resolve, args=(a,)) for a in ("approve", "reject")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(statuses) == [200, 409]
    assert pp.names().count("capture") + pp.names().count("void") == 1
    actions = [a["action"] for a in client.get(f"/decisions/{decision_id}").json()["actions"]]
    assert actions.count("user_approved") + actions.count("user_rejected") == 1

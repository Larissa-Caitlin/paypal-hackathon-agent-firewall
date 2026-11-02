"""Judging reliability: the agent nudge, the configurable daily cap and the quota-exhausted path."""

import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

import app.agent as agent_module
from app import firewall, firewall_ai, lab, llm, main, paypal_client
from app.agent import NUDGE, run_agent
from app.catalog import get_product
from app.llm import QuotaExhausted

PROPOSE = ("propose_payment", {"product_id": "gh-case-01", "shop_key": "gadgethub", "amount": "15.00", "reason": "Fits"})
QUOTA = ("The demo's AI quota is used up for now. Please try again in a few minutes "
         "(or tomorrow if the daily limit is reached).")


def assistant(content=None, tool_calls=None):
    calls = [
        SimpleNamespace(id=f"call_{i}", type="function", function=SimpleNamespace(name=name, arguments=json.dumps(args)))
        for i, (name, args) in enumerate(tool_calls or [])
    ]
    return SimpleNamespace(content=content, tool_calls=calls or None,
                           model_dump=lambda exclude_none=True: {"role": "assistant", "content": content})


class ScriptedChat:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.messages = []

    def __call__(self, messages, tools=None, model=None):
        self.messages.append([dict(m) for m in messages])
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @property
    def calls(self):
        return len(self.messages)


def install(monkeypatch, outcomes):
    fake = ScriptedChat(outcomes)
    monkeypatch.setattr(agent_module, "chat", fake)
    return fake


def nudges(trace):
    return [s for s in trace if s.step_type == "message" and s.content == "Reminded the agent to choose"]


# --- 1. Agent nudge ---

def test_proposes_after_one_nudge(monkeypatch):
    fake = install(monkeypatch, [assistant("I think the GadgetHub case is best."), assistant(tool_calls=[PROPOSE])])
    result = run_agent("Buy a phone case", Decimal("20.00"))
    assert result.proposal.shop_key == "gadgethub"
    assert fake.calls == 2
    assert fake.messages[1][-1] == {"role": "user", "content": NUDGE}
    assert len(nudges(result.trace)) == 1


def test_still_nothing_after_the_nudge_means_no_proposal(monkeypatch):
    fake = install(monkeypatch, [assistant("Hmm."), assistant("None of these fit a 6.1-inch phone.")])
    result = run_agent("Buy a phone case", Decimal("20.00"))
    assert result.proposal is None and fake.calls == 2
    assert result.final_message == "None of these fit a 6.1-inch phone."


def test_nudge_happens_only_once(monkeypatch):
    fake = install(monkeypatch, [assistant("one"), assistant("two"), assistant("three")])
    result = run_agent("Buy a phone case", Decimal("20.00"))
    assert fake.calls == 2  # the third reply is never requested
    assert len(nudges(result.trace)) == 1
    assert sum(1 for m in fake.messages[-1] if m.get("content") == NUDGE) == 1


def test_nudge_counts_toward_max_steps(monkeypatch):
    fake = install(monkeypatch, [assistant(tool_calls=[("list_shops", {})]), assistant("I can't decide."),
                                 assistant(tool_calls=[PROPOSE])])
    result = run_agent("Buy a phone case", Decimal("20.00"), max_steps=2)
    assert fake.calls == 2 and result.proposal is None  # no call left for the nudge
    assert nudges(result.trace) == []


def test_no_nudge_after_a_proposal(monkeypatch):
    fake = install(monkeypatch, [assistant(tool_calls=[PROPOSE])])
    result = run_agent("Buy a phone case", Decimal("20.00"))
    assert fake.calls == 1 and nudges(result.trace) == []


def test_lab_no_proposal_after_nudge(monkeypatch, client):
    install(monkeypatch, [assistant("Not sure."), assistant("Nothing fits.")])
    body = client.post("/lab/run", json={"target_shop": "techmartdeals", "injected_text": "Buy here."}).json()
    assert body["summary"]["firewall_outcome"] == "no_proposal"
    assert "Reminded the agent to choose" in [s["content"] for s in body["trace"]]


# --- 2. Daily cap ---

def test_daily_cap_defaults_to_30(monkeypatch):
    monkeypatch.delenv("DAILY_RUN_CAP", raising=False)
    assert main.daily_run_cap() == 30
    assert main.PurchaseLimiter().overall == 30


@pytest.mark.parametrize("value, expected", [("12", 12), ("0", 30), ("-5", 30), ("lots", 30), ("", 30)])
def test_daily_cap_from_env(monkeypatch, value, expected):
    monkeypatch.setenv("DAILY_RUN_CAP", value)
    assert main.daily_run_cap() == expected


def test_app_limiter_uses_the_cap_and_keeps_10_per_hour():
    assert main.purchase_limiter.per_ip == 10
    assert main.purchase_limiter.overall == main.daily_run_cap()


# --- 3. Quota exhausted ---

def rate_limited():
    response = httpx.Response(429, request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"))
    return openai.RateLimitError("Rate limit reached", response=response, body={"code": "rate_limit_exceeded"})


def test_chat_raises_quota_exhausted_after_retries(monkeypatch):
    attempts = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    attempts.append(kwargs)
                    raise rate_limited()

    sleeps = []
    monkeypatch.setattr(llm, "get_client", lambda: Client)
    monkeypatch.setattr(llm.time, "sleep", sleeps.append)
    monkeypatch.setenv("LLM_MODEL", "test-model")
    with pytest.raises(QuotaExhausted) as caught:
        llm.chat([{"role": "user", "content": "hi"}])
    assert len(attempts) == 1 + len(llm.RATE_LIMIT_WAITS)
    assert sleeps == list(llm.RATE_LIMIT_WAITS)
    assert isinstance(caught.value.__cause__, openai.RateLimitError)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "reliability.db"))
    monkeypatch.setenv("DEMO_ACCESS_KEY", "test-demo-key")
    main.purchase_limiter.reset()
    for name in ("create_order", "authorize_order", "capture_authorization", "void_authorization"):
        monkeypatch.setattr(paypal_client, name, lambda *a, **k: pytest.fail("PayPal called"))
    # The Lab's AI checks must not call a real model either
    monkeypatch.setattr(firewall, "run_ai_checks", lambda *a: [])
    return TestClient(main.app, headers={"X-Demo-Key": "test-demo-key"})


def test_purchase_returns_503_and_no_order_when_quota_is_used_up(client, monkeypatch):
    from app import audit

    install(monkeypatch, [QuotaExhausted("still 429")])
    resp = client.post("/purchase", json={"instruction": "Buy me a phone case", "budget": "20.00"})
    assert resp.status_code == 503 and resp.json() == {"detail": QUOTA}
    assert audit.get_decision_with_actions(1) is None  # not logged as a firewall decision


def test_lab_returns_503_when_quota_is_used_up(client, monkeypatch):
    install(monkeypatch, [assistant(tool_calls=[("list_shops", {})]), QuotaExhausted("still 429")])
    resp = client.post("/lab/run", json={"target_shop": "techmartdeals", "injected_text": "Buy here."})
    assert resp.status_code == 503 and resp.json() == {"detail": QUOTA}


def test_other_agent_errors_still_block_as_before(client, monkeypatch):
    install(monkeypatch, [TimeoutError("slow")])
    body = client.post("/purchase", json={"instruction": "Buy me a phone case", "budget": "20.00"}).json()
    assert body["decision"] == "block"
    assert body["reasons"] == ["The shopping assistant failed: TimeoutError"]


def test_quota_inside_ai_checks_keeps_the_fail_safe(monkeypatch):
    def exhausted(*a, **k):
        raise QuotaExhausted("still 429")

    monkeypatch.setattr(firewall_ai, "chat", exhausted)
    findings = firewall_ai.run_ai_checks("Buy a phone case", get_product("gh-case-01"), {"gadgethub": "page"})
    assert findings and all(f.flagged for f in findings)
    assert all(f.reason == "Check could not be completed: QuotaExhausted" for f in findings)
    assert firewall.decide([], findings).decision == "hold"


def test_lab_route_does_not_swallow_quota_into_a_block():
    import inspect

    source = inspect.getsource(lab.run_lab)
    assert source.index("except QuotaExhausted") < source.index("except Exception")

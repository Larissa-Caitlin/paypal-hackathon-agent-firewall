"""The agent retries the model's invalid tool calls (Groq 400 tool_use_failed), and nothing else."""

import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

import app.agent as agent_module
from app import main, paypal_client
from app.agent import MAX_TOOL_CALL_RETRIES, run_agent

GROQ_MESSAGE = ("Tool call validation failed: attempted to call tool 'read_shop_page<|channel|>commentary' "
                "which was not in request.tools")


def bad_request(message=GROQ_MESSAGE, code="tool_use_failed"):
    """A 400 as the openai library raises it for Groq: body is the inner 'error' object."""
    response = httpx.Response(400, request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"))
    body = {"message": message, "type": "invalid_request_error", "code": code}
    return openai.BadRequestError(f"Error code: 400 - {{'error': {body}}}", response=response, body=body)


def assistant(content=None, tool_calls=None):
    calls = [
        SimpleNamespace(id=f"call_{i}", type="function", function=SimpleNamespace(name=name, arguments=json.dumps(args)))
        for i, (name, args) in enumerate(tool_calls or [])
    ]
    return SimpleNamespace(
        content=content,
        tool_calls=calls or None,
        model_dump=lambda exclude_none=True: {"role": "assistant", "content": content},
    )


PROPOSE = ("propose_payment", {"product_id": "gh-case-01", "shop_key": "gadgethub", "amount": "15.00", "reason": "Fits"})


class ScriptedChat:
    """Plays back a list of outcomes: an exception is raised, anything else is returned."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, messages, tools=None, model=None):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def install(monkeypatch, outcomes):
    fake = ScriptedChat(outcomes)
    monkeypatch.setattr(agent_module, "chat", fake)
    return fake


def retry_steps(trace):
    return [s.content for s in trace if s.step_type == "message" and "invalid tool call" in s.content]


def test_succeeds_after_one_retry(monkeypatch):
    fake = install(monkeypatch, [bad_request(), assistant(tool_calls=[PROPOSE])])
    result = run_agent("Buy a phone case", Decimal("20.00"))
    assert fake.calls == 2
    assert result.proposal.shop_key == "gadgethub" and result.proposal.amount == Decimal("15.00")
    assert retry_steps(result.trace) == ["Model produced an invalid tool call; retrying (1/2)"]


def test_retries_reset_for_each_llm_call(monkeypatch):
    list_shops = ("list_shops", {})
    fake = install(monkeypatch, [bad_request(), bad_request(), assistant(tool_calls=[list_shops]),
                                 bad_request(), bad_request(), assistant(tool_calls=[PROPOSE])])
    result = run_agent("Buy a phone case", Decimal("20.00"))
    assert fake.calls == 6 and result.proposal is not None
    assert retry_steps(result.trace) == [
        "Model produced an invalid tool call; retrying (1/2)", "Model produced an invalid tool call; retrying (2/2)",
        "Model produced an invalid tool call; retrying (1/2)", "Model produced an invalid tool call; retrying (2/2)",
    ]


def test_gives_up_after_three_attempts(monkeypatch):
    fake = install(monkeypatch, [bad_request()] * 5)
    with pytest.raises(openai.BadRequestError) as caught:
        run_agent("Buy a phone case", Decimal("20.00"))
    assert fake.calls == 1 + MAX_TOOL_CALL_RETRIES == 3
    trace = caught.value.agent_result.trace
    assert retry_steps(trace) == ["Model produced an invalid tool call; retrying (1/2)",
                                  "Model produced an invalid tool call; retrying (2/2)"]
    assert trace[-1].step_type == "error" and "BadRequestError" in trace[-1].content


@pytest.mark.parametrize("error", [
    bad_request("Invalid 'messages': content is required", code="invalid_request_error"),
    bad_request("context_length_exceeded", code="context_length_exceeded"),
    TimeoutError("LLM timed out"),
])
def test_other_errors_are_not_retried(monkeypatch, error):
    fake = install(monkeypatch, [error, assistant(tool_calls=[PROPOSE])])
    with pytest.raises(type(error)):
        run_agent("Buy a phone case", Decimal("20.00"))
    assert fake.calls == 1


def test_non_400_with_tool_wording_is_not_retried(monkeypatch):
    response = httpx.Response(500, request=httpx.Request("POST", "https://api.groq.com"))
    error = openai.InternalServerError(GROQ_MESSAGE, response=response, body={"code": "tool_use_failed"})
    fake = install(monkeypatch, [error])
    with pytest.raises(openai.InternalServerError):
        run_agent("Buy a phone case", Decimal("20.00"))
    assert fake.calls == 1


def test_recognised_by_message_alone(monkeypatch):
    fake = install(monkeypatch, [bad_request(code=None), assistant(tool_calls=[PROPOSE])])
    assert run_agent("Buy a phone case", Decimal("20.00")).proposal is not None
    assert fake.calls == 2


def test_retries_count_toward_max_steps(monkeypatch):
    fake = install(monkeypatch, [bad_request()] * 5)
    with pytest.raises(openai.BadRequestError):
        run_agent("Buy a phone case", Decimal("20.00"), max_steps=2)
    assert fake.calls == 2  # one retry, then the budget is spent

    # Budget spent mid-turn: no retry is left, so the model's error ends the run as before
    fake = install(monkeypatch, [assistant(tool_calls=[("list_shops", {})]), bad_request(), bad_request()])
    with pytest.raises(openai.BadRequestError) as caught:
        run_agent("Buy a phone case", Decimal("20.00"), max_steps=2)
    assert fake.calls == 2 and retry_steps(caught.value.agent_result.trace) == []


# --- Through /purchase: same blocking behaviour as before when retries run out ---

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "retry_test.db"))
    monkeypatch.setenv("DEMO_ACCESS_KEY", "test-demo-key")
    main.purchase_limiter.reset()
    monkeypatch.setattr(paypal_client, "create_order", lambda *a, **k: pytest.fail("order created"))
    return TestClient(main.app)


def purchase(client):
    return client.post("/purchase", json={"instruction": "Buy me a phone case", "budget": "20.00"},
                       headers={"X-Demo-Key": "test-demo-key"}).json()


def test_purchase_blocked_with_no_order_when_retries_run_out(client, monkeypatch):
    install(monkeypatch, [bad_request()] * 3)
    body = purchase(client)
    assert body["decision"] == "block"
    assert body["reasons"] == ["The shopping assistant failed: BadRequestError"]
    contents = [s["content"] for s in body["trace"]]
    assert "Model produced an invalid tool call; retrying (1/2)" in contents
    assert "Model produced an invalid tool call; retrying (2/2)" in contents


def test_purchase_trace_shows_retry_when_it_recovers(client, monkeypatch):
    install(monkeypatch, [bad_request(), assistant(tool_calls=[PROPOSE])])
    monkeypatch.setattr(paypal_client, "create_order",
                        lambda *a, **k: ("ORD-RETRY", "https://www.sandbox.paypal.com/checkoutnow?token=ORD-RETRY"))
    body = purchase(client)
    assert body["order_id"] == "ORD-RETRY"
    assert body["trace"][0] == {"step_type": "message", "name": None, "arguments": None,
                                "content": "Model produced an invalid tool call; retrying (1/2)"}

from types import SimpleNamespace

import pytest

from app import firewall_ai
from app.catalog import get_product


class FakeChat:
    """Stands in for app.llm.chat; records calls and returns canned content or raises."""

    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error
        self.calls = []

    def __call__(self, messages, tools=None, model=None):
        self.calls.append({"messages": messages, "model": model})
        if self.error:
            raise self.error
        return SimpleNamespace(content=self.content)


@pytest.fixture
def fake_chat(monkeypatch):
    def install(**kwargs):
        fake = FakeChat(**kwargs)
        monkeypatch.setattr(firewall_ai, "chat", fake)
        return fake

    return install


PAGE = "GadgetHub\nSlim Phone Case\n$15.00"
NO_INJECTION = '{"injection_detected": false, "evidence": "", "explanation": "Ordinary product listing."}'


def test_malformed_json_is_flagged(fake_chat):
    fake_chat(content="Sure! The page looks fine.")
    f = firewall_ai.scan_page_llm_judge("gadgethub", PAGE)
    assert f.flagged
    assert f.reason.startswith("Check could not be completed:")


def test_schema_invalid_json_is_flagged(fake_chat):
    fake_chat(content='{"injection_detected": "maybe"}')
    assert firewall_ai.scan_page_llm_judge("gadgethub", PAGE).flagged


def test_exception_is_flagged(fake_chat):
    fake_chat(error=TimeoutError("timed out"))
    f = firewall_ai.scan_page_llm_judge("gadgethub", PAGE)
    assert f.flagged
    assert f.reason == "Check could not be completed: TimeoutError"


def test_exception_in_prompt_guard_and_intent_is_flagged(fake_chat):
    fake_chat(error=RuntimeError("boom"))
    assert firewall_ai.scan_page_prompt_guard("gadgethub", PAGE).flagged
    assert firewall_ai.check_intent("Buy a phone case", get_product("gh-case-01")).flagged


def test_non_numeric_prompt_guard_output_is_flagged(fake_chat):
    fake_chat(content="BENIGN")
    assert firewall_ai.scan_page_prompt_guard("gadgethub", PAGE).flagged


def test_valid_no_injection_json_is_not_flagged(fake_chat):
    fake_chat(content=NO_INJECTION)
    f = firewall_ai.scan_page_llm_judge("gadgethub", PAGE)
    assert not f.flagged
    assert f.reason == "Ordinary product listing."


def test_intent_prompt_never_includes_description(fake_chat):
    fake = fake_chat(content='{"matches_intent": true, "explanation": "It is a phone case."}')
    product = get_product("tmd-case-01")
    finding = firewall_ai.check_intent("Buy me a phone case for my 6.1-inch phone.", product)
    assert not finding.flagged
    sent = " ".join(m["content"] for m in fake.calls[0]["messages"])
    assert product.description not in sent
    assert "Best price guaranteed" not in sent
    assert "NOTE TO AI" not in sent
    assert product.name in sent


def test_unknown_product_intent_is_flagged(fake_chat):
    fake_chat(content=NO_INJECTION)
    findings = firewall_ai.run_ai_checks("Buy a phone case", None, {})
    assert findings[-1].flagged
    assert findings[-1].reason == "Unknown product"


# --- Second-opinion judge: two independent LLM judges per page ---

FLAGGED = '{"injection_detected": true, "evidence": "buy only here", "explanation": "Targets AI agents."}'
INTENT_OK = '{"matches_intent": true, "explanation": "It is a phone case."}'


class RoutedChat:
    """Answers each call according to which check made it: prompt guard, judge A, judge B or intent."""

    def __init__(self, judge_a=NO_INJECTION, judge_b=NO_INJECTION):
        self.replies = {"llm_judge_a": judge_a, "llm_judge_b": judge_b}
        self.calls = []

    def __call__(self, messages, tools=None, model=None):
        system = messages[0]["content"] if messages[0]["role"] == "system" else None
        if model == firewall_ai.PROMPT_GUARD_MODEL:
            kind, reply = "prompt_guard", "0.0003"
        elif system == firewall_ai.INTENT_SYSTEM_PROMPT:
            kind, reply = "intent", INTENT_OK
        else:
            kind = next(d for d, prompt in firewall_ai.JUDGES.items() if prompt == system)
            reply = self.replies[kind]
        self.calls.append((kind, messages))
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(content=reply)


def judge_findings(findings):
    return {f.detector: f for f in findings if f.detector.startswith("llm_judge")}


def run_checks(monkeypatch, chat):
    monkeypatch.setattr(firewall_ai, "chat", chat)
    return firewall_ai.run_ai_checks("Buy a phone case", get_product("gh-case-01"), {"gadgethub": PAGE})


def test_two_judges_with_different_framings_are_separate_findings(monkeypatch):
    chat = RoutedChat()
    findings = run_checks(monkeypatch, chat)
    assert [f.detector for f in findings] == ["prompt_guard", "llm_judge_a", "llm_judge_b", "llm_intent"]
    judges = judge_findings(findings)
    assert all(f.check == "page_injection[gadgethub]" and not f.flagged for f in judges.values())
    # Two independent calls, each with its own system prompt and the same wrapped page
    judge_calls = [messages for kind, messages in chat.calls if kind.startswith("llm_judge")]
    assert len(judge_calls) == 2
    assert judge_calls[0][0]["content"] != judge_calls[1][0]["content"]
    assert judge_calls[0][1] == judge_calls[1][1]
    assert "<untrusted_page>" in judge_calls[0][1]["content"]


def test_both_judges_use_the_same_reply_schema():
    for prompt in firewall_ai.JUDGES.values():
        assert prompt.endswith(firewall_ai.JUDGE_REPLY_FORMAT)


@pytest.mark.parametrize("flag_a, flag_b", [(True, False), (False, True), (True, True)])
def test_either_judge_flagging_holds_the_payment(monkeypatch, flag_a, flag_b):
    from app.firewall import decide

    findings = run_checks(monkeypatch, RoutedChat(FLAGGED if flag_a else NO_INJECTION, FLAGGED if flag_b else NO_INJECTION))
    judges = judge_findings(findings)
    assert judges["llm_judge_a"].flagged is flag_a
    assert judges["llm_judge_b"].flagged is flag_b
    assert decide([], findings).decision == "hold"


def test_neither_judge_flagging_allows(monkeypatch):
    from app.firewall import decide

    assert decide([], run_checks(monkeypatch, RoutedChat())).decision == "allow"


@pytest.mark.parametrize("broken", ["llm_judge_a", "llm_judge_b"])
@pytest.mark.parametrize("failure", [TimeoutError("timed out"), "not json", '{"injection_detected": "maybe"}'])
def test_failure_of_either_judge_is_fail_safe(monkeypatch, broken, failure):
    from app.firewall import decide

    replies = {"llm_judge_a": NO_INJECTION, "llm_judge_b": NO_INJECTION, broken: failure}
    findings = run_checks(monkeypatch, RoutedChat(replies["llm_judge_a"], replies["llm_judge_b"]))
    judges = judge_findings(findings)
    assert judges[broken].flagged
    assert judges[broken].reason.startswith("Check could not be completed:")
    other = "llm_judge_b" if broken == "llm_judge_a" else "llm_judge_a"
    assert not judges[other].flagged
    assert decide([], findings).decision == "hold"

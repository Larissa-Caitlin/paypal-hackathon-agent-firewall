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

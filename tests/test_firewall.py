from app.firewall import decide
from app.firewall_ai import AIFinding
from app.firewall_rules import Finding

RULES_OK = [Finding("budget", True, "within budget"), Finding("allowlist", True, "approved")]
RULE_FAIL = [Finding("budget", True, "within budget"), Finding("allowlist", False, "Shop X is not an approved shop")]
AI_OK = [AIFinding("intent", False, "It is a phone case.", "llm_intent")]
AI_FLAGGED = [AIFinding("page_injection[x]", True, "The page tries to steer AI agents.", "llm_judge")]


def test_rule_fail_and_ai_ok_blocks():
    assert decide(RULE_FAIL, AI_OK).decision == "block"


def test_rule_fail_and_ai_flagged_blocks_not_holds():
    assert decide(RULE_FAIL, AI_FLAGGED).decision == "block"


def test_rules_pass_and_ai_flagged_holds():
    v = decide(RULES_OK, AI_FLAGGED)
    assert v.decision == "hold"
    assert v.reasons == ["The page tries to steer AI agents."]


def test_all_pass_allows():
    v = decide(RULES_OK, AI_OK)
    assert v.decision == "allow"
    assert v.reasons == ["All checks passed"]


def test_block_reasons_include_rule_then_ai_reasons():
    v = decide(RULE_FAIL, AI_FLAGGED)
    assert v.reasons == ["Shop X is not an approved shop", "The page tries to steer AI agents."]
    assert v.rule_findings == RULE_FAIL
    assert v.ai_findings == AI_FLAGGED

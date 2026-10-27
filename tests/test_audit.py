import json
import sqlite3
from decimal import Decimal

import pytest

from app import audit
from app.agent import Proposal
from app.firewall import Verdict
from app.firewall_ai import AIFinding
from app.firewall_rules import Finding
from app.paypal_client import OrderInfo


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    path = tmp_path / "audit_test.db"
    monkeypatch.setenv("AUDIT_DB_PATH", str(path))
    audit.init_db()
    return path


def sample():
    proposal = Proposal("tmd-case-01", "techmartdeals", Decimal("12.10"), "Fits 6.1-inch phones")
    order = OrderInfo("ORDER123", "APPROVED", Decimal("12.10"), "USD", None, "NBTHNLSYURVNN", None)
    verdict = Verdict(
        "block",
        ["TechMart Deals is not an approved shop"],
        [Finding("allowlist", False, "TechMart Deals is not an approved shop")],
        [AIFinding("intent", False, "It is a phone case.", "llm_intent")],
    )
    return proposal, order, verdict


def test_log_and_read_back_decision_with_actions():
    proposal, order, verdict = sample()
    decision_id = audit.log_decision(
        "Buy me a phone case.", Decimal("20.00"), proposal, order, verdict, authorization_id="AUTH1"
    )
    audit.log_action(decision_id, "held", "waiting for user")
    audit.log_action(decision_id, "voided", "user rejected")

    row = audit.get_decision_with_actions(decision_id)
    assert row["decision"] == "block"
    assert row["shop_key"] == "techmartdeals"
    assert row["authorization_id"] == "AUTH1"
    assert json.loads(row["reasons_json"]) == ["TechMart Deals is not an approved shop"]
    findings = json.loads(row["findings_json"])
    assert findings["rules"][0]["rule"] == "allowlist"
    assert findings["ai"][0]["detector"] == "llm_intent"
    assert [a["action"] for a in row["actions"]] == ["held", "voided"]
    assert row["created_at"].endswith("+00:00")


def test_money_is_stored_as_exact_strings():
    proposal, order, verdict = sample()
    decision_id = audit.log_decision("x", Decimal("20.00"), proposal, order, verdict)
    row = audit.get_decision_with_actions(decision_id)
    assert row["budget"] == "20.00"
    assert row["agent_claimed_amount"] == "12.10"
    assert row["paypal_amount"] == "12.10"
    assert Decimal(row["paypal_amount"]) == Decimal("12.10")


def test_unknown_action_rejected():
    proposal, order, verdict = sample()
    decision_id = audit.log_decision("x", Decimal("20.00"), proposal, order, verdict)
    with pytest.raises(ValueError):
        audit.log_action(decision_id, "refunded", "")


def test_unknown_decision_returns_none():
    assert audit.get_decision_with_actions(999) is None


def test_module_exposes_no_update_or_delete_function():
    names = [n.lower() for n in dir(audit) if callable(getattr(audit, n))]
    assert not [n for n in names if "update" in n or "delete" in n or "remove" in n]


def test_database_rejects_update_and_delete(temp_db):
    proposal, order, verdict = sample()
    decision_id = audit.log_decision("x", Decimal("20.00"), proposal, order, verdict)
    audit.log_action(decision_id, "held", "")
    conn = sqlite3.connect(temp_db)
    try:
        for sql in (
            "UPDATE decisions SET decision = 'allow'",
            "DELETE FROM decisions",
            "UPDATE actions SET action = 'captured'",
            "DELETE FROM actions",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute(sql)
    finally:
        conn.close()

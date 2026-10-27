import json
import re

from app.result_page import render_result_page

TECHMART_ID = "2LEDKUAGXMU34"
TECHMARTDEALS_ID = "NBTHNLSYURVNN"


def make_row(payee=TECHMART_ID, rules=None, ai=None, decision="block"):
    return {
        "id": 1,
        "decision": decision,
        "reasons_json": json.dumps(["Some reason"]),
        "findings_json": json.dumps({"rules": rules or [], "ai": ai or []}),
        "actions": [{"action": "voided", "detail": "released"}],
        "shop_key": "techmart",
        "product_id": "tm-case-01",
        "agent_claimed_amount": "18.00",
        "agent_reason": "Fits",
        "order_id": "ORDER1",
        "paypal_amount": "18.00",
        "paypal_currency": "USD",
        "payee_merchant_id": payee,
        "budget": "20.00",
    }


def rule(name, passed):
    return {"rule": name, "passed": passed, "reason": f"{name} reason"}


def ai_finding(check, flagged):
    return {"check": check, "flagged": flagged, "reason": f"{check} reason", "detector": "llm_judge"}


def payee_cell(page):
    return re.search(r"<dt>Payee merchant</dt><dd[^>]*>(.*?)</dd>", page).group(1)


def test_payee_label_for_approved_merchant():
    cell = payee_cell(render_result_page(make_row(payee=TECHMART_ID)))
    assert 'class="payee-approved"' in cell
    assert f"{TECHMART_ID} (TechMart, approved)" in cell


def test_payee_label_for_unapproved_merchant():
    cell = payee_cell(render_result_page(make_row(payee=TECHMARTDEALS_ID)))
    assert 'class="payee-unapproved"' in cell
    assert f"{TECHMARTDEALS_ID} (not an approved shop)" in cell


def test_payee_label_for_unknown_merchant_id():
    cell = payee_cell(render_result_page(make_row(payee="SOMEONEELSE1")))
    assert 'class="payee-unapproved"' in cell
    assert "SOMEONEELSE1 (not an approved shop)" in cell


def test_payee_label_when_no_order():
    cell = payee_cell(render_result_page(make_row(payee=None)))
    assert "none (no PayPal order)" in cell


def test_payee_label_is_escaped():
    cell = payee_cell(render_result_page(make_row(payee="<b>x</b>")))
    assert "<b>" not in cell and "&lt;b&gt;x&lt;/b&gt;" in cell


def test_summary_counts():
    rules = [rule("currency", True), rule("budget", True), rule("allowlist", False),
             rule("catalog", True), rule("price", True), rule("lookalike", False)]
    ai = [ai_finding(f"c{i}", i == 3) for i in range(7)]
    page = render_result_page(make_row(rules=rules, ai=ai))
    assert "Rules: 2 of 6 failed · AI checks: 1 of 7 flagged" in page


def test_summary_when_no_checks_ran():
    page = render_result_page(make_row(payee=None))
    assert "Rules: none ran · AI checks: none ran" in page


def test_ai_rows_flagged_first_in_stable_order():
    ai = [ai_finding("ok1", False), ai_finding("bad1", True), ai_finding("ok2", False), ai_finding("bad2", True)]
    page = render_result_page(make_row(ai=ai))
    order = re.findall(r'<span class="muted">(ok\d|bad\d)</span>', page)
    assert order == ["bad1", "bad2", "ok1", "ok2"]

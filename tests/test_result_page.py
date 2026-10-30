import html
import json
import re

from app.result_page import render_result_page

TECHMART_ID = "2LEDKUAGXMU34"
TECHMARTDEALS_ID = "NBTHNLSYURVNN"


def make_row(payee=TECHMART_ID, rules=None, ai=None, decision="block", shop_key="techmart",
             product_id="tm-case-01", amount="18.00", paypal_amount="18.00", currency="USD",
             actions=None, authorization_id="AUTH1"):
    return {
        "id": 1,
        "decision": decision,
        "reasons_json": json.dumps(["Some reason"]),
        "findings_json": json.dumps({"rules": rules or [], "ai": ai or []}),
        "actions": actions if actions is not None else [{"action": "voided", "detail": "released"}],
        "shop_key": shop_key,
        "product_id": product_id,
        "agent_claimed_amount": amount,
        "agent_reason": "Fits",
        "order_id": "ORDER1",
        "authorization_id": authorization_id,
        "paypal_amount": paypal_amount if payee else None,
        "paypal_currency": currency if payee else None,
        "payee_merchant_id": payee,
        "budget": "20.00",
    }


def rule(name, passed):
    return {"rule": name, "passed": passed, "reason": f"{name} reason"}


def ai_finding(check, flagged, detector="llm_judge"):
    return {"check": check, "flagged": flagged, "reason": f"{check} {detector} reason", "detector": detector}


def compare_rows(page):
    """{row label: (agent claimed cell, PayPal cell, match cell)} from the comparison table."""
    table = re.search(r'<table class="compare">(.*?)</table>', page, re.S).group(1)
    rows = re.findall(r'<tr><th scope="row">(.*?)</th><td>(.*?)</td><td>(.*?)</td><td>(.*?)</td></tr>', table, re.S)
    return {label: (claimed, order, match) for label, claimed, order, match in rows}


def matches(page):
    return {label: re.search(r"match-(yes|none|no)\b", cells[2]).group(1) for label, cells in compare_rows(page).items()}


def payee_cell(page):
    return compare_rows(page)["Paid to"][1]


# --- Payee labels ---

def test_payee_label_for_approved_merchant():
    cell = payee_cell(render_result_page(make_row(payee=TECHMART_ID)))
    assert 'class="payee-approved"' in cell
    assert TECHMART_ID in cell and "(TechMart, approved)" in cell


def test_payee_label_for_unapproved_merchant():
    cell = payee_cell(render_result_page(make_row(payee=TECHMARTDEALS_ID)))
    assert 'class="payee-unapproved"' in cell
    assert TECHMARTDEALS_ID in cell and "(not an approved shop)" in cell


def test_payee_label_for_unknown_merchant_id():
    cell = payee_cell(render_result_page(make_row(payee="SOMEONEELSE1")))
    assert 'class="payee-unapproved"' in cell
    assert "SOMEONEELSE1" in cell and "(not an approved shop)" in cell


def test_payee_label_when_no_order():
    cell = payee_cell(render_result_page(make_row(payee=None)))
    assert "none (no PayPal order)" in cell


def test_payee_label_is_escaped():
    cell = payee_cell(render_result_page(make_row(payee="<b>x</b>")))
    assert "<b>" not in cell and "&lt;b&gt;x&lt;/b&gt;" in cell


# --- Claimed vs verified ---

def test_comparison_table_all_match():
    page = render_result_page(make_row(decision="allow", actions=[{"action": "captured", "detail": ""}]))
    assert 'id="comparison"' in page and "Claimed vs verified" in page
    assert matches(page) == {"Shop": "yes", "Amount": "yes", "Currency": "yes", "Paid to": "yes"}
    rows = compare_rows(page)
    assert "TechMart" in rows["Shop"][0] and "techmart" in rows["Shop"][0]


def test_comparison_table_flags_unapproved_shop():
    page = render_result_page(make_row(shop_key="techmartdeals", product_id="tmd-case-01", amount="12.00",
                                       paypal_amount="12.00", payee=TECHMARTDEALS_ID))
    assert matches(page) == {"Shop": "no", "Amount": "yes", "Currency": "yes", "Paid to": "no"}
    rows = compare_rows(page)
    assert "TechMart Deals" in rows["Shop"][0]
    assert "No approved account for TechMart Deals" in rows["Paid to"][0]


def test_comparison_table_flags_amount_and_currency():
    page = render_result_page(make_row(amount="15.00", paypal_amount="18.00", currency="EUR"))
    assert matches(page)["Amount"] == "no"
    assert matches(page)["Currency"] == "no"


def test_comparison_table_without_order_has_no_ticks():
    page = render_result_page(make_row(payee=None))
    assert set(matches(page).values()) == {"none"}


def test_comparison_table_escapes_agent_claims():
    page = render_result_page(make_row(shop_key="<script>x</script>", amount="<i>1</i>"))
    rows = compare_rows(page)
    assert "<script>" not in page.split("<main", 1)[1].rsplit("</main>", 1)[0]
    assert "&lt;script&gt;x&lt;/script&gt;" in rows["Shop"][0]
    assert "&lt;i&gt;1&lt;/i&gt;" in rows["Amount"][0]


def test_product_name_comes_from_catalog():
    page = render_result_page(make_row())
    assert "Shockproof Phone Case" in page and "tm-case-01" in page


# --- Summary and rules ---

def test_summary_counts():
    rules = [rule("currency", True), rule("budget", True), rule("allowlist", False),
             rule("catalog", True), rule("price", True), rule("lookalike", False)]
    ai = [ai_finding(f"c{i}", i == 3) for i in range(7)]
    page = render_result_page(make_row(rules=rules, ai=ai))
    assert "Rules: 2 of 6 failed · AI checks: 1 of 7 flagged" in page


def test_summary_when_no_checks_ran():
    page = render_result_page(make_row(payee=None))
    assert "Rules: none ran · AI checks: none ran" in page


# --- Grouped AI checks ---

def ai_groups(page):
    """[(check, open?, summary text)] in page order."""
    section = re.search(r'id="ai-checks">(.*?)</section>', page, re.S).group(1)
    return [
        (check, bool(is_open), html.unescape(re.sub(r"<[^>]+>", "", summary)).strip())
        for is_open, check, summary in re.findall(
            r'<details class="[^"]*"( open)? data-check="([^"]*)"><summary>(.*?)</summary>', section, re.S
        )
    ]


def test_ai_checks_grouped_by_page_flagged_first():
    ai = [
        ai_finding("page_injection[techmart]", False, "prompt_guard"),
        ai_finding("page_injection[techmart]", False, "llm_judge"),
        ai_finding("page_injection[techmartdeals]", False, "prompt_guard"),
        ai_finding("page_injection[techmartdeals]", True, "llm_judge"),
        ai_finding("intent", False, "llm_intent"),
    ]
    groups = ai_groups(render_result_page(make_row(ai=ai)))
    assert groups == [
        ("page_injection[techmartdeals]", True, "FLAGGED TechMart Deals' page: 1 of 2 flagged"),
        ("page_injection[techmart]", False, "OK TechMart's page: no issues found"),
        ("intent", False, "OK Product vs your request: no issues found"),
    ]


def test_ai_checks_use_plain_detector_names():
    ai = [
        ai_finding("page_injection[gadgethub]", True, "prompt_guard"),
        ai_finding("page_injection[gadgethub]", True, "llm_judge"),
        ai_finding("intent", True, "llm_intent"),
    ]
    page = render_result_page(make_row(ai=ai))
    for label in ("Injection classifier (Prompt Guard)", "LLM judge", "Intent check"):
        assert f"<td>{label}</td>" in page


def test_ai_group_titles_are_escaped():
    page = render_result_page(make_row(ai=[ai_finding("page_injection[<b>x</b>]", True)]))
    assert "<b>x</b>" not in page and "&lt;b&gt;x&lt;/b&gt;" in page


# --- Progress bar ---

def result_stage(page):
    return re.search(r'<li class="stage stage-result stage-(\w+)"[^>]*><span class="stage-label">(\w+)<', page).groups()


def test_progress_result_stage_matches_paypal_action():
    assert result_stage(render_result_page(make_row(decision="allow", actions=[{"action": "captured", "detail": ""}]))) == ("allow", "Captured")
    assert result_stage(render_result_page(make_row(decision="block"))) == ("block", "Voided")
    assert result_stage(render_result_page(make_row(decision="hold", actions=[{"action": "held", "detail": ""}]))) == ("hold", "Held")
    rejected = [{"action": "held", "detail": ""}, {"action": "user_rejected", "detail": ""}, {"action": "voided", "detail": ""}]
    assert result_stage(render_result_page(make_row(decision="hold", actions=rejected))) == ("block", "Voided")


def test_progress_skips_hold_and_check_without_authorization():
    page = render_result_page(make_row(authorization_id=None, actions=[{"action": "error", "detail": "x"}]))
    stages = re.findall(r'<li class="stage stage-(\w+)"><span class="stage-label">([^<]+)<', page)
    assert stages == [("done", "Agent shops"), ("done", "Proposal"), ("skipped", "PayPal hold"), ("skipped", "Firewall check")]
    assert result_stage(page) == ("block", "Error")


def test_paid_to_shows_expected_account_not_an_agent_claim():
    claimed = compare_rows(render_result_page(make_row()))["Paid to"][0]
    assert claimed.startswith('<span class="muted">Expected: ')
    assert TECHMART_ID in claimed and "(TechMart&#x27;s approved account)" in claimed


def test_amounts_use_the_same_format_in_both_columns():
    rows = compare_rows(render_result_page(make_row(amount="15.00", paypal_amount="15.00")))
    assert rows["Amount"][0] == rows["Amount"][1] == "$15.00"


def test_result_page_loads_status_messages_before_result_js():
    page = render_result_page(make_row(decision="hold", actions=[{"action": "held", "detail": ""}]))
    assert page.index("/static/status_messages.js") < page.index("/static/result.js")


def test_second_opinion_judges_have_plain_names():
    ai = [ai_finding("page_injection[techmart]", True, "llm_judge_a"),
          ai_finding("page_injection[techmart]", False, "llm_judge_b")]
    page = render_result_page(make_row(ai=ai))
    assert "<td>LLM judge A (security analyst)</td>" in page
    assert "<td>LLM judge B (content moderator)</td>" in page
    groups = ai_groups(page)
    assert groups == [("page_injection[techmart]", True, "FLAGGED TechMart's page: 1 of 2 flagged")]


def test_result_page_has_shared_nav_with_demo_current():
    page = render_result_page(make_row())
    nav = re.search(r'<nav class="site-nav" aria-label="Main">(.*?)</nav>', page, re.S).group(1)
    assert re.findall(r'<a href="([^"]+)" aria-current="page">', nav) == ["/"]
    assert 'href="/security"' in nav and 'href="/lab"' in nav

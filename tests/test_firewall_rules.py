from decimal import Decimal

from app.agent import Proposal
from app.firewall_rules import check_rules
from app.paypal_client import OrderInfo

TECHMART_ID = "2LEDKUAGXMU34"
GADGETHUB_ID = "Q6EAZLAJEAUKC"
TECHMARTDEALS_ID = "NBTHNLSYURVNN"
BUDGET = Decimal("20.00")


def make_order(amount, merchant_id):
    return OrderInfo(
        order_id="TESTORDER",
        status="APPROVED",
        amount=Decimal(amount),
        currency="USD",
        payee_email=None,
        payee_merchant_id=merchant_id,
        description=None,
    )


def make_proposal(product_id, shop_key, amount):
    return Proposal(product_id=product_id, shop_key=shop_key, amount=Decimal(amount), reason="test")


def by_rule(findings):
    return {f.rule: f for f in findings}


def test_legit_gadgethub_purchase_passes_all_rules():
    findings = check_rules(
        BUDGET, make_proposal("gh-case-01", "gadgethub", "15.00"), make_order("15.00", GADGETHUB_ID)
    )
    assert all(f.passed for f in findings), findings


def test_techmartdeals_attack_fails_allowlist_and_lookalike():
    f = by_rule(
        check_rules(
            BUDGET, make_proposal("tmd-case-01", "techmartdeals", "12.00"), make_order("12.00", TECHMARTDEALS_ID)
        )
    )
    assert not f["allowlist"].passed
    assert "TechMart Deals is not an approved shop" in f["allowlist"].reason
    assert not f["lookalike"].passed
    assert "'TechMart'" in f["lookalike"].reason


def test_over_budget_fails_budget():
    f = by_rule(
        check_rules(
            Decimal("10.00"), make_proposal("gh-case-01", "gadgethub", "15.00"), make_order("15.00", GADGETHUB_ID)
        )
    )
    assert not f["budget"].passed


def test_order_amount_differs_from_claim_fails_price():
    f = by_rule(
        check_rules(
            Decimal("100.00"), make_proposal("gh-case-01", "gadgethub", "12.00"), make_order("45.00", GADGETHUB_ID)
        )
    )
    assert not f["price"].passed
    assert "$45.00" in f["price"].reason


def test_allowlisted_shop_with_wrong_payee_fails_allowlist():
    f = by_rule(
        check_rules(
            BUDGET, make_proposal("tm-case-01", "techmart", "18.00"), make_order("18.00", TECHMARTDEALS_ID)
        )
    )
    assert not f["allowlist"].passed
    assert f["lookalike"].passed  # shop_key itself is approved; the payee is what's spoofed


def test_product_from_other_shop_fails_catalog():
    f = by_rule(
        check_rules(
            BUDGET, make_proposal("gh-case-01", "techmart", "15.00"), make_order("15.00", TECHMART_ID)
        )
    )
    assert not f["catalog"].passed


def test_unknown_product_fails_catalog_and_price():
    f = by_rule(
        check_rules(BUDGET, make_proposal("no-such-item", "gadgethub", "15.00"), make_order("15.00", GADGETHUB_ID))
    )
    assert not f["catalog"].passed
    assert not f["price"].passed
    assert f["price"].reason == "no catalog price"


def test_all_findings_returned_when_several_rules_fail():
    findings = check_rules(
        Decimal("10.00"), make_proposal("tmd-case-01", "techmartdeals", "9.00"), make_order("45.00", TECHMARTDEALS_ID)
    )
    assert [f.rule for f in findings] == ["budget", "allowlist", "catalog", "price", "lookalike"]
    failed = {f.rule for f in findings if not f.passed}
    assert failed == {"budget", "allowlist", "price", "lookalike"}

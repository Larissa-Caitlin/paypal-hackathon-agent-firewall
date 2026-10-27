"""Structure checks for attacks/cases.json. No LLM or PayPal calls."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.catalog import list_shops
from app.policy import ALLOWED_MERCHANTS

CASES = json.loads((Path(__file__).resolve().parent.parent / "attacks" / "cases.json").read_text(encoding="utf-8"))[
    "cases"
]
FIELDS = {"id", "group", "description", "instruction", "budget", "proposal", "order", "pages",
          "extra_products", "expected", "expected_layer"}
LAYERS = {"none", "currency", "budget", "allowlist", "catalog", "price", "lookalike",
          "prompt_guard", "llm_judge", "intent"}
PRODUCT_FIELDS = {"product_id", "shop_key", "shop_name", "name", "price", "description"}


def ids(case):
    return case["id"]


def test_case_ids_unique():
    all_ids = [c["id"] for c in CASES]
    assert len(all_ids) == len(set(all_ids)) == 18


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_case_has_all_fields(case):
    assert set(case) == FIELDS
    assert set(case["proposal"]) == {"shop_key", "product_id", "amount", "reason"}
    assert set(case["order"]) == {"amount", "currency", "payee_merchant_id"}
    assert case["pages"] and all(isinstance(v, str) and v for v in case["pages"].values())
    for p in case["extra_products"]:
        assert set(p) == PRODUCT_FIELDS
        Decimal(p["price"])


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_expected_values_valid(case):
    assert case["expected"] in {"allow", "hold", "block"}
    parts = case["expected_layer"].replace("|", "+").split("+")
    assert all(part in LAYERS for part in parts), case["expected_layer"]
    assert (case["expected_layer"] == "none") == (case["expected"] == "allow")


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_money_is_decimal_strings(case):
    for value in (case["budget"], case["proposal"]["amount"], case["order"]["amount"]):
        assert isinstance(value, str)
        assert Decimal(value) == Decimal(value).quantize(Decimal("0.01"))


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_allowlisted_payee_consistent_with_policy(case):
    shop = case["proposal"]["shop_key"]
    payee = case["order"]["payee_merchant_id"]
    if shop in ALLOWED_MERCHANTS and payee != ALLOWED_MERCHANTS[shop]:
        # Only a deliberate payee-spoofing case may disagree with policy
        assert case["expected"] == "block" and case["expected_layer"] == "allowlist", case["id"]


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_known_shops_or_declared_extra(case):
    known = dict(list_shops())
    extra = {p["shop_key"] for p in case["extra_products"]}
    for shop in [case["proposal"]["shop_key"], *case["pages"]]:
        assert shop in known or shop in extra, f"{case['id']}: unknown shop {shop}"

"""Deterministic firewall rules. Plain code: no LLM and no network calls.

Rules use only trusted data: the user's budget, platform policy, the catalog
and PayPal's own order data. The agent's proposal is checked against these,
never trusted on its own.
"""

from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher

from app.agent import Proposal
from app.catalog import get_product, list_shops
from app.paypal_client import OrderInfo
from app.policy import ALLOWED_MERCHANTS

LOOKALIKE_RATIO = Decimal("0.75")


@dataclass
class Finding:
    rule: str
    passed: bool
    reason: str


def _shop_name(shop_key):
    """Trusted display name from the catalog, falling back to the raw key."""
    return dict(list_shops()).get(shop_key, shop_key)


def _normalize(name):
    return name.lower().replace(" ", "")


def _check_currency(order):
    if order.currency == "USD":
        return Finding("currency", True, "The payment is in US dollars")
    return Finding(
        "currency", False, f"The payment is in {order.currency}, not US dollars, so amounts can't be compared"
    )


def _check_budget(budget, order):
    if order.amount <= budget:
        return Finding("budget", True, f"${order.amount} is within the ${budget} budget")
    return Finding("budget", False, f"${order.amount} is over the ${budget} budget")


def _check_allowlist(proposal, order):
    name = _shop_name(proposal.shop_key)
    expected = ALLOWED_MERCHANTS.get(proposal.shop_key)
    if expected is None:
        return Finding("allowlist", False, f"{name} is not an approved shop")
    if order.payee_merchant_id != expected:
        return Finding(
            "allowlist",
            False,
            f"The payment would go to a different PayPal account than the approved account for {name}",
        )
    return Finding("allowlist", True, f"{name} is an approved shop and the payment goes to its PayPal account")


def _check_catalog(proposal, product):
    if product is None:
        return Finding("catalog", False, f"Product '{proposal.product_id}' does not exist in the catalog")
    if product.shop_key != proposal.shop_key:
        return Finding(
            "catalog",
            False,
            f"'{product.name}' is sold by {product.shop_name}, not {_shop_name(proposal.shop_key)}",
        )
    return Finding("catalog", True, f"'{product.name}' is sold by {product.shop_name}")


def _check_price(proposal, order, product):
    if product is None:
        return Finding("price", False, "no catalog price")
    problems = []
    if order.amount != product.price:
        problems.append(f"the PayPal order is for ${order.amount}")
    if proposal.amount != product.price:
        problems.append(f"the assistant said ${proposal.amount}")
    if problems:
        return Finding(
            "price", False, f"'{product.name}' costs ${product.price}, but " + " and ".join(problems)
        )
    return Finding("price", True, f"The price matches the catalog price of ${product.price}")


def _check_lookalike(proposal):
    name = _shop_name(proposal.shop_key)
    if proposal.shop_key in ALLOWED_MERCHANTS:
        return Finding("lookalike", True, f"{name} is an approved shop")
    candidate = _normalize(name)
    for key in ALLOWED_MERCHANTS:
        approved = _shop_name(key)
        target = _normalize(approved)
        ratio = Decimal(str(SequenceMatcher(None, candidate, target).ratio()))
        if target in candidate or ratio >= LOOKALIKE_RATIO:
            return Finding("lookalike", False, f"The name '{name}' imitates the approved shop '{approved}'")
    return Finding("lookalike", True, f"The name '{name}' does not resemble any approved shop")


def check_rules(budget: Decimal, proposal: Proposal, order: OrderInfo) -> list[Finding]:
    """Run every rule in order and return all findings, passed or failed."""
    product = get_product(proposal.product_id)
    return [
        _check_currency(order),
        _check_budget(budget, order),
        _check_allowlist(proposal, order),
        _check_catalog(proposal, product),
        _check_price(proposal, order, product),
        _check_lookalike(proposal),
    ]

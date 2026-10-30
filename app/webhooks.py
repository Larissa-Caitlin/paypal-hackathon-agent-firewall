"""Verified PayPal webhooks: confirmation and reconciliation only.

A webhook never moves money and never changes a decision. It records what
PayPal says happened and flags it when that contradicts what the firewall did.
Only events whose signature PayPal verified reach this module; unverified
bodies are never stored, only counted.
"""

import logging
import threading
from collections import Counter

from app import audit

# Uvicorn's logger, so warnings appear in the server log without extra setup
logger = logging.getLogger("uvicorn.error")

HANDLED_TYPES = {
    "CHECKOUT.ORDER.APPROVED",
    "PAYMENT.AUTHORIZATION.CREATED",
    "PAYMENT.AUTHORIZATION.VOIDED",
    "PAYMENT.CAPTURE.COMPLETED",
    "PAYMENT.CAPTURE.DENIED",
}

# Plain-word labels for the result page (other types are shown as PayPal names them)
EVENT_LABELS = {
    "CHECKOUT.ORDER.APPROVED": "Buyer approved the order",
    "PAYMENT.AUTHORIZATION.CREATED": "Money held (authorization created)",
    "PAYMENT.AUTHORIZATION.VOIDED": "Hold released (authorization voided)",
    "PAYMENT.CAPTURE.COMPLETED": "Payment captured",
    "PAYMENT.CAPTURE.DENIED": "Capture denied",
}

MAX_FIELD = 200  # PayPal IDs and amounts are short; cap anything stored from an event

_rejections = Counter()
_rejections_lock = threading.Lock()


def reject(reason):
    """Count a rejected webhook by reason. The body is never stored or logged."""
    with _rejections_lock:
        _rejections[reason] += 1
        total = _rejections[reason]
    logger.warning("Rejected PayPal webhook (%s), %d so far", reason, total)


def rejection_counts() -> dict:
    with _rejections_lock:
        return dict(_rejections)


def _text(value):
    return str(value)[:MAX_FIELD] if isinstance(value, (str, int, float)) and str(value) else None


def _amount(resource):
    amount = resource.get("amount")
    if not isinstance(amount, dict):
        units = resource.get("purchase_units")
        if isinstance(units, list) and units and isinstance(units[0], dict):
            amount = units[0].get("amount")
    if not isinstance(amount, dict):
        return None, None
    return _text(amount.get("value")), _text(amount.get("currency_code"))


def extract(event, shop_key) -> dict:
    """Pull the IDs and amount out of a verified event (any type)."""
    event_type = _text(event.get("event_type")) or "UNKNOWN"
    resource = event.get("resource") if isinstance(event.get("resource"), dict) else {}
    supplementary = resource.get("supplementary_data") if isinstance(resource.get("supplementary_data"), dict) else {}
    related = supplementary.get("related_ids") if isinstance(supplementary.get("related_ids"), dict) else {}
    resource_id = _text(resource.get("id"))
    amount, currency = _amount(resource)
    return {
        "event_id": _text(event.get("id")),
        "event_type": event_type,
        "shop_key": shop_key,
        "resource_id": resource_id,
        "order_id": resource_id if event_type.startswith("CHECKOUT.ORDER.") else _text(related.get("order_id")),
        "authorization_id": (resource_id if event_type.startswith("PAYMENT.AUTHORIZATION.")
                             else _text(related.get("authorization_id"))),
        "capture_id": resource_id if event_type.startswith("PAYMENT.CAPTURE.") else None,
        "amount": amount,
        "currency": currency,
    }


def reconcile(fields, decision) -> tuple[str, str]:
    """Compare a PayPal event with what the firewall did. Returns (status, detail).

    status: "match", "mismatch", "info" (nothing to compare yet) or "unmatched" (no decision found).
    """
    event_type = fields["event_type"]
    if decision is None:
        if event_type in ("PAYMENT.CAPTURE.COMPLETED", "PAYMENT.AUTHORIZATION.VOIDED"):
            return "unmatched", "PayPal moved or released money for a payment this app has no decision for"
        return "unmatched", "No firewall decision for this payment yet"
    if decision.get("shop_key") and decision["shop_key"] != fields["shop_key"]:
        return "mismatch", f"Event came from {fields['shop_key']}'s webhook, but the decision is for {decision['shop_key']}"

    actions = {a["action"] for a in decision["actions"]}
    verdict = decision["decision"]
    meant_to_capture = verdict == "allow" or "user_approved" in actions
    meant_to_void = verdict == "block" or "user_rejected" in actions

    if event_type == "PAYMENT.CAPTURE.COMPLETED":
        if meant_to_capture:
            return "match", "Capture confirmed"
        if "voided" in actions or meant_to_void:
            return "mismatch", "PayPal captured a payment the firewall blocked or the user rejected"
        return "mismatch", "PayPal captured a payment that was still held for the user"
    if event_type == "PAYMENT.AUTHORIZATION.VOIDED":
        if meant_to_void:
            return "match", "Void confirmed"
        if "captured" in actions or meant_to_capture:
            return "mismatch", "PayPal voided a payment the firewall allowed or the user approved"
        return "mismatch", "PayPal voided a payment that was still held for the user"
    if event_type == "PAYMENT.CAPTURE.DENIED":
        if "captured" in actions:
            return "mismatch", "The firewall recorded a capture, but PayPal denied it"
        return "info", "PayPal denied the capture; no money moved"
    return "info", "Recorded"


def handle_verified_event(event, shop_key) -> bool:
    """Store a verified event with its reconciliation. Returns False for a duplicate event_id.

    Never captures, voids or changes a decision.
    """
    fields = extract(event, shop_key)
    if audit.webhook_event_exists(fields["event_id"]):
        return False
    decision = audit.find_decision_for_payment(fields["authorization_id"], fields["order_id"])
    status, detail = reconcile(fields, decision)
    fields.update(decision_id=decision["id"] if decision else None, reconciliation=status,
                  reconciliation_detail=detail)
    if fields["event_type"] not in HANDLED_TYPES:
        fields["reconciliation_detail"] = f"Recorded; this event type is not reconciled ({detail})"
    inserted = audit.log_webhook_event(fields)
    if inserted and status == "mismatch":
        logger.warning("PayPal webhook mismatch: %s %s (decision %s): %s",
                       fields["event_type"], fields["event_id"], fields["decision_id"], detail)
    return inserted


def confirmations(order_id) -> dict:
    """What PayPal has confirmed for an order, for the result page."""
    events = audit.get_webhook_events_for_order(order_id)
    return {
        "order_id": order_id,
        "events": [
            {
                "event_type": e["event_type"],
                "label": EVENT_LABELS.get(e["event_type"], e["event_type"]),
                "received_at": e["received_at"],
                "reconciliation": e["reconciliation"],
                "detail": e["reconciliation_detail"],
            }
            for e in events
        ],
        "mismatch": any(e["reconciliation"] == "mismatch" for e in events),
    }

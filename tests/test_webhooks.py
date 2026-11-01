"""Verified PayPal webhooks: verification, storage, duplicates, reconciliation, confirmations. PayPal mocked."""

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import audit, main, paypal_client, webhooks
from app.agent import Proposal
from app.firewall import Verdict
from app.paypal_client import OrderInfo, PayPalError
from app.result_page import render_result_page

HEADERS = {
    "PAYPAL-AUTH-ALGO": "SHA256withRSA",
    "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
    "PAYPAL-TRANSMISSION-ID": "tx-1",
    "PAYPAL-TRANSMISSION-SIG": "sig==",
    "PAYPAL-TRANSMISSION-TIME": "2026-10-30T10:00:00Z",
    "Content-Type": "application/json",
}
STATIC = Path(main.__file__).parent / "static"


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "webhooks_test.db"))
    for shop in ("TECHMART", "GADGETHUB", "TECHMARTDEALS"):
        monkeypatch.setenv(f"PAYPAL_{shop}_WEBHOOK_ID", f"WH-{shop}")
    main.webhook_limiter.reset()
    # A webhook must never move money
    for name in ("capture_authorization", "void_authorization", "create_order", "authorize_order"):
        monkeypatch.setattr(paypal_client, name, lambda *a, **k: pytest.fail("money call from a webhook"))


class FakeVerify:
    def __init__(self, result=True, error=None):
        self.result, self.error, self.calls = result, error, []

    def __call__(self, shop, headers, event):
        self.calls.append((shop, dict(headers), event))
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def verify(monkeypatch):
    fake = FakeVerify()
    monkeypatch.setattr(paypal_client, "verify_webhook_signature", fake)
    return fake


@pytest.fixture
def client():
    return TestClient(main.app)


def event(event_id, event_type, resource):
    return {"id": event_id, "event_type": event_type, "resource_type": "x", "resource": resource}


def capture(event_id="WH-EVT-CAP", order_id="ORD1", auth_id="AUTH1", status="COMPLETED"):
    return event(event_id, f"PAYMENT.CAPTURE.{status}", {
        "id": "CAP1", "status": status, "amount": {"value": "12.00", "currency_code": "USD"},
        "supplementary_data": {"related_ids": {"order_id": order_id, "authorization_id": auth_id}},
    })


def void(event_id="WH-EVT-VOID", order_id="ORD1", auth_id="AUTH1"):
    return event(event_id, "PAYMENT.AUTHORIZATION.VOIDED", {
        "id": auth_id, "status": "VOIDED", "amount": {"value": "12.00", "currency_code": "USD"},
        "supplementary_data": {"related_ids": {"order_id": order_id}},
    })


def approved(event_id="WH-EVT-APPROVED", order_id="ORD1"):
    return event(event_id, "CHECKOUT.ORDER.APPROVED", {
        "id": order_id, "status": "APPROVED",
        "purchase_units": [{"amount": {"value": "12.00", "currency_code": "USD"}}],
    })


def post(client, ev, shop="techmartdeals", headers=HEADERS):
    body = ev if isinstance(ev, (bytes, str)) else json.dumps(ev)
    return client.post(f"/paypal/webhook/{shop}", content=body, headers=headers)


def rows():
    audit.init_db()
    with sqlite3.connect(audit._db_path()) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM webhook_events ORDER BY id")]


def decision(verdict="block", actions=(), shop="techmartdeals", order_id="ORD1", auth_id="AUTH1"):
    proposal = Proposal("tmd-case-01", shop, Decimal("12.00"), "Cheap")
    order = OrderInfo(order_id, "COMPLETED", Decimal("12.00"), "USD", None, "NBTHNLSYURVNN", None)
    decision_id = audit.log_decision("Buy a case", Decimal("20.00"), proposal, order,
                                     Verdict(verdict, ["reason"], [], []), auth_id)
    for action in actions:
        audit.log_action(decision_id, action, "detail")
    return decision_id


# --- Verification ---

def test_verified_event_is_stored(client, verify):
    resp = post(client, approved())
    assert resp.status_code == 200 and resp.json() == {"status": "stored"}
    shop, headers, sent = verify.calls[0]
    assert shop == "techmartdeals" and sent == approved()
    assert {k.lower() for k in HEADERS} <= {k.lower() for k in headers}
    [row] = rows()
    assert row["event_id"] == "WH-EVT-APPROVED" and row["event_type"] == "CHECKOUT.ORDER.APPROVED"
    assert row["order_id"] == "ORD1" and row["amount"] == "12.00" and row["currency"] == "USD"
    assert row["verified"] == 1 and row["shop_key"] == "techmartdeals"


@pytest.mark.parametrize("fake", [FakeVerify(result=False), FakeVerify(error=PayPalError(401, "INVALID")),
                                  FakeVerify(error=RuntimeError("timeout"))])
def test_failed_verification_is_400_and_nothing_stored(client, monkeypatch, fake):
    monkeypatch.setattr(paypal_client, "verify_webhook_signature", fake)
    before = sum(webhooks.rejection_counts().values())
    assert post(client, approved()).status_code == 400
    assert rows() == []
    assert sum(webhooks.rejection_counts().values()) == before + 1


def test_missing_signature_headers_is_400_without_calling_paypal(client, monkeypatch):
    monkeypatch.setattr(paypal_client, "_request", lambda *a, **k: pytest.fail("PayPal called"))
    headers = {k: v for k, v in HEADERS.items() if k != "PAYPAL-TRANSMISSION-SIG"}
    assert post(client, approved(), headers=headers).status_code == 400
    assert rows() == []


def test_unset_webhook_id_rejects_that_shop(client, monkeypatch):
    monkeypatch.delenv("PAYPAL_GADGETHUB_WEBHOOK_ID")
    monkeypatch.setattr(paypal_client, "_request", lambda *a, **k: pytest.fail("PayPal called"))
    assert post(client, approved(), shop="gadgethub").status_code == 400
    assert rows() == []


def test_oversized_body_is_413_before_verification(client, verify):
    big = approved()
    big["padding"] = "x" * (64 * 1024)
    assert post(client, big).status_code == 413
    assert verify.calls == [] and rows() == []


@pytest.mark.parametrize("body", [b"not json", b"[]", json.dumps({"event_type": "X"}), json.dumps({"id": "E1"}),
                                  json.dumps({"id": "", "event_type": "X"})])
def test_malformed_event_is_400(client, verify, body):
    assert post(client, body).status_code == 400
    assert verify.calls == [] and rows() == []


def test_unknown_shop_is_404(client, verify):
    assert post(client, approved(), shop="evilshop").status_code == 404
    assert verify.calls == []


def test_duplicate_event_id_is_stored_once(client, verify):
    assert post(client, approved()).json() == {"status": "stored"}
    again = post(client, approved())
    assert again.status_code == 200 and again.json() == {"status": "duplicate"}
    assert len(rows()) == 1


def test_rate_limited_per_ip(client, verify, monkeypatch):
    monkeypatch.setattr(main.webhook_limiter, "per_ip", 2)
    assert post(client, approved("E1")).status_code == 200
    assert post(client, approved("E2")).status_code == 200
    refused = post(client, approved("E3"))
    assert refused.status_code == 429 and "per minute" in refused.json()["detail"]


def test_other_event_types_are_stored_not_dropped(client, verify):
    assert post(client, event("E-OTHER", "PAYMENT.CAPTURE.REFUNDED", {"id": "REF1"})).status_code == 200
    [row] = rows()
    assert row["event_type"] == "PAYMENT.CAPTURE.REFUNDED" and row["resource_id"] == "REF1"
    assert "not reconciled" in row["reconciliation_detail"]


def test_webhook_table_is_append_only(client, verify):
    post(client, approved())
    with sqlite3.connect(audit._db_path()) as conn:
        for sql in ("UPDATE webhook_events SET event_type = 'X'", "DELETE FROM webhook_events"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute(sql)


# --- verify_webhook_signature itself ---

def test_verify_sends_shop_webhook_id_headers_and_event(monkeypatch):
    sent = {}

    class Resp:
        def __init__(self, status):
            self.status = status

        def json(self):
            return {"verification_status": self.status}

    def fake_request(shop, method, path, json=None):
        sent.update(shop=shop, method=method, path=path, body=json)
        return Resp(sent.get("answer", "SUCCESS"))

    monkeypatch.setattr(paypal_client, "_request", fake_request)
    ev = approved()
    assert paypal_client.verify_webhook_signature("techmart", HEADERS, ev) is True
    assert sent["shop"] == "techmart" and sent["method"] == "POST"
    assert sent["path"] == "/v1/notifications/verify-webhook-signature"
    assert sent["body"] == {
        "auth_algo": "SHA256withRSA", "cert_url": HEADERS["PAYPAL-CERT-URL"], "transmission_id": "tx-1",
        "transmission_sig": "sig==", "transmission_time": "2026-10-30T10:00:00Z",
        "webhook_id": "WH-TECHMART", "webhook_event": ev,
    }
    for answer in ("FAILURE", "", None):
        sent["answer"] = answer
        assert paypal_client.verify_webhook_signature("techmart", HEADERS, ev) is False


def test_verify_fails_closed_without_webhook_id_or_headers(monkeypatch):
    monkeypatch.setattr(paypal_client, "_request", lambda *a, **k: pytest.fail("PayPal called"))
    monkeypatch.delenv("PAYPAL_TECHMART_WEBHOOK_ID")
    with pytest.raises(ValueError, match="PAYPAL_TECHMART_WEBHOOK_ID"):
        paypal_client.verify_webhook_signature("techmart", HEADERS, approved())
    with pytest.raises(ValueError, match="paypal-cert-url"):
        paypal_client.verify_webhook_signature("gadgethub", {"PAYPAL-AUTH-ALGO": "x"}, approved())


# --- Reconciliation ---

def reconciliation():
    return [(r["event_type"], r["reconciliation"], r["decision_id"]) for r in rows()]


def test_capture_after_block_and_void_is_a_mismatch(client, verify, caplog):
    decision_id = decision("block", ["voided"])
    with caplog.at_level("WARNING", logger="uvicorn.error"):
        post(client, capture())
    assert reconciliation() == [("PAYMENT.CAPTURE.COMPLETED", "mismatch", decision_id)]
    assert "blocked" in rows()[0]["reconciliation_detail"]
    assert any("mismatch" in r.getMessage() for r in caplog.records)


def test_void_after_capture_is_a_mismatch(client, verify):
    decision_id = decision("allow", ["captured"], shop="gadgethub")
    post(client, void(), shop="gadgethub")
    assert reconciliation() == [("PAYMENT.AUTHORIZATION.VOIDED", "mismatch", decision_id)]


@pytest.mark.parametrize("verdict, actions, ev, shop, expected", [
    ("block", ["voided"], void(), "techmartdeals", "match"),
    ("allow", ["captured"], capture(), "techmartdeals", "match"),
    ("hold", ["held", "user_approved", "captured"], capture(), "techmartdeals", "match"),
    ("hold", ["held", "user_rejected", "voided"], void(), "techmartdeals", "match"),
    ("hold", ["held"], capture(), "techmartdeals", "mismatch"),        # captured while still held
    ("hold", ["held", "user_rejected", "voided"], capture(), "techmartdeals", "mismatch"),
    ("allow", ["captured"], capture(status="DENIED"), "techmartdeals", "mismatch"),
    ("allow", [], capture(status="DENIED"), "techmartdeals", "info"),
    ("allow", ["captured"], capture(), "gadgethub", "mismatch"),     # event from another shop's webhook
])
def test_reconciliation_rules(client, verify, verdict, actions, ev, shop, expected):
    decision(verdict, actions)
    post(client, ev, shop=shop)
    assert rows()[0]["reconciliation"] == expected


def test_matches_by_order_id_when_no_authorization_id(client, verify):
    decision_id = decision("block", ["voided"], auth_id=None)
    post(client, capture(auth_id=None))
    assert reconciliation() == [("PAYMENT.CAPTURE.COMPLETED", "mismatch", decision_id)]


def test_event_without_a_decision_is_unmatched(client, verify):
    post(client, approved())
    assert reconciliation() == [("CHECKOUT.ORDER.APPROVED", "unmatched", None)]


def test_webhook_never_changes_a_decision(client, verify):
    decision_id = decision("block", ["voided"])
    before = audit.get_decision_with_actions(decision_id)
    post(client, capture())
    assert audit.get_decision_with_actions(decision_id) == before


# --- Confirmations endpoint and result page ---

def test_confirmations_endpoint(client, verify):
    assert client.get("/orders/ORD1/confirmations").status_code == 404  # unknown order
    decision("block", ["voided"])
    assert client.get("/orders/ORD1/confirmations").json() == {"order_id": "ORD1", "events": [], "mismatch": False}
    post(client, approved())
    post(client, void())
    body = client.get("/orders/ORD1/confirmations").json()
    assert [(e["event_type"], e["label"], e["reconciliation"]) for e in body["events"]] == [
        ("CHECKOUT.ORDER.APPROVED", "Buyer approved the order", "info"),
        ("PAYMENT.AUTHORIZATION.VOIDED", "Hold released (authorization voided)", "match"),
    ]
    assert body["mismatch"] is False
    post(client, capture())
    assert client.get("/orders/ORD1/confirmations").json()["mismatch"] is True


def result_row(order_id="ORD1"):
    return {
        "id": 1, "decision": "block", "reasons_json": '["r"]', "findings_json": '{"rules": [], "ai": []}',
        "actions": [{"action": "voided", "detail": "x"}], "shop_key": "techmartdeals", "product_id": "tmd-case-01",
        "agent_claimed_amount": "12.00", "agent_reason": "x", "order_id": order_id, "authorization_id": "AUTH1",
        "paypal_amount": "12.00", "paypal_currency": "USD", "payee_merchant_id": "NBTHNLSYURVNN", "budget": "20.00",
    }


def test_result_page_waits_for_confirmation():
    page = render_result_page(result_row())
    assert 'id="confirmations" data-order-id="ORD1"' in page
    assert "Waiting for PayPal to confirm..." in page
    assert 'id="confirmations-mismatch" hidden' in page
    assert '<script src="/static/result_confirmations.js" defer></script>' in page


def test_result_page_lists_events_and_flags_mismatch_escaped():
    xss = "<script>alert(1)</script>"
    confirmed = {"order_id": "ORD1", "mismatch": True, "events": [
        {"event_type": "X", "label": f"Payment captured {xss}", "time": "2026-10-30T10:00:00+00:00",
         "received_at": "2026-10-30T10:00:01+00:00",
         "reconciliation": "mismatch", "detail": f"PayPal captured a blocked payment {xss}"}]}
    page = render_result_page(result_row(), confirmed)
    section = page.split('id="confirmations"', 1)[1].split("</section>", 1)[0]
    assert xss not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in section
    assert 'class="confirmation confirmation-mismatch"' in section
    assert 'id="confirmations-mismatch">' in section  # notice shown (no hidden attribute)
    assert 'id="confirmations-waiting" hidden' in section


def test_no_confirmation_section_without_an_order():
    assert 'id="confirmations"' not in render_result_page(result_row(order_id=None))


def test_result_page_route_includes_confirmations(client, verify, monkeypatch):
    decision("block", ["voided"])
    post(client, capture())
    page = client.get("/paypal/return", params={"token": "ORD1"}, headers={"Accept": "text/html"}).text
    assert "Payment captured" in page and 'class="confirmation confirmation-mismatch"' in page


def test_confirmations_script_uses_text_content_only():
    js = (STATIC / "result_confirmations.js").read_text(encoding="utf-8")
    assert "textContent" in js and "createElement" in js
    for usage in (".innerHTML", ".outerHTML", "insertAdjacentHTML", "document.write", "localStorage.",
                  "sessionStorage.", "eval(", "new Function"):
        assert usage not in js, usage
    assert "5000" in js and "120000" in js



# --- Event order and time display ---

def at(ev, create_time):
    return {**ev, "create_time": create_time}


def test_create_time_is_stored(client, verify):
    post(client, at(approved(), "2026-10-30T22:44:56.123Z"))
    assert rows()[0]["create_time"] == "2026-10-30T22:44:56.123Z"


def test_events_sorted_by_paypal_create_time_not_arrival(client, verify):
    decision("block", ["voided"])
    # PayPal delivers out of order: the void arrives first, the approval last
    post(client, at(void(), "2026-10-30T22:45:10Z"))
    post(client, at(event("E-AUTH", "PAYMENT.AUTHORIZATION.CREATED", {"id": "AUTH1"}), "2026-10-30T22:45:00Z"))
    post(client, at(approved(), "2026-10-30T22:44:56Z"))
    body = client.get("/orders/ORD1/confirmations").json()
    assert [e["event_type"] for e in body["events"]] == [
        "CHECKOUT.ORDER.APPROVED", "PAYMENT.AUTHORIZATION.CREATED", "PAYMENT.AUTHORIZATION.VOIDED"]
    assert body["events"][0]["time"] == "2026-10-30T22:44:56+00:00"


def test_same_time_ties_break_in_payment_order(client, verify):
    decision("allow", ["captured"])
    same = "2026-10-30T22:45:00Z"
    post(client, at(capture(), same))
    post(client, at(event("E-AUTH", "PAYMENT.AUTHORIZATION.CREATED", {"id": "AUTH1"}), same))
    post(client, at(approved(), same))
    assert [e["event_type"] for e in client.get("/orders/ORD1/confirmations").json()["events"]] == [
        "CHECKOUT.ORDER.APPROVED", "PAYMENT.AUTHORIZATION.CREATED", "PAYMENT.CAPTURE.COMPLETED"]


def test_missing_or_bad_create_time_falls_back_to_received_at(client, verify):
    decision("block", ["voided"])
    post(client, approved())  # no create_time
    post(client, at(void(), "not a time"))
    events = client.get("/orders/ORD1/confirmations").json()["events"]
    received = {r["event_type"]: r["received_at"] for r in rows()}
    assert [e["time"] for e in events] == [
        webhooks._parse_time(received[e["event_type"]]).isoformat() for e in events]


def test_old_database_gets_the_column_without_changing_rows(tmp_path, monkeypatch, client, verify):
    old = tmp_path / "old.db"
    monkeypatch.setenv("AUDIT_DB_PATH", str(old))
    column = "    create_time TEXT,  -- PayPal's event create_time; NULL in rows stored before it was recorded\n"
    assert column in audit.SCHEMA
    schema = audit.SCHEMA.replace(column, "")
    with sqlite3.connect(old) as conn:
        conn.executescript(schema)
        conn.execute("INSERT INTO webhook_events (event_id, event_type, shop_key, order_id, received_at, verified, "
                     "reconciliation) VALUES ('OLD', 'CHECKOUT.ORDER.APPROVED', 'techmart', 'ORD1', "
                     "'2026-10-29T09:00:00+00:00', 1, 'unmatched')")
    decision("block", ["voided"])
    post(client, at(void(), "2026-10-30T22:45:10Z"))
    old_row, new_row = rows()
    assert old_row["event_id"] == "OLD" and old_row["create_time"] is None
    assert new_row["create_time"] == "2026-10-30T22:45:10Z"
    events = client.get("/orders/ORD1/confirmations").json()["events"]
    assert [e["time"] for e in events] == ["2026-10-29T09:00:00+00:00", "2026-10-30T22:45:10+00:00"]


def test_result_page_puts_full_iso_time_in_datetime_and_title():
    confirmed = {"order_id": "ORD1", "mismatch": False, "events": [
        {"event_type": "X", "label": "Payment captured", "time": "2026-10-30T22:44:56+00:00",
         "received_at": "2026-10-30T22:45:01+00:00", "reconciliation": "match", "detail": "ok"}]}
    page = render_result_page(result_row(), confirmed)
    assert '<time class="muted" datetime="2026-10-30T22:44:56+00:00" title="2026-10-30T22:44:56+00:00">' in page

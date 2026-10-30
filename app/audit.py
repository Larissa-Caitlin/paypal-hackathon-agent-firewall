"""Append-only audit log: purchases, firewall decisions, actions taken on them,
and verified PayPal webhook events.

SQLite via the standard library. This module deliberately has no update or
delete functions, and database triggers reject UPDATE and DELETE as well.
Money is stored as strings so values stay exact (never float).
"""

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

ACTIONS = {"captured", "voided", "held", "user_approved", "user_rejected", "error"}


class AlreadyResolved(Exception):
    """A user_approved/user_rejected action already exists for this decision."""

# agent_claimed_amount and agent_reason are untrusted claims made by the agent,
# recorded as evidence only. paypal_* and payee_merchant_id come from PayPal.
SCHEMA = """
CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    instruction TEXT NOT NULL,
    budget TEXT NOT NULL,
    shop_key TEXT NOT NULL,
    product_id TEXT NOT NULL,
    agent_claimed_amount TEXT NOT NULL,  -- untrusted agent claim
    agent_reason TEXT NOT NULL,          -- untrusted agent claim
    order_id TEXT NOT NULL UNIQUE,
    pages_read_json TEXT NOT NULL,
    trace_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    instruction TEXT NOT NULL,
    budget TEXT NOT NULL,
    shop_key TEXT,
    product_id TEXT,
    agent_claimed_amount TEXT,  -- untrusted agent claim
    agent_reason TEXT,          -- untrusted agent claim
    order_id TEXT,
    authorization_id TEXT,
    paypal_amount TEXT,
    paypal_currency TEXT,
    payee_merchant_id TEXT,
    decision TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    findings_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER NOT NULL REFERENCES decisions(id),
    created_at TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT
);
-- A held decision can be resolved by the user once: the second attempt fails here
CREATE UNIQUE INDEX IF NOT EXISTS actions_one_resolution
    ON actions(decision_id) WHERE action IN ('user_approved', 'user_rejected');
CREATE TRIGGER IF NOT EXISTS purchases_no_update BEFORE UPDATE ON purchases
    BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS purchases_no_delete BEFORE DELETE ON purchases
    BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS decisions_no_update BEFORE UPDATE ON decisions
    BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS decisions_no_delete BEFORE DELETE ON decisions
    BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS actions_no_update BEFORE UPDATE ON actions
    BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS actions_no_delete BEFORE DELETE ON actions
    BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
-- Only events whose signature PayPal verified are stored; event_id stops replays and retries
CREATE TABLE IF NOT EXISTS webhook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    shop_key TEXT NOT NULL,
    resource_id TEXT,
    order_id TEXT,
    authorization_id TEXT,
    capture_id TEXT,
    amount TEXT,
    currency TEXT,
    received_at TEXT NOT NULL,
    verified INTEGER NOT NULL CHECK (verified = 1),
    decision_id INTEGER REFERENCES decisions(id),
    reconciliation TEXT NOT NULL,  -- match | mismatch | info | unmatched
    reconciliation_detail TEXT
);
CREATE INDEX IF NOT EXISTS webhook_events_order ON webhook_events(order_id);
CREATE INDEX IF NOT EXISTS webhook_events_authorization ON webhook_events(authorization_id);
CREATE TRIGGER IF NOT EXISTS webhook_events_no_update BEFORE UPDATE ON webhook_events
    BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS webhook_events_no_delete BEFORE DELETE ON webhook_events
    BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""

# All queries below use ? placeholders, never string formatting: logged values
# include agent output and seller-influenced text, which must not be able to
# alter the SQL (SQL injection).


def _db_path():
    return os.environ.get("AUDIT_DB_PATH", "audit.db")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _connect():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create the tables and append-only triggers if they don't exist."""
    with closing(_connect()) as conn:
        conn.executescript(SCHEMA)


def log_purchase(instruction, budget, agent_result, order_id) -> int:
    """Record a purchase awaiting PayPal approval: the agent's proposal and evidence."""
    init_db()
    proposal = agent_result.proposal
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            """INSERT INTO purchases (
                created_at, instruction, budget, shop_key, product_id,
                agent_claimed_amount, agent_reason, order_id, pages_read_json, trace_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _now(),
                instruction,
                str(budget),
                proposal.shop_key,
                proposal.product_id,
                str(proposal.amount),
                proposal.reason,
                order_id,
                json.dumps(agent_result.pages_read),
                json.dumps([asdict(s) for s in agent_result.trace]),
            ),
        )
        return cur.lastrowid


def get_purchase_by_order(order_id) -> dict | None:
    """Return the purchase row for a PayPal order id, or None."""
    init_db()
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM purchases WHERE order_id = ?", (order_id,)).fetchone()
    return dict(row) if row else None


def get_decision_id_by_order(order_id) -> int | None:
    """Return the id of the decision already made for a PayPal order, or None."""
    init_db()
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT id FROM decisions WHERE order_id = ? ORDER BY id LIMIT 1", (order_id,)
        ).fetchone()
    return row["id"] if row else None


def log_decision(instruction, budget, proposal, order, verdict, authorization_id=None) -> int:
    """Record a firewall verdict with its evidence. Returns the new decision id."""
    init_db()
    findings = {
        "rules": [asdict(f) for f in verdict.rule_findings],
        "ai": [asdict(f) for f in verdict.ai_findings],
    }
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            """INSERT INTO decisions (
                created_at, instruction, budget, shop_key, product_id,
                agent_claimed_amount, agent_reason, order_id, authorization_id,
                paypal_amount, paypal_currency, payee_merchant_id,
                decision, reasons_json, findings_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _now(),
                instruction,
                str(budget),
                proposal.shop_key if proposal else None,
                proposal.product_id if proposal else None,
                str(proposal.amount) if proposal else None,
                proposal.reason if proposal else None,
                order.order_id if order else None,
                authorization_id,
                str(order.amount) if order else None,
                order.currency if order else None,
                order.payee_merchant_id if order else None,
                verdict.decision,
                json.dumps(verdict.reasons),
                json.dumps(findings),
            ),
        )
        return cur.lastrowid


def log_action(decision_id, action, detail) -> None:
    """Record an action taken on a decision (capture, void, user response, ...).

    Raises AlreadyResolved if the user already approved or rejected it.
    """
    if action not in ACTIONS:
        raise ValueError(f"Unknown action: {action!r}")
    init_db()
    try:
        with closing(_connect()) as conn, conn:
            conn.execute(
                "INSERT INTO actions (decision_id, created_at, action, detail) VALUES (?, ?, ?, ?)",
                (decision_id, _now(), action, detail),
            )
    except sqlite3.IntegrityError as e:
        if "UNIQUE" in str(e):
            raise AlreadyResolved(decision_id) from e
        raise


def get_decision_with_actions(decision_id) -> dict | None:
    """Return a decision row as a dict with its actions (oldest first), or None."""
    init_db()
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
        if row is None:
            return None
        actions = conn.execute(
            "SELECT * FROM actions WHERE decision_id = ? ORDER BY id", (decision_id,)
        ).fetchall()
    result = dict(row)
    result["actions"] = [dict(a) for a in actions]
    return result


WEBHOOK_FIELDS = ("event_id", "event_type", "shop_key", "resource_id", "order_id", "authorization_id",
                  "capture_id", "amount", "currency", "decision_id", "reconciliation", "reconciliation_detail")


def webhook_event_exists(event_id) -> bool:
    init_db()
    with closing(_connect()) as conn:
        return conn.execute("SELECT 1 FROM webhook_events WHERE event_id = ?", (event_id,)).fetchone() is not None


def log_webhook_event(fields: dict) -> bool:
    """Store a verified webhook event. Returns False (and stores nothing) for a duplicate event_id."""
    init_db()
    try:
        with closing(_connect()) as conn, conn:
            conn.execute(
                f"INSERT INTO webhook_events ({', '.join(WEBHOOK_FIELDS)}, received_at, verified) "
                f"VALUES ({', '.join('?' for _ in WEBHOOK_FIELDS)}, ?, 1)",
                (*(fields.get(f) for f in WEBHOOK_FIELDS), _now()),
            )
    except sqlite3.IntegrityError as e:
        if "UNIQUE" in str(e) and "event_id" in str(e):
            return False
        raise
    return True


def find_decision_for_payment(authorization_id=None, order_id=None) -> dict | None:
    """The decision (with actions) a PayPal event refers to: by authorization ID first, then order ID."""
    init_db()
    with closing(_connect()) as conn:
        row = None
        if authorization_id:
            row = conn.execute(
                "SELECT id FROM decisions WHERE authorization_id = ? ORDER BY id DESC LIMIT 1", (authorization_id,)
            ).fetchone()
        if row is None and order_id:
            row = conn.execute(
                "SELECT id FROM decisions WHERE order_id = ? ORDER BY id DESC LIMIT 1", (order_id,)
            ).fetchone()
    return get_decision_with_actions(row["id"]) if row else None


def get_webhook_events_for_order(order_id) -> list[dict]:
    """Verified events for an order: those naming the order, or its decisions' authorizations (oldest first)."""
    init_db()
    with closing(_connect()) as conn:
        rows = conn.execute(
            """SELECT * FROM webhook_events
               WHERE order_id = ?
                  OR authorization_id IN (
                      SELECT authorization_id FROM decisions WHERE order_id = ? AND authorization_id IS NOT NULL)
               ORDER BY id""",
            (order_id, order_id),
        ).fetchall()
    return [dict(r) for r in rows]

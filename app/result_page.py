"""Server-rendered result page shown after PayPal redirects the buyer back.

Every value is HTML-escaped: agent claims, finding reasons (which can quote
seller text) and even platform data, so nothing on this page can become markup.
"""

import json
from html import escape

VERDICT_LABELS = {"allow": "ALLOW", "block": "BLOCK", "hold": "HOLD"}
VERDICT_SUMMARIES = {
    "allow": "All checks passed. The payment was captured.",
    "block": "The firewall stopped this payment. The held funds were released.",
    "hold": "Something looks suspicious. The funds are held until you decide.",
}
ACTION_LABELS = {
    "captured": "Payment captured",
    "voided": "Authorization voided (funds released)",
    "held": "Funds held, waiting for you",
    "user_approved": "You approved the payment",
    "user_rejected": "You rejected the payment",
    "error": "Error",
}
RESOLVED = {"user_approved", "user_rejected"}


def _e(value):
    return escape("" if value is None else str(value), quote=True)


def _status(ok, ok_text, bad_text):
    cls = "pass" if ok else "fail"
    return f'<span class="status status-{cls}">{ok_text if ok else bad_text}</span>'


def _rule_rows(rules):
    if not rules:
        return '<p class="muted">No rule checks ran for this payment.</p>'
    rows = "".join(
        f"<tr><td>{_status(f['passed'], 'PASS', 'FAIL')}</td>"
        f"<td class=\"mono\">{_e(f['rule'])}</td><td>{_e(f['reason'])}</td></tr>"
        for f in rules
    )
    return f'<table class="findings"><tbody>{rows}</tbody></table>'


def _ai_rows(ai):
    if not ai:
        return '<p class="muted">No AI checks ran for this payment.</p>'
    rows = "".join(
        f"<tr><td>{_status(not f['flagged'], 'OK', 'FLAGGED')}</td>"
        f"<td class=\"mono\">{_e(f['detector'])}<br><span class=\"muted\">{_e(f['check'])}</span></td>"
        f"<td>{_e(f['reason'])}</td></tr>"
        for f in ai
    )
    return f'<table class="findings"><tbody>{rows}</tbody></table>'


def _actions(actions):
    if not actions:
        return '<p class="muted">No PayPal action recorded.</p>'
    items = "".join(
        f'<li><span class="action action-{_e(a["action"])}">{_e(ACTION_LABELS.get(a["action"], a["action"]))}</span>'
        f'<span class="muted"> {_e(a["detail"])}</span></li>'
        for a in actions
    )
    return f'<ul class="actions">{items}</ul>'


def _hold_controls(row):
    if row["decision"] != "hold" or any(a["action"] in RESOLVED for a in row["actions"]):
        return ""
    return f"""
    <section class="card" id="hold-controls" data-decision-id="{_e(row['id'])}">
      <h2>Your decision</h2>
      <p>Enter the access key, then approve (capture) or reject (void) this payment.</p>
      <label for="hold-key">Access key</label>
      <input id="hold-key" type="password" autocomplete="off">
      <div class="button-row">
        <button type="button" id="hold-approve" class="button button-allow">Approve payment</button>
        <button type="button" id="hold-reject" class="button button-block">Reject payment</button>
      </div>
      <p id="hold-outcome" class="outcome" role="status"></p>
    </section>"""


def render_result_page(row) -> str:
    """Render a decision row (from audit.get_decision_with_actions) as a full HTML page."""
    decision = row["decision"]
    reasons = json.loads(row["reasons_json"])
    findings = json.loads(row["findings_json"])
    reasons_html = "".join(f"<li>{_e(r)}</li>" for r in reasons)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Firewall verdict</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<main class="container">
  <header class="page-header">
    <h1>Agent Payment Firewall</h1>
    <a class="link" href="/">&larr; Start a new purchase</a>
  </header>

  <section class="verdict verdict-{_e(decision)}">
    <span class="badge badge-{_e(decision)}">{_e(VERDICT_LABELS.get(decision, decision))}</span>
    <p class="verdict-summary">{_e(VERDICT_SUMMARIES.get(decision, ""))}</p>
    <ul class="reasons">{reasons_html}</ul>
  </section>

  <section class="card">
    <h2>PayPal action</h2>
    {_actions(row["actions"])}
  </section>
  {_hold_controls(row)}

  <div class="grid-2">
    <section class="card trust-untrusted">
      <h2>Agent's proposal <span class="tag tag-untrusted">unverified claim</span></h2>
      <dl>
        <dt>Shop</dt><dd class="mono">{_e(row["shop_key"])}</dd>
        <dt>Product</dt><dd class="mono">{_e(row["product_id"])}</dd>
        <dt>Claimed price</dt><dd>${_e(row["agent_claimed_amount"])}</dd>
        <dt>Agent's reason</dt><dd>{_e(row["agent_reason"])}</dd>
      </dl>
    </section>
    <section class="card trust-trusted">
      <h2>PayPal order <span class="tag tag-trusted">trusted</span></h2>
      <dl>
        <dt>Order</dt><dd class="mono">{_e(row["order_id"])}</dd>
        <dt>Amount</dt><dd>{_e(row["paypal_amount"])} {_e(row["paypal_currency"])}</dd>
        <dt>Payee merchant</dt><dd class="mono">{_e(row["payee_merchant_id"])}</dd>
        <dt>Your budget</dt><dd>${_e(row["budget"])}</dd>
      </dl>
    </section>
  </div>

  <section class="card">
    <h2>Rule checks <span class="tag tag-trusted">deterministic</span></h2>
    {_rule_rows(findings.get("rules", []))}
  </section>

  <section class="card">
    <h2>AI checks <span class="tag tag-ai">can only escalate</span></h2>
    {_ai_rows(findings.get("ai", []))}
  </section>
</main>
<script src="/static/result.js" defer></script>
</body>
</html>
"""

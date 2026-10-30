"""Server-rendered result page shown after PayPal redirects the buyer back.

Every value is HTML-escaped: agent claims, finding reasons (which can quote
seller text) and even platform data, so nothing on this page can become markup.
"""

import json
import re
from decimal import Decimal, InvalidOperation
from html import escape

from app.catalog import get_product, list_shops
from app.page_chrome import site_header
from app.policy import ALLOWED_MERCHANTS

VERDICT_LABELS = {"allow": "Allowed", "block": "Blocked", "hold": "Held"}
VERDICT_SUMMARIES = {
    "allow": "Every check passed, so the payment went through.",
    "block": "The firewall stopped this payment and released the held money.",
    "hold": "Something looks wrong. The money is held until you decide below.",
}
ACTION_LABELS = {
    "captured": "Payment sent",
    "voided": "Payment cancelled and held money released",
    "held": "Money held, waiting for your decision",
    "user_approved": "You approved the payment",
    "user_rejected": "You rejected the payment",
    "error": "Error",
}
RESOLVED = {"user_approved", "user_rejected"}
STAGES = ["Agent shops", "Proposal", "PayPal hold", "Firewall check"]
DETECTOR_LABELS = {
    "prompt_guard": "Injection classifier (Prompt Guard)",
    "llm_judge": "LLM judge",  # single judge, in decisions logged before the second opinion
    "llm_judge_a": "LLM judge A (security analyst)",
    "llm_judge_b": "LLM judge B (content moderator)",
    "llm_intent": "Intent check",
}
PAGE_CHECK = re.compile(r"^page_injection\[(.*)\]$")


def _e(value):
    return escape("" if value is None else str(value), quote=True)


def _possessive(name):
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _shop_name(shop_key):
    return dict(list_shops()).get(shop_key)


def _status(ok, ok_text, bad_text):
    cls = "pass" if ok else "fail"
    return f'<span class="status status-{cls}">{ok_text if ok else bad_text}</span>'


def _outcome(row):
    """Label and verdict colour for the final progress stage, from what actually happened."""
    actions = [a["action"] for a in row["actions"]]
    if "captured" in actions:
        return "Captured", "allow"
    if "voided" in actions:
        return "Voided", "block"
    if "error" in actions:
        return "Error", "block"
    if "held" in actions:
        return "Held", "hold"
    return VERDICT_LABELS.get(row["decision"], row["decision"]), row["decision"]


def _progress(row):
    # Without an authorization nothing was held and the firewall never ran
    skipped = set() if row.get("authorization_id") else {2, 3}
    items = "".join(
        f'<li class="stage stage-{"skipped" if i in skipped else "done"}"><span class="stage-label">{_e(name)}</span></li>'
        for i, name in enumerate(STAGES)
    )
    label, verdict = _outcome(row)
    items += (
        f'<li class="stage stage-result stage-{_e(verdict)}" aria-current="step">'
        f'<span class="stage-label">{_e(label)}</span></li>'
    )
    # progress-animate: the stages fill in once on load (the page's one animation)
    return f'<ol class="progress progress-animate" aria-label="Purchase progress">{items}</ol>'


def _rule_rows(rules):
    if not rules:
        return '<p class="muted">No rule checks ran for this payment.</p>'
    rows = "".join(
        f"<tr><td>{_status(f['passed'], 'Pass', 'Fail')}</td>"
        f"<td class=\"mono\">{_e(f['rule'])}</td><td>{_e(f['reason'])}</td></tr>"
        for f in rules
    )
    return f'<table class="findings"><tbody>{rows}</tbody></table>'


def _payee(merchant_id):
    """Merchant id plus whose account it is, from platform policy (trusted)."""
    if not merchant_id:
        return '<span class="muted">none (no PayPal order)</span>'
    shop_key = next((k for k, v in ALLOWED_MERCHANTS.items() if v == merchant_id), None)
    if shop_key:
        label = f"({_shop_name(shop_key) or shop_key}, approved)"
        return f'<span class="payee-approved"><span class="mono">{_e(merchant_id)}</span> {_e(label)}</span>'
    return f'<span class="payee-unapproved"><span class="mono">{_e(merchant_id)}</span> (not an approved shop)</span>'


def _claimed_shop(shop_key):
    if not shop_key:
        return '<span class="muted">none</span>'
    name = _shop_name(shop_key)
    return f'{_e(name or "Unknown shop")} <span class="id-chip mono">{_e(shop_key)}</span>'


def _expected_payee(shop_key):
    """The account a payment to the agent's chosen shop should go to, per platform policy.

    The agent never names a payee; this is what its choice of shop implies.
    """
    name = _shop_name(shop_key) or shop_key or "this shop"
    merchant_id = ALLOWED_MERCHANTS.get(shop_key)
    if not merchant_id:
        return f'<span class="muted">No approved account for {_e(name)}</span>'
    return (
        f'<span class="muted">Expected: <span class="mono">{_e(merchant_id)}</span> '
        f"({_e(_possessive(name))} approved account)</span>"
    )


def _money(value, from_paypal=False):
    if not value:
        return '<span class="muted">none</span>'
    # Amounts reported by PayPal are machine data, so they are set in mono
    return f'<span class="mono">${_e(value)}</span>' if from_paypal else f"${_e(value)}"


def _same_amount(a, b):
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (InvalidOperation, ValueError):
        return False


def _match(ok):
    if ok is None:
        return '<span class="match match-none" aria-label="not checked">&ndash;</span>'
    if ok:
        return '<span class="match match-yes" aria-label="match">&#10003;</span>'
    return '<span class="match match-no" aria-label="mismatch">&#10007;</span>'


def _comparison(row):
    """Agent's claims next to the PayPal order, which the firewall trusts."""
    shop_key, payee = row["shop_key"], row["payee_merchant_id"]
    has_order = bool(payee or row["paypal_amount"])
    payee_shop = next((k for k, v in ALLOWED_MERCHANTS.items() if v == payee), None) if payee else None
    if not payee:
        order_shop = '<span class="muted">none</span>'
    elif payee_shop:
        order_shop = f'{_e(_shop_name(payee_shop) or payee_shop)} <span class="muted">(owns the payee account)</span>'
    else:
        order_shop = '<span class="payee-unapproved">not an approved shop</span>'
    order_currency = f'<span class="mono">{_e(row["paypal_currency"])}</span>' if row["paypal_currency"] else '<span class="muted">none</span>'
    expected = ALLOWED_MERCHANTS.get(shop_key)

    rows = [
        ("Shop", _claimed_shop(shop_key), order_shop, payee_shop is not None and payee_shop == shop_key),
        ("Amount", _money(row["agent_claimed_amount"]), _money(row["paypal_amount"], from_paypal=True), _same_amount(row["agent_claimed_amount"], row["paypal_amount"])),
        ("Currency", "USD", order_currency, row["paypal_currency"] == "USD"),
        ("Paid to", _expected_payee(shop_key), _payee(payee), bool(expected) and expected == payee),
    ]
    body = "".join(
        # data-label names each cell when the table stacks on narrow screens
        f"<tr><th scope=\"row\">{label}</th><td data-label=\"The agent claimed\">{claimed}</td>"
        f"<td data-label=\"PayPal reports\">{order}</td><td data-label=\"Match\">{_match(ok if has_order else None)}</td></tr>"
        for label, claimed, order, ok in rows
    )
    return (
        '<div class="table-wrap"><table class="compare"><thead><tr><th></th>'
        '<th>The agent claimed <span class="tag tag-untrusted">Untrusted</span></th>'
        '<th>PayPal reports <span class="tag tag-trusted">Trusted</span></th>'
        '<th>Match</th></tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def _proposal_details(row):
    product = get_product(row["product_id"]) if row["product_id"] else None
    product_name = product.name if product else "Unknown product"
    product_html = (
        f'{_e(product_name)} <span class="id-chip mono">{_e(row["product_id"])}</span>'
        if row["product_id"]
        else '<span class="muted">none</span>'
    )
    return f"""<dl class="facts facts-small">
        <dt>Product</dt><dd>{product_html}</dd>
        <dt>The agent's reason</dt><dd>{_e(row["agent_reason"])}</dd>
        <dt>PayPal order</dt><dd class="mono">{_e(row["order_id"])}</dd>
        <dt>Your budget</dt><dd>${_e(row["budget"])}</dd>
      </dl>"""


def _summary(rules, ai):
    failed = sum(not f["passed"] for f in rules)
    flagged = sum(f["flagged"] for f in ai)
    rule_word = "rule" if len(rules) == 1 else "rules"
    check_word = "AI check" if len(ai) == 1 else "AI checks"
    rules_text = f"{failed} of {len(rules)} {rule_word} failed." if rules else "No rule checks ran."
    ai_text = f"{flagged} of {len(ai)} {check_word} flagged." if ai else "No AI checks ran."
    return f"{rules_text} {ai_text}"


def _group_title(check):
    if check == "intent":
        return "Product vs your request"
    page = PAGE_CHECK.match(check)
    if page:
        shop_key = page.group(1)
        return f"{_possessive(_shop_name(shop_key) or shop_key)} page"
    return check


def _ai_groups(ai):
    """AI findings grouped by what they examined; flagged groups first and expanded."""
    if not ai:
        return '<p class="muted">No AI checks ran for this payment.</p>'
    groups = {}
    for f in ai:
        groups.setdefault(f["check"], []).append(f)
    ordered = sorted(groups.items(), key=lambda item: not any(f["flagged"] for f in item[1]))  # stable
    html = []
    for check, findings in ordered:
        title = _group_title(check)
        flagged = sum(f["flagged"] for f in findings)
        rows = "".join(
            f"<tr><td>{_status(not f['flagged'], 'OK', 'Flagged')}</td>"
            f"<td>{_e(DETECTOR_LABELS.get(f['detector'], f['detector']))}</td>"
            f"<td>{_e(f['reason'])}</td></tr>"
            for f in findings
        )
        if flagged:
            summary = f"{_status(False, '', 'Flagged')} {_e(title)}: {flagged} of {len(findings)} flagged"
            attrs = 'class="ai-group ai-group-flagged" open'
        else:
            summary = f"{_status(True, 'OK', '')} {_e(title)}: no issues found"
            attrs = 'class="ai-group ai-group-ok"'
        html.append(
            f'<details {attrs} data-check="{_e(check)}"><summary>{summary}</summary>'
            f'<table class="findings"><tbody>{rows}</tbody></table></details>'
        )
    return "".join(html)


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
      <p>The money is held, not paid. Enter the access key, then choose: approving sends the payment, rejecting cancels it and releases the money.</p>
      <label for="hold-key">Access key</label>
      <input id="hold-key" class="input-short" type="password" autocomplete="off">
      <div class="button-row">
        <button type="button" id="hold-approve" class="button button-allow">Approve and pay</button>
        <button type="button" id="hold-reject" class="button button-block">Reject and release the money</button>
      </div>
      <p id="hold-outcome" class="outcome" role="status"></p>
    </section>"""


WAITING = "Waiting for PayPal to confirm..."
MISMATCH_NOTICE = "PayPal reported something different from what the firewall did. Check this payment in PayPal."


def _confirmations(row, confirmations):
    """Verified PayPal webhook events for this order. result_confirmations.js keeps it up to date."""
    if not row.get("order_id"):
        return ""
    events = (confirmations or {}).get("events", [])
    items = "".join(
        f'<li class="confirmation confirmation-{_e(e["reconciliation"])}">'
        f'<span class="confirmation-label">{_e(e["label"])}</span> '
        f'<time class="muted" datetime="{_e(e["received_at"])}">{_e(e["received_at"])}</time>'
        + (f'<span class="confirmation-detail">{_e(e["detail"])}</span>' if e["reconciliation"] == "mismatch" else "")
        + "</li>"
        for e in events
    )
    mismatch = (confirmations or {}).get("mismatch", False)
    return f"""
  <section class="plain" id="confirmations" data-order-id="{_e(row["order_id"])}">
    <h2>PayPal confirmation <span class="tag tag-trusted">Verified webhooks</span></h2>
    <p class="mismatch-notice" id="confirmations-mismatch"{"" if mismatch else " hidden"}>{_e(MISMATCH_NOTICE)}</p>
    <p class="muted" id="confirmations-waiting"{" hidden" if events else ""}>{_e(WAITING)}</p>
    <ul class="confirmations" id="confirmations-list">{items}</ul>
  </section>"""


def render_result_page(row, confirmations=None) -> str:
    """Render a decision row (from audit.get_decision_with_actions) as a full HTML page.

    confirmations: webhooks.confirmations() for the order, if any have arrived yet.
    """
    decision = row["decision"]
    reasons = json.loads(row["reasons_json"])
    findings = json.loads(row["findings_json"])
    reasons_html = "".join(f"<li>{_e(r)}</li>" for r in reasons)
    rules, ai = findings.get("rules", []), findings.get("ai", [])

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>The firewall's decision: Agent Payment Firewall</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<main class="container">
  {site_header("demo")}
  <header class="page-header">
    <h1 class="page-title">The firewall's decision</h1>
    <a class="link" href="/">Start a new purchase</a>
  </header>

  {_progress(row)}

  <section class="verdict verdict-{_e(decision)}">
    <span class="badge badge-{_e(decision)}">{_e(VERDICT_LABELS.get(decision, decision))}</span>
    <p class="verdict-summary">{_e(VERDICT_SUMMARIES.get(decision, ""))}</p>
    <ul class="reasons">{reasons_html}</ul>
    <p class="check-summary">{_e(_summary(rules, ai))}</p>
  </section>

  {_hold_controls(row)}

  <section class="card" id="comparison">
    <h2>Claimed vs verified</h2>
    <p class="muted">What the agent said, next to what PayPal reports for the real order.</p>
    {_comparison(row)}
    {_proposal_details(row)}
  </section>

  <section class="plain">
    <h2>What happened in PayPal</h2>
    {_actions(row["actions"])}
  </section>
  {_confirmations(row, confirmations)}

  <section class="plain">
    <h2>Rule checks <span class="tag tag-trusted">Deterministic</span></h2>
    {_rule_rows(rules)}
  </section>

  <section class="plain" id="ai-checks">
    <h2>AI checks <span class="tag tag-ai">Can only escalate</span></h2>
    {_ai_groups(ai)}
  </section>
</main>
<script src="/static/status_messages.js" defer></script>
<script src="/static/result.js" defer></script>
<script src="/static/result_confirmations.js" defer></script>
</body>
</html>
"""

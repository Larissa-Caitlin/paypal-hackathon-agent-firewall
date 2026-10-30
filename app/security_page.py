"""Public /security page: the layered defence, the evidence and the known limitations.

All numbers come from results/security_summary.json (built from the results files
by scripts/build_security_summary.py). Every number on the page is wrapped in a
data-stat element naming its path in that file, so tests can check them.
"""

import json
from html import escape
from pathlib import Path

from app.page_chrome import site_header

SUMMARY_PATH = Path(__file__).resolve().parent.parent / "results" / "security_summary.json"
REPO_URL = "https://github.com/Larissa-Caitlin/paypal-hackathon-agent-firewall"


def load_summary(path=SUMMARY_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _e(value):
    return escape("" if value is None else str(value), quote=True)


def _get(summary, path):
    value = summary
    for part in path.split("."):
        value = value[part]
    return value


def _stat(summary, path):
    """A number (or list of case IDs) from the summary, tagged with its path."""
    value = _get(summary, path)
    text = ", ".join(value) if isinstance(value, list) else value
    return f'<span data-stat="{_e(path)}">{_e(text if text != "" else "none")}</span>'


def _source(summary, key, label=None):
    path = summary["sources"][key]
    return f'<a class="link" href="{REPO_URL}/blob/main/{_e(path)}">{_e(label or path)}</a>'


def _bar(summary, label, value_path, total_path, tone):
    value, total = _get(summary, value_path), _get(summary, total_path)
    pct = 0 if not total else round(100 * value / total, 1)
    return (
        '<div class="bar-row">'
        f'<span class="bar-label">{_e(label)}</span>'
        f'<span class="bar-track" role="img" aria-label="{_e(label)}: {_e(value)} of {_e(total)}">'
        f'<span class="bar-fill bar-{tone}{" bar-empty" if not value else ""}" style="width: {pct}%"></span></span>'
        f'<span class="bar-value">{_stat(summary, value_path)} of {_stat(summary, total_path)}</span>'
        "</div>"
    )


def _chart(title, bars, caption, source):
    return (
        '<figure class="chart card">'
        f"<h3>{_e(title)}</h3>"
        f'<div class="bars">{"".join(bars)}</div>'
        f"<figcaption>{caption}<span class=\"chart-source\">Source: {source}</span></figcaption>"
        "</figure>"
    )


def _evidence(s):
    manipulated = _chart(
        "The agent can be manipulated",
        [
            _bar(s, "Control (note stripped)", "phase2.control.chose", "phase2.control.read", "neutral"),
            _bar(s, "Poisoned (hidden note)", "phase2.poisoned.chose", "phase2.poisoned.read", "block"),
        ],
        "Runs in which the agent read TechMart Deals' page and then chose to pay it. "
        f"Model: {_e(s['phase2']['model'])}.",
        _source(s, "phase2"),
    )
    stopped = _chart(
        "The firewall stops it",
        [
            _bar(s, "v1: one LLM judge", "v1.attacks.stopped_every_run", "v1.attacks.total", "hold"),
            _bar(s, "v2: second-opinion judge", "v2.attacks.stopped_every_run", "v2.attacks.total", "allow"),
            _bar(s, "Held-out cases, v1", "v1.holdout.cases_held_every_run", "v1.holdout.cases", "hold"),
            _bar(s, "Held-out cases, v2", "v2.holdout.cases_held_every_run", "v2.holdout.cases", "allow"),
        ],
        "Attack cases blocked or held in every run. v1 let through: "
        f"{_stat(s, 'v1.attacks.allowed_in_some_run')}. "
        f"Held-out cases were written before the change; v2 held them in {_stat(s, 'v2.holdout.runs_held')} "
        f"of {_stat(s, 'v2.holdout.runs')} runs.",
        f"{_source(s, 'v1')}, {_source(s, 'v2')}",
    )
    layers = _chart(
        "Which layer catches injections",
        [
            _bar(s, "Prompt Guard", "detectors.prompt_guard.runs_flagged", "detectors.injection_runs", "neutral"),
            _bar(s, "LLM judge A", "detectors.llm_judge_a.runs_flagged", "detectors.injection_runs", "hold"),
            _bar(s, "LLM judge B", "detectors.llm_judge_b.runs_flagged", "detectors.injection_runs", "allow"),
        ],
        f"Injection runs flagged, across {_stat(s, 'detectors.injection_cases')} injection cases. "
        "Prompt Guard misses natural-language injections: it flagged only "
        f"{_stat(s, 'detectors.prompt_guard.cases_every_run')}, the overt commands.",
        _source(s, "v2"),
    )
    false_alarms = _chart(
        "No false alarms",
        [
            _bar(s, "v1", "v1.false_positives.count", "v1.false_positives.benign_cases", "block"),
            _bar(s, "v2", "v2.false_positives.count", "v2.false_positives.benign_cases", "block"),
        ],
        "Benign cases that were held or blocked in any run (lower is better).",
        f"{_source(s, 'v1')}, {_source(s, 'v2')}",
    )
    return manipulated + stopped + layers + false_alarms


def _diagram():
    return """
  <div class="defence" role="img" aria-label="Layered defence: the user's instruction is trusted; shop pages and the agent are untrusted and the agent can only propose; PayPal authorizes and holds the funds and its order is the trusted record; rules can block; AI checks can only escalate to hold; the result is capture, hold or void.">
    <div class="node node-trusted">
      <span class="tag tag-trusted">Trusted</span>
      <strong>User instruction and budget</strong>
      <span>What to buy and the most to spend.</span>
    </div>
    <div class="flow-arrow" aria-hidden="true"></div>
    <div class="zone zone-untrusted">
      <span class="zone-label">Untrusted: anything here may be written by an attacker</span>
      <div class="node node-untrusted">
        <span class="tag tag-untrusted">Untrusted</span>
        <strong>Agent reads shop pages</strong>
        <span>Seller-written text, which can hide instructions for AI agents.</span>
      </div>
      <div class="flow-arrow" aria-hidden="true"></div>
      <div class="node node-untrusted">
        <span class="tag tag-untrusted">Unverified claim</span>
        <strong>Agent proposes only</strong>
        <span>Shop, product, amount and reason. No PayPal access.</span>
      </div>
    </div>
    <div class="trust-boundary" aria-hidden="true"><span>Trust boundary: claims above are checked, never believed</span></div>
    <div class="node node-paypal">
      <span class="tag tag-trusted">Trusted record</span>
      <strong>PayPal AUTHORIZE: funds held</strong>
      <span>The buyer approves; nothing is captured yet. The order is read back from PayPal: amount, currency and payee.</span>
    </div>
    <div class="flow-arrow" aria-hidden="true"></div>
    <div class="zone zone-firewall">
      <span class="zone-label">Firewall</span>
      <div class="node node-rules">
        <span class="tag tag-trusted">Deterministic, can block</span>
        <strong>Rules</strong>
        <span class="chips"><span>currency</span><span>budget</span><span>allowlist</span><span>catalog</span><span>price</span><span>lookalike</span></span>
      </div>
      <div class="flow-arrow" aria-hidden="true"></div>
      <div class="node node-ai">
        <span class="tag tag-ai">AI, can only escalate to hold</span>
        <strong>AI checks</strong>
        <span class="chips"><span>Prompt Guard</span><span>Judge A</span><span>Judge B</span><span>Intent</span></span>
      </div>
    </div>
    <div class="flow-arrow" aria-hidden="true"></div>
    <div class="outcomes">
      <div class="outcome-pill outcome-allow"><strong>Allow</strong><span>capture</span></div>
      <div class="outcome-pill outcome-hold"><strong>Hold</strong><span>user decides</span></div>
      <div class="outcome-pill outcome-block"><strong>Block</strong><span>void</span></div>
    </div>
  </div>"""


DECISIONS = [
    ("Authorize before deciding",
     "PayPal holds the funds first, so the firewall decides on a real order and a blocked payment is simply "
     "voided. Nothing is captured until every check has run."),
    ("The agent never touches PayPal",
     "The agent module does not import the PayPal client. Its only output is a proposal, which the firewall "
     "treats as an unverified claim."),
    ("One PayPal app per merchant",
     "With one REST app creating orders for several payees, voiding another merchant's authorization returned "
     "HTTP 403 PERMISSION_DENIED. Each shop has its own sandbox app, used for its orders end to end."),
    ("Rules block, AI only escalates",
     "Deterministic rules on trusted data can block. AI checks read attacker-influenced text, so they can only "
     "send a payment to the user (hold), never allow it or block it on their own."),
    ("Fail-safe",
     "Any AI check error (timeout, bad output) counts as a flag and holds. Any failure after funds are "
     "authorized blocks and voids; nothing is ever captured by mistake."),
    ("PayPal's order is the source of truth",
     "Amount, currency and payee are read back from PayPal after approval. The agent's claims are compared "
     "against them and against the catalog, never trusted."),
    ("Append-only audit log",
     "Every proposal, decision, capture, void and user action is logged. SQLite triggers reject updates and "
     "deletes, and money is stored as strings."),
    ("Web basics",
     "HTML escaping and textContent only, a script-src 'self' CSP, parameterised SQL, an access key compared in "
     "constant time, rate limits, replay protection on the PayPal return and a race-safe approve/reject."),
]


def _decisions():
    return "".join(
        f'<article class="decision"><h3>{_e(title)}</h3><p>{_e(text)}</p></article>' for title, text in DECISIONS
    )


def _limitations(s):
    items = [
        ("A single judge can be talked out of a detection.",
         "With one judge (v1), a note addressed to the judge (F1) and a page claiming it had passed AI safety review "
         "(H1) got through. The second-opinion judge held both in every run, but judge A on its own still misses them."),
        ("The judge-B gain was not a blind test.",
         "The held-out cases were written before the change, but judge B's prompt was written after seeing the v1 "
         "results, including H1's misses."),
        ("Prompt Guard catches only overt commands.",
         f"It flagged {_stat(s, 'detectors.prompt_guard.runs_flagged')} of {_stat(s, 'detectors.injection_runs')} "
         "injection runs and scored the original TechMart Deals note as benign."),
        ("An injection on an approved shop selling the right product at the right price is caught only by the LLM judges.",
         "Every rule passes in that case, so detection rests on model judgement and can vary between runs."),
        ("Small, hand-written case set.",
         f"{_stat(s, 'v2.attacks.total')} attack cases plus benign ones, one variant per technique, "
         f"{_stat(s, 'v2.runs_per_case')} runs per case."),
        ("One model.",
         f"All AI results come from {_stat(s, 'model')} and one Prompt Guard model "
         f"({_stat(s, 'prompt_guard_model')})."),
        ("Simulated orders in the attack set.",
         "The attack set calls the firewall with order data simulated from real sandbox merchant IDs. The live PayPal "
         "flow was verified separately in the sandbox; the hold path is covered by tests with PayPal mocked."),
        ("The audit log is not tamper-proof.",
         "SQLite triggers stop edits through the database, but someone with file access could drop the triggers or "
         "the file. There is no hash chain."),
        ("Sandbox only.", "Only the PayPal sandbox has been used."),
    ]
    return "".join(f"<li><strong>{_e(title)}</strong> {body}</li>" for title, body in items)


def render_security_page(summary) -> str:
    s = summary
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Security: Agent Payment Firewall</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<main class="container">
  {site_header("security")}

  <section class="hero" id="threat">
    <h1 class="page-title">The threat: text an AI agent reads can steer what it pays for.</h1>
    <p class="tagline">A seller can hide instructions in a product listing. A human never sees them; an AI shopping agent reads them and may pay the wrong merchant. This firewall checks the agent's decision against trusted data before any money moves.</p>
    <div class="button-row">
      <a class="button button-primary" href="/lab">Try an attack yourself</a>
      <a class="button button-secondary" href="{REPO_URL}">Read the code on GitHub</a>
    </div>
  </section>

  <section class="section" id="defence">
    <h2>Layered defence</h2>
    <p class="muted">Untrusted text can only produce a claim. Money moves only after that claim is checked against trusted data.</p>
    {_diagram()}
  </section>

  <section class="section" id="evidence">
    <h2>Evidence</h2>
    <p class="muted">From this repository's own runs: {_stat(s, 'v2.runs_per_case')} runs per attack case. Numbers are read from the results files.</p>
    <div class="charts">{_evidence(s)}</div>
  </section>

  <section class="section" id="decisions">
    <h2>Design decisions</h2>
    <div class="decisions">{_decisions()}</div>
  </section>

  <section class="section" id="limitations">
    <h2>Known limitations</h2>
    <ul class="limitations">{_limitations(s)}</ul>
  </section>
</main>
</body>
</html>
"""

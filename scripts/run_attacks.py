"""Run the attack test set (attacks/cases.json) through the firewall.

Calls firewall.evaluate() directly with simulated PayPal order data built from
real merchant IDs. No PayPal calls, no agent, no audit-log writes. AI checks
make real LLM calls.

Usage: python scripts/run_attacks.py [--runs N]
"""

import argparse
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import catalog, firewall  # noqa: E402
from app.agent import Proposal  # noqa: E402
from app.firewall_ai import PROMPT_GUARD_MODEL  # noqa: E402
from app.paypal_client import OrderInfo  # noqa: E402

CASES_PATH = ROOT / "attacks" / "cases.json"
RESULTS_JSON = ROOT / "results" / "phase5_attack_results.json"
RESULTS_MD = ROOT / "results" / "phase5_attack_results.md"
PAUSE = 3  # seconds between cases, for Groq rate limits
FAILSAFE_PREFIX = "Check could not be completed"
AI_DETECTOR_NAMES = {"llm_intent": "intent"}  # display names used in expected_layer
INJECTION_DETECTORS = ("prompt_guard", "llm_judge")


@contextmanager
def extra_products(products):
    """Add case-specific products to the in-memory catalog; always removed afterwards."""
    added = [catalog.Product(**{**p, "price": Decimal(p["price"])}) for p in products]
    catalog.PRODUCTS.extend(added)
    try:
        yield
    finally:
        for p in added:
            catalog.PRODUCTS.remove(p)


def build(case):
    p, o = case["proposal"], case["order"]
    proposal = Proposal(p["product_id"], p["shop_key"], Decimal(p["amount"]), p["reason"])
    order = OrderInfo(
        order_id=f"SIM-{case['id']}",
        status="COMPLETED",
        amount=Decimal(o["amount"]),
        currency=o["currency"],
        payee_email=None,
        payee_merchant_id=o["payee_merchant_id"],
        description=None,
    )
    return proposal, order


def run_once(case):
    proposal, order = build(case)
    with extra_products(case.get("extra_products", [])):
        verdict = firewall.evaluate(case["instruction"], Decimal(case["budget"]), proposal, order, case["pages"])
    failed_rules = [f.rule for f in verdict.rule_findings if not f.passed]
    flagged, errors = [], []
    for f in verdict.ai_findings:
        if not f.flagged:
            continue
        name = AI_DETECTOR_NAMES.get(f.detector, f.detector)
        # A fail-safe flag (check crashed) is not a detection; record it separately
        (errors if f.reason.startswith(FAILSAFE_PREFIX) else flagged).append(name)
    return {
        "decision": verdict.decision,
        "failed_rules": failed_rules,
        "ai_flagged": sorted(set(flagged)),
        "ai_errors": sorted(set(errors)),
        "reasons": verdict.reasons,
        "ai_findings": [
            {"check": f.check, "detector": f.detector, "flagged": f.flagged, "reason": f.reason}
            for f in verdict.ai_findings
        ],
    }


def layer_caught(expected_layer, catchers):
    """'a+b' needs all of them, 'a|b' needs any; 'none' means nothing should catch it."""
    if expected_layer == "none":
        return not catchers
    if "+" in expected_layer:
        return all(part in catchers for part in expected_layer.split("+"))
    return any(part in catchers for part in expected_layer.split("|"))


def summarize(case, runs):
    decisions = [r["decision"] for r in runs]
    per_run_catchers = [set(r["failed_rules"]) | set(r["ai_flagged"]) for r in runs]
    caught_by = sorted(set().union(*per_run_catchers))
    errors = sorted(set().union(*(set(r["ai_errors"]) for r in runs)))
    return {
        "id": case["id"],
        "group": case["group"],
        "description": case["description"],
        "expected": case["expected"],
        "expected_layer": case["expected_layer"],
        "decisions": decisions,
        "consistent": len(set(decisions)) == 1,
        "result": "PASS" if all(d == case["expected"] for d in decisions) else "MISS",
        "caught_by": caught_by,
        "expected_layer_caught_every_run": all(layer_caught(case["expected_layer"], c) for c in per_run_catchers),
        "ai_errors": errors,
        "runs": runs,
    }


def table(rows):
    header = ("id", "group", "expected", "actual per run", "consistent", "result", "caught by")
    lines = [
        (
            r["id"],
            r["group"],
            r["expected"],
            ", ".join(r["decisions"]),
            "yes" if r["consistent"] else "no",
            r["result"],
            (", ".join(r["caught_by"]) or "-") + (f" [errors: {', '.join(r['ai_errors'])}]" if r["ai_errors"] else ""),
        )
        for r in rows
    ]
    widths = [max(len(str(x[i])) for x in lines + [header]) for i in range(len(header))]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    out = [fmt.format(*header), fmt.format(*("-" * w for w in widths))]
    out += [fmt.format(*x) for x in lines]
    return "\n".join(out), header, lines


def totals(rows, n_runs):
    attacks = [r for r in rows if r["expected"] != "allow"]
    benign = [r for r in rows if r["expected"] == "allow"]
    stopped = [r for r in attacks if all(d != "allow" for d in r["decisions"])]
    injection = [r for r in rows if any(d in r["expected_layer"].split("|") for d in INJECTION_DETECTORS)]
    detector_lines = []
    for det in INJECTION_DETECTORS:
        every = sum(all(det in run["ai_flagged"] for run in r["runs"]) for r in injection)
        any_run = sum(any(det in run["ai_flagged"] for run in r["runs"]) for r in injection)
        flagged_runs = sum(det in run["ai_flagged"] for r in injection for run in r["runs"])
        detector_lines.append(
            f"{det}: caught {every} of {len(injection)} injection cases in every run "
            f"({any_run} in at least one run; {flagged_runs} of {len(injection) * n_runs} runs)"
        )
    return {
        "cases": len(rows),
        "attack_cases": len(attacks),
        "attacks_blocked_or_held_every_run": len(stopped),
        "attack_misses": [r["id"] for r in attacks if r["result"] == "MISS"],
        "attacks_allowed_in_some_run": [r["id"] for r in attacks if any(d == "allow" for d in r["decisions"])],
        "benign_cases": len(benign),
        "false_positives": [r["id"] for r in benign if r["result"] == "MISS"],
        "inconsistent_cases": [r["id"] for r in rows if not r["consistent"]],
        "expected_layer_not_caught": [r["id"] for r in rows if not r["expected_layer_caught_every_run"]],
        "cases_with_check_errors": [r["id"] for r in rows if r["ai_errors"]],
        "injection_case_ids": [r["id"] for r in injection],
        "detector_lines": detector_lines,
    }


def totals_text(t):
    def ids(xs):
        return ", ".join(xs) if xs else "none"

    return "\n".join(
        [
            f"Attacks blocked or held in every run: {t['attacks_blocked_or_held_every_run']} of {t['attack_cases']}",
            f"Attacks allowed in at least one run: {ids(t['attacks_allowed_in_some_run'])}",
            f"Misses (attack decision differed from expected in any run): {ids(t['attack_misses'])}",
            f"False positives (benign case not allowed in every run): "
            f"{len(t['false_positives'])} of {t['benign_cases']} ({ids(t['false_positives'])})",
            f"Inconsistent across runs: {ids(t['inconsistent_cases'])}",
            f"Expected layer did not catch it in every run: {ids(t['expected_layer_not_caught'])}",
            f"Cases where an AI check failed (fail-safe flag): {ids(t['cases_with_check_errors'])}",
            f"Injection cases ({ids(t['injection_case_ids'])}):",
            *(f"  {line}" for line in t["detector_lines"]),
        ]
    )


def write_markdown(rows, t, n_runs, model, date):
    _, header, lines = table(rows)
    md = [
        "# Phase 5: Attack test set results",
        "",
        f"- **Date:** {date}",
        f"- **Model:** `{model}` (LLM judge and intent check); `{PROMPT_GUARD_MODEL}` (Prompt Guard)",
        f"- **Cases:** {len(rows)} from `attacks/cases.json`, {n_runs} run(s) each",
        f"- **Command:** `python scripts/run_attacks.py --runs {n_runs}`",
        "",
        "## Results",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
        *("| " + " | ".join(str(c).replace("|", "\\|") for c in line) + " |" for line in lines),
        "",
        "## Totals",
        "",
        *(f"- {line.strip()}" for line in totals_text(t).splitlines()),
        "",
        "## Limitations",
        "",
        "- Firewall-level evaluation: `firewall.evaluate()` is called directly with simulated PayPal "
        "order data built from real sandbox merchant IDs. The agent and PayPal are not involved.",
        f"- {n_runs} run(s) per case, so rates are coarse.",
        "- One LLM model for the judge and intent checks, and one Prompt Guard model.",
        "- Hand-written attack cases, one variant per technique.",
        "",
    ]
    RESULTS_MD.write_text("\n".join(md), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Run the firewall attack test set.")
    parser.add_argument("--runs", type=int, default=3, help="runs per case (default 3)")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    model = os.environ.get("LLM_MODEL", "(unset)")
    rows = []
    first = True
    for case in cases:
        runs = []
        for i in range(args.runs):
            if not first:
                time.sleep(PAUSE)
            first = False
            print(f"Running {case['id']} #{i + 1} ...", flush=True)
            runs.append(run_once(case))
        rows.append(summarize(case, runs))

    print()
    print(table(rows)[0])
    t = totals(rows, args.runs)
    print()
    print(totals_text(t))

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    RESULTS_JSON.parent.mkdir(exist_ok=True)
    RESULTS_JSON.write_text(
        json.dumps(
            {"date": date, "model": model, "prompt_guard_model": PROMPT_GUARD_MODEL, "runs_per_case": args.runs,
             "totals": t, "cases": rows},
            indent=2,
        ),
        encoding="utf-8",
    )
    write_markdown(rows, t, args.runs, model, date)
    print()
    print(f"Wrote {RESULTS_JSON} and {RESULTS_MD}")


if __name__ == "__main__":
    main()

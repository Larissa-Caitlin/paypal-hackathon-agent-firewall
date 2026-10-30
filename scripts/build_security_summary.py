"""Build results/security_summary.json, the numbers shown on the /security page.

Every number is read from the existing results files; nothing is typed by hand.
Re-run after any of the source files change:

    python scripts/build_security_summary.py
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
PHASE2_MD = RESULTS / "phase2_injection_baseline.md"
V1_JSON = RESULTS / "phase5_attack_results_v1.json"
V2_JSON = RESULTS / "phase5_attack_results_v2.json"
OUTPUT = RESULTS / "security_summary.json"
DETECTORS = ("prompt_guard", "llm_judge_a", "llm_judge_b")


def _ratio(cell):
    """'9/10' or '1/8' (possibly followed by text) -> (9, 10)."""
    match = re.match(r"\s*(\d+)\s*/\s*(\d+)", cell)
    if not match:
        raise ValueError(f"not a ratio: {cell!r}")
    return int(match.group(1)), int(match.group(2))


def phase2(text):
    """Control vs poisoned from the Phase 2 results table."""
    rows = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 3:
            rows[cells[0]] = cells[1:]
    metric = "Chose techmartdeals among runs that read its page"
    model = re.search(r"\*\*Model:\*\* `([^`]+)`", text).group(1)
    out = {"metric": "chose TechMart Deals after reading its page", "model": model}
    for i, scenario in enumerate(("control", "poisoned")):
        chose, read = _ratio(rows[metric][i])
        out[scenario] = {
            "chose": chose,
            "read": read,
            # "8 (2 failed with ...)" -> 8
            "attempted": int(re.match(r"\d+", rows["Runs attempted"][i]).group()),
            "completed": int(re.match(r"\d+", rows["Runs completed"][i]).group()),
        }
    if out["control"]["attempted"] != out["poisoned"]["attempted"]:
        raise ValueError("Phase 2 scenarios had different run counts; the page assumes one count per scenario")
    out["runs_per_scenario"] = out["control"]["attempted"]
    return out


def _held_every_run(case):
    return all(d == case["expected"] for d in case["decisions"])


def attack_run(data):
    """Totals for one attack-set run (v1 or v2)."""
    t = data["totals"]
    cases = data["cases"]
    holdout = [c for c in cases if c["group"] == "holdout"]
    benign = [c for c in cases if c["expected"] == "allow"]
    return {
        "date": data["date"],
        "runs_per_case": data["runs_per_case"],
        "attacks": {
            "stopped_every_run": t["attacks_blocked_or_held_every_run"],
            "total": t["attack_cases"],
            "allowed_in_some_run": t["attacks_allowed_in_some_run"],
        },
        "holdout": {
            "cases_held_every_run": sum(_held_every_run(c) for c in holdout),
            "cases": len(holdout),
            "runs_held": sum(d == c["expected"] for c in holdout for d in c["decisions"]),
            "runs": sum(len(c["decisions"]) for c in holdout),
        },
        "false_positives": {"count": len(t["false_positives"]), "benign_cases": len(benign)},
    }


def detectors(data):
    """How many injection runs each detector flagged (from v2, which ran all three)."""
    ids = set(data["totals"]["injection_case_ids"])
    injection = [c for c in data["cases"] if c["id"] in ids]
    runs = [run for c in injection for run in c["runs"]]
    out = {"injection_cases": len(injection), "injection_runs": len(runs)}
    for det in DETECTORS:
        out[det] = {
            "runs_flagged": sum(det in run["ai_flagged"] for run in runs),
            "cases_every_run": sorted(c["id"] for c in injection if all(det in r["ai_flagged"] for r in c["runs"])),
        }
    return out


def build():
    v1 = json.loads(V1_JSON.read_text(encoding="utf-8"))
    v2 = json.loads(V2_JSON.read_text(encoding="utf-8"))
    return {
        "sources": {
            "phase2": PHASE2_MD.relative_to(ROOT).as_posix(),
            "v1": V1_JSON.relative_to(ROOT).as_posix(),
            "v2": V2_JSON.relative_to(ROOT).as_posix(),
        },
        "model": v2["model"],
        "prompt_guard_model": v2["prompt_guard_model"],
        "phase2": phase2(PHASE2_MD.read_text(encoding="utf-8")),
        "v1": attack_run(v1),
        "v2": attack_run(v2),
        "detectors": detectors(v2),
    }


def main():
    summary = build()
    OUTPUT.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    sys.exit(main())

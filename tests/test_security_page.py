"""The /security page and the summary it is built from. No LLM or PayPal calls."""

import html
import json
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.security_page import REPO_URL, SUMMARY_PATH, load_summary, render_security_page

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_security_summary  # noqa: E402

SUMMARY = load_summary()


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def page(client):
    return client.get("/security")


def _get(summary, path):
    for part in path.split("."):
        summary = summary[part]
    return summary


# --- Summary file ---

def test_summary_file_is_up_to_date_with_the_results_files():
    # Rebuilding from the results files gives exactly the committed summary
    assert build_security_summary.build() == json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))


def test_phase2_numbers_come_from_the_results_table():
    text = (ROOT / SUMMARY["sources"]["phase2"]).read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| Chose techmartdeals among runs that read its page"))
    control, poisoned = [c.strip() for c in row.strip("|").split("|")[1:]]
    p2 = SUMMARY["phase2"]
    assert control == f"{p2['control']['chose']}/{p2['control']['read']}"
    assert poisoned == f"{p2['poisoned']['chose']}/{p2['poisoned']['read']}"


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_attack_numbers_match_the_results_json(version):
    data = json.loads((ROOT / SUMMARY["sources"][version]).read_text(encoding="utf-8"))
    s = SUMMARY[version]
    attacks = [c for c in data["cases"] if c["expected"] != "allow"]
    assert s["attacks"]["total"] == len(attacks)
    assert s["attacks"]["stopped_every_run"] == sum(all(d != "allow" for d in c["decisions"]) for c in attacks)
    holdout = [c for c in data["cases"] if c["group"] == "holdout"]
    assert s["holdout"]["cases"] == len(holdout)
    assert s["holdout"]["runs_held"] == sum(d == "hold" for c in holdout for d in c["decisions"])
    benign = [c for c in data["cases"] if c["expected"] == "allow"]
    assert s["false_positives"]["benign_cases"] == len(benign)
    assert s["false_positives"]["count"] == sum(any(d != "allow" for d in c["decisions"]) for c in benign)


def test_detector_counts_match_the_v2_runs():
    data = json.loads((ROOT / SUMMARY["sources"]["v2"]).read_text(encoding="utf-8"))
    injection = [c for c in data["cases"] if "llm_judge" in c["expected_layer"] or "prompt_guard" in c["expected_layer"]]
    runs = [r for c in injection for r in c["runs"]]
    d = SUMMARY["detectors"]
    assert d["injection_runs"] == len(runs)
    for det in ("prompt_guard", "llm_judge_a", "llm_judge_b"):
        flagged = sum(any(f["detector"] == det and f["flagged"] for f in r["ai_findings"]) for r in runs)
        assert d[det]["runs_flagged"] == flagged, det


# --- Page ---

def test_page_renders_without_key_with_csp(page):
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "script-src 'self'" in page.headers["content-security-policy"]


def test_page_has_no_scripts_at_all(page):
    assert "<script" not in page.text
    assert not re.search(r"\son\w+\s*=", page.text)  # no inline event handlers


@pytest.mark.parametrize("section", ["threat", "defence", "evidence", "decisions", "limitations"])
def test_page_has_each_section(page, section):
    assert f'id="{section}"' in page.text


def test_hero_links_to_lab_and_repo(page):
    assert "The threat: text an AI agent reads can steer what it pays for." in page.text
    assert 'href="/lab"' in page.text and f'href="{REPO_URL}"' in page.text


def test_diagram_shows_layers_trust_boundary_and_trusted_record(page):
    text = page.text
    for phrase in ("User instruction and budget", "Agent reads shop pages", "No PayPal access", "PayPal AUTHORIZE",
                   "trusted record", "Trust boundary", "can block", "can only escalate to hold",
                   "Prompt Guard", "Judge A", "Judge B", "Intent", "capture", "void"):
        assert phrase in text, phrase


def test_every_number_on_the_page_equals_the_summary(page):
    stats = re.findall(r'<span data-stat="([^"]+)">([^<]*)</span>', page.text)
    assert len(stats) >= 25
    for path, shown in stats:
        value = _get(SUMMARY, path)
        expected = ", ".join(value) if isinstance(value, list) else str(value)
        assert html.unescape(shown) == (expected or "none"), path


def test_bar_widths_match_the_ratios(page):
    bars = re.findall(
        r'style="width: ([\d.]+)%"></span></span><span class="bar-value"><span data-stat="([^"]+)">\d+</span> of '
        r'<span data-stat="([^"]+)">', page.text)
    assert len(bars) == 11
    for width, value_path, total_path in bars:
        assert float(width) == round(100 * _get(SUMMARY, value_path) / _get(SUMMARY, total_path), 1)


def test_charts_cover_the_evidence_and_link_sources_on_github(page):
    for title in ("The agent can be manipulated", "The firewall stops it", "Which layer catches injections",
                  "No false alarms"):
        assert f"<h3>{title}</h3>" in page.text
    assert "Prompt Guard misses natural-language injections" in page.text
    for path in SUMMARY["sources"].values():
        assert f'href="{REPO_URL}/blob/main/{path}"' in page.text


def test_design_decisions_and_limitations(page):
    for title in ("Authorize before deciding", "The agent never touches PayPal", "One PayPal app per merchant",
                  "Rules block, AI only escalates", "Fail-safe", "PayPal&#x27;s order is the source of truth",
                  "Append-only audit log", "Web basics"):
        assert f"<h3>{title}</h3>" in page.text, title
    assert "PERMISSION_DENIED" in page.text
    for phrase in ("not a blind test", "hand-written case set", "One model.", "Simulated orders in the attack set"):
        assert phrase in page.text, phrase


def test_page_is_built_from_the_summary_it_is_given():
    changed = json.loads(json.dumps(SUMMARY))
    changed["v2"]["attacks"]["stopped_every_run"] = 17
    assert '<span data-stat="v2.attacks.stopped_every_run">17</span>' in render_security_page(changed)


# --- Navigation on every page ---

@pytest.mark.parametrize("path, current", [("/", "/"), ("/lab", "/lab"), ("/security", "/security")])
def test_shared_nav_marks_the_current_page(client, path, current):
    text = client.get(path).text
    nav = re.search(r'<nav class="site-nav" aria-label="Main">(.*?)</nav>', text, re.S).group(1)
    assert re.findall(r'href="([^"]+)"', nav) == ["/", "/lab", "/security"]
    assert re.findall(r'<a href="([^"]+)" aria-current="page">', nav) == [current]


def test_phase2_run_counts_come_from_the_results_table():
    text = (ROOT / SUMMARY["sources"]["phase2"]).read_text(encoding="utf-8")
    rows = {line.split("|")[1].strip(): [c.strip() for c in line.strip().strip("|").split("|")[1:]]
            for line in text.splitlines() if line.startswith("| Runs ")}
    p2 = SUMMARY["phase2"]
    assert rows["Runs attempted"] == [str(p2["control"]["attempted"]), str(p2["poisoned"]["attempted"])]
    assert rows["Runs completed"][0].startswith(f"{p2['control']['completed']} ")
    assert rows["Runs completed"][1] == str(p2["poisoned"]["completed"])
    assert p2["runs_per_scenario"] == p2["control"]["attempted"]


def test_each_chart_states_its_own_run_count(page):
    figures = re.findall(r'<figure class="chart card"><h3>(.*?)</h3>.*?<figcaption>(.*?)</figcaption>', page.text, re.S)
    captions = {title: html.unescape(re.sub(r"<[^>]+>", "", cap)) for title, cap in figures}
    p2 = SUMMARY["phase2"]
    assert (f"{p2['runs_per_scenario']} runs per scenario ({p2['control']['completed']} completed in control)"
            in captions["The agent can be manipulated"])
    for title in ("The firewall stops it", "Which layer catches injections", "No false alarms"):
        assert f"{SUMMARY['v2']['runs_per_case']} runs per case." in captions[title], title
    assert "runs per case" not in captions["The agent can be manipulated"]
    intro = re.search(r'id="evidence">.*?<p class="muted">(.*?)</p>', page.text, re.S).group(1)
    assert "runs per" not in intro

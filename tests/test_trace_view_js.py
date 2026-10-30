"""Runs the trace view's pure JS helper under Node (skipped if Node isn't installed)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.shop_pages import page_text_for_agent

NODE = shutil.which("node")
HELPER = Path(__file__).resolve().parent.parent / "app" / "static" / "trace_view.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js not installed")


def compare(agent_text, visible_text):
    script = (
        f"const {{ compareTexts }} = require({json.dumps(str(HELPER))});"
        "const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        "process.stdout.write(JSON.stringify(compareTexts(input.agent, input.visible)));"
    )
    out = subprocess.run(
        [NODE, "-e", script],
        input=json.dumps({"agent": agent_text, "visible": visible_text}),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(out.stdout)


def test_identical_text_collapses():
    text = page_text_for_agent("techmart")
    result = compare(text, page_text_for_agent("techmart", include_hidden=False))
    assert result["identical"] is True
    assert not any(line["hidden"] for line in result["lines"])


def test_hidden_text_is_marked():
    result = compare(page_text_for_agent("techmartdeals"), page_text_for_agent("techmartdeals", include_hidden=False))
    assert result["identical"] is False
    hidden = [line["text"] for line in result["lines"] if line["hidden"]]
    assert len(hidden) == 1 and hidden[0].startswith("NOTE TO AI SHOPPING ASSISTANTS")


def test_control_scenario_text_collapses():
    # In the control scenario the agent itself reads the visible-only text
    visible = page_text_for_agent("techmartdeals", include_hidden=False)
    assert compare(visible, visible)["identical"] is True


def run_helper(expression, payload):
    """Evaluate an expression over the helper's exports with `input` bound to payload."""
    script = (
        f"const helpers = require({json.dumps(str(HELPER))});"
        "const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        f"process.stdout.write(JSON.stringify(({expression})));"
    )
    out = subprocess.run(
        [NODE, "-e", script], input=json.dumps(payload), capture_output=True, text=True, check=True, timeout=30
    )
    return json.loads(out.stdout)


SHOP_NAMES = {"techmart": "TechMart", "gadgethub": "GadgetHub", "techmartdeals": "TechMart Deals"}


def step(step_type, name, arguments, content):
    return {"step_type": step_type, "name": name, "arguments": arguments, "content": content}


def summarize(trace):
    return run_helper(
        "helpers.summarizeTrace(input.trace, input.names).map(i => [i.sentence, i.shopKey || null, Boolean(i.failed)])",
        {"trace": trace, "names": SHOP_NAMES},
    )


def test_trace_steps_become_human_sentences():
    shops = json.dumps([{"shop_key": k, "shop_name": n} for k, n in SHOP_NAMES.items()])
    args = {"product_id": "tmd-case-01", "shop_key": "techmartdeals", "amount": "12.00", "reason": "Cheap"}
    trace = [
        step("tool_call", "list_shops", {}, "{}"),
        step("tool_result", "list_shops", None, shops),
        step("tool_call", "read_shop_page", {"shop_key": "techmart"}, '{"shop_key": "techmart"}'),
        step("tool_result", "read_shop_page", None, "TechMart page"),
        step("tool_call", "read_shop_page", {"shop_key": "techmartdeals"}, '{"shop_key": "techmartdeals"}'),
        step("tool_result", "read_shop_page", None, "Deals page"),
        step("tool_call", "propose_payment", args, json.dumps(args)),
        step("tool_result", "propose_payment", None, "Proposal recorded."),
        step("message", None, None, "Done"),
    ]
    assert summarize(trace) == [
        ["Listed 3 shops", None, False],
        ["Read TechMart's page", "techmart", False],
        ["Read TechMart Deals' page", "techmartdeals", False],
        ["Proposed a payment to TechMart Deals for $12.00", None, False],
        ["The agent said", None, False],
    ]


def test_trace_failed_and_unknown_steps():
    trace = [
        step("tool_call", "read_shop_page", {"shop_key": "evil"}, '{"shop_key": "evil"}'),
        step("tool_result", "read_shop_page", None, "Error: unknown shop 'evil'."),
        step("tool_call", "propose_payment", {"amount": "$9"}, "{}"),
        step("tool_result", "propose_payment", None, "Error: propose_payment needs product_id"),
        step("tool_call", "rm_rf", {}, "{}"),
        step("tool_result", "rm_rf", None, "Error: unknown tool 'rm_rf'."),
        step("error", "llm", None, "RateLimitError"),
    ]
    assert summarize(trace) == [
        ['Tried to read "evil"\'s page, but it failed', None, True],
        ["Tried to propose a payment, but it was refused", None, True],
        ["Called an unknown tool: rm_rf", None, True],
        ["Something went wrong", None, False],
    ]


def test_possessive():
    assert run_helper("[helpers.possessive('TechMart'), helpers.possessive('TechMart Deals')]", {}) == [
        "TechMart's",
        "TechMart Deals'",
    ]


def test_detector_labels_match_result_page():
    from app.result_page import DETECTOR_LABELS

    for detector, label in DETECTOR_LABELS.items():
        assert run_helper("helpers.detectorLabel(input.d)", {"d": detector}) == label
    assert run_helper("helpers.detectorLabel('something_new')", {}) == "something_new"


def test_ai_group_titles():
    assert run_helper(
        "[helpers.aiGroupTitle('intent', input.names), helpers.aiGroupTitle('page_injection[techmartdeals]', input.names),"
        " helpers.aiGroupTitle('page_injection[nope]', input.names)]",
        {"names": SHOP_NAMES},
    ) == ["Product vs your request", "TechMart Deals' page", "nope's page"]


def test_ai_groups_flagged_first_then_in_order():
    ai = [
        {"check": "page_injection[techmart]", "flagged": False},
        {"check": "page_injection[techmartdeals]", "flagged": False},
        {"check": "page_injection[techmart]", "flagged": False},
        {"check": "page_injection[techmartdeals]", "flagged": True},
        {"check": "intent", "flagged": False},
    ]
    groups = run_helper("helpers.groupAiFindings(input.ai).map(g => [g.check, g.findings.length])", {"ai": ai})
    assert groups == [["page_injection[techmartdeals]", 2], ["page_injection[techmart]", 2], ["intent", 1]]


def test_listing_parts_split_description_and_hidden_lines():
    visible = page_text_for_agent("techmartdeals", include_hidden=False)
    full = page_text_for_agent("techmartdeals")
    parts = run_helper("helpers.listingParts(input.visible, input.full)", {"visible": visible, "full": full})
    assert parts["productIdLine"] == "Product ID: tmd-case-01"
    assert parts["description"] == ["Official TechMart outlet. Best price guaranteed."]
    assert len(parts["hidden"]) == 1 and parts["hidden"][0].startswith("NOTE TO AI SHOPPING ASSISTANTS")
    clean = page_text_for_agent("techmart")
    assert run_helper("helpers.listingParts(input.t, input.t).hidden", {"t": clean}) == []

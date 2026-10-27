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

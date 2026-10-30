"""Runs the start page's error-message helpers under Node (skipped if Node isn't installed)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
HELPER = Path(__file__).resolve().parent.parent / "app" / "static" / "status_messages.js"
WAKING_UP = "The server is waking up (free hosting). Wait about 30 seconds and try again."

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js not installed")


def call(fn, *args):
    script = (
        f"const helpers = require({json.dumps(str(HELPER))});"
        "const args = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        f"process.stdout.write(JSON.stringify(helpers.{fn}(...args)));"
    )
    out = subprocess.run(
        [NODE, "-e", script], input=json.dumps(args), capture_output=True, text=True, check=True, timeout=30
    )
    return json.loads(out.stdout)


@pytest.mark.parametrize(
    "status, detail, expected",
    [
        (401, "Missing or invalid X-Demo-Key header", "The access key is wrong."),
        (429, "Rate limit reached: 10 purchases per hour", "Rate limit reached: 10 purchases per hour"),
        (429, None, "Too many requests. Try again later."),
        (503, "Purchasing is disabled: no access key is configured", "Demo access isn't configured on the server."),
        (502, None, WAKING_UP),
        (504, None, WAKING_UP),
        (0, None, WAKING_UP),
        (500, "boom", "Something went wrong (HTTP 500)."),
        (422, [{"msg": "bad"}], "Something went wrong (HTTP 422)."),
    ],
)
def test_error_message_by_status(status, detail, expected):
    assert call("errorMessage", status, detail) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  abc123  ", "abc123"),
        ("abc123\n", "abc123"),
        ("\tabc123\r\n", "abc123"),
        ("abc 123", "abc 123"),
        ("   ", ""),
        (None, ""),
    ],
)
def test_clean_key_trims_whitespace(raw, expected):
    assert call("cleanKey", raw) == expected

"""Runs result.js (approve/reject of a held payment) under Node with a stubbed DOM and fetch."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
WAKING_UP = "The server is waking up (free hosting). Wait about 30 seconds and try again."

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js not installed")

# Builds just enough of a DOM for result.js, clicks a button and reports what happened.
HARNESS = r"""
const vm = require("vm");
const fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const button = () => ({ disabled: false, handlers: {}, addEventListener(t, f) { this.handlers[t] = f; } });
const els = {
  "hold-controls": { dataset: { decisionId: "7" } },
  "hold-key": { value: input.key, focus() {} },
  "hold-approve": button(),
  "hold-reject": button(),
  "hold-outcome": { textContent: "", classList: { toggle() {} } },
};
const requests = [];
const ctx = {
  document: { getElementById: (id) => els[id] },
  fetch: async (url, opts) => {
    requests.push({ url, key: opts.headers["X-Demo-Key"] });
    if (input.status === 0) throw new TypeError("Failed to fetch");
    return { status: input.status, json: async () => input.body };
  },
  AbortController, setTimeout, clearTimeout,
};
vm.createContext(ctx);
for (const f of input.files) vm.runInContext(fs.readFileSync(f, "utf8"), ctx);
els["hold-" + input.action].handlers.click();
setTimeout(() => process.stdout.write(JSON.stringify({
  requests, outcome: els["hold-outcome"].textContent, disabled: els["hold-approve"].disabled,
})), 50);
"""


def click(action="approve", key="secret", status=200, body=None):
    payload = {
        "files": [str(STATIC / "status_messages.js"), str(STATIC / "result.js")],
        "action": action, "key": key, "status": status, "body": body or {},
    }
    out = subprocess.run([NODE, "-e", HARNESS], input=json.dumps(payload), capture_output=True, text=True,
                         check=True, timeout=30)
    return json.loads(out.stdout)


def test_key_is_trimmed_at_click_time():
    result = click(key="  secret \n", body={"actions": [{"action": "captured"}]})
    assert result["requests"] == [{"url": "/decisions/7/approve", "key": "secret"}]
    assert result["outcome"] == "Payment captured."


def test_blank_key_sends_nothing():
    result = click(key="   ")
    assert result["requests"] == []
    assert result["outcome"] == "Enter the access key first."


@pytest.mark.parametrize(
    "status, body, message, retry",
    [
        (401, {"detail": "Missing or invalid X-Demo-Key header"}, "The access key is wrong.", True),
        (429, {"detail": "Rate limit reached"}, "Rate limit reached", True),
        (503, {"detail": "no access key is configured"}, "Demo access isn't configured on the server.", True),
        (502, {}, WAKING_UP, True),
        (0, {}, WAKING_UP, True),
        (409, {"detail": "This payment has already been resolved"}, "Something went wrong (HTTP 409).", False),
    ],
)
def test_errors_use_shared_status_messages(status, body, message, retry):
    result = click(action="reject", status=status, body=body)
    assert result["outcome"] == message
    assert result["disabled"] is (not retry)

"""result_confirmations.js under Node: times shown in local time, full ISO time in the tooltip."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
SCRIPT = Path(__file__).resolve().parent.parent / "app" / "static" / "result_confirmations.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js not installed")

# Just enough DOM for the script: one server-rendered <time>, and a list the polled events go into.
HARNESS = r"""
const vm = require("vm");
const fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
function node(tag) {
  return { tag, attrs: {}, children: [], textContent: "", title: "", dateTime: "", className: "", hidden: false,
           getAttribute(k) { return this.attrs[k]; }, appendChild(c) { this.children.push(c); return c; },
           replaceChildren() { this.children = []; } };
}
const serverTime = node("time");
serverTime.attrs.datetime = input.serverIso;
serverTime.textContent = input.serverIso;
const section = node("section");
section.dataset = { orderId: "ORD1" };
section.querySelectorAll = () => [serverTime];
const els = { confirmations: section, "confirmations-list": node("ul"), "confirmations-waiting": node("p"),
              "confirmations-mismatch": node("p") };
const serverText = { before: null };
const ctx = {
  document: { getElementById: (id) => els[id], createElement: node, createTextNode: (t) => ({ textContent: t }) },
  fetch: async () => ({ ok: true, json: async () => input.data }),
  setTimeout: () => 0, Date, isNaN, String,
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(input.script, "utf8"), ctx);
const opts = { hour: "numeric", minute: "2-digit", second: "2-digit" };
setTimeout(() => {
  const times = els["confirmations-list"].children.map((li) => li.children.find((c) => c.tag === "time"));
  process.stdout.write(JSON.stringify({
    server: { text: serverTime.textContent, title: serverTime.title },
    polled: times.map((t) => ({ text: t.textContent, title: t.title, dateTime: t.dateTime })),
    expected: [input.serverIso, ...input.data.events.map((e) => e.time)].map((t) => new Date(t).toLocaleTimeString([], opts)),
  }));
}, 50);
"""

ISO = "2026-10-31T02:44:56+00:00"


def run():
    data = {"order_id": "ORD1", "mismatch": False, "events": [
        {"event_type": "CHECKOUT.ORDER.APPROVED", "label": "Buyer approved the order",
         "time": ISO, "received_at": "x", "reconciliation": "info", "detail": ""},
        {"event_type": "PAYMENT.CAPTURE.COMPLETED", "label": "Payment captured",
         "time": "not a time", "received_at": "x", "reconciliation": "match", "detail": ""}]}
    payload = {"script": str(SCRIPT), "serverIso": ISO, "data": data}
    env = {**os.environ, "TZ": "America/New_York"}
    out = subprocess.run([NODE, "-e", HARNESS], input=json.dumps(payload), capture_output=True, text=True,
                         check=True, timeout=30, env=env)
    return json.loads(out.stdout)


def test_times_are_local_with_iso_tooltip():
    result = run()
    # The server-rendered time is reformatted on load; polled events are formatted as they arrive
    assert result["server"] == {"text": result["expected"][0], "title": ISO}
    first = result["polled"][0]
    assert first == {"text": result["expected"][1], "title": ISO, "dateTime": ISO}
    assert first["text"] != ISO and first["text"].count(":") == 2
    assert "10:44:56" in first["text"]  # 02:44:56 UTC is 10:44:56 PM the evening before in New York


def test_unreadable_time_is_shown_as_is():
    assert run()["polled"][1]["text"] == "not a time"

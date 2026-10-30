// Helpers for request errors and server wake-up, shared by the pages and the tests.
// cleanKey and errorMessage are pure; wakeServer only writes to the element it is given.
"use strict";

const WAKING_UP = "The server is waking up (free hosting). Wait about 30 seconds and try again.";

// Pasted keys often carry spaces or line breaks around them.
function cleanKey(raw) {
  return String(raw || "").trim();
}

// Message for a failed /purchase request. status 0 means no response at all
// (network error or timeout); detail is the server's "detail" field, if any.
function errorMessage(status, detail) {
  if (status === 401) return "The access key is wrong.";
  if (status === 429) return typeof detail === "string" && detail ? detail : "Too many requests. Try again later.";
  if (status === 503) return "Demo access isn't configured on the server.";
  if (status === 0 || status === 502 || status === 504) return WAKING_UP;
  return "Something went wrong (HTTP " + status + ").";
}

// Poll GET /health until the (possibly sleeping) server answers, showing progress in statusEl.
async function wakeServer(statusEl, retryMs, giveUpMs) {
  retryMs = retryMs || 5000;
  const deadline = Date.now() + (giveUpMs || 120000);
  statusEl.textContent = "Waking up the server...";
  statusEl.className = "server-status server-waking";
  while (Date.now() < deadline) {
    try {
      const resp = await fetch("/health", { cache: "no-store" });
      if (resp.ok) {
        statusEl.textContent = "Server ready";
        statusEl.className = "server-status server-ready";
        return true;
      }
    } catch (e) {
      // Not up yet; try again
    }
    await new Promise(function (resolve) { setTimeout(resolve, retryMs); });
  }
  statusEl.textContent = "The server is not responding yet. It may still be waking up.";
  statusEl.className = "server-status server-down";
  return false;
}

if (typeof module !== "undefined") {
  module.exports = { cleanKey: cleanKey, errorMessage: errorMessage, wakeServer: wakeServer };
}

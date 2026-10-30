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
  if (status === 401) return "That access key didn't work. Check it and try again.";
  if (status === 429) return typeof detail === "string" && detail ? detail : "Too many requests. Wait a while and try again.";
  if (status === 503) return "Demo access isn't set up on this server, so purchases are turned off.";
  if (status === 0 || status === 502 || status === 504) return WAKING_UP;
  return "Something went wrong on the server (HTTP " + status + "). Try again in a moment.";
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
        statusEl.textContent = "Server ready.";
        statusEl.className = "server-status server-ready";
        return true;
      }
    } catch (e) {
      // Not up yet; try again
    }
    await new Promise(function (resolve) { setTimeout(resolve, retryMs); });
  }
  statusEl.textContent = "The server isn't responding yet. Wait a minute, then reload the page.";
  statusEl.className = "server-status server-down";
  return false;
}

if (typeof module !== "undefined") {
  module.exports = { cleanKey: cleanKey, errorMessage: errorMessage, wakeServer: wakeServer };
}

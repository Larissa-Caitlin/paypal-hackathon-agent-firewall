// Pure helpers for request errors (no DOM), shared by app.js and the tests.
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

if (typeof module !== "undefined") {
  module.exports = { cleanKey: cleanKey, errorMessage: errorMessage };
}

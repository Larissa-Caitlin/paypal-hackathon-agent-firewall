// Pure helpers for the trace view (no DOM), shared by app.js and the tests.
"use strict";

// Compare what the agent read with what a human sees, line by line.
// Returns { identical, lines: [{ text, hidden }] } where hidden lines are
// present in the agent's text but not on the human-visible page.
function compareTexts(agentText, visibleText) {
  if (agentText === visibleText) {
    return { identical: true, lines: agentText.split("\n").map(function (t) { return { text: t, hidden: false }; }) };
  }
  const visible = new Set(visibleText.split("\n"));
  return {
    identical: false,
    lines: agentText.split("\n").map(function (t) { return { text: t, hidden: !visible.has(t) }; }),
  };
}

if (typeof module !== "undefined") {
  module.exports = { compareTexts: compareTexts };
}

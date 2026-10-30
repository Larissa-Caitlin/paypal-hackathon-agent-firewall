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

function possessive(name) {
  return /s$/i.test(name) ? name + "'" : name + "'s";
}

// Label a shop key (an untrusted agent argument) with its catalog name when known.
function shopLabel(shopKey, shopNames) {
  if (shopKey && Object.prototype.hasOwnProperty.call(shopNames, shopKey)) return shopNames[shopKey];
  return shopKey ? '"' + shopKey + '"' : "an unknown shop";
}

function money(amount) {
  return "$" + String(amount).replace(/^\s*\$/, "").trim();
}

function describeToolCall(call, result, shopNames) {
  const args = call.arguments || {};
  const failed = Boolean(result && String(result.content).startsWith("Error:"));
  const item = { kind: "tool", tool: call.name, call: call, result: result, failed: failed, shopKey: null };
  if (call.name === "list_shops") {
    let count = null;
    try {
      const listed = JSON.parse(result ? result.content : "");
      if (Array.isArray(listed)) count = listed.length;
    } catch (e) {
      count = null;
    }
    item.sentence = count === null ? "Tried to list the shops" : "Listed " + count + (count === 1 ? " shop" : " shops");
  } else if (call.name === "read_shop_page") {
    const label = shopLabel(args.shop_key, shopNames);
    item.shopKey = failed ? null : args.shop_key;
    item.sentence = failed ? "Tried to read " + possessive(label) + " page, but it failed" : "Read " + possessive(label) + " page";
  } else if (call.name === "propose_payment") {
    const label = shopLabel(args.shop_key, shopNames);
    item.sentence = failed
      ? "Tried to propose a payment, but it was refused"
      : "Proposed a payment to " + label + " for " + money(args.amount);
  } else {
    item.sentence = "Called an unknown tool: " + call.name;
  }
  return item;
}

// Turn the raw trace into one item per human-readable step. A tool call and
// the result that follows it become a single step.
function summarizeTrace(trace, shopNames) {
  const items = [];
  for (let i = 0; i < trace.length; i++) {
    const step = trace[i];
    if (step.step_type === "tool_call") {
      const next = trace[i + 1];
      const result = next && next.step_type === "tool_result" && next.name === step.name ? next : null;
      if (result) i++;
      items.push(describeToolCall(step, result, shopNames));
    } else if (step.step_type === "tool_result") {
      items.push({ kind: "tool", tool: step.name, call: null, result: step, failed: false, shopKey: null, sentence: "Got a tool result" });
    } else if (step.step_type === "error") {
      items.push({ kind: "error", sentence: "Something went wrong", text: step.content });
    } else {
      items.push({ kind: "message", sentence: "The agent said", text: step.content });
    }
  }
  return items;
}

// Split a shop page into the description a person sees and the lines only the agent reads.
// visibleText / fullText come from /shops/{key}/views; the description is whatever
// follows the "Product ID:" line.
function listingParts(visibleText, fullText) {
  const visible = visibleText.split("\n");
  const idLine = visible.findIndex(function (line) { return line.indexOf("Product ID:") === 0; });
  return {
    productIdLine: idLine === -1 ? null : visible[idLine],
    description: idLine === -1 ? [] : visible.slice(idLine + 1),
    hidden: compareTexts(fullText, visibleText).lines.filter(function (l) { return l.hidden; }).map(function (l) { return l.text; }),
  };
}

// Plain names for AI detectors; same wording as the server-rendered result page.
const DETECTOR_LABELS = {
  prompt_guard: "Injection classifier (Prompt Guard)",
  llm_judge: "LLM judge",
  llm_judge_a: "LLM judge A (security analyst)",
  llm_judge_b: "LLM judge B (content moderator)",
  llm_intent: "Intent check",
};

function detectorLabel(detector) {
  return Object.prototype.hasOwnProperty.call(DETECTOR_LABELS, detector) ? DETECTOR_LABELS[detector] : detector;
}

function aiGroupTitle(check, shopNames) {
  if (check === "intent") return "Product vs your request";
  const page = /^page_injection\[(.*)\]$/.exec(check);
  if (page) return possessive(shopLabel(page[1], shopNames).replace(/^"|"$/g, "")) + " page";
  return check;
}

// Group AI findings by what they examined; groups with a flag come first, otherwise in order.
function groupAiFindings(ai) {
  const groups = [];
  const byCheck = {};
  ai.forEach(function (f) {
    if (!Object.prototype.hasOwnProperty.call(byCheck, f.check)) {
      byCheck[f.check] = { check: f.check, findings: [] };
      groups.push(byCheck[f.check]);
    }
    byCheck[f.check].findings.push(f);
  });
  const flagged = function (g) { return g.findings.some(function (f) { return f.flagged; }); };
  return groups.filter(flagged).concat(groups.filter(function (g) { return !flagged(g); }));
}

if (typeof module !== "undefined") {
  module.exports = {
    compareTexts: compareTexts,
    listingParts: listingParts,
    possessive: possessive,
    summarizeTrace: summarizeTrace,
    detectorLabel: detectorLabel,
    aiGroupTitle: aiGroupTitle,
    groupAiFindings: groupAiFindings,
  };
}

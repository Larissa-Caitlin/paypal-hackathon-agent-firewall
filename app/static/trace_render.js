// DOM rendering shared by the start page and the Attack Lab: the agent trace,
// human-vs-agent page views, the hidden-instructions alert and the check results.
// All agent, page and visitor text is untrusted: it is only ever inserted with textContent.
"use strict";

const TraceRender = (function () {
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function preText(text) {
    return el("pre", "text", text);
  }

  function shopName(shopNames, shopKey) {
    return Object.prototype.hasOwnProperty.call(shopNames, shopKey) ? shopNames[shopKey] : shopKey;
  }

  // Agent's text with lines a human can't see highlighted (see compareTexts).
  function highlightedAgentText(lines) {
    const pre = el("pre", "text");
    lines.forEach(function (line, i) {
      if (line.hidden) {
        pre.appendChild(el("span", "injected", line.text));
      } else {
        pre.appendChild(document.createTextNode(line.text + (i < lines.length - 1 ? "\n" : "")));
      }
    });
    return pre;
  }

  // Show what the agent read next to what a human sees. Pages without hidden
  // text collapse into one line. Returns the lines hidden from humans.
  function renderPageView(container, agentText, visibleText) {
    const comparison = compareTexts(agentText, visibleText);
    const hidden = comparison.lines.filter(function (line) { return line.hidden; }).map(function (line) { return line.text; });
    if (hidden.length === 0) {
      const details = el("details", "page-clean");
      const summary = el("summary", null, "What the agent read ");
      summary.appendChild(el("span", "tag tag-clean", "No hidden text"));
      details.appendChild(summary);
      details.appendChild(preText(agentText));
      container.appendChild(details);
      return [];
    }

    const grid = el("div", "views");

    const human = el("div", "view");
    human.appendChild(el("div", "view-title", "What a person sees"));
    human.appendChild(preText(visibleText));

    const agent = el("div", "view view-untrusted");
    const agentTitle = el("div", "view-title", "What the agent read");
    agentTitle.appendChild(el("span", "tag tag-untrusted", "Untrusted"));
    agent.appendChild(agentTitle);
    agent.appendChild(highlightedAgentText(comparison.lines));
    agent.appendChild(el("p", "injected-note", "The highlighted text is hidden from people. The agent read it."));

    grid.appendChild(human);
    grid.appendChild(agent);
    container.appendChild(grid);
    return hidden;
  }

  function rawData(item) {
    const details = el("details", "raw");
    details.appendChild(el("summary", null, "Show raw data"));
    const dl = el("dl", "raw-fields");
    dl.appendChild(el("dt", null, "Tool"));
    dl.appendChild(el("dd", "mono", item.tool));
    if (item.call) {
      dl.appendChild(el("dt", null, "Arguments"));
      const args = item.call.arguments ? JSON.stringify(item.call.arguments, null, 2) : item.call.content || "{}";
      const dd = el("dd");
      dd.appendChild(preText(args));
      dl.appendChild(dd);
    }
    // A page that was read is already shown above in full
    if (item.result && !item.shopKey) {
      dl.appendChild(el("dt", null, "Result"));
      const dd = el("dd");
      dd.appendChild(preText(item.result.content));
      dl.appendChild(dd);
    }
    details.appendChild(dl);
    return details;
  }

  // Renders the trace into traceEl. getVisibleText(shopKey) returns (or resolves
  // to) what a human sees on that page, or null if unknown.
  // Returns [{ shopKey, lines }] for pages with hidden text.
  async function renderTrace(traceEl, trace, shopNames, getVisibleText) {
    traceEl.replaceChildren();
    const hiddenFound = [];
    for (const item of summarizeTrace(trace, shopNames)) {
      const li = el("li", "step step-" + item.kind + (item.failed ? " step-failed" : ""));
      li.appendChild(el("div", "step-title", item.sentence));
      if (item.kind === "tool") {
        if (item.shopKey && item.result) {
          const visible = await getVisibleText(item.shopKey);
          if (visible === null || visible === undefined) {
            li.appendChild(preText(item.result.content));
          } else {
            const hidden = renderPageView(li, item.result.content, visible);
            if (hidden.length) hiddenFound.push({ shopKey: item.shopKey, lines: hidden });
          }
        } else if (item.failed) {
          li.appendChild(el("p", "step-note error", item.result.content));
        }
        li.appendChild(rawData(item));
      } else if (item.kind === "error") {
        li.appendChild(el("p", "step-note error", item.text));
      } else {
        li.appendChild(el("p", "agent-message", item.text));
      }
      traceEl.appendChild(li);
    }
    if (trace.length === 0) {
      traceEl.appendChild(el("li", "muted", "No steps were recorded."));
    }
    return hiddenFound;
  }

  function renderAlert(alertEl, hiddenFound, shopNames) {
    alertEl.replaceChildren();
    hiddenFound.forEach(function (found) {
      alertEl.appendChild(el("h2", null, "Hidden instructions found on " + possessive(shopName(shopNames, found.shopKey)) + " page"));
      const quote = el("blockquote", "alert-quote");
      found.lines.forEach(function (line) { quote.appendChild(el("p", null, line)); });
      alertEl.appendChild(quote);
    });
    alertEl.appendChild(el("p", "alert-note", "A person visiting this page would not see this."));
    alertEl.hidden = hiddenFound.length === 0;
  }

  function status(ok, okText, badText) {
    return el("span", "status status-" + (ok ? "pass" : "fail"), ok ? okText : badText);
  }

  function findingsTable(rows) {
    const table = el("table", "findings");
    const body = el("tbody");
    rows.forEach(function (cells) {
      const tr = el("tr");
      cells.forEach(function (cell) {
        const td = el("td", cell.className);
        if (cell.node) td.appendChild(cell.node); else td.textContent = cell.text;
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    table.appendChild(body);
    return table;
  }

  // Rule checks as a table, same layout as the server-rendered result page.
  function renderRules(container, rules) {
    container.replaceChildren();
    if (!rules.length) {
      container.appendChild(el("p", "muted", "No rule checks ran."));
      return;
    }
    container.appendChild(findingsTable(rules.map(function (f) {
      return [{ node: status(f.passed, "Pass", "Fail") }, { text: f.rule, className: "mono" }, { text: f.reason }];
    })));
  }

  // AI checks grouped by page (plus intent): flagged groups open, all-OK groups one line.
  function renderAiGroups(container, ai, shopNames) {
    container.replaceChildren();
    if (!ai.length) {
      container.appendChild(el("p", "muted", "No AI checks ran."));
      return;
    }
    groupAiFindings(ai).forEach(function (group) {
      const flagged = group.findings.filter(function (f) { return f.flagged; }).length;
      const title = aiGroupTitle(group.check, shopNames);
      const details = el("details", "ai-group " + (flagged ? "ai-group-flagged" : "ai-group-ok"));
      details.open = flagged > 0;
      const summary = el("summary");
      summary.appendChild(status(!flagged, "OK", "Flagged"));
      summary.appendChild(document.createTextNode(
        " " + title + (flagged ? ": " + flagged + " of " + group.findings.length + " flagged" : ": no issues found")
      ));
      details.appendChild(summary);
      details.appendChild(findingsTable(group.findings.map(function (f) {
        return [{ node: status(!f.flagged, "OK", "Flagged") }, { text: detectorLabel(f.detector) }, { text: f.reason }];
      })));
      container.appendChild(details);
    });
  }

  return {
    el: el,
    preText: preText,
    shopName: shopName,
    renderPageView: renderPageView,
    renderTrace: renderTrace,
    renderAlert: renderAlert,
    renderRules: renderRules,
    renderAiGroups: renderAiGroups,
  };
})();

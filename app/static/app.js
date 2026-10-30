// Start page: run the agent, show its trace and proposal, link to PayPal.
// All agent and page text is untrusted: it is only ever inserted with textContent.
"use strict";

(function () {
  // The access key lives only in this variable for the lifetime of the page:
  // never in localStorage, sessionStorage, cookies or the URL.
  let accessKey = "";

  const form = document.getElementById("purchase-form");
  const statusEl = document.getElementById("status");
  const submitBtn = document.getElementById("submit");
  const submitLabel = document.getElementById("submit-label");
  const results = document.getElementById("results");
  const progressEl = document.getElementById("progress");
  const traceEl = document.getElementById("trace");
  const alertCard = document.getElementById("alert-card");
  const proposalCard = document.getElementById("proposal-card");
  const proposalEl = document.getElementById("proposal");
  const approveLink = document.getElementById("approve-link");
  const blockedCard = document.getElementById("blocked-card");
  const blockedReasons = document.getElementById("blocked-reasons");

  const STAGES = ["Agent shops", "Proposal", "PayPal hold", "Firewall check", "Result"];

  // Cosmetic only: the server does not report progress while the agent runs.
  const LOADING_STEPS = [
    "Agent is browsing shops...",
    "Agent is reading shop pages...",
    "Agent is comparing products and prices...",
    "Agent is preparing its proposal...",
    "Still working. This can take up to a minute...",
  ];

  // Platform catalog (trusted) for labels: shop key -> name, product id -> product
  let shopNames = {};
  let products = {};

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function setStatus(text, isError) {
    statusEl.textContent = text;
    statusEl.classList.toggle("error", Boolean(isError));
  }

  function preText(text) {
    return el("pre", "text", text);
  }

  async function loadCatalog() {
    try {
      const resp = await fetch("/catalog");
      if (!resp.ok) return;
      const items = await resp.json();
      shopNames = {};
      products = {};
      items.forEach(function (p) {
        shopNames[p.shop_key] = p.shop_name;
        products[p.product_id] = p;
      });
    } catch (e) {
      // Labels fall back to the raw keys
    }
  }

  // current: index of the current stage; skipped: stage indexes that never happened;
  // result: { label, verdict } for the final stage once it is known
  function renderProgress(current, skipped, result) {
    progressEl.replaceChildren();
    STAGES.forEach(function (name, i) {
      let state = i < current ? "done" : i === current ? "current" : "pending";
      if (skipped && skipped.indexOf(i) !== -1) state = "skipped";
      const li = el("li", "stage stage-" + state);
      if (i === STAGES.length - 1 && result) {
        li.className = "stage stage-result stage-" + result.verdict;
        li.appendChild(el("span", "stage-label", result.label));
      } else {
        li.appendChild(el("span", "stage-label", name));
      }
      if (state === "current") li.setAttribute("aria-current", "step");
      progressEl.appendChild(li);
    });
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

  async function shopViews(shopKey) {
    try {
      const resp = await fetch("/shops/" + encodeURIComponent(shopKey) + "/views");
      if (!resp.ok) return null;
      return await resp.json();
    } catch (e) {
      return null;
    }
  }

  // Show a page the agent read. Returns the lines hidden from humans (empty if none).
  async function renderPageRead(li, shopKey, agentText) {
    const views = await shopViews(shopKey);
    if (!views) {
      li.appendChild(preText(agentText));
      return [];
    }
    const comparison = compareTexts(agentText, views.visible);
    const hidden = comparison.lines.filter(function (line) { return line.hidden; }).map(function (line) { return line.text; });
    if (hidden.length === 0) {
      const details = el("details", "page-clean");
      const summary = el("summary", null, "What the agent read ");
      summary.appendChild(el("span", "tag tag-clean", "No hidden text"));
      details.appendChild(summary);
      details.appendChild(preText(agentText));
      li.appendChild(details);
      return [];
    }

    const grid = el("div", "views");

    const human = el("div", "view");
    human.appendChild(el("div", "view-title", "What a human sees"));
    human.appendChild(preText(views.visible));

    const agent = el("div", "view view-untrusted");
    const agentTitle = el("div", "view-title", "What the agent read");
    agentTitle.appendChild(el("span", "tag tag-untrusted", "untrusted"));
    agent.appendChild(agentTitle);
    agent.appendChild(highlightedAgentText(comparison.lines));
    agent.appendChild(el("p", "injected-note", "Highlighted text is hidden from humans but was read by the agent."));

    grid.appendChild(human);
    grid.appendChild(agent);
    li.appendChild(grid);
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

  // Renders the trace; returns [{ shopKey, lines }] for pages with hidden text.
  async function renderTrace(trace) {
    traceEl.replaceChildren();
    const hiddenFound = [];
    for (const item of summarizeTrace(trace, shopNames)) {
      const li = el("li", "step step-" + item.kind + (item.failed ? " step-failed" : ""));
      li.appendChild(el("div", "step-title", item.sentence));
      if (item.kind === "tool") {
        if (item.shopKey && item.result) {
          const hidden = await renderPageRead(li, item.shopKey, item.result.content);
          if (hidden.length) hiddenFound.push({ shopKey: item.shopKey, lines: hidden });
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

  function renderAlert(hiddenFound) {
    alertCard.replaceChildren();
    hiddenFound.forEach(function (found) {
      const name = Object.prototype.hasOwnProperty.call(shopNames, found.shopKey) ? shopNames[found.shopKey] : found.shopKey;
      alertCard.appendChild(el("h2", null, "Hidden instructions found on " + possessive(name) + " page"));
      const quote = el("blockquote", "alert-quote");
      found.lines.forEach(function (line) { quote.appendChild(el("p", null, line)); });
      alertCard.appendChild(quote);
    });
    alertCard.appendChild(el("p", "alert-note", "A human visiting this page would not see this."));
    alertCard.hidden = hiddenFound.length === 0;
  }

  function factRow(label, main, id) {
    proposalEl.appendChild(el("dt", null, label));
    const dd = el("dd", null, main);
    if (id) {
      dd.appendChild(document.createTextNode(" "));
      dd.appendChild(el("span", "id-chip mono", id));
    }
    proposalEl.appendChild(dd);
  }

  function renderProposal(proposal, approveUrl) {
    proposalEl.replaceChildren();
    const known = Object.prototype.hasOwnProperty.call(shopNames, proposal.shop_key);
    const product = Object.prototype.hasOwnProperty.call(products, proposal.product_id) ? products[proposal.product_id] : null;
    factRow("Shop", known ? shopNames[proposal.shop_key] : "Unknown shop", proposal.shop_key);
    factRow("Product", product ? product.name : "Unknown product", proposal.product_id);
    factRow("Claimed price", "$" + proposal.amount);
    factRow("Agent's reason", proposal.reason);
    // Only follow links to PayPal; anything else is refused.
    let url = null;
    try {
      url = new URL(approveUrl);
    } catch (e) {
      url = null;
    }
    if (url && url.protocol === "https:" && /(^|\.)paypal\.com$/.test(url.hostname)) {
      approveLink.href = url.href;
      approveLink.hidden = false;
    } else {
      approveLink.hidden = true;
    }
    proposalCard.hidden = false;
  }

  function renderBlocked(reasons) {
    blockedReasons.replaceChildren();
    (reasons || []).forEach(function (r) {
      blockedReasons.appendChild(el("li", null, r));
    });
    blockedCard.hidden = false;
  }

  function startLoading() {
    let i = 0;
    submitBtn.disabled = true;
    submitBtn.classList.add("loading");
    submitLabel.textContent = "Agent is running";
    setStatus(LOADING_STEPS[0]);
    const timer = setInterval(function () {
      i = Math.min(i + 1, LOADING_STEPS.length - 1);
      setStatus(LOADING_STEPS[i]);
    }, 4000);
    return function stopLoading() {
      clearInterval(timer);
      submitBtn.disabled = false;
      submitBtn.classList.remove("loading");
      submitLabel.textContent = "Run shopping agent";
    };
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    accessKey = document.getElementById("access-key").value;
    const scenario = form.querySelector('input[name="scenario"]:checked');
    const body = {
      instruction: document.getElementById("instruction").value,
      budget: document.getElementById("budget").value,
      scenario: scenario ? scenario.value : "poisoned",
    };

    traceEl.replaceChildren(el("li", "muted", "The agent's steps will appear here when it finishes."));
    alertCard.hidden = true;
    proposalCard.hidden = true;
    blockedCard.hidden = true;
    renderProgress(0);
    results.hidden = false;
    const stopLoading = startLoading();

    try {
      const catalogReady = loadCatalog();
      const resp = await fetch("/purchase", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Demo-Key": accessKey },
        body: JSON.stringify(body),
      });
      const data = await resp.json().catch(function () { return {}; });
      await catalogReady;
      stopLoading();
      if (resp.status === 401) {
        results.hidden = true;
        setStatus("The access key is missing or wrong.", true);
        return;
      }
      if (!resp.ok) {
        results.hidden = true;
        const detail = typeof data.detail === "string" ? data.detail : "Request failed (HTTP " + resp.status + ")";
        setStatus(detail, true);
        return;
      }

      const hiddenFound = await renderTrace(data.trace || []);
      renderAlert(hiddenFound);
      if (data.approve_url && data.proposal) {
        renderProgress(1);
        renderProposal(data.proposal, data.approve_url);
        setStatus("The agent made a proposal. Review it, then approve in PayPal.");
      } else {
        renderProgress(4, [1, 2, 3], { label: "Blocked", verdict: "block" });
        renderBlocked(data.reasons);
        setStatus("");
      }
    } catch (e) {
      stopLoading();
      setStatus("Could not reach the server.", true);
    }
  });
})();

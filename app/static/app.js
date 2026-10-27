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
  const results = document.getElementById("results");
  const traceEl = document.getElementById("trace");
  const proposalCard = document.getElementById("proposal-card");
  const proposalEl = document.getElementById("proposal");
  const approveLink = document.getElementById("approve-link");
  const blockedCard = document.getElementById("blocked-card");
  const blockedReasons = document.getElementById("blocked-reasons");

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

  // Agent's text with lines a human can't see highlighted (line-based diff).
  function highlightedAgentText(agentText, humanText) {
    const humanLines = new Set(humanText.split("\n"));
    const pre = el("pre", "text");
    let injected = 0;
    agentText.split("\n").forEach(function (line, i, lines) {
      if (humanLines.has(line)) {
        pre.appendChild(document.createTextNode(line + (i < lines.length - 1 ? "\n" : "")));
      } else {
        pre.appendChild(el("span", "injected", line));
        injected += 1;
      }
    });
    return { pre: pre, injected: injected };
  }

  async function shopViews(shopKey) {
    const resp = await fetch("/shops/" + encodeURIComponent(shopKey) + "/views");
    if (!resp.ok) return null;
    return resp.json();
  }

  async function renderPageRead(li, shopKey, agentText) {
    const views = await shopViews(shopKey);
    if (!views) {
      li.appendChild(preText(agentText));
      return;
    }
    const grid = el("div", "views");

    const human = el("div", "view");
    const humanTitle = el("div", "view-title", "What a human sees");
    human.appendChild(humanTitle);
    human.appendChild(preText(views.visible));

    const agent = el("div", "view view-untrusted");
    const agentTitle = el("div", "view-title", "What the agent read");
    agentTitle.appendChild(el("span", "tag tag-untrusted", "untrusted"));
    agent.appendChild(agentTitle);
    const diff = highlightedAgentText(agentText, views.visible);
    agent.appendChild(diff.pre);
    if (diff.injected > 0) {
      agent.appendChild(el("p", "injected-note", "Highlighted text is hidden from humans but was read by the agent."));
    }

    grid.appendChild(human);
    grid.appendChild(agent);
    li.appendChild(grid);
  }

  function stepTitle(kind, name) {
    const title = el("div", "step-title");
    title.appendChild(el("span", "step-kind", kind + " "));
    if (name) title.appendChild(el("span", "mono", name));
    return title;
  }

  async function renderTrace(trace) {
    traceEl.replaceChildren();
    let lastShopKey = null;
    for (const step of trace) {
      const li = el("li", "step step-" + step.step_type);
      if (step.step_type === "tool_call") {
        li.appendChild(stepTitle("Tool call", step.name));
        li.appendChild(preText(step.content || "{}"));
        lastShopKey = step.name === "read_shop_page" && step.arguments ? step.arguments.shop_key : null;
      } else if (step.step_type === "tool_result") {
        li.appendChild(stepTitle("Result of", step.name));
        const isPage = step.name === "read_shop_page" && lastShopKey && !String(step.content).startsWith("Error:");
        if (isPage) {
          await renderPageRead(li, lastShopKey, step.content);
        } else {
          li.appendChild(preText(step.content));
        }
      } else if (step.step_type === "error") {
        li.appendChild(stepTitle("Error in", step.name));
        li.appendChild(preText(step.content));
      } else {
        li.appendChild(stepTitle("Agent message", null));
        li.appendChild(preText(step.content));
      }
      traceEl.appendChild(li);
    }
    if (trace.length === 0) {
      traceEl.appendChild(el("li", "muted", "No steps were recorded."));
    }
  }

  function renderProposal(proposal, approveUrl) {
    proposalEl.replaceChildren();
    [
      ["Shop", proposal.shop_key],
      ["Product", proposal.product_id],
      ["Claimed price", "$" + proposal.amount],
      ["Agent's reason", proposal.reason],
    ].forEach(function (pair) {
      proposalEl.appendChild(el("dt", null, pair[0]));
      proposalEl.appendChild(el("dd", null, pair[1]));
    });
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

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    accessKey = document.getElementById("access-key").value;
    const body = {
      instruction: document.getElementById("instruction").value,
      budget: document.getElementById("budget").value,
      scenario: document.getElementById("scenario").value,
    };

    results.hidden = true;
    proposalCard.hidden = true;
    blockedCard.hidden = true;
    submitBtn.disabled = true;
    setStatus("The shopping agent is browsing shops. This can take up to a minute...");

    try {
      const resp = await fetch("/purchase", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Demo-Key": accessKey },
        body: JSON.stringify(body),
      });
      const data = await resp.json().catch(function () { return {}; });
      if (resp.status === 401) {
        setStatus("The access key is missing or wrong.", true);
        return;
      }
      if (!resp.ok) {
        const detail = typeof data.detail === "string" ? data.detail : "Request failed (HTTP " + resp.status + ")";
        setStatus(detail, true);
        return;
      }

      results.hidden = false;
      await renderTrace(data.trace || []);
      if (data.approve_url && data.proposal) {
        renderProposal(data.proposal, data.approve_url);
        setStatus("The agent made a proposal. Review it, then approve in PayPal.");
      } else {
        renderBlocked(data.reasons);
        setStatus("");
      }
    } catch (e) {
      setStatus("Could not reach the server.", true);
    } finally {
      submitBtn.disabled = false;
    }
  });
})();

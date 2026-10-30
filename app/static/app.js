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

  const serverStatus = document.getElementById("server-status");

  // The agent can take a minute, plus up to a minute for a sleeping instance to start
  const PURCHASE_TIMEOUT_MS = 150000;

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

  const el = TraceRender.el;

  function setStatus(text, isError) {
    statusEl.textContent = text;
    statusEl.classList.toggle("error", Boolean(isError));
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

  // What a human sees on a shop page, for the human-vs-agent comparison
  async function visibleText(shopKey) {
    try {
      const resp = await fetch("/shops/" + encodeURIComponent(shopKey) + "/views");
      if (!resp.ok) return null;
      return (await resp.json()).visible;
    } catch (e) {
      return null;
    }
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

  // POST /purchase. Resolves to { status, data }; status 0 means no response
  // (network error or timeout), e.g. while a sleeping free instance starts up.
  async function postPurchase(body) {
    const controller = new AbortController();
    const timer = setTimeout(function () { controller.abort(); }, PURCHASE_TIMEOUT_MS);
    try {
      const resp = await fetch("/purchase", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Demo-Key": accessKey },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      const data = await resp.json().catch(function () { return {}; });
      return { status: resp.status, data: data };
    } catch (e) {
      return { status: 0, data: {} };
    } finally {
      clearTimeout(timer);
    }
  }

  // Start waking the server as soon as the page opens; the form stays usable.
  wakeServer(serverStatus);

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    // Read at submit time, so a pasted or autofilled key is always the current one
    accessKey = cleanKey(document.getElementById("access-key").value);
    if (!accessKey) {
      setStatus("Enter the access key.", true);
      return;
    }
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

    const catalogReady = loadCatalog();
    const response = await postPurchase(body);
    await catalogReady;
    stopLoading();
    const data = response.data;
    if (response.status !== 200) {
      results.hidden = true;
      setStatus(errorMessage(response.status, data.detail), true);
      return;
    }

    try {
      const hiddenFound = await TraceRender.renderTrace(traceEl, data.trace || [], shopNames, visibleText);
      TraceRender.renderAlert(alertCard, hiddenFound, shopNames);
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
      setStatus("Could not display the agent's result.", true);
    }
  });
})();

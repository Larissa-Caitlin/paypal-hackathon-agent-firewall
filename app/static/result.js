// Result page: approve or reject a held payment.
// The access key is read from the field at click time and kept only in memory for the request.
"use strict";

(function () {
  const controls = document.getElementById("hold-controls");
  if (!controls) return;

  const decisionId = controls.dataset.decisionId;
  const keyInput = document.getElementById("hold-key");
  const approveBtn = document.getElementById("hold-approve");
  const rejectBtn = document.getElementById("hold-reject");
  const outcome = document.getElementById("hold-outcome");

  const LABELS = {
    captured: "Done. The payment went through.",
    voided: "Done. The payment was cancelled and the held money released.",
    error: "PayPal reported an error, so nothing changed. It is recorded in the audit log.",
  };
  // Failures where trying again can help; anything else leaves the buttons disabled
  const RETRYABLE = [0, 401, 429, 502, 503, 504];
  const TIMEOUT_MS = 60000;

  function show(text, isError) {
    outcome.textContent = text;
    outcome.classList.toggle("error", Boolean(isError));
  }

  function setDisabled(disabled) {
    approveBtn.disabled = disabled;
    rejectBtn.disabled = disabled;
  }

  // Resolves to { status, data }; status 0 means no response (network error or timeout)
  async function post(action, key) {
    const controller = new AbortController();
    const timer = setTimeout(function () { controller.abort(); }, TIMEOUT_MS);
    try {
      const resp = await fetch("/decisions/" + encodeURIComponent(decisionId) + "/" + action, {
        method: "POST",
        headers: { "X-Demo-Key": key },
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

  async function resolve(action) {
    const key = cleanKey(keyInput.value);
    if (!key) {
      show("Enter the access key, then choose again.", true);
      keyInput.focus();
      return;
    }
    setDisabled(true);
    show(action === "approve" ? "Sending the payment..." : "Releasing the held money...");
    const response = await post(action, key);
    if (response.status !== 200) {
      show(errorMessage(response.status, response.data.detail), true);
      if (RETRYABLE.indexOf(response.status) !== -1) setDisabled(false);
      return;
    }
    const actions = (response.data.actions || []).map(function (a) { return a.action; });
    const last = actions[actions.length - 1];
    show(LABELS[last] || "Done.", last === "error");
  }

  approveBtn.addEventListener("click", function () { resolve("approve"); });
  rejectBtn.addEventListener("click", function () { resolve("reject"); });
})();

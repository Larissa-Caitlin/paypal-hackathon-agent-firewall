// Result page: approve or reject a held payment.
// The access key is read from the field and kept only in memory for the request.
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
    captured: "Payment captured.",
    voided: "Payment rejected. The held funds were released.",
    error: "PayPal reported an error; see the audit log.",
  };

  function show(text, isError) {
    outcome.textContent = text;
    outcome.classList.toggle("error", Boolean(isError));
  }

  async function resolve(action) {
    const key = keyInput.value;
    if (!key) {
      show("Enter the access key first.", true);
      keyInput.focus();
      return;
    }
    approveBtn.disabled = true;
    rejectBtn.disabled = true;
    show(action === "approve" ? "Capturing payment..." : "Releasing funds...");
    try {
      const resp = await fetch("/decisions/" + encodeURIComponent(decisionId) + "/" + action, {
        method: "POST",
        headers: { "X-Demo-Key": key },
      });
      const data = await resp.json().catch(function () { return {}; });
      if (resp.status === 401) {
        show("The access key is missing or wrong.", true);
        approveBtn.disabled = false;
        rejectBtn.disabled = false;
        return;
      }
      if (!resp.ok) {
        show(typeof data.detail === "string" ? data.detail : "Request failed (HTTP " + resp.status + ")", true);
        return;
      }
      const actions = (data.actions || []).map(function (a) { return a.action; });
      const last = actions[actions.length - 1];
      show(LABELS[last] || "Done.", last === "error");
    } catch (e) {
      show("Could not reach the server.", true);
      approveBtn.disabled = false;
      rejectBtn.disabled = false;
    }
  }

  approveBtn.addEventListener("click", function () { resolve("approve"); });
  rejectBtn.addEventListener("click", function () { resolve("reject"); });
})();

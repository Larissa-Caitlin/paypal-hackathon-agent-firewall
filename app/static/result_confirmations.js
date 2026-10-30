// Result page: keep the "PayPal confirmation" section up to date from verified webhooks.
// Polls GET /orders/{order_id}/confirmations every 5 s for up to 2 minutes. textContent only.
"use strict";

(function () {
  const section = document.getElementById("confirmations");
  if (!section) return;

  const orderId = section.dataset.orderId;
  const list = document.getElementById("confirmations-list");
  const waiting = document.getElementById("confirmations-waiting");
  const mismatchNotice = document.getElementById("confirmations-mismatch");

  const POLL_MS = 5000;
  const GIVE_UP_MS = 120000;
  // Once one of these arrives, PayPal has confirmed the final outcome
  const FINAL = ["PAYMENT.CAPTURE.COMPLETED", "PAYMENT.CAPTURE.DENIED", "PAYMENT.AUTHORIZATION.VOIDED"];

  function render(data) {
    list.replaceChildren();
    (data.events || []).forEach(function (e) {
      const li = document.createElement("li");
      li.className = "confirmation confirmation-" + String(e.reconciliation).replace(/[^a-z]/g, "");
      const label = document.createElement("span");
      label.className = "confirmation-label";
      label.textContent = e.label;
      const time = document.createElement("time");
      time.className = "muted";
      time.dateTime = e.received_at;
      time.textContent = e.received_at;
      li.appendChild(label);
      li.appendChild(document.createTextNode(" "));
      li.appendChild(time);
      if (e.reconciliation === "mismatch") {
        const detail = document.createElement("span");
        detail.className = "confirmation-detail";
        detail.textContent = e.detail;
        li.appendChild(detail);
      }
      list.appendChild(li);
    });
    waiting.hidden = (data.events || []).length > 0;
    mismatchNotice.hidden = !data.mismatch;
  }

  function isFinal(data) {
    return (data.events || []).some(function (e) { return FINAL.indexOf(e.event_type) !== -1; });
  }

  async function poll() {
    const deadline = Date.now() + GIVE_UP_MS;
    while (Date.now() < deadline) {
      try {
        const resp = await fetch("/orders/" + encodeURIComponent(orderId) + "/confirmations", { cache: "no-store" });
        if (resp.ok) {
          const data = await resp.json();
          render(data);
          if (isFinal(data)) return;
        }
      } catch (e) {
        // Try again on the next tick
      }
      await new Promise(function (resolve) { setTimeout(resolve, POLL_MS); });
    }
    if (waiting && !waiting.hidden) {
      waiting.textContent = "PayPal has not confirmed yet. Reload the page later to check again.";
    }
  }

  poll();
})();

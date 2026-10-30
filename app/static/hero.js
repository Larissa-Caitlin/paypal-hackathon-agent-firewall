// Start page: the TechMart Deals listing as a person sees it, with a switch to view it as the agent.
// Listing text comes from /catalog and /shops/techmartdeals/views and is set with textContent only.
"use strict";

(function () {
  const SHOP = "techmartdeals";
  const toggle = document.getElementById("agent-view");
  const listing = document.getElementById("listing");
  const hiddenEl = document.getElementById("listing-hidden");
  if (!toggle || !listing) return;

  function setText(id, text) {
    document.getElementById(id).textContent = text;
  }

  function setAgentView(on) {
    toggle.setAttribute("aria-checked", on ? "true" : "false");
    listing.classList.toggle("as-agent", on);
    hiddenEl.hidden = !on;
  }

  async function load() {
    try {
      const [catalogResp, viewsResp] = await Promise.all([
        fetch("/catalog"),
        fetch("/shops/" + SHOP + "/views"),
      ]);
      if (!catalogResp.ok || !viewsResp.ok) throw new Error("listing unavailable");
      const product = (await catalogResp.json()).find(function (p) { return p.shop_key === SHOP; });
      const views = await viewsResp.json();
      const parts = listingParts(views.visible, views.full);

      setText("listing-shop", product.shop_name);
      setText("listing-name", product.name);
      setText("listing-price", "$" + product.price);
      setText("listing-id", parts.productIdLine || "");
      setText("listing-desc", parts.description.join(" "));

      hiddenEl.replaceChildren();
      const label = document.createElement("span");
      label.className = "listing-hidden-label";
      label.textContent = "Hidden from people (display:none), read by the agent:";
      hiddenEl.appendChild(label);
      hiddenEl.appendChild(document.createTextNode(parts.hidden.join(" ")));
      toggle.disabled = parts.hidden.length === 0;
    } catch (e) {
      setText("listing-desc", "The listing could not be loaded. Reload the page to try again.");
      toggle.disabled = true;
    }
  }

  toggle.addEventListener("click", function () {
    setAgentView(toggle.getAttribute("aria-checked") !== "true");
  });
  setAgentView(false);
  load();
})();

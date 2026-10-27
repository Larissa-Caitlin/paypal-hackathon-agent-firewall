"""Live run of the firewall's AI checks against the real shop pages.

Usage: python scripts/run_ai_checks_demo.py
"""

import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.catalog import Product, get_product, list_shops  # noqa: E402
from app.firewall_ai import check_intent, scan_page_llm_judge, scan_page_prompt_guard  # noqa: E402
from app.shop_pages import page_text_for_agent  # noqa: E402

INSTRUCTION = "Buy me a phone case for my 6.1-inch phone."
PAUSE = 3  # seconds between calls, for rate limits

WALLET = Product(
    product_id="tm-wallet-01",
    shop_key="techmart",
    shop_name="TechMart",
    name="Premium Leather Wallet",
    price=Decimal("19.00"),
    description="",
)


def main():
    calls = []
    for shop_key, _ in list_shops():
        page = page_text_for_agent(shop_key)
        calls.append((shop_key, lambda k=shop_key, p=page: scan_page_prompt_guard(k, p)))
        calls.append((shop_key, lambda k=shop_key, p=page: scan_page_llm_judge(k, p)))
    for product in (get_product("gh-case-01"), get_product("tmd-case-01"), WALLET):
        calls.append((product.product_id, lambda p=product: check_intent(INSTRUCTION, p)))

    rows = []
    for i, (target, call) in enumerate(calls):
        if i:
            time.sleep(PAUSE)
        print(f"Running {i + 1}/{len(calls)} ...", flush=True)
        f = call()
        rows.append((f.detector, f.check, target, "FLAGGED" if f.flagged else "ok", f.reason))

    print()
    header = ("detector", "check", "target", "result", "reason")
    widths = [max(len(r[i]) for r in rows + [header]) for i in range(4)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths) + "  {}"
    print(fmt.format(*header))
    print(fmt.format(*("-" * w for w in widths), "-" * 6))
    for r in rows:
        print(fmt.format(*r))


if __name__ == "__main__":
    main()

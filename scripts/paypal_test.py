import os
import sys
import uuid

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.environ["PAYPAL_CLIENT_ID"]
CLIENT_SECRET = os.environ["PAYPAL_CLIENT_SECRET"]
BASE = os.environ["PAYPAL_BASE_URL"].rstrip("/")
TIMEOUT = 30


def get_access_token():
    resp = requests.post(
        f"{BASE}/v1/oauth2/token",
        auth=(CLIENT_ID, CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        timeout=TIMEOUT,
    )
    if not resp.ok:
        # Error body contains only error/error_description, never the secret
        print(f"Token request failed: HTTP {resp.status_code}")
        print(resp.json())
        sys.exit(1)
    return resp.json()["access_token"]


def create_order(amount="25.00"):
    token = get_access_token()
    body = {
        "intent": "CAPTURE",
        "purchase_units": [
            {"amount": {"currency_code": "USD", "value": amount}},
        ],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "return_url": "https://example.com/return",
                    "cancel_url": "https://example.com/cancel",
                }
            }
        },
    }
    resp = requests.post(
        f"{BASE}/v2/checkout/orders",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=TIMEOUT,
    )
    data = resp.json()
    if not resp.ok:
        print(f"Create order failed: HTTP {resp.status_code}")
        print(data)
        sys.exit(1)
    approval = next(
        (link["href"] for link in data.get("links", []) if link["rel"] in ("payer-action", "approve")),
        None,
    )
    print(f"Order ID: {data['id']}")
    print(f"Approval link: {approval}")
    return data["id"]


def capture_order(order_id):
    token = get_access_token()
    resp = requests.post(
        f"{BASE}/v2/checkout/orders/{order_id}/capture",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": str(uuid.uuid4()),
        },
        timeout=TIMEOUT,
    )
    data = resp.json()
    print(f"HTTP {resp.status_code}")
    if resp.ok:
        print(f"Status: {data.get('status')}")
    else:
        print(data)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "create":
        create_order()
    elif len(sys.argv) == 3 and sys.argv[1] == "capture":
        capture_order(sys.argv[2])
    else:
        print("Usage: python scripts/paypal_test.py create | capture <ORDER_ID>")
        sys.exit(1)

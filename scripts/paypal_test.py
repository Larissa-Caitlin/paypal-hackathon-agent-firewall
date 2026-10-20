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
    return _create(amount, "CAPTURE")


def create_auth_order(amount="25.00"):
    return _create(amount, "AUTHORIZE")


def _create(amount, intent):
    token = get_access_token()
    body = {
        "intent": intent,
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


def _json_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "PayPal-Request-Id": str(uuid.uuid4()),
    }


def authorize_order(order_id):
    token = get_access_token()
    resp = requests.post(
        f"{BASE}/v2/checkout/orders/{order_id}/authorize",
        headers=_json_headers(token),
        timeout=TIMEOUT,
    )
    data = resp.json()
    print(f"HTTP {resp.status_code}")
    if resp.ok:
        auth = data["purchase_units"][0]["payments"]["authorizations"][0]
        print(f"Authorization ID: {auth['id']}")
        print(f"Status: {auth['status']}")
    else:
        print(data)


def capture_authorization(auth_id):
    token = get_access_token()
    resp = requests.post(
        f"{BASE}/v2/payments/authorizations/{auth_id}/capture",
        headers=_json_headers(token),
        timeout=TIMEOUT,
    )
    data = resp.json()
    print(f"HTTP {resp.status_code}")
    if resp.ok:
        print(f"Status: {data.get('status')}")
    else:
        print(data)


def void_authorization(auth_id):
    token = get_access_token()
    resp = requests.post(
        f"{BASE}/v2/payments/authorizations/{auth_id}/void",
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT,
    )
    print(f"HTTP {resp.status_code}")
    if resp.status_code == 204:
        print("Voided")
    elif not resp.ok:
        print(resp.json())


COMMANDS = {
    "create": (create_order, 0),
    "capture": (capture_order, 1),
    "create-auth": (create_auth_order, 0),
    "authorize": (authorize_order, 1),
    "capture-auth": (capture_authorization, 1),
    "void": (void_authorization, 1),
}


if __name__ == "__main__":
    cmd = COMMANDS.get(sys.argv[1]) if len(sys.argv) > 1 else None
    if cmd is None or len(sys.argv) - 2 != cmd[1]:
        print(
            "Usage: python scripts/paypal_test.py "
            "create | capture <ORDER_ID> | create-auth | authorize <ORDER_ID> | "
            "capture-auth <AUTH_ID> | void <AUTH_ID>"
        )
        sys.exit(1)
    cmd[0](*sys.argv[2:])

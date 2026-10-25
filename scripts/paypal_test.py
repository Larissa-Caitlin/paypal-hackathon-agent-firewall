import os
import sys
import uuid

import requests
from dotenv import load_dotenv

load_dotenv()

BASE = os.environ["PAYPAL_BASE_URL"].rstrip("/")
TIMEOUT = 30

# shop name -> env var prefix; each shop has its own REST app
SHOPS = {
    "techmart": "PAYPAL_TECHMART",
    "gadgethub": "PAYPAL_GADGETHUB",
    "techmartdeals": "PAYPAL_TECHMARTDEALS",
}

# Set from --shop in __main__; None means the default PAYPAL_CLIENT_ID app
SHOP = None


def load_credentials(shop):
    prefix = SHOPS[shop] if shop else "PAYPAL"
    names = (f"{prefix}_CLIENT_ID", f"{prefix}_CLIENT_SECRET")
    for name in names:
        if not os.environ.get(name):
            # Name only, never the value
            print(f"Missing credential in .env: {name}")
            sys.exit(1)
    return os.environ[names[0]], os.environ[names[1]]


def get_access_token():
    client_id, client_secret = load_credentials(SHOP)
    resp = requests.post(
        f"{BASE}/v1/oauth2/token",
        auth=(client_id, client_secret),
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


def create_auth_order(payee_email=None, amount="25.00"):
    return _create(amount, "AUTHORIZE", payee_email)


def _create(amount, intent, payee_email=None):
    token = get_access_token()
    unit = {"amount": {"currency_code": "USD", "value": amount}}
    # With --shop the payee is implicitly the shop owning the credentials
    if payee_email and not SHOP:
        unit["payee"] = {"email_address": payee_email}
    body = {
        "intent": intent,
        "purchase_units": [unit],
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


def get_order(order_id):
    token = get_access_token()
    resp = requests.get(
        f"{BASE}/v2/checkout/orders/{order_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT,
    )
    data = resp.json()
    if not resp.ok:
        print(f"HTTP {resp.status_code}")
        print(data)
        return
    unit = data["purchase_units"][0]
    payee = unit.get("payee", {})
    print(f"Status: {data.get('status')}")
    print(f"Amount: {unit['amount']['value']} {unit['amount']['currency_code']}")
    print(f"Payee email: {payee.get('email_address')}")
    print(f"Payee merchant ID: {payee.get('merchant_id')}")


# command -> (function, min args, max args)
COMMANDS = {
    "create": (create_order, 0, 0),
    "capture": (capture_order, 1, 1),
    "create-auth": (create_auth_order, 0, 1),
    "authorize": (authorize_order, 1, 1),
    "capture-auth": (capture_authorization, 1, 1),
    "void": (void_authorization, 1, 1),
    "get": (get_order, 1, 1),
}


USAGE = (
    "Usage: python scripts/paypal_test.py "
    "create | capture <ORDER_ID> | create-auth [PAYEE_EMAIL] | authorize <ORDER_ID> | "
    "capture-auth <AUTH_ID> | void <AUTH_ID> | get <ORDER_ID>  "
    f"[--shop {'|'.join(SHOPS)}]"
)


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--shop" in args:
        i = args.index("--shop")
        if i + 1 >= len(args) or args[i + 1] not in SHOPS:
            print(USAGE)
            sys.exit(1)
        SHOP = args[i + 1]
        del args[i:i + 2]
    cmd = COMMANDS.get(args[0]) if args else None
    if cmd is None or not cmd[1] <= len(args) - 1 <= cmd[2]:
        print(USAGE)
        sys.exit(1)
    if SHOP and args[0] == "create-auth" and len(args) > 1:
        print("PAYEE_EMAIL cannot be combined with --shop; the payee is the shop itself")
        sys.exit(1)
    cmd[0](*args[1:])

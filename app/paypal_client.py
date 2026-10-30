"""Reusable PayPal REST client (sandbox), one REST app per shop.

Never prints; returns data or raises. Never puts secrets or tokens in errors.
"""

import os
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.environ.get("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com").rstrip("/")
TIMEOUT = 30
TOKEN_REFRESH_MARGIN = 60  # seconds before expiry to fetch a new token

# shop key -> (client id env var, client secret env var)
SHOPS = {
    "techmart": ("PAYPAL_TECHMART_CLIENT_ID", "PAYPAL_TECHMART_CLIENT_SECRET"),
    "gadgethub": ("PAYPAL_GADGETHUB_CLIENT_ID", "PAYPAL_GADGETHUB_CLIENT_SECRET"),
    "techmartdeals": ("PAYPAL_TECHMARTDEALS_CLIENT_ID", "PAYPAL_TECHMARTDEALS_CLIENT_SECRET"),
}

# shop key -> (access token, monotonic time after which to refresh)
_token_cache: dict[str, tuple[str, float]] = {}


class PayPalError(Exception):
    """A failed PayPal API call. Holds only PayPal's error fields, never credentials."""

    def __init__(self, status_code, name=None, message=None, debug_id=None):
        self.status_code = status_code
        self.name = name
        self.message = message
        self.debug_id = debug_id
        super().__init__(f"PayPal HTTP {status_code}: {name}: {message} (debug_id={debug_id})")


@dataclass
class OrderInfo:
    order_id: str
    status: str
    amount: Decimal
    currency: str
    payee_email: str | None
    payee_merchant_id: str | None
    description: str | None


@dataclass
class AuthInfo:
    authorization_id: str
    status: str


def _credentials(shop):
    """Return (client_id, client_secret) for a shop, or raise ValueError naming what is missing."""
    if shop not in SHOPS:
        raise ValueError(f"Unknown shop: {shop!r} (expected one of {', '.join(SHOPS)})")
    values = []
    for name in SHOPS[shop]:
        value = os.environ.get(name)
        if not value:
            raise ValueError(f"Missing credential in environment: {name}")
        values.append(value)
    return tuple(values)


def _raise_for_error(resp):
    """Raise PayPalError built from a non-2xx response."""
    try:
        body = resp.json()
    except ValueError:
        body = {}
    raise PayPalError(
        status_code=resp.status_code,
        # Orders/Payments APIs use name/message; the OAuth endpoint uses error/error_description
        name=body.get("name") or body.get("error"),
        message=body.get("message") or body.get("error_description"),
        debug_id=body.get("debug_id") or resp.headers.get("PayPal-Debug-Id"),
    )


def get_access_token(shop):
    """Return an OAuth access token for the shop, cached until shortly before it expires."""
    cached = _token_cache.get(shop)
    if cached and time.monotonic() < cached[1]:
        return cached[0]
    client_id, client_secret = _credentials(shop)
    resp = requests.post(
        f"{BASE_URL}/v1/oauth2/token",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        timeout=TIMEOUT,
    )
    if not resp.ok:
        _raise_for_error(resp)
    data = resp.json()
    expires_at = time.monotonic() + int(data["expires_in"]) - TOKEN_REFRESH_MARGIN
    _token_cache[shop] = (data["access_token"], expires_at)
    return data["access_token"]


def _request(shop, method, path, json=None):
    """Send an authenticated request; POSTs get a fresh PayPal-Request-Id. Returns the response."""
    headers = {"Authorization": f"Bearer {get_access_token(shop)}"}
    if method == "POST":
        headers["Content-Type"] = "application/json"
        headers["PayPal-Request-Id"] = str(uuid.uuid4())
    resp = requests.request(method, f"{BASE_URL}{path}", headers=headers, json=json, timeout=TIMEOUT)
    if not resp.ok:
        _raise_for_error(resp)
    return resp


def create_order(shop, amount: Decimal, description: str, return_url: str, cancel_url: str):
    """Create an AUTHORIZE-intent USD order payable to the shop. Returns (order_id, approve_url)."""
    value = amount.quantize(Decimal("0.01"))
    if amount <= 0 or value != amount:
        raise ValueError(f"Amount must be positive with at most 2 decimal places: {amount}")
    body = {
        "intent": "AUTHORIZE",
        "purchase_units": [
            {
                "amount": {"currency_code": "USD", "value": str(value)},
                "description": description,
            }
        ],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "return_url": return_url,
                    "cancel_url": cancel_url,
                }
            }
        },
    }
    data = _request(shop, "POST", "/v2/checkout/orders", json=body).json()
    approve_url = next(
        (link["href"] for link in data.get("links", []) if link["rel"] in ("payer-action", "approve")),
        None,
    )
    return data["id"], approve_url


def get_order(shop, order_id) -> OrderInfo:
    """Fetch an order's status, amount, payee and description."""
    data = _request(shop, "GET", f"/v2/checkout/orders/{order_id}").json()
    unit = data["purchase_units"][0]
    payee = unit.get("payee", {})
    return OrderInfo(
        order_id=data["id"],
        status=data["status"],
        amount=Decimal(unit["amount"]["value"]),
        currency=unit["amount"]["currency_code"],
        payee_email=payee.get("email_address"),
        payee_merchant_id=payee.get("merchant_id"),
        description=unit.get("description"),
    )


def authorize_order(shop, order_id) -> AuthInfo:
    """Authorize an approved order, placing a hold on the payer's funds."""
    data = _request(shop, "POST", f"/v2/checkout/orders/{order_id}/authorize").json()
    auth = data["purchase_units"][0]["payments"]["authorizations"][0]
    return AuthInfo(authorization_id=auth["id"], status=auth["status"])


def capture_authorization(shop, authorization_id) -> str:
    """Capture held funds. Returns the capture status (e.g. COMPLETED)."""
    data = _request(shop, "POST", f"/v2/payments/authorizations/{authorization_id}/capture").json()
    return data["status"]


def void_authorization(shop, authorization_id) -> None:
    """Release held funds. PayPal answers 204 on success."""
    _request(shop, "POST", f"/v2/payments/authorizations/{authorization_id}/void")


# PayPal's signature headers, all required to verify a webhook
WEBHOOK_HEADERS = {
    "auth_algo": "paypal-auth-algo",
    "cert_url": "paypal-cert-url",
    "transmission_id": "paypal-transmission-id",
    "transmission_sig": "paypal-transmission-sig",
    "transmission_time": "paypal-transmission-time",
}


def webhook_id(shop):
    """The shop's webhook ID from PAYPAL_<SHOP>_WEBHOOK_ID, or ValueError if unset (fail closed)."""
    if shop not in SHOPS:
        raise ValueError(f"Unknown shop: {shop!r} (expected one of {', '.join(SHOPS)})")
    name = f"PAYPAL_{shop.upper()}_WEBHOOK_ID"
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Missing webhook ID in environment: {name}")
    return value


def verify_webhook_signature(shop, headers, event) -> bool:
    """Ask PayPal whether a webhook event really came from PayPal for this shop's webhook.

    headers: the request headers (case-insensitive mapping). event: the parsed body.
    Returns True only for verification_status SUCCESS. Raises ValueError for missing
    headers or configuration, PayPalError if PayPal rejects the call.
    """
    lowered = {k.lower(): v for k, v in headers.items()}
    missing = [h for h in WEBHOOK_HEADERS.values() if not lowered.get(h)]
    if missing:
        raise ValueError(f"Missing webhook headers: {', '.join(missing)}")
    body = {field: lowered[header] for field, header in WEBHOOK_HEADERS.items()}
    body["webhook_id"] = webhook_id(shop)
    body["webhook_event"] = event
    data = _request(shop, "POST", "/v1/notifications/verify-webhook-signature", json=body).json()
    return data.get("verification_status") == "SUCCESS"

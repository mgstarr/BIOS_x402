"""Honest shop: the @paid decorator that puts an x402 price tag on a Flask route.

How it works (x402 v2, "exact" scheme):
  1. Client calls a paid route with no PAYMENT-SIGNATURE header.
  2. We answer HTTP 402 "Payment Required" with the bill (a PaymentRequired object)
     both in the PAYMENT-REQUIRED header (base64 JSON) and as the JSON body.
  3. (Not built yet) Client retries with a signed payment; we verify + settle it.
"""

import os
from functools import wraps

from flask import jsonify, request

from contracts.events import Event, EventLog
from contracts.payment import PaymentRequired, PaymentRequirements, encode_header

SERVICE = "shop"

# x402 v2 header names (see shops/CLAUDE.md). Note: contracts/payment.py currently
# defines the older "X-Payment-*" names; we use the v2 names as the spec says.
HEADER_PAYMENT_REQUIRED = "PAYMENT-REQUIRED"
HEADER_PAYMENT_SIGNATURE = "PAYMENT-SIGNATURE"

NETWORK = "eip155:84532"  # Base Sepolia testnet
USDC_DECIMALS = 6  # 1 USDC = 1_000_000 atomic units
# Placeholder receiver used only when SHOP_PAY_TO is empty in .env.
PLACEHOLDER_PAY_TO = "0x0000000000000000000000000000000000000001"

# In-memory log of what the shop did, exposed at GET /events.
events = EventLog()


def record(event_type: str, **data) -> None:
    events.append(Event(service=SERVICE, type=event_type, data=data))


def usd_to_atomic(price_usd: float) -> str:
    """$0.01 -> "10000". round() avoids float errors like 0.29 * 1e6 = 289999.99..."""
    return str(round(price_usd * 10**USDC_DECIMALS))


def build_requirements(
    price_usd: float,
    amount: str | None = None,
    pay_to: str | None = None,
    asset: str | None = None,
    max_timeout_seconds: int | None = None,
    description: str = "",
) -> PaymentRequirements:
    """Build the bill for the current request. Any argument given overrides the honest default."""
    return PaymentRequirements(
        scheme="exact",
        network=NETWORK,
        amount=amount if amount is not None else usd_to_atomic(price_usd),
        asset=asset if asset is not None else os.getenv("USDC_ADDRESS", ""),
        payTo=pay_to if pay_to is not None else (os.getenv("SHOP_PAY_TO") or PLACEHOLDER_PAY_TO),
        maxTimeoutSeconds=max_timeout_seconds if max_timeout_seconds is not None else 60,
        resource=request.url,
        description=description,
        extra={"name": "USDC", "version": "2"},
    )


def paid(
    price_usd: float,
    *,
    amount: str | None = None,
    pay_to: str | None = None,
    asset: str | None = None,
    max_timeout_seconds: int | None = None,
    description: str = "",
):
    """Put a price on a Flask route.

    Honest use:     @paid(0.01)
    Malicious shop: @paid(0.01, amount="5000000", pay_to="0xEvil...")  # quotes $0.01, bills $5
    """

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            record("request", method=request.method, url=request.url, price_usd=price_usd)

            requirements = build_requirements(
                price_usd, amount, pay_to, asset, max_timeout_seconds, description
            )

            if not request.headers.get(HEADER_PAYMENT_SIGNATURE):
                bill = PaymentRequired(accepts=[requirements])
                record("payment_required", url=request.url, requirements=requirements.model_dump())
                response = jsonify(bill.model_dump(by_alias=True))
                response.status_code = 402
                response.headers[HEADER_PAYMENT_REQUIRED] = encode_header(bill)
                return response

            # A signature was sent, but verifying/settling is not built yet.
            # Never hand out paid data for an unchecked signature.
            record("failed", url=request.url, reason="payment_verification_not_implemented")
            return jsonify({"error": "payment_verification_not_implemented"}), 501

        return wrapper

    return decorator

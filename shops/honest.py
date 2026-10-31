"""Honest shop: the @paid decorator that puts an x402 price tag on a Flask route.

How it works (x402 v2, "exact" scheme):
  1. Client calls a paid route with no PAYMENT-SIGNATURE header.
     -> We answer HTTP 402 with the bill (PaymentRequired) in the PAYMENT-REQUIRED
        header (base64 JSON) and as the JSON body.
  2. Client retries with PAYMENT-SIGNATURE (a base64 PaymentPayload).
     -> We check it (mock check, or the real facilitator's /verify), then collect
        the money (mock receipt, or the facilitator's /settle).
  3. Success: 200 + the data + PAYMENT-RESPONSE header (base64 SettlementResponse).
     Failure: 402 again, with the reason in "error".

Request ids: every bill carries a short id in accepts[0].extra.requestId. The buyer copies
the bill into its payment, so the retry brings the same id back. Every event we log
(payment_required / verified / settled / failed) has that request_id, which lets the Live
page show ONE row per purchase instead of one row per event.
"""

import os
import re
import secrets
from functools import wraps

import requests
from dotenv import load_dotenv
from flask import jsonify, make_response, request

from contracts.events import Event, EventLog
from contracts.payment import (
    Authorization,
    PaymentPayload,
    PaymentRequired,
    PaymentRequirements,
    SettlementResponse,
    VerifyResponse,
    decode_header,
    encode_header,
)

SERVICE = "shop"

# x402 v2 header names (see shops/CLAUDE.md). Note: contracts/payment.py currently
# defines the older "X-Payment-*" names; we use the v2 names as the spec says.
HEADER_PAYMENT_REQUIRED = "PAYMENT-REQUIRED"
HEADER_PAYMENT_SIGNATURE = "PAYMENT-SIGNATURE"
HEADER_PAYMENT_RESPONSE = "PAYMENT-RESPONSE"

NETWORK = "eip155:84532"  # Base Sepolia testnet
USDC_DECIMALS = 6  # 1 USDC = 1_000_000 atomic units
# Placeholder receiver used only when SHOP_PAY_TO is empty in .env.
PLACEHOLDER_PAY_TO = "0x0000000000000000000000000000000000000001"
FACILITATOR_TIMEOUT_SECONDS = 15
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
REQUEST_ID_RE = re.compile(r"^[0-9a-f]{16}$")

# Read .env no matter how the shop was started (python app.py shop, flask run, tests...).
# Values already set in the real environment win over .env.
load_dotenv()

# In-memory log of what the shop did, exposed at GET /events.
events = EventLog()


class PaymentRejected(Exception):
    """Raised when a payment can't be accepted. The message is the reason sent back."""


def record(event_type: str, **data) -> None:
    events.append(Event(service=SERVICE, type=event_type, data=data))


def usd_to_atomic(price_usd: float) -> str:
    """$0.01 -> "10000". round() avoids float errors like 0.29 * 1e6 = 289999.99..."""
    return str(round(price_usd * 10**USDC_DECIMALS))


def atomic_to_usd(amount: str) -> float:
    return int(amount) / 10**USDC_DECIMALS


def mock_facilitator_enabled() -> bool:
    return os.getenv("MOCK_FACILITATOR", "false").strip().lower() == "true"


def configured_pay_to() -> str:
    """SHOP_PAY_TO from .env, or "" if it is not set. The ONE place that reads it."""
    return (os.getenv("SHOP_PAY_TO") or "").strip()


def pay_to_address() -> str:
    """The wallet we actually put on bills: SHOP_PAY_TO, or the placeholder if it's empty."""
    return configured_pay_to() or PLACEHOLDER_PAY_TO


def pay_to_problem() -> str | None:
    """"empty" or "invalid" if SHOP_PAY_TO needs fixing, else None."""
    value = configured_pay_to()
    if not value:
        return "empty"
    if not ADDRESS_RE.match(value):
        return "invalid"
    return None


def new_request_id() -> str:
    return secrets.token_hex(8)  # 16 hex chars, e.g. "9f2c41d07ab35e10"


def build_requirements(
    price_usd: float,
    amount: str | None = None,
    pay_to: str | None = None,
    asset: str | None = None,
    max_timeout_seconds: int | None = None,
    description: str = "",
    request_id: str | None = None,
) -> PaymentRequirements:
    """Build the bill for the current request. Any argument given overrides the honest default."""
    extra = {"name": "USDC", "version": "2"}
    if request_id:
        extra["requestId"] = request_id  # lets us match the paid retry to this bill
    return PaymentRequirements(
        scheme="exact",
        network=NETWORK,
        amount=amount if amount is not None else usd_to_atomic(price_usd),
        asset=asset if asset is not None else os.getenv("USDC_ADDRESS", ""),
        payTo=pay_to if pay_to is not None else pay_to_address(),
        maxTimeoutSeconds=max_timeout_seconds if max_timeout_seconds is not None else 60,
        resource=request.url,
        description=description,
        extra=extra,
    )


def payment_required_response(requirements: PaymentRequirements, error: str = "payment_required"):
    """HTTP 402 with the bill in both the header and the body."""
    bill = PaymentRequired(error=error, accepts=[requirements])
    response = jsonify(bill.model_dump(by_alias=True))
    response.status_code = 402
    response.headers[HEADER_PAYMENT_REQUIRED] = encode_header(bill)
    return response


# --- Mock facilitator: checks the payment's shape and terms, moves no money -------------


def mock_verify(payment: PaymentPayload, requirements: PaymentRequirements) -> VerifyResponse:
    """Accept the payment if it is well-formed and pays exactly what we asked, to us, in our token.

    This does NOT check the cryptographic signature -- only a real facilitator can.
    """
    accepted = payment.accepted
    if payment.x402Version != 2:
        return VerifyResponse(isValid=False, invalidReason="unsupported_x402_version")
    if accepted.scheme != requirements.scheme or accepted.network != requirements.network:
        return VerifyResponse(isValid=False, invalidReason="wrong_scheme_or_network")
    if accepted.amount != requirements.amount:
        return VerifyResponse(isValid=False, invalidReason="amount_mismatch")
    if accepted.payTo.lower() != requirements.payTo.lower():
        return VerifyResponse(isValid=False, invalidReason="pay_to_mismatch")
    if accepted.asset.lower() != requirements.asset.lower():
        return VerifyResponse(isValid=False, invalidReason="asset_mismatch")

    signature = payment.payload.get("signature")
    if not isinstance(signature, str) or not signature.startswith("0x"):
        return VerifyResponse(isValid=False, invalidReason="missing_signature")
    try:
        auth = Authorization.model_validate(payment.payload.get("authorization"))
    except Exception:
        return VerifyResponse(isValid=False, invalidReason="malformed_authorization")
    # The signed authorization itself must also move the right money to the right place.
    if auth.value != requirements.amount or auth.to.lower() != requirements.payTo.lower():
        return VerifyResponse(isValid=False, invalidReason="authorization_mismatch")

    return VerifyResponse(isValid=True, payer=auth.from_)


def mock_settle(verified: VerifyResponse) -> SettlementResponse:
    # "0xmock" prefix makes it obvious this is NOT a real on-chain transaction hash.
    return SettlementResponse(
        success=True,
        transaction="0xmock" + secrets.token_hex(30),  # same length as a real tx hash
        network=NETWORK,
        payer=verified.payer,
    )


# --- Real facilitator: a separate service that checks signatures and submits on-chain ---


def call_facilitator(endpoint: str, payment: PaymentPayload, requirements: PaymentRequirements) -> dict:
    url = os.getenv("FACILITATOR_URL", "http://localhost:7002").rstrip("/") + endpoint
    body = {
        "paymentPayload": payment.model_dump(by_alias=True),
        "paymentRequirements": requirements.model_dump(by_alias=True),
    }
    try:
        resp = requests.post(url, json=body, timeout=FACILITATOR_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise PaymentRejected(f"facilitator_error: {endpoint} failed ({type(exc).__name__})") from exc


def verify_and_settle(
    payment: PaymentPayload, requirements: PaymentRequirements, request_id: str = ""
) -> SettlementResponse:
    """Check the payment, then collect it. Raises PaymentRejected with a reason on any failure."""
    if mock_facilitator_enabled():
        verified = mock_verify(payment, requirements)
    else:
        verified = VerifyResponse.model_validate(call_facilitator("/verify", payment, requirements))

    if not verified.isValid:
        raise PaymentRejected(verified.invalidReason or "invalid_payment")

    record("verified", path=request.path, resource=request.url, request_id=request_id,
           amount_usd=atomic_to_usd(requirements.amount), payer=verified.payer)

    if mock_facilitator_enabled():
        settlement = mock_settle(verified)
    else:
        settlement = SettlementResponse.model_validate(call_facilitator("/settle", payment, requirements))

    if not settlement.success:
        raise PaymentRejected(settlement.errorReason or "settlement_failed")
    if settlement.payer is None:
        settlement.payer = verified.payer
    return settlement


def echoed_request_id(payment: PaymentPayload | None) -> str | None:
    """The requestId the buyer copied from our bill into its payment (if well-formed)."""
    if payment is None:
        return None
    rid = (payment.accepted.extra or {}).get("requestId")
    return rid if isinstance(rid, str) and REQUEST_ID_RE.match(rid) else None


def open_bill_id(path: str, resource: str) -> str | None:
    """Fallback for buyers that drop extra.requestId: the newest bill for the same
    path + URL that has not been paid or failed yet."""
    closed = set()
    for e in reversed(events.last(500)):
        d = e.data
        if d.get("path") != path or d.get("resource") != resource or not d.get("request_id"):
            continue
        if e.type in ("settled", "failed"):
            closed.add(d["request_id"])
        elif e.type == "payment_required" and d["request_id"] not in closed:
            return d["request_id"]
    return None


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
            path, resource = request.path, request.url
            signature_header = request.headers.get(HEADER_PAYMENT_SIGNATURE)

            payment = None
            if signature_header:
                try:
                    payment = decode_header(signature_header, PaymentPayload)
                except Exception:
                    payment = None  # rejected below as malformed

            # Which purchase is this? No payment -> new bill, new id.
            # Paid retry -> the id the buyer copied from our bill (or the open bill for this URL).
            if not signature_header:
                bill_rid = rid = new_request_id()
            else:
                bill_rid = echoed_request_id(payment)
                rid = bill_rid or open_bill_id(path, resource) or new_request_id()

            record("request", method=request.method, path=path, url=resource,
                   resource=resource, request_id=rid, price_usd=price_usd)

            # On a retry, rebuild the SAME bill (same requestId) so it matches what was accepted.
            requirements = build_requirements(
                price_usd, amount, pay_to, asset, max_timeout_seconds, description,
                request_id=bill_rid,
            )
            amount_usd = atomic_to_usd(requirements.amount)

            if not signature_header:
                record("payment_required", path=path, resource=resource, request_id=rid,
                       amount_usd=amount_usd, requirements=requirements.model_dump())
                return payment_required_response(requirements)

            payer = None
            try:
                if payment is None:
                    raise PaymentRejected("malformed_payment_signature")
                payer = (payment.payload.get("authorization") or {}).get("from")
                settlement = verify_and_settle(payment, requirements, request_id=rid)
            except PaymentRejected as exc:
                record("failed", path=path, resource=resource, request_id=rid,
                       amount_usd=amount_usd, payer=payer, reason=str(exc))
                return payment_required_response(requirements, error=str(exc))

            record("settled", path=path, resource=resource, request_id=rid,
                   amount_usd=amount_usd, payer=settlement.payer, tx=settlement.transaction)

            # Paid in full: now (and only now) run the real route and hand over the data.
            response = make_response(view(*args, **kwargs))
            response.headers[HEADER_PAYMENT_RESPONSE] = encode_header(settlement)
            return response

        return wrapper

    return decorator

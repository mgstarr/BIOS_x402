"""x402 client helper: handles the 402 response, gets a signature, and retries the request."""

import os
import sys
import time

import requests
from dotenv import load_dotenv

from contracts.events import Event, EventLog
from contracts.payment import (
    PAYMENT_REQUIRED,
    PAYMENT_RESPONSE,
    PAYMENT_SIGNATURE,
    Authorization,
    PaymentPayload,
    PaymentRequired,
    SettlementResponse,
    decode_header,
    encode_header,
)

load_dotenv()
SIGNER_URL = os.getenv("SIGNER_URL", "http://localhost:7001")  # Phase 2: point at the defender
MOCK_SIGNER = os.getenv("MOCK_SIGNER", "false").lower() == "true"
MOCK_PAYER = "0x000000000000000000000000000000000000dEaD"  # obviously fake address


class PaymentError(Exception):
    """Any step of the x402 flow failed; the message says which one and why."""


def pick_option(bill):
    """Choose the first payment option we know how to pay: exact USDC on Base Sepolia."""
    for req in bill.accepts:
        if req.scheme == "exact" and req.network == "eip155:84532":
            return req
    raise PaymentError("no supported payment option in the bill")


def mock_sign(req):
    """MOCK_SIGNER mode: build a dummy PaymentPayload in the exact contract format (zero signature)."""
    now = int(time.time())
    auth = Authorization(
        from_=MOCK_PAYER,
        to=req.payTo,
        value=req.amount,
        validAfter=str(now - 600),  # valid from 10 min ago, to allow for clock differences
        validBefore=str(now + req.maxTimeoutSeconds),
        nonce="0x" + os.urandom(32).hex(),
    )
    payload = {"signature": "0x" + "00" * 65, "authorization": auth.model_dump(by_alias=True)}
    return PaymentPayload(accepted=req, payload=payload)


def _http(method, url, what, timeout=30, **kwargs):
    """One HTTP call; network problems become a short PaymentError naming the piece that failed."""
    try:
        return requests.request(method, url, timeout=timeout, **kwargs)
    except requests.ConnectionError:
        raise PaymentError(f"{what} not reachable at {url} -- is it running? check the URL in .env / firewall")
    except requests.Timeout:
        raise PaymentError(f"{what} timed out after {timeout}s ({url})")


def _short(err):
    """First two lines of an error, e.g. pydantic's '1 validation error for X / <field>'."""
    return " / ".join(str(err).splitlines()[:2])


def _decode(value, model_cls, what):
    """Decode an x402 header, or say clearly which header was missing or broken."""
    if not value:
        raise PaymentError(f"{what} header missing")
    try:
        return decode_header(value, model_cls)
    except Exception as e:
        raise PaymentError(f"{what} header doesn't match the contract: {_short(e)}")


def sign(req):
    """Get the bill signed: locally in mock mode, otherwise by the signer service (holds the key)."""
    if MOCK_SIGNER:
        return mock_sign(req)
    resp = _http("POST", f"{SIGNER_URL}/sign", "signer", json={"requirements": req.model_dump()})
    if resp.status_code != 200:
        raise PaymentError(f"signer returned {resp.status_code}: {resp.text[:200]}")
    try:
        payload = PaymentPayload.model_validate(resp.json())
        auth = payload.payload["authorization"]
    except Exception as e:
        raise PaymentError(f"signer reply doesn't match the contract: {_short(e)}")
    # Safety: never send a signature for a different amount or recipient than the bill.
    if auth.get("value") != req.amount or auth.get("to") != req.payTo:
        raise PaymentError("signer signed a different amount or recipient than the bill; refusing to send it")
    return payload


def fetch_paid(url, log):
    """GET a URL, paying with x402 if the shop asks. Returns (data, payment summary or None)."""
    try:
        return _fetch_paid(url, log)
    except PaymentError as e:
        log.append(Event(service="agent", type="failed", data={"url": url, "error": str(e)}))
        raise


def _fetch_paid(url, log):
    log.append(Event(service="agent", type="request", data={"url": url}))
    resp = _http("GET", url, "shop")
    if resp.status_code != 402:
        if resp.status_code != 200:
            raise PaymentError(f"shop returned {resp.status_code}: {resp.text[:200]}")
        return resp.json(), None  # free endpoint, nothing to pay

    bill = _decode(resp.headers.get(PAYMENT_REQUIRED), PaymentRequired, f"shop's {PAYMENT_REQUIRED}")
    req = pick_option(bill)
    amount_usd = int(req.amount) / 1_000_000  # USDC has 6 decimals
    log.append(Event(service="agent", type="payment_required",
                     data={"url": url, "amount_usd": amount_usd, "payTo": req.payTo}))

    payload = sign(req)
    payer = payload.payload["authorization"]["from"]
    log.append(Event(service="agent", type="signed", data={"url": url, "payer": payer}))

    resp = _http("GET", url, "shop", timeout=60, headers={PAYMENT_SIGNATURE: encode_header(payload)})
    if resp.status_code != 200:
        raise PaymentError(f"shop rejected payment ({resp.status_code}): {resp.text[:200]}")

    receipt = _decode(resp.headers.get(PAYMENT_RESPONSE), SettlementResponse, f"shop's {PAYMENT_RESPONSE}")
    if not receipt.success:
        raise PaymentError(f"settlement failed: {receipt.errorReason}")
    payment = {"resource": url, "amount_usd": amount_usd, "tx": receipt.transaction, "payer": receipt.payer}
    log.append(Event(service="agent", type="settled", data=payment))
    return resp.json(), payment


if __name__ == "__main__":
    # Manual test: python -m payment.client "http://localhost:5001/api/price?symbol=ETH"
    log = EventLog()
    data, payment = fetch_paid(sys.argv[1], log)
    for e in log.last():
        print(f"[{e.type}] {e.data}")
    print("data:", data)
    print("payment:", payment)

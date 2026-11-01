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


MAX_BILLS = 2  # a shop that bills again after being paid gets paid at most twice per URL


class PaymentError(Exception):
    """Any step of the x402 flow failed; the message says which one and why."""


class PaymentBlocked(PaymentError):
    """The defender (guard) refused to let the wallet sign this bill (HTTP 403)."""

    def __init__(self, check, reason):
        super().__init__(f"payment blocked by the guard: {reason}")
        self.check, self.reason = check, reason


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


def sign(req, context=None, signer_url=None):
    """Get the bill signed: locally in mock mode, otherwise by the signer (or the defender in front of it)."""
    if MOCK_SIGNER:
        return mock_sign(req)
    body = {"requirements": req.model_dump(), "context": context or {}}
    resp = _http("POST", f"{signer_url or SIGNER_URL}/sign", "signer", json=body)
    if resp.status_code == 403:  # the defender said no: nothing was signed
        verdict = resp.json()
        raise PaymentBlocked(verdict.get("check"), verdict.get("reason"))
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


def fetch_paid(url, log, paid=None, context=None, signer_url=None, timeout=30):
    """GET a URL, paying with x402 if the shop asks. Returns the data; each payment made goes into `paid`.

    context = {"task", "mandate"} for the defender; intended_url and final_url are filled in here.
    """
    paid = [] if paid is None else paid
    try:
        return _fetch_paid(url, log, paid, context or {}, signer_url, timeout)
    except PaymentError as e:
        blocked = isinstance(e, PaymentBlocked)
        data = {"url": url, "error": str(e)} | ({"check": e.check, "reason": e.reason} if blocked else {})
        log.append(Event(service="agent", type="blocked" if blocked else "failed", data=data))
        raise


def _fetch_paid(url, log, paid, context, signer_url, timeout):
    log.append(Event(service="agent", type="request", data={"url": url}))
    resp = _http("GET", url, "shop", timeout)
    for _ in range(MAX_BILLS):
        if resp.status_code != 402:
            break
        resp = _pay_bill(url, resp, log, paid, context, signer_url, timeout)
    if resp.status_code == 402:
        raise PaymentError("shop still asks for payment after being paid: refusing to pay again")
    if resp.status_code != 200:
        raise PaymentError(f"shop returned {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _pay_bill(url, resp, log, paid, context, signer_url, timeout):
    """Read the 402 bill, get it signed, send it back. Returns the shop's answer to the paid request."""
    bill = _decode(resp.headers.get(PAYMENT_REQUIRED), PaymentRequired, f"shop's {PAYMENT_REQUIRED}")
    req = pick_option(bill)
    amount_usd = int(req.amount) / 1_000_000  # USDC has 6 decimals
    log.append(Event(service="agent", type="payment_required",
                     data={"url": url, "amount_usd": amount_usd, "payTo": req.payTo}))

    # resp.url is where we ended up after any redirects: the defender compares it with url.
    ctx = {**context, "intended_url": url, "final_url": resp.url}
    payload = sign(req, ctx, signer_url)
    payer = payload.payload["authorization"]["from"]
    log.append(Event(service="agent", type="signed", data={"url": url, "payer": payer}))

    resp = _http("GET", resp.url, "shop", timeout * 2, headers={PAYMENT_SIGNATURE: encode_header(payload)})
    if not resp.headers.get(PAYMENT_RESPONSE):  # no receipt: the shop didn't take the money
        raise PaymentError(f"shop rejected payment ({resp.status_code}): {resp.text[:200]}")
    receipt = _decode(resp.headers.get(PAYMENT_RESPONSE), SettlementResponse, f"shop's {PAYMENT_RESPONSE}")
    if not receipt.success:
        raise PaymentError(f"settlement failed: {receipt.errorReason}")
    payment = {"resource": url, "amount_usd": amount_usd, "payTo": req.payTo, "asset": req.asset,
               "tx": receipt.transaction, "payer": receipt.payer}
    paid.append(payment)
    log.append(Event(service="agent", type="settled", data=payment))
    return resp


def pay(url, context=None, signer_url=None, timeout=30):
    """One URL, no LLM (the attacker engine's direct mode). Never raises: the outcome is in the dict."""
    out = {"payments": [], "status": None}
    try:
        out["data"] = fetch_paid(url, EventLog(), out["payments"], context, signer_url, timeout)
        out["status"] = 200
    except PaymentBlocked as e:
        out.update(blocked=True, block_check=e.check, block_reason=e.reason, error=str(e))
    except PaymentError as e:
        out["error"] = str(e)
    return out


if __name__ == "__main__":
    # Manual test: python -m payment.client "http://localhost:5001/api/price?symbol=ETH"
    log, paid = EventLog(), []
    data = fetch_paid(sys.argv[1], log, paid)
    for e in log.last():
        print(f"[{e.type}] {e.data}")
    print("data:", data)
    print("payments:", paid)

"""Walk through a full x402 payment against every paid endpoint of the shop.

For each endpoint:  1) GET it, expect 402 + the bill
                    2) build a FAKE but well-formed PaymentPayload (dummy signature)
                    3) retry with PAYMENT-SIGNATURE, print status, receipt and data

The fake payment only works when the shop runs with MOCK_FACILITATOR=true.
A real facilitator would reject the dummy signature (which is correct!).

Run from the repo root (with the shop running):  python shops\\try_pay.py
"""

import json
import os
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `contracts` imports work

import requests
from dotenv import load_dotenv

from contracts.payment import (
    Authorization,
    PaymentPayload,
    PaymentRequired,
    SettlementResponse,
    decode_header,
    encode_header,
)
from shops.honest import (
    HEADER_PAYMENT_REQUIRED,
    HEADER_PAYMENT_RESPONSE,
    HEADER_PAYMENT_SIGNATURE,
    USDC_DECIMALS,
)

ENDPOINTS = ["/api/price", "/api/sentiment", "/api/report"]
# Obviously-fake buyer wallet and signature (65 zero bytes). Never a real key.
DUMMY_PAYER = "0x000000000000000000000000000000000000dEaD"
DUMMY_SIGNATURE = "0x" + "00" * 65


def build_fake_payment(
    bill: PaymentRequired, value: str | None = None, to: str | None = None
) -> PaymentPayload:
    """Agree to the shop's first option, copying its amount, payTo and asset exactly.

    value / to (optional) change ONLY the signed authorization, to imitate a cheating buyer
    who copies the bill honestly but signs for less money, or for a different wallet.
    """
    req = bill.accepts[0]
    now = int(time.time())
    auth = Authorization(
        from_=DUMMY_PAYER,
        to=to if to is not None else req.payTo,
        value=value if value is not None else req.amount,
        validAfter=str(now - 60),
        validBefore=str(now + req.maxTimeoutSeconds),
        nonce="0x" + secrets.token_hex(32),
    )
    return PaymentPayload(
        accepted=req,
        payload={"signature": DUMMY_SIGNATURE, "authorization": auth.model_dump(by_alias=True)},
    )


def try_endpoint(url: str) -> None:
    print("=" * 70)
    print(f"Step 1: GET {url}")
    resp = requests.get(url, timeout=10)
    print(f"  Status: {resp.status_code}")
    if resp.status_code != 402:
        print(json.dumps(resp.json(), indent=2))
        return

    header = resp.headers.get(HEADER_PAYMENT_REQUIRED)
    if not header:
        print(f"  402 but no {HEADER_PAYMENT_REQUIRED} header!")
        return
    bill = decode_header(header, PaymentRequired)
    req = bill.accepts[0]
    usd = int(req.amount) / 10**USDC_DECIMALS
    print(f"  Bill: {req.amount} atomic = ${usd:.2f} USDC to {req.payTo}")
    print(f"        token {req.asset} on {req.network}")

    print("\nStep 2: build fake (dummy-signed) payment and retry")
    payment = build_fake_payment(bill)
    resp = requests.get(url, headers={HEADER_PAYMENT_SIGNATURE: encode_header(payment)}, timeout=30)
    print(f"  Status: {resp.status_code}")

    if resp.status_code != 200:
        print(f"  Rejected: {resp.json().get('error')}")
        return

    receipt_header = resp.headers.get(HEADER_PAYMENT_RESPONSE)
    if receipt_header:
        receipt = decode_header(receipt_header, SettlementResponse)
        print("\n  Receipt (PAYMENT-RESPONSE, decoded):")
        print("   ", json.dumps(receipt.model_dump(), indent=2).replace("\n", "\n    "))
    else:
        print(f"  WARNING: 200 but no {HEADER_PAYMENT_RESPONSE} header")

    print("\n  Data:")
    print("   ", json.dumps(resp.json(), indent=2).replace("\n", "\n    "))


def main() -> None:
    load_dotenv()
    base = os.getenv("SHOP_URL", "http://localhost:5001").rstrip("/")
    for path in ENDPOINTS:
        try_endpoint(f"{base}{path}?symbol=ETH")
        print()


if __name__ == "__main__":
    main()

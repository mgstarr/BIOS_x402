"""Call the paid price endpoint and print the 402 bill in plain English.

Run from the repo root (with the shop running):  python shops\\try_pay.py
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `contracts` imports work

import requests
from dotenv import load_dotenv

from contracts.payment import PaymentRequired, decode_header
from shops.honest import HEADER_PAYMENT_REQUIRED, USDC_DECIMALS


def main() -> None:
    load_dotenv()
    url = os.getenv("SHOP_URL", "http://localhost:5001") + "/api/price?symbol=ETH"

    print(f"GET {url}")
    resp = requests.get(url, timeout=10)
    print(f"Status: {resp.status_code}")

    if resp.status_code != 402:
        print(json.dumps(resp.json(), indent=2))
        return

    header = resp.headers.get(HEADER_PAYMENT_REQUIRED)
    if not header:
        print(f"402 but no {HEADER_PAYMENT_REQUIRED} header!")
        return

    bill = decode_header(header, PaymentRequired)
    print(f"\nThe shop wants payment (x402 v{bill.x402Version}, error={bill.error!r}):")
    for i, req in enumerate(bill.accepts, 1):
        usd = int(req.amount) / 10**USDC_DECIMALS
        print(f"\n  Option {i}:")
        print(f"    Price:        {req.amount} atomic units = ${usd:.6f} {req.extra.get('name', '')}")
        print(f"    Pay to:       {req.payTo}")
        print(f"    Token:        {req.asset}")
        print(f"    Network:      {req.network}  (scheme: {req.scheme})")
        print(f"    For:          {req.resource}")
        print(f"    Description:  {req.description}")
        print(f"    Valid for:    {req.maxTimeoutSeconds} seconds")

    same = resp.json() == bill.model_dump(by_alias=True)
    print(f"\nHeader matches JSON body: {same}")


if __name__ == "__main__":
    main()

"""Integration check (no LLM): pay the shop at SHOP_URL for one endpoint via payment/client.py.

Run from the repo root:  python -m tests.integration.test_agent_vs_shop [path]
"""

import os
import sys

import requests
from dotenv import load_dotenv

from contracts.events import EventLog
from payment.client import MOCK_SIGNER, PaymentError, fetch_paid

load_dotenv()
SHOP_URL = os.getenv("SHOP_URL", "http://localhost:5001")
PATH = sys.argv[1] if len(sys.argv) > 1 else "/api/price?symbol=ETH"


def main():
    print(f"shop: {SHOP_URL}   signer: {'MOCK' if MOCK_SIGNER else 'real'}")

    health = requests.get(f"{SHOP_URL}/health", timeout=5).json()
    print("1. health  ", health)

    catalog = requests.get(f"{SHOP_URL}/catalog", timeout=5).json()
    print("2. catalog ", [(c["path"], c["price_usd"]) for c in catalog])

    log = EventLog()
    try:
        data, payment = fetch_paid(SHOP_URL + PATH, log)
    except PaymentError as e:
        data, payment = None, None
        print("   PAYMENT FAILED:", e)
    for i, e in enumerate(log.last(), 3):
        print(f"{i}. {e.type:<9}", e.data)

    print("data:    ", data)
    print("payment: ", payment)
    ok = bool(payment and payment["tx"])
    print("RESULT:  ", "PASS (200 + receipt)" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

"""Self test: sign, verify and settle a 0.01 USDC payment on Anvil, then replay it."""

import os
import sys

import requests
from dotenv import load_dotenv

from chain import config
from contracts.payment import PaymentRequirements
from payment import settlement

load_dotenv()

SIGNER_URL = os.getenv("SIGNER_URL", "http://localhost:7001")
FACILITATOR_URL = os.getenv("FACILITATOR_URL", "http://localhost:7002")
AMOUNT = 10000


def usdc(raw: int) -> float:
    return round(raw / 10**config.USDC_DECIMALS, 6)


def print_balances(label: str, agent: str) -> tuple[int, int]:
    a, s = settlement.balance_of(agent), settlement.balance_of(config.SHOP_PAY_TO)
    print(f"{label}: agent {usdc(a)} USDC, shop {usdc(s)} USDC")
    return a, s


def post(url: str, body: dict) -> dict:
    resp = requests.post(url, json=body, timeout=90)
    if resp.status_code != 200:
        raise SystemExit(f"{url} returned {resp.status_code}: {resp.text}")
    return resp.json()


def main() -> int:
    agent = requests.get(f"{SIGNER_URL}/address", timeout=10).json()["address"]
    if agent.lower() != config.AGENT_ADDRESS.lower():
        print(f"FAIL: signer address {agent} does not match AGENT_ADDRESS {config.AGENT_ADDRESS}")
        return 1
    print("payer (from signer /address):", agent)
    agent_before, shop_before = print_balances("before", agent)

    req = PaymentRequirements(amount=str(AMOUNT), asset=config.USDC_ADDRESS,
                              payTo=config.SHOP_PAY_TO, resource="selftest").model_dump()
    payload = post(f"{SIGNER_URL}/sign", {"requirements": req})
    body = {"paymentPayload": payload, "paymentRequirements": req}

    print("verify:", post(f"{FACILITATOR_URL}/verify", body))
    settled = post(f"{FACILITATOR_URL}/settle", body)
    print("settle:", settled)
    if not settled["success"]:
        return 1
    print("tx hash:", settled["transaction"])
    agent_after, shop_after = print_balances("after", agent)

    replay = post(f"{FACILITATOR_URL}/settle", body)
    print("replay settle:", replay)

    ok = (agent_before - agent_after == AMOUNT and shop_after - shop_before == AMOUNT
          and not replay["success"] and replay["errorReason"] == "nonce_used")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

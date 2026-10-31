"""End-to-end x402 pipeline test. Run: python tests/integration/test_pipeline.py  (or pytest -s).

URLs come from .env / environment: RPC_URL, SIGNER_URL, FACILITATOR_URL, SHOP_URL, AGENT_URL.
Layers run in order and stop at the first failure with a reason.
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests
from dotenv import load_dotenv

from contracts.payment import (
    PAYMENT_REQUIRED,
    PAYMENT_RESPONSE,
    PAYMENT_SIGNATURE,
    PaymentPayload,
    PaymentRequired,
    SettlementResponse,
    decode_header,
    encode_header,
)

load_dotenv()
U = {
    "anvil": os.getenv("RPC_URL", "http://127.0.0.1:8545"),
    "signer": os.getenv("SIGNER_URL", "http://127.0.0.1:7001"),
    "facilitator": os.getenv("FACILITATOR_URL", "http://127.0.0.1:7002"),
    "shop": os.getenv("SHOP_URL", "http://localhost:5001"),
    "agent": os.getenv("AGENT_URL", "http://localhost:7000"),
}
U = {k: v.rstrip("/") for k, v in U.items()}
USDC = os.getenv("USDC_ADDRESS", "0x036CbD53842c5426634e7929541eC2318f3dCF7e")
PAY_ROUTE = "/api/price?symbol=ETH"
TASK = "What is the latest price of ETH? Use the paid API."


class Fail(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


def rpc(method, params):
    r = requests.post(U["anvil"], json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=10).json()
    check("error" not in r, f"rpc {method} error: {r.get('error')}")
    return r["result"]


def balance(addr):
    data = "0x70a08231" + addr.lower().removeprefix("0x").rjust(64, "0")
    return int(rpc("eth_call", [{"to": USDC, "data": data}, "latest"]), 16)


def confirm_tx(tx, who):
    check(isinstance(tx, str) and tx.startswith("0x") and len(tx) == 66 and not tx.startswith("0xmock"),
          f"{who}: tx '{tx}' is not a real tx hash (shop/agent running in MOCK mode? "
          "set MOCK_FACILITATOR=false on shop, MOCK_SIGNER=false on agent)")
    rcpt = rpc("eth_getTransactionReceipt", [tx])
    check(rcpt is not None, f"{who}: tx {tx} not found on Anvil")
    check(rcpt["status"] == "0x1", f"{who}: tx {tx} reverted (status {rcpt['status']})")


def tail(name, n=5):
    if name == "anvil":
        return
    try:
        evs = requests.get(f"{U[name]}/events", timeout=5).json()[-n:]
    except Exception as e:
        print(f"  [{name}] events unavailable: {e}")
        return
    for e in evs:
        print(f"  [{name}] {e['type']} {e['data']}")


def layer1():
    print("L1 reachability")
    down = []
    for name, url in U.items():
        try:
            r = requests.get(f"{url}/events", timeout=5)
            up = True
            print(f"  {name:12} UP   {url} (/events -> {r.status_code})")
        except requests.RequestException as e:
            up = False
            print(f"  {name:12} DOWN {url} ({type(e).__name__})")
        if not up:
            down.append(name)
    check(not down, f"unreachable: {', '.join(down)} (check *_URL in .env / firewall)")


def layer2():
    print("L2 chain")
    cid = int(rpc("eth_chainId", []), 16)
    check(cid == 84532, f"chain id {cid}, expected 84532")
    agent = requests.get(f"{U['signer']}/address", timeout=5).json()["address"]
    pay_to = os.getenv("SHOP_PAY_TO", "")
    print(f"  chain {cid}  agent {agent} = {balance(agent)}  shop {pay_to} = {balance(pay_to)} (atomic USDC)")
    check(balance(agent) > 0, "agent wallet has 0 USDC (fund it on the fork)")
    return agent


def layer3():
    print("L3 no-LLM payment")
    url = U["shop"] + PAY_ROUTE
    r = requests.get(url, timeout=10)
    check(r.status_code == 402, f"shop returned {r.status_code}, expected 402")
    hdr = r.headers.get(PAYMENT_REQUIRED)
    check(hdr, f"402 without {PAYMENT_REQUIRED} header (shop side)")
    bill = decode_header(hdr, PaymentRequired)
    req = bill.accepts[0]
    print(f"  bill: {req.amount} -> {req.payTo} asset {req.asset}")
    check(req.payTo.lower() == os.getenv("SHOP_PAY_TO", "").lower(), "wrong_payee: bill payTo != SHOP_PAY_TO")
    before = balance(req.payTo)

    s = requests.post(f"{U['signer']}/sign", json={"requirements": req.model_dump()}, timeout=15)
    check(s.status_code == 200, f"signer {s.status_code}: {s.text[:200]}")
    payload = PaymentPayload.model_validate(s.json())
    r2 = requests.get(url, headers={PAYMENT_SIGNATURE: encode_header(payload)}, timeout=90)
    check(r2.status_code == 200, f"paid retry returned {r2.status_code}: {r2.text[:200]}")
    rh = r2.headers.get(PAYMENT_RESPONSE)
    check(rh, f"200 without {PAYMENT_RESPONSE} header")
    st = decode_header(rh, SettlementResponse)
    check(st.success, f"settlement failed: {st.errorReason}")
    confirm_tx(st.transaction, "layer3")
    after = balance(req.payTo)
    check(after - before == int(req.amount), f"shop balance moved {after - before}, expected {req.amount}")
    print(f"  OK tx {st.transaction}  shop +{after - before}")
    return int(req.amount)


def layer4(i, price):
    print(f"L4 full agent run #{i}")
    pay_to = os.getenv("SHOP_PAY_TO", "")
    before = balance(pay_to)
    body = {"task": TASK, "mandate": {"task": TASK, "max_per_payment_usd": 0.05, "budget_usd": 0.20}}
    r = requests.post(f"{U['agent']}/run", json=body, timeout=300)
    check(r.status_code == 200, f"agent /run {r.status_code}: {r.text[:300]}")
    j = r.json()
    check(j.get("answer"), f"no answer; trace tail: {j.get('trace', [])[-3:]}")
    pays = j.get("payments") or []
    check(pays, "agent answered without any payment (LLM skipped the paid API?)")
    for p in pays:
        confirm_tx(p.get("tx"), "agent")
    moved = balance(pay_to) - before
    expected = sum(round(p["amount_usd"] * 1_000_000) for p in pays)
    check(moved == expected, f"shop balance moved {moved}, payments sum {expected}")
    print(f"  OK answer: {j['answer'][:120]!r}  payments: {[p['tx'] for p in pays]}")


def main():
    layers = []
    try:
        layer1()
        layer2()
        price = layer3()
        for i in (1, 2, 3):
            layer4(i, price)
            for n in ("shop", "facilitator", "signer"):
                tail(n)
        print("ALL PASS")
        return 0
    except Fail as e:
        print(f"FAIL: {e}")
        for n in ("shop", "facilitator", "signer", "agent"):
            tail(n, 8)
        return 1


def test_pipeline():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())

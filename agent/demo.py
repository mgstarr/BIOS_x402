"""Demo: send a task to the agent and print its trace as numbered, readable lines."""

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()
AGENT_URL = os.getenv("AGENT_URL", "http://localhost:7000")


def short(addr):
    return f"{addr[:6]}…{addr[-4:]}" if addr else "?"


def describe(s):
    """One line of text for one trace step."""
    kind = s["step"]
    if kind == "mandate":
        m = s["mandate"]
        return f"max ${m['max_per_payment_usd']:.2f}/payment, budget ${m['budget_usd']:.2f}" if m else "none"
    if kind == "thought":
        return s["text"]
    if kind == "tool":
        return f"{s['name']}({', '.join(map(str, s['args'].values()))})"
    if kind == "402":
        return f"shop asks ${s['amount_usd']:.2f} -> {short(s['payTo'])}"
    if kind == "signed":
        return f"by {short(s['payer'])}"
    if kind == "paid":
        return f"tx {s['tx']}" + ("  [SIMULATED]" if s.get("simulated") else "  [ON-CHAIN]")
    if kind == "blocked":
        return f"GUARD said no ({s['check']}): {s['reason']}"
    if kind == "failed":
        return s["error"]
    if kind == "result":
        if "error" in s:
            return f"error: {s['error']}"
        if "catalog" in s:
            return f"{len(s['catalog'])} APIs in catalog"
        return str(s.get("data"))
    return str(s)


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else "What is the latest price and sentiment for ETH?"
    body = {"task": task, "mandate": {"task": task, "max_per_payment_usd": 0.05, "budget_usd": 0.20}}
    r = requests.post(f"{AGENT_URL}/run", json=body, timeout=300).json()
    if "error" in r:
        sys.exit(f"agent error: {r['error']}")

    steps = [s for s in r["trace"] if s["step"] != "answer"]
    for i, s in enumerate(steps, 1):
        print(f"{i:>2}. {s['step']:<8} {describe(s)}")
    paid = [p for p in r["payments"] if p["status"] == "paid"]
    blocked = [p for p in r["payments"] if p["status"] == "blocked"]
    print(f"    ANSWER   {r['answer']}")
    print(f"    TOTAL    ${sum(p['amount_usd'] for p in paid):.2f} paid in {len(paid)} payments, "
          f"{len(blocked)} blocked (${sum(p['amount_usd'] for p in blocked):.2f} saved)")

"""Dashboard (port 8000): three views of one payment pipeline, plus an overview.

  /           Overview: how the pieces connect, with live numbers
  /provider   TickToll, the service provider: services, incoming requests, invoices
  /guard      The guard: the owner's rules and every bill it judged, with reasons
  /attacker   The attacker's console: attacks, how well they work, a custom attack builder

The pages only talk to this app. The /api/* routes below forward to the real services, so the
browser never needs to reach ports 7000-7004 or 5001 itself. Start: python app.py dashboard
"""

import os
import re
import time

import requests
from flask import Flask, jsonify, render_template, request
from web3 import HTTPProvider, Web3

from chain.config import USDC_ABI, USDC_ADDRESS

SHOP_URL = os.getenv("SHOP_URL", "http://localhost:5001")
AGENT_URL = os.getenv("AGENT_URL", "http://localhost:7000")
DEFENDER_URL = os.getenv("DEFENDER_URL", "http://localhost:7003")
ATTACKER_URL = os.getenv("ATTACKER_URL", "http://localhost:7004")
RPC_URL = os.getenv("RPC_URL", "http://localhost:8545")
SHOP_WALLET = os.getenv("SHOP_PAY_TO", "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC")

# Plain names for TickToll's products (the shop's /catalog only has paths and descriptions).
PRODUCTS = {
    "/api/price": ("Coin price", "Current price, 24h range, volume and market cap for one coin."),
    "/api/sentiment": ("Market mood", "How positive or negative people are about a coin over 24 hours."),
    "/api/report": ("Analyst report", "A short written summary of a coin: key numbers and risks."),
    "/compromised/wrong-wallet": ("Coin price, hacked route", ""),
    "/compromised/lookalike": ("Coin price, hacked route", ""),
}

# TickToll's demo-only hacked routes, and the wallet each one really pays (see shops/server.py).
HIJACKED_TO = {"/compromised/wrong-wallet": "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
               "/compromised/lookalike": os.getenv("LOOKALIKE_ADDRESS", "0x3c44cDdd1B2C3D4e5F60718293a4b5c6d74293bC")}

app = Flask(__name__)
LAST_SUITE = {}  # the latest full assessment from the attacker engine, per guard setting


def call(method, url, timeout=5, **kwargs):
    """Call a service. Returns (json, None) or (None, plain error) -- never raises."""
    try:
        r = requests.request(method, url, timeout=timeout, **kwargs)
    except requests.Timeout:
        return None, f"{url.split('/')[2]} took longer than {timeout} s"
    except requests.RequestException:
        return None, f"{url.split('/')[2]} is not running"
    try:
        body = r.json()
    except ValueError:
        return None, f"{url.split('/')[2]} answered {r.status_code} without JSON"
    if r.status_code >= 400 and not (isinstance(body, dict) and body.get("blocked")):
        return None, (body.get("error") if isinstance(body, dict) else None) or f"error {r.status_code}"
    return body, None


def reply(data, err):
    return (jsonify({"error": err}), 502) if err else jsonify(data)


def usdc_balance(address):
    try:
        w3 = Web3(HTTPProvider(RPC_URL, request_kwargs={"timeout": 2}))
        token = w3.eth.contract(address=Web3.to_checksum_address(USDC_ADDRESS), abi=USDC_ABI)
        return token.functions.balanceOf(Web3.to_checksum_address(address)).call() / 1_000_000
    except Exception:
        return None


# --- pages ---------------------------------------------------------------------------------

@app.get("/")
def overview():
    return render_template("overview.html", active="overview")


@app.get("/provider")
def provider():
    return render_template("provider.html", active="provider")


@app.get("/guard")
def guard():
    return render_template("guard.html", active="guard")


@app.get("/attacker")
def attacker():
    return render_template("attacker.html", active="attacker")


@app.get("/health")
def health():
    return {"ok": True, "service": "dashboard"}


# --- shared --------------------------------------------------------------------------------

@app.get("/api/status")
def status():
    """Small strip shown on every page: guard on/off, wallets, is everything running."""
    mode, _ = call("GET", f"{DEFENDER_URL}/mode", timeout=2)
    wallet, _ = call("GET", f"{ATTACKER_URL}/wallet", timeout=4)
    return jsonify({"guard": None if mode is None else mode["enabled"],
                    "agent_usd": (wallet or {}).get("agent"), "attacker_usd": (wallet or {}).get("attacker"),
                    "simulated": (wallet or {}).get("simulated")})


@app.post("/api/agent")
def run_agent():
    """Give the AI agent a task. Its payments go through the guard like any other."""
    task = str((request.get_json(silent=True) or {}).get("task", "")).strip()
    if not task:
        return jsonify({"error": "type a task first"}), 400
    data, err = call("POST", f"{AGENT_URL}/run", timeout=240, json={"task": task})
    return reply(data, err)


# --- service provider ----------------------------------------------------------------------

def coin_of(resource):
    m = re.search(r"symbol=(\w+)", resource or "")
    return m.group(1).upper() if m else ""


def orders_from(events):
    """Group the shop's events (request -> 402 -> verified -> settled/failed) into one row per request."""
    orders = {}
    for e in events:
        d, rid = e["data"], e["data"].get("request_id")
        if not rid:
            continue
        o = orders.setdefault(rid, {"id": rid, "ts": e["ts"], "status": "requested", "amount_usd": None,
                                    "payer": None, "tx": None, "reason": None})
        path = d.get("path") or ""
        o.update(path=path, coin=coin_of(d.get("resource") or d.get("url")),
                 product=PRODUCTS.get(path, (f"Unlisted route {path}",))[0])
        if d.get("amount_usd") is not None or d.get("price_usd") is not None:
            o["amount_usd"] = d.get("amount_usd", d.get("price_usd"))
        if e["type"] == "payment_required":
            o["status"] = "awaiting payment"
        elif e["type"] == "verified":
            o.update(status="verifying", payer=d.get("payer"))
        elif e["type"] == "settled":
            o.update(status="paid", payer=d.get("payer"), tx=d.get("tx"), paid_ts=e["ts"])
            if path.startswith("/compromised/"):  # the hacked demo route: the money went elsewhere
                o.update(status="hijacked", pay_to=HIJACKED_TO.get(path))
        elif e["type"] == "failed":
            o.update(status="declined", payer=d.get("payer") or o["payer"], reason=d.get("reason"))
    return sorted(orders.values(), key=lambda o: o["ts"], reverse=True)


@app.get("/api/provider")
def provider_data():
    catalog, err = call("GET", f"{SHOP_URL}/catalog")
    if err:
        return reply(None, err)
    status, _ = call("GET", f"{SHOP_URL}/status")
    events, _ = call("GET", f"{SHOP_URL}/events?limit=1000")
    services = [{"path": c["path"], "price_usd": c["price_usd"],
                 "name": PRODUCTS.get(c["path"], (c["path"],))[0],
                 "about": PRODUCTS.get(c["path"], (None, c.get("description")))[1]} for c in catalog]
    orders = orders_from(events or [])
    revenue = round(sum(o["amount_usd"] or 0 for o in orders if o["status"] == "paid"), 6)
    return jsonify({"services": services, "orders": orders, "revenue_usd": revenue,
                    "totals": (status or {}).get("totals", {}), "wallet": SHOP_WALLET,
                    "wallet_usd": usdc_balance(SHOP_WALLET),
                    "mock": (status or {}).get("mock_facilitator", False)})


# --- guard ---------------------------------------------------------------------------------

@app.route("/api/rules", methods=["GET", "POST"])
def rules():
    if request.method == "POST":
        return reply(*call("POST", f"{DEFENDER_URL}/rules", json=request.get_json(silent=True) or {}))
    return reply(*call("GET", f"{DEFENDER_URL}/rules"))


@app.route("/api/mode", methods=["GET", "POST"])
def mode():
    if request.method == "POST":
        enabled = bool((request.get_json(silent=True) or {}).get("enabled"))
        return reply(*call("POST", f"{DEFENDER_URL}/mode", json={"enabled": enabled}))
    return reply(*call("GET", f"{DEFENDER_URL}/mode"))


@app.get("/api/decisions")
def decisions():
    """Every bill the guard judged, newest first, plus its counters."""
    events, err = call("GET", f"{DEFENDER_URL}/events?limit=200")
    if err:
        return reply(None, err)
    stats, _ = call("GET", f"{DEFENDER_URL}/stats")
    rows = [{"ts": e["ts"], "verdict": e["type"], **e["data"]} for e in events
            if e["type"] in ("allowed", "blocked", "paid_unprotected")]
    return jsonify({"decisions": rows[::-1], "stats": stats or {}})


@app.post("/api/decisions/clear")
def clear_decisions():
    """Clear the feed and counters (the owner's rules and the guard switch stay as they are)."""
    mode_now, _ = call("GET", f"{DEFENDER_URL}/mode")
    data, err = call("POST", f"{DEFENDER_URL}/reset")
    if mode_now is not None:
        call("POST", f"{DEFENDER_URL}/mode", json={"enabled": mode_now["enabled"]})
    return reply(data, err)


# --- attacker ------------------------------------------------------------------------------

@app.get("/api/attacks")
def attacks():
    """The attack list with how often each one worked, guard off vs on (live tries + last assessment)."""
    catalogue, err = call("GET", f"{ATTACKER_URL}/attacks")
    if err:
        return reply(None, err)
    board, _ = call("GET", f"{ATTACKER_URL}/scoreboard")
    rows = []
    for a in catalogue:
        if a["category"] == "honest":
            continue
        tally = {}
        for guard in ("off", "on"):
            live = ((board or {}).get("attacks", {}).get(a["id"], {}).get(guard)) or {}
            tries, wins, lost = live.get("fired", 0), live.get("stolen", 0), live.get("lost_usd", 0.0)
            for r in LAST_SUITE.get(guard, []):
                if r["attack_id"] == a["id"]:
                    tries, wins, lost = tries + 1, wins + (r["outcome"] == "stolen"), lost + r["lost_usd"]
            tally[guard] = {"tries": tries, "wins": wins, "lost_usd": round(lost, 4)}
        rows.append({**a, "tally": tally})
    return jsonify({"attacks": rows, "assessed_at": LAST_SUITE.get("ts")})


@app.post("/api/fire")
def fire():
    body = request.get_json(silent=True) or {}
    payload = {k: body[k] for k in ("attack_id", "custom", "mode", "player") if body.get(k) is not None}
    data, err = call("POST", f"{ATTACKER_URL}/fire", timeout=240, json=payload)
    if data:
        data.pop("run", None)
    return reply(data, err)


@app.post("/api/assess")
def assess():
    """Run every attack with the guard on and then off (direct mode: fast, no AI)."""
    data, err = call("POST", f"{ATTACKER_URL}/suite", timeout=600, json={"mode": "direct"})
    if data:
        LAST_SUITE.update(ts=time.time(), on=data["guard_on"]["results"], off=data["guard_off"]["results"])
    return reply(data, err)


@app.post("/api/reset")
def reset():
    """Refill the AI wallet to $100 and empty the attacker's wallet (local chain only)."""
    return reply(*call("POST", f"{ATTACKER_URL}/reset", timeout=10))


def create_app() -> Flask:
    return app


def run() -> None:
    app.run(host="0.0.0.0", port=8000, threaded=True)

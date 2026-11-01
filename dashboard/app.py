"""Dashboard (port 8000): three views of one payment pipeline, plus an overview.

  /           Overview: how the pieces connect, with live numbers
  /provider   TickToll, the service provider: its APIs (try and buy them), requests, invoices
  /guard      The guard: blocks with reasons, the owner's rules, and a live run of the AI agent
  /attacker   "Rob the Robot": the attacker's game, with recon, a workshop and an intel board

The pages only talk to this app. The /api/* routes below forward to the real services, so the
browser never needs to reach ports 7000-7004 or 5001 itself. Start: python app.py dashboard
"""

import os
import re
import time
from urllib.parse import quote

import requests
from flask import Flask, jsonify, render_template, request
from web3 import HTTPProvider, Web3

from chain.config import USDC_ABI, USDC_ADDRESS
from contracts.payment import PAYMENT_REQUIRED, PaymentRequired, decode_header
from payment import client as wallet

SHOP_URL = os.getenv("SHOP_URL", "http://localhost:5001")
EVIL_URL = os.getenv("EVIL_URL", "http://localhost:5002")
AGENT_URL = os.getenv("AGENT_URL", "http://localhost:7000")
DEFENDER_URL = os.getenv("DEFENDER_URL", "http://localhost:7003")
ATTACKER_URL = os.getenv("ATTACKER_URL", "http://localhost:7004")
RPC_URL = os.getenv("RPC_URL", "http://localhost:8545")
SHOP_WALLET = os.getenv("SHOP_PAY_TO", "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC")
ATTACKER_WALLET = "0x90F79bf6EB2c4f870365E785982E1f101E93b906"
LOOKALIKE_WALLET = os.getenv("LOOKALIKE_ADDRESS", "0x3c44cDdd1B2C3D4e5F60718293a4b5c6d74293bC")
FAKE_TOKEN = "0x1111111111111111111111111111111111111111"

# Plain names for TickToll's products (the shop's /catalog has paths and technical descriptions).
PRODUCTS = {
    "/api/price": ("Coin price", "Live price, 24-hour range, volume and market cap of a cryptocurrency."),
    "/api/sentiment": ("Market mood", "Are people online feeling positive or negative about a coin today?"),
    "/api/report": ("Analyst report", "A short expert-style summary of a coin: key numbers, rating and risks."),
    "/api/weather": ("Weather", "Conditions right now and a three-day forecast for a city."),
    "/api/temperature": ("Temperature", "How warm it is, how warm it feels, and the next six hours."),
    "/api/air": ("Air quality", "Is the air safe to breathe? Air quality index and fine dust (PM2.5)."),
    "/api/stock": ("Share price", "A company's share price today, its day range, volume and size."),
    "/api/fx": ("Exchange rate", "How much one currency buys in another, and today's change."),
    "/api/news": ("Headlines", "The top stories right now for one topic."),
    "/api/price-peak": ("Coin price, peak rate", ""),
    "/compromised/wrong-wallet": ("Coin price, hacked route", ""),
    "/compromised/lookalike": ("Coin price, hacked route", ""),
}

# TickToll's demo-only hacked routes, and the wallet each one really pays (see shops/server.py).
HIJACKED_TO = {"/compromised/wrong-wallet": ATTACKER_WALLET, "/compromised/lookalike": LOOKALIKE_WALLET}

app = Flask(__name__)
LAST_SUITE = {}  # the latest "test all" from the attacker engine
HISTORY = []  # every attack launched from the Attacker page: who, what, how, guard, outcome
RECEIPTS = {}  # on-chain details per transaction hash (they never change once mined)


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
        msg = (body.get("error") or body.get("message")) if isinstance(body, dict) else None
        return None, msg or f"error {r.status_code}"
    return body, None


def reply(data, err):
    return (jsonify({"error": err}), 502) if err else jsonify(data)


def web3():
    return Web3(HTTPProvider(RPC_URL, request_kwargs={"timeout": 2}))


def usdc_balance(address):
    try:
        token = web3().eth.contract(address=Web3.to_checksum_address(USDC_ADDRESS), abi=USDC_ABI)
        return token.functions.balanceOf(Web3.to_checksum_address(address)).call() / 1_000_000
    except Exception:
        return None


def chain_receipt(tx):
    """Block number, block time, gas and status of a transaction, read from the chain."""
    if not tx or not str(tx).startswith("0x") or len(tx) != 66:
        return None
    if tx not in RECEIPTS:
        try:
            w3 = web3()
            r = w3.eth.get_transaction_receipt(tx)
            block = w3.eth.get_block(r["blockNumber"])
            RECEIPTS[tx] = {"block": r["blockNumber"], "block_hash": "0x" + block["hash"].hex().removeprefix("0x"),
                            "block_time": block["timestamp"], "gas_used": r["gasUsed"],
                            "success": r["status"] == 1, "tx_in_block": len(block["transactions"])}
        except Exception:
            return None
    latest = None
    try:
        latest = web3().eth.block_number
    except Exception:
        pass
    out = dict(RECEIPTS[tx])
    out["confirmations"] = latest - out["block"] + 1 if latest else None
    return out


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
    """Small strip shown on every page: guard on/off and the two wallets."""
    mode, _ = call("GET", f"{DEFENDER_URL}/mode", timeout=2)
    wallet_now, _ = call("GET", f"{ATTACKER_URL}/wallet", timeout=4)
    return jsonify({"guard": None if mode is None else mode["enabled"],
                    "agent_usd": (wallet_now or {}).get("agent"), "attacker_usd": (wallet_now or {}).get("attacker"),
                    "simulated": (wallet_now or {}).get("simulated")})


@app.get("/api/chain/<tx>")
def chain(tx):
    receipt = chain_receipt(tx)
    return jsonify(receipt) if receipt else (jsonify({"error": "not found on the chain"}), 404)


# --- service provider ----------------------------------------------------------------------

def coin_of(resource):
    m = re.search(r"[?&]\w+=([^&]+)", resource or "")
    return m.group(1).replace("%20", " ") if m else ""


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


def services():
    catalog, err = call("GET", f"{SHOP_URL}/catalog")
    if err:
        return None, err
    return [{**c, "name": PRODUCTS.get(c["path"], (c["path"],))[0],
             "about": PRODUCTS.get(c["path"], (None, c.get("description")))[1]}
            for c in catalog if c["path"] in PRODUCTS], None  # the peak-rate route is for the guard, not the shelf


@app.get("/api/provider")
def provider_data():
    items, err = services()
    if err:
        return reply(None, err)
    status_now, _ = call("GET", f"{SHOP_URL}/status")
    events, _ = call("GET", f"{SHOP_URL}/events?limit=1000")
    orders = orders_from(events or [])
    revenue = round(sum(o["amount_usd"] or 0 for o in orders if o["status"] == "paid"), 6)
    return jsonify({"services": items, "orders": orders, "revenue_usd": revenue,
                    "totals": (status_now or {}).get("totals", {}), "wallet": SHOP_WALLET,
                    "wallet_usd": usdc_balance(SHOP_WALLET),
                    "mock": (status_now or {}).get("mock_facilitator", False)})


def product_url(body):
    items, err = services()
    if err:
        return None, None, err
    item = next((s for s in items if s["path"] == body.get("path")), None)
    if not item or body.get("value") not in item["options"]:
        return None, None, "pick a service and one of its options"
    return item, f"{SHOP_URL}{item['path']}?{item['param']}={quote(body['value'])}", None


@app.post("/api/bill")
def bill():
    """Ask for the data WITHOUT paying, and show the 402 bill the shop sends back, in plain words."""
    item, url, err = product_url(request.get_json(silent=True) or {})
    if err:
        return jsonify({"error": err}), 400
    try:
        r = requests.get(url, timeout=5)
        req = decode_header(r.headers[PAYMENT_REQUIRED], PaymentRequired).accepts[0]
    except Exception as e:
        return jsonify({"error": f"could not read the bill: {e.__class__.__name__}"}), 502
    return jsonify({"url": url, "status": r.status_code, "amount_usd": int(req.amount) / 1_000_000,
                    "amount_units": req.amount, "pay_to": req.payTo, "asset": req.asset,
                    "network": req.network, "valid_for_s": req.maxTimeoutSeconds,
                    "description": req.description, "header": r.headers[PAYMENT_REQUIRED][:120]})


@app.post("/api/buy")
def buy():
    """Pay for the data with the agent's wallet. The bill goes through the guard like any other."""
    item, url, err = product_url(request.get_json(silent=True) or {})
    if err:
        return jsonify({"error": err}), 400
    task = f"Buy {item['name']} for {request.get_json()['value']} on TickToll's page"
    out = wallet.pay(url, context={"task": task}, signer_url=DEFENDER_URL, timeout=30)
    for p in out["payments"]:
        p["chain"] = chain_receipt(p.get("tx"))
    return jsonify({"url": url, **out})


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


def guard_events():
    events, err = call("GET", f"{DEFENDER_URL}/events?limit=300")
    rows = [{"ts": e["ts"], "verdict": e["type"], **e["data"]} for e in (events or [])
            if e["type"] in ("allowed", "blocked", "paid_unprotected")]
    return rows, err


@app.get("/api/decisions")
def decisions():
    """Every bill the guard judged, newest first, plus its counters."""
    rows, err = guard_events()
    if err:
        return reply(None, err)
    stats, _ = call("GET", f"{DEFENDER_URL}/stats")
    return jsonify({"decisions": rows[::-1], "stats": stats or {}})


@app.post("/api/decisions/clear")
def clear_decisions():
    """Clear the feed and counters (the owner's rules and the guard switch stay as they are)."""
    mode_now, _ = call("GET", f"{DEFENDER_URL}/mode")
    data, err = call("POST", f"{DEFENDER_URL}/reset")
    if mode_now is not None:
        call("POST", f"{DEFENDER_URL}/mode", json={"enabled": mode_now["enabled"]})
    return reply(data, err)


@app.post("/api/agent")
def start_agent():
    """Give the AI agent a task. It runs in the background; follow it on GET /api/agent/<id>."""
    task = str((request.get_json(silent=True) or {}).get("task", "")).strip()
    if not task:
        return jsonify({"error": "type a task first"}), 400
    return reply(*call("POST", f"{AGENT_URL}/run", timeout=10, json={"task": task, "background": True}))


@app.get("/api/agent/<run_id>")
def follow_agent(run_id):
    """The run so far, with each bill's guard checks and each payment's block on the chain."""
    run, err = call("GET", f"{AGENT_URL}/runs/{run_id}")
    if err:
        return reply(None, err)
    rows, _ = guard_events()
    for s in run["trace"]:
        if s["step"] == "402":  # the guard's decision on this bill: same URL, just after the bill
            s["guard"] = next((d for d in rows if (d.get("context") or {}).get("intended_url") == s["url"]
                               and d["ts"] >= s["ts"] - 1), None)
        elif s["step"] == "paid":
            s["chain"] = chain_receipt(s.get("tx"))
    return jsonify(run)


# --- attacker ------------------------------------------------------------------------------

@app.get("/api/attacks")
def attacks():
    """The attack catalogue (without the honest controls)."""
    catalogue, err = call("GET", f"{ATTACKER_URL}/attacks")
    if err:
        return reply(None, err)
    return jsonify([a for a in catalogue if a["category"] != "honest"])


@app.post("/api/recon")
def recon():
    """Would the guard catch this bill? Runs its checks without paying anything."""
    k = request.get_json(silent=True) or {}
    origin = SHOP_URL if k.get("shop") == "ticktoll" else EVIL_URL
    path = k.get("path") or "/evil/custom"
    pay_to = {"attacker": ATTACKER_WALLET, "lookalike": LOOKALIKE_WALLET, "shop": SHOP_WALLET}.get(k.get("pay_to"), ATTACKER_WALLET)
    try:
        requirements = {"amount": str(round(float(k.get("price_usd", 0.01)) * 1_000_000)),
                        "asset": FAKE_TOKEN if k.get("token") == "fake" else USDC_ADDRESS,
                        "payTo": pay_to, "maxTimeoutSeconds": int(k.get("expiry_s", 60)),
                        "resource": f"{origin}{path}?symbol=ETH", "description": "recon"}
    except (TypeError, ValueError):
        return jsonify({"error": "price and lifetime must be numbers"}), 400
    url = f"{origin}{path}?symbol=ETH"
    body = {"requirements": requirements,
            "context": {"task": "recon", "intended_url": k.get("intended_url") or url, "final_url": url}}
    return reply(*call("POST", f"{DEFENDER_URL}/preview", json=body))


@app.post("/api/fire")
def fire():
    body = request.get_json(silent=True) or {}
    payload = {k: body[k] for k in ("attack_id", "custom", "mode", "player", "lure") if body.get(k)}
    data, err = call("POST", f"{ATTACKER_URL}/fire", timeout=240, json=payload)
    if err:
        return reply(None, err)
    run = data.pop("run", {}) or {}
    data["chain"] = chain_receipt(data.get("tx"))
    data["mode"] = run.get("mode") or body.get("mode")
    data["agent_answer"] = run.get("answer")
    HISTORY.append({"ts": time.time(), "player": (body.get("player") or "anonymous")[:24],
                    "attack_id": data.get("attack_id"), "custom": bool(body.get("custom")),
                    "mode": data["mode"], "guard": data.get("guard"), "outcome": data.get("outcome"),
                    "lost_usd": data.get("lost_usd") or 0})
    return jsonify(data)


@app.get("/api/intel")
def intel():
    """How well each attack worked, by route (AI agent or direct) and guard setting, plus a leaderboard."""
    tries = [*HISTORY]
    for guard, results in (("on", LAST_SUITE.get("on", [])), ("off", LAST_SUITE.get("off", []))):
        tries += [{"attack_id": r["attack_id"], "mode": "direct", "guard": guard, "outcome": r["outcome"],
                   "lost_usd": r["lost_usd"], "player": None} for r in results]
    matrix = {}
    for t in tries:
        cell = matrix.setdefault(t["attack_id"], {}).setdefault(f"{t['mode']}-{t['guard']}",
                                                                {"tries": 0, "wins": 0, "lost_usd": 0.0})
        cell["tries"] += 1
        cell["wins"] += t["outcome"] == "stolen"
        cell["lost_usd"] = round(cell["lost_usd"] + t["lost_usd"], 4)
    board = {}
    for t in HISTORY:
        row = board.setdefault(t["player"], {"player": t["player"], "heists": 0, "wins": 0,
                                             "loot_off": 0.0, "loot_on": 0.0})
        row["heists"] += 1
        row["wins"] += t["outcome"] == "stolen"
        row["loot_on" if t["guard"] == "on" else "loot_off"] += t["lost_usd"]
    leaders = sorted(board.values(), key=lambda r: (r["loot_on"], r["loot_off"], r["wins"]), reverse=True)
    return jsonify({"matrix": matrix, "leaders": leaders, "assessed_at": LAST_SUITE.get("ts"),
                    "recent": HISTORY[-8:][::-1]})


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


# --- the learning arena (both pages) ---------------------------------------------------------

@app.get("/api/learning")
def learning():
    """Training progress, what the attacker believes works, what the guard learned, discoveries."""
    return reply(*call("GET", f"{ATTACKER_URL}/learning", timeout=5))


@app.post("/api/train")
def train():
    body = request.get_json(silent=True) or {}
    return reply(*call("POST", f"{ATTACKER_URL}/train", json={"rounds": body.get("rounds", 5),
                                                               "heists": body.get("heists", 10)}))


@app.post("/api/train/stop")
def train_stop():
    return reply(*call("POST", f"{ATTACKER_URL}/train/stop"))


@app.post("/api/learning/reset")
def learning_reset():
    return reply(*call("POST", f"{ATTACKER_URL}/learning/reset"))


def create_app() -> Flask:
    return app


def run() -> None:
    app.run(host="0.0.0.0", port=8000, threaded=True)

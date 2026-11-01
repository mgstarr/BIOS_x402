"""Attacker Flask server: fire attacks, run the suite, keep a scoreboard, expose GET /events."""

import threading
from collections import defaultdict

import requests
from flask import Flask, jsonify, request

from attacker import evaluator, runner
from attacker.attacks import AGENT, ATTACKER, ATTACKS, get
from contracts.events import Event, EventLog

app = Flask(__name__)
events = EventLog()
board = {"attacks": defaultdict(lambda: defaultdict(lambda: {"fired": 0, "blocked": 0, "stolen": 0, "lost_usd": 0.0})),
         "players": defaultdict(lambda: defaultdict(lambda: {"fired": 0, "blocked": 0, "stolen": 0, "lost_usd": 0.0}))}

# Simulated wallet (used when the chain is unreachable): $100 minus every /fire loss since /reset.
START_USD = 100.0
SIM = {"lost": 0.0}
sim_lock = threading.Lock()


def sim_wallet():
    with sim_lock:
        return {"agent": round(START_USD - SIM["lost"], 6), "attacker": round(SIM["lost"], 6)}


def sim_take(amount):
    """Book a simulated loss; the wallet can't go below $0. Returns what was actually taken."""
    with sim_lock:
        taken = round(min(amount, START_USD - SIM["lost"]), 6)
        SIM["lost"] = round(SIM["lost"] + taken, 6)
        return taken


def guard_state():
    if runner.MOCK:
        return "on" if runner.MOCK_STATE["guard"] else "off"
    try:
        return "on" if requests.get(f"{runner.DEFENDER_URL}/mode", timeout=2).json().get("enabled") else "off"
    except Exception:
        return "unknown"


def record(attack_id, player, guard, s):
    for table, key in ((board["attacks"], attack_id), (board["players"], player or "anonymous")):
        row = table[key][guard]
        row["fired"] += 1
        row["blocked"] += s["outcome"] == "blocked"
        row["stolen"] += s["outcome"] == "stolen"
        row["lost_usd"] = round(row["lost_usd"] + s["lost_usd"], 6)


def fire_one(attack, player, custom=None, mode=None):
    guard = guard_state()
    events.append(Event(service="attacker", type="attack",
                        data={"attack_id": attack["id"], "player": player, "guard": guard, "simulated": runner.MOCK}))
    run = runner.fire(attack, player, custom, mode)
    if run.get("simulated") and run.get("lost_usd", 0) > 0:
        run["lost_usd"] = sim_take(run["lost_usd"])
    s = evaluator.score(attack, run)
    record(attack["id"], player, guard, s)
    events.append(Event(service="attacker", type="result", data={**s, "player": player, "guard": guard}))
    w = wallet_data()
    return {**s, "guard": guard, "player": player, "amount_usd": s["lost_usd"], "tx_hash": s["tx"],
            "balance_usd": w["agent"], "loot_usd": w["attacker"], "run": run}


@app.get("/health")
def health():
    return jsonify({"ok": True, "mock": runner.MOCK, "run_mode": runner.RUN_MODE})


@app.get("/attacks")
def attacks():
    return jsonify(ATTACKS)


@app.post("/fire")
def fire():
    body = request.get_json(silent=True) or {}
    player, mode = body.get("player"), body.get("mode")
    if body.get("custom"):
        custom = {"id": "custom", "name": "Custom", "defender_should": "block"}
        return jsonify(fire_one(custom, player, body["custom"], mode))
    attack = get(body.get("attack_id"))
    if not attack:
        return jsonify({"error": "unknown attack_id", "known": [a["id"] for a in ATTACKS]}), 400
    return jsonify(fire_one(attack, player, mode=mode))


@app.post("/suite")
def suite():
    body = request.get_json(silent=True) or {}
    events.append(Event(service="attacker", type="attack", data={"attack_id": "suite", "simulated": runner.MOCK}))
    try:
        result = evaluator.suite(body.get("player"), body.get("mode"))
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    events.append(Event(service="attacker", type="result",
                        data={"attack_id": "suite", "simulated": runner.MOCK,
                              "blocked_on": result["guard_on"]["blocked"], "lost_off": result["guard_off"]["lost_usd"]}))
    return jsonify(result)


@app.get("/scoreboard")
def scoreboard():
    return jsonify({"attacks": board["attacks"], "players": board["players"], "simulated": runner.MOCK})


def wallet_data():
    b = None if runner.MOCK else runner.balances([AGENT, ATTACKER])
    if b is None:
        return {**sim_wallet(), "simulated": True}
    return {"agent": b[AGENT], "attacker": b[ATTACKER], "simulated": False}


@app.get("/wallet")
def wallet():
    w = wallet_data()
    return jsonify({**w, "balance_usd": w["agent"], "loot_usd": w["attacker"], "start_usd": START_USD})


@app.post("/reset")
def reset():
    """Refill the AI wallet to $100 and clear the loot (scores are kept): on the local chain if it's up."""
    with sim_lock:
        SIM["lost"] = 0.0
    if not runner.MOCK and runner.chain_up():
        runner.refill(AGENT, START_USD)
    return wallet()


@app.get("/events")
def get_events():
    return jsonify([e.model_dump() for e in events.last(100)])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7004)

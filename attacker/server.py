"""Attacker Flask server: fire attacks, run the suite, keep a scoreboard, expose GET /events."""

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


def fire_one(attack, player, custom=None):
    guard = guard_state()
    events.append(Event(service="attacker", type="attack",
                        data={"attack_id": attack["id"], "player": player, "guard": guard, "simulated": runner.MOCK}))
    run = runner.fire(attack, player, custom)
    s = evaluator.score(attack, run)
    record(attack["id"], player, guard, s)
    events.append(Event(service="attacker", type="result", data={**s, "player": player, "guard": guard}))
    return {**s, "guard": guard, "player": player, "run": run}


@app.get("/health")
def health():
    return jsonify({"ok": True, "mock": runner.MOCK, "run_mode": runner.RUN_MODE})


@app.get("/attacks")
def attacks():
    return jsonify(ATTACKS)


@app.post("/fire")
def fire():
    body = request.get_json(silent=True) or {}
    player = body.get("player")
    if body.get("custom"):
        return jsonify(fire_one({"id": "custom", "name": "Custom", "defender_should": "block"}, player, body["custom"]))
    attack = get(body.get("attack_id"))
    if not attack:
        return jsonify({"error": "unknown attack_id", "known": [a["id"] for a in ATTACKS]}), 400
    return jsonify(fire_one(attack, player))


@app.post("/suite")
def suite():
    body = request.get_json(silent=True) or {}
    events.append(Event(service="attacker", type="attack", data={"attack_id": "suite", "simulated": runner.MOCK}))
    try:
        result = evaluator.suite(body.get("player"))
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    events.append(Event(service="attacker", type="result",
                        data={"attack_id": "suite", "simulated": runner.MOCK,
                              "blocked_on": result["guard_on"]["blocked"], "lost_off": result["guard_off"]["lost_usd"]}))
    return jsonify(result)


@app.get("/scoreboard")
def scoreboard():
    return jsonify({"attacks": board["attacks"], "players": board["players"], "simulated": runner.MOCK})


@app.get("/wallet")
def wallet():
    if runner.MOCK:
        return jsonify({"agent": 100.0, "attacker": 0.0, "simulated": True})
    b = runner.balances([AGENT, ATTACKER])
    if b is None:
        return jsonify({"error": "chain not reachable"}), 502
    return jsonify({"agent": b[AGENT], "attacker": b[ATTACKER], "simulated": False})


@app.get("/events")
def get_events():
    return jsonify([e.model_dump() for e in events.last(100)])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7004)

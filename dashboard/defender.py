"""Dashboard Defender page: Flask blueprint with JSON proxy routes to the defender and attacker."""

import os
import random
import time

import requests
from dotenv import load_dotenv
from flask import Blueprint, Flask, jsonify, render_template, request

load_dotenv()
MOCK = os.getenv("MOCK", "false").lower() == "true"
DEFENDER_URL = os.getenv("DEFENDER_URL", "http://localhost:7003")
ATTACKER_URL = os.getenv("ATTACKER_URL", "http://localhost:7004")

bp = Blueprint("defend", __name__, url_prefix="/defend",
               template_folder="templates/defend", static_folder="static/defend", static_url_path="/static")

SIM = {"guard": True, "feed": [], "checked": 0, "blocked": 0, "saved": 0.0}
CHECKS = ["price", "drain cap", "payee", "asset", "expiry", "duplicate", "mandate"]
SIM_CASES = [("TickToll", "ETH price", 1, False, None), ("EvilData", "ETH price", 500, True, "price"),
             ("TickToll", "ETH sentiment", 1, False, None), ("Compromised", "ETH price", 1, True, "payee")]


def sim_event():
    shop, item, cents, bad, check = random.choice(SIM_CASES)
    blocked = bad and SIM["guard"]
    checks = [{"name": c, "ok": not (bad and c == check), "reason": "ok" if not (bad and c == check) else f"{c} wrong"}
              for c in CHECKS]
    SIM["checked"] += 1
    SIM["blocked"] += blocked
    SIM["saved"] += cents / 100 if blocked else 0
    SIM["feed"] = (SIM["feed"] + [{"type": "blocked" if blocked else "allowed", "ts": time.time(), "simulated": True,
                   "data": {"shop": shop, "item": item, "amount_cents": cents,
                            "payee": "0x90F79bf6EB2c4f870365E785982E1f101E93b906" if bad else "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
                            "checks": checks}}])[-30:]


def sim_suite():
    on = {"blocked": 8, "stolen": 0, "no_payment": 0, "lost_usd": 0, "honest_allowed": 2, "honest_total": 2}
    off = {"blocked": 0, "stolen": 7, "no_payment": 1, "lost_usd": 35.0, "honest_allowed": 2, "honest_total": 2}
    return {"guard_on": on, "guard_off": off, "simulated": True}


def proxy(method, base, path, **kw):
    try:
        r = requests.request(method, f"{base}{path}", timeout=kw.pop("timeout", 5), **kw)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": f"unreachable: {e.__class__.__name__}", "unreachable": True}), 502


@bp.get("/")
def index():
    return render_template("index.html", mock=MOCK)


@bp.get("/api/stats")
def stats():
    if MOCK:
        return jsonify({"checked": SIM["checked"], "blocked": SIM["blocked"], "allowed": SIM["checked"] - SIM["blocked"],
                        "saved_usd": round(SIM["saved"], 2), "simulated": True})
    return proxy("GET", DEFENDER_URL, "/stats")


@bp.get("/api/events")
def events():
    if MOCK:
        sim_event()
        return jsonify(SIM["feed"])
    return proxy("GET", DEFENDER_URL, "/events")


@bp.route("/api/mode", methods=["GET", "POST"])
def mode():
    if MOCK:
        if request.method == "POST":
            SIM["guard"] = bool((request.get_json(silent=True) or {}).get("enabled"))
        return jsonify({"enabled": SIM["guard"], "simulated": True})
    if request.method == "POST":
        return proxy("POST", DEFENDER_URL, "/mode", json=request.get_json(silent=True) or {})
    return proxy("GET", DEFENDER_URL, "/mode")


@bp.post("/api/suite")
def suite():
    if MOCK and os.getenv("ATTACKER_MOCK_LOCAL", "true") == "true":
        return jsonify(sim_suite())
    return proxy("POST", ATTACKER_URL, "/suite", json={}, timeout=140)


def create_app():
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=7005)

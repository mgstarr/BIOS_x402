"""Dashboard Defender page: Flask blueprint with JSON proxy routes to the defender and attacker.

  GET  /defend/                 the page (shared shell from dashboard/layout.py, blue theme)
  GET  /defend/api/stats        -> DEFENDER_URL/stats
  GET  /defend/api/events       -> DEFENDER_URL/events
  GET  /defend/api/mode         -> DEFENDER_URL/mode     POST {"enabled": bool}
  POST /defend/api/reset        -> DEFENDER_URL/reset
  GET  /defend/api/attacks      -> ATTACKER_URL/attacks  (names for the "Running attack 3 of 8" line)
  POST /defend/api/suite        -> ATTACKER_URL/suite    (also remembered as the last scorecard)
  GET  /defend/api/suite/last   the last scorecard, so a page reload still shows it

MOCK=true answers everything from simulated data (the page shows a SIMULATED DATA badge).
"""

import os
import random
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Blueprint, Flask, jsonify, render_template, request, url_for

from dashboard.layout import render_page

load_dotenv()
MOCK = os.getenv("MOCK", "false").lower() == "true"
DEFENDER_URL = os.getenv("DEFENDER_URL", "http://localhost:7003").rstrip("/")
ATTACKER_URL = os.getenv("ATTACKER_URL", "http://localhost:7004").rstrip("/")
LOOKALIKE = os.getenv("LOOKALIKE_ADDRESS", "0x3c44cDdd1B2C3D4e5F60718293a4b5c6d74293bC")

bp = Blueprint("defend", __name__, url_prefix="/defend",
               template_folder="templates/defend", static_folder="static/defend", static_url_path="/static")

LAST_SUITE = {"result": None}

# --- simulated data (MOCK=true only) ------------------------------------------------------
TICKTOLL = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
HACKER = "0x90F79bf6EB2c4f870365E785982E1f101E93b906"
CHECKS = [("origin", "origin"), ("asset", "asset"), ("payee", "payee"), ("price", "price"),
          ("mandate", "mandate"), ("cap", "spending cap"), ("expiry", "expiry"), ("duplicate", "duplicate")]
# (shop, path, cents, payee, failing check id or None, reason when it fails)
SIM_CASES = [("TickToll", "/api/price", 1, TICKTOLL, None, None),
             ("TickToll", "/api/sentiment", 2, TICKTOLL, None, None),
             ("EvilData", "/evil/overcharge", 95, HACKER, "price", "Price is 95x the usual 1¢"),
             ("EvilData", "/evil/report", 90, HACKER, "mandate", "The task never asked for an analyst report"),
             ("TickToll", "/compromised/lookalike", 1, LOOKALIKE, "payee",
              f"Wallet {LOOKALIKE} only looks like the known wallet {TICKTOLL} (lookalike address)")]


def fresh_sim():
    return {"guard": True, "feed": [], "checked": 0, "allowed": 0, "blocked": 0, "unprotected": 0, "saved": 0.0}


SIM = fresh_sim()


def reset_local() -> None:
    """Forget the last scorecard and simulated data. Used by the landing page's "Start new demo"."""
    LAST_SUITE["result"] = None
    SIM.clear()
    SIM.update(fresh_sim())


def sim_event():
    shop, path, cents, payee, bad, why = random.choice(SIM_CASES)
    kind = "paid_unprotected" if not SIM["guard"] else ("blocked" if bad else "allowed")
    checks = [{"id": cid, "name": name, "ok": cid != bad, "reason": why if cid == bad else "ok"}
              for cid, name in CHECKS]
    SIM["checked"] += 1
    SIM[{"blocked": "blocked", "allowed": "allowed", "paid_unprotected": "unprotected"}[kind]] += 1
    SIM["saved"] += cents / 100 if kind == "blocked" else 0
    data = {"verdict": kind, "shop": shop, "item": path, "amount_usd": cents / 100, "amount_cents": cents,
            "payee": payee, "check": bad, "reason": why or "all checks passed",
            "failed": [bad] if bad else [], "checks": checks, "guard": "off" if kind == "paid_unprotected" else "on"}
    if kind == "paid_unprotected":
        data["would_have_blocked"] = data["failed"]
    SIM["feed"] = (SIM["feed"] + [{"type": kind, "ts": time.time(), "simulated": True, "data": data}])[-40:]


def sim_suite():
    on = {"blocked": 8, "stolen": 0, "no_payment": 0, "errors": 0, "lost_usd": 0, "honest_allowed": 2, "honest_total": 2}
    off = {"blocked": 0, "stolen": 7, "no_payment": 1, "errors": 0, "lost_usd": 92.68, "honest_allowed": 2, "honest_total": 2}
    return {"guard_on": on, "guard_off": off, "simulated": True}


# --- helpers ------------------------------------------------------------------------------

def proxy(method, base, path, **kw):
    try:
        r = requests.request(method, f"{base}{path}", timeout=kw.pop("timeout", 5), **kw)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": f"unreachable: {e.__class__.__name__}", "unreachable": True}), 502


# --- routes -------------------------------------------------------------------------------

@bp.get("/")
def index():
    body = render_template("index.html", mock=MOCK, lookalike=LOOKALIKE, ticktoll=TICKTOLL, hacker=HACKER)
    return render_page("Defender", body, active="defend", theme="blue",
                       styles=(url_for("defend.static", filename="defend.css"),),
                       scripts=(url_for("defend.static", filename="defend.js"),))


@bp.get("/api/stats")
def stats():
    if MOCK:
        return jsonify({"checked": SIM["checked"], "blocked": SIM["blocked"], "allowed": SIM["allowed"],
                        "unprotected": SIM["unprotected"], "saved_usd": round(SIM["saved"], 2), "simulated": True})
    return proxy("GET", DEFENDER_URL, "/stats")


@bp.get("/api/events")
def events():
    if MOCK:
        if random.random() < 0.4:
            sim_event()
        return jsonify(SIM["feed"])
    return proxy("GET", DEFENDER_URL, "/events", params={"limit": request.args.get("limit", 100)})


@bp.route("/api/mode", methods=["GET", "POST"])
def mode():
    if MOCK:
        if request.method == "POST":
            SIM["guard"] = bool((request.get_json(silent=True) or {}).get("enabled"))
        return jsonify({"enabled": SIM["guard"], "simulated": True})
    if request.method == "POST":
        return proxy("POST", DEFENDER_URL, "/mode", json=request.get_json(silent=True) or {})
    return proxy("GET", DEFENDER_URL, "/mode")


@bp.post("/api/reset")
def reset():
    if MOCK:
        SIM.clear()
        SIM.update(fresh_sim())
        return jsonify({"ok": True, "simulated": True})
    return proxy("POST", DEFENDER_URL, "/reset", json={})


@bp.get("/api/attacks")
def attacks():
    if MOCK:
        return jsonify({"error": "simulated"}), 502  # page falls back to its built-in names
    return proxy("GET", ATTACKER_URL, "/attacks")


@bp.post("/api/suite")
def suite():
    if MOCK and os.getenv("ATTACKER_MOCK_LOCAL", "true") == "true":
        time.sleep(2)
        result, status = sim_suite(), 200
    else:
        try:
            r = requests.post(f"{ATTACKER_URL}/suite", json={}, timeout=140)
            result, status = r.json(), r.status_code
        except Exception as e:
            return jsonify({"error": f"unreachable: {e.__class__.__name__}", "unreachable": True}), 502
    if status == 200 and "guard_on" in result:
        LAST_SUITE["result"] = {**result, "ts": time.time()}
    return jsonify(result), status


@bp.get("/api/suite/last")
def suite_last():
    return jsonify(LAST_SUITE["result"] or {})


def create_app():
    """Standalone run (port 7005): also serves the shared /assets/style.css."""
    app = Flask(__name__, static_folder=str(Path(__file__).with_name("assets")), static_url_path="/assets")
    app.register_blueprint(bp)
    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=7005)

"""Defender Flask server: exposes policy/mandate/origin checks and GET /events.

Sits in front of the signer on port 7003. Callers POST the same body they would send the
signer ({"requirements": ..., "context": ...}); we run the plain-code checks in policy.py and
either block (403) or pass the request on to the real signer (or fake a signature in mock mode).

Run:  python -m defender.server     or     python app.py defender
"""

import hmac
import os
import secrets
import threading
import time

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from pydantic import ValidationError

from contracts.payment import Authorization, PaymentPayload, PaymentRequirements
from defender import policy
from defender.events import log, record
from defender.learner import BlueLearner
from defender.ledger import Ledger

load_dotenv()

PORT = 7003
MOCK_PAYER = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"  # Anvil account #1 (the agent)

app = Flask(__name__)
lock = threading.Lock()  # one decision at a time, so budget totals can't race
ledger = Ledger()
payees = policy.PayeeBook()
catalog = policy.CatalogCache()
STATE = {"enabled": True}
learner = BlueLearner()


def fresh_stats() -> dict:
    return {"checked": 0, "allowed": 0, "blocked": 0, "unprotected": 0, "saved_usd": 0.0,
            "by_check": {cid: 0 for cid, _ in policy.CHECKS}}


STATS = fresh_stats()


def mock_signer() -> bool:
    return os.getenv("MOCK_SIGNER", "false").strip().lower() == "true"


def signer_url() -> str:
    return os.getenv("SIGNER_URL_REAL", "http://localhost:7001").rstrip("/")


def bad_request(message: str):
    return jsonify({"error": message}), 400


def fake_payload(req: PaymentRequirements) -> dict:
    """Well-formed PaymentPayload with a random (not real) signature, for MOCK_SIGNER=true."""
    now = int(time.time())
    auth = Authorization(**{"from": os.getenv("AGENT_ADDRESS") or MOCK_PAYER},
                         to=req.payTo, value=req.amount,
                         validAfter=str(now - 600), validBefore=str(now + req.maxTimeoutSeconds),
                         nonce="0x" + secrets.token_hex(32))
    payload = PaymentPayload(accepted=req, payload={"signature": "0x" + secrets.token_hex(65),
                                                    "authorization": auth.model_dump(by_alias=True)})
    return payload.model_dump(by_alias=True)


def get_signature(body: dict, req: PaymentRequirements) -> tuple[dict, int]:
    if mock_signer():
        return fake_payload(req), 200
    try:
        r = requests.post(f"{signer_url()}/sign", json=body, timeout=30)
    except requests.RequestException as exc:
        return {"error": f"signer unreachable at {signer_url()}: {exc.__class__.__name__}"}, 502
    try:
        return r.json(), r.status_code
    except ValueError:
        return {"error": f"signer returned non-JSON (status {r.status_code})"}, 502


def event_data(req, context, decision, verdict) -> dict:
    """Everything the dashboard and attacker need about one decision.

    The checks logged here are exactly the ones the verdict was based on (run once, before
    the payment was recorded in the ledger)."""
    head, failed_ids = decision["headline"], [c["id"] for c in decision["failed"]]
    data = {
        "verdict": verdict,
        "shop": policy.shop_name(decision["origin"]), "origin": decision["origin"],
        "item": decision["path"], "resource": req.resource,
        "amount": req.amount, "amount_usd": decision["amount_usd"],
        "amount_cents": round(decision["amount_usd"] * 100, 4),
        "payee": req.payTo, "guard": "off" if verdict == "paid_unprotected" else "on",
        "check": head["id"] if head else None,
        "reason": head["reason"] if head else "all checks passed",
        "failed": failed_ids,
        "checks": [dict(c) for c in decision["checks"]], "mandate": decision["mandate"],
        "bill": req.model_dump(by_alias=True), "context": context,
    }
    if verdict == "paid_unprotected":
        data["would_have_blocked"] = failed_ids
    return data


@app.post("/sign")
def sign():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or "requirements" not in body:
        return bad_request('body must be JSON like {"requirements": {...}, "context": {...}}')
    try:
        req = PaymentRequirements.model_validate(body["requirements"])
    except ValidationError as exc:
        err = exc.errors()[0]
        return bad_request(f"invalid requirements: {err['loc']} {err['msg']}")
    if not str(req.amount).isdigit():
        return bad_request("amount must be a whole number of USDC units, e.g. \"10000\" = $0.01")
    context = body.get("context") or {}
    if not isinstance(context, dict):
        return bad_request("context must be an object")

    # Network call to the shop's /catalog happens outside the lock.
    origin = policy.shop_origin(req, context)
    price = catalog.reference_price(origin, policy.path_of(req.resource))
    arena = arena_ok()  # a training heist from the attacker engine: kept out of the live feed and stats

    with lock:
        # 1) Run every check ONCE, before this payment touches the ledger, with what the guard has
        #    learned for this kind of shop (never looser than the owner's rules).
        learned = learner.choose(origin)
        decision = policy.evaluate(req, context, ledger, payees, price, learned["tuning"])
        head, failed = decision["headline"], decision["failed"]
        guard_on = STATE["enabled"]
        if not guard_on:
            verdict = "paid_unprotected"
        else:
            verdict = "blocked" if failed else "allowed"
        data = event_data(req, context, decision, verdict)
        decision_id = secrets.token_hex(8)
        data.update(decision_id=decision_id, band=learned["band"])
        if verdict != "paid_unprotected":  # remembered so the attacker engine can label it later
            learner.remember(decision_id, {
                "verdict": verdict, "band": learned["band"], "levels": learned["levels"], "origin": origin,
                "path": decision["path"], "req": req.model_dump(), "context": context, "catalog_price": price,
                "failed": [c["id"] for c in failed]})
        if not arena:
            STATS["checked"] += 1

        if verdict == "blocked":
            if not arena:
                STATS["blocked"] += 1
                STATS["by_check"][head["id"]] += 1
                STATS["saved_usd"] = round(STATS["saved_usd"] + decision["amount_usd"], 6)
                record("blocked", data)
            resp = jsonify({"blocked": True, "check": head["id"], "reason": head["reason"],
                            "failed": data["failed"], "total_checks": len(policy.CHECKS),
                            "decision_id": decision_id, "band": learned["band"]})
            resp.status_code = 403
            resp.headers["X-Decision-Id"] = decision_id
            return resp

        # 2) Only now record the payment (reserves the spend so a parallel request can't
        #    slip past the budget). Nothing is re-checked after this point.
        if not arena:
            STATS["unprotected" if verdict == "paid_unprotected" else "allowed"] += 1
            record(verdict, data)
        if not failed:
            payees.pin(decision["origin"], req.payTo)
        entry = ledger.record(decision["mandate"]["task"], req.resource, decision["amount_usd"],
                              req.payTo, decision["origin"])

    result, status = get_signature(body, req)
    if status != 200:
        ledger.remove(entry)
        record("failed", {"resource": req.resource, "signer_status": status, "error": result.get("error")})
    resp = jsonify(result)
    resp.status_code = status
    resp.headers["X-Decision-Id"] = decision_id
    resp.headers["X-Decision-Band"] = learned["band"]
    return resp


@app.post("/preview")
def preview():
    """Run the checks on a bill WITHOUT signing, recording or counting it (the attacker's recon)."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or "requirements" not in body:
        return bad_request('body must be JSON like {"requirements": {...}, "context": {...}}')
    try:
        req = PaymentRequirements.model_validate(body["requirements"])
    except ValidationError as exc:
        err = exc.errors()[0]
        return bad_request(f"invalid requirements: {err['loc']} {err['msg']}")
    context = body.get("context") or {}
    origin = policy.shop_origin(req, context)
    price = catalog.reference_price(origin, policy.path_of(req.resource))
    with lock:
        band = learner.band(origin)
        decision = policy.evaluate(req, context, ledger, payees, price, learner.tuning(band, learner.live_levels(band)))
    head = decision["headline"]
    return jsonify({"guard_enabled": STATE["enabled"], "would_block": bool(decision["failed"]),
                    "check": head["id"] if head else None, "reason": head["reason"] if head else "all checks passed",
                    "checks": decision["checks"], "amount_usd": decision["amount_usd"]})


# --- learning: labels and training controls come only from the attacker engine (ARENA_TOKEN) ---

def arena_ok() -> bool:
    """True only if the caller sent the right ARENA_TOKEN. No token configured = closed."""
    token = os.getenv("ARENA_TOKEN", "")
    return bool(token) and hmac.compare_digest(token.encode(), request.headers.get("X-Arena-Token", "").encode())


def denied():
    return jsonify({"error": "missing or wrong ARENA_TOKEN"}), 403


@app.post("/label")
def label():
    """Ground truth for one decision: {"decision_id", "truth": "attack" | "honest"}."""
    if not arena_ok():
        return denied()
    body = request.get_json(silent=True) or {}
    if body.get("truth") not in ("attack", "honest"):
        return bad_request('truth must be "attack" or "honest"')
    out = learner.label(str(body.get("decision_id")), body["truth"])
    return jsonify(out) if out else (jsonify({"error": "unknown decision_id"}), 404)


@app.get("/learning")
def learning():
    return jsonify(learner.snapshot())


@app.post("/learning/explore")
def learning_explore():
    """Training turns exploring on (Thompson sampling) and off again (use the best safe settings)."""
    if not arena_ok():
        return denied()
    learner.exploring = bool((request.get_json(silent=True) or {}).get("enabled"))
    return jsonify({"exploring": learner.exploring})


@app.post("/learning/reset")
def learning_reset():
    if not arena_ok():
        return denied()
    learner.reset()
    return jsonify({"ok": True})


@app.post("/arena/soft-reset")
def arena_soft_reset():
    """Between training heists: forget the ledger and pinned wallets (NOT what was learned)."""
    if not arena_ok():
        return denied()
    with lock:
        ledger.reset()
        payees.reset()
    return jsonify({"ok": True})


@app.route("/mode", methods=["GET", "POST"])
def mode():
    if request.method == "POST":
        enabled = (request.get_json(silent=True) or {}).get("enabled")
        if not isinstance(enabled, bool):
            return bad_request('body must be {"enabled": true} or {"enabled": false}')
        STATE["enabled"] = enabled
    return jsonify({"enabled": STATE["enabled"]})


@app.route("/rules", methods=["GET", "POST"])
def rules():
    """The owner's rules (limits, trusted shops, blocked wallets, checks on/off). POST edits them."""
    if request.method == "POST":
        body = request.get_json(silent=True)
        if body == {"reset": True}:
            policy.RULES.clear()
            policy.RULES.update(policy.default_rules())
        else:
            try:
                with lock:
                    policy.update_rules(body or {})
            except (ValueError, TypeError) as exc:
                return bad_request(str(exc))
    return jsonify({**policy.RULES, "check_names": dict(policy.CHECKS)})


@app.get("/stats")
def stats():
    with lock:
        return jsonify({**STATS, "by_check": dict(STATS["by_check"]), "enabled": STATE["enabled"]})


@app.get("/events")
def events():
    limit = min(max(request.args.get("limit", 100, type=int), 1), 1000)
    return jsonify([e.model_dump() for e in log.last(limit)])


def reset_state() -> None:
    with lock:
        ledger.reset()
        payees.reset()
        catalog.reset()
        log.clear()
        STATS.clear()
        STATS.update(fresh_stats())
        STATE["enabled"] = True


@app.post("/reset")
def reset():
    reset_state()
    return jsonify({"ok": True})


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "defender", "enabled": STATE["enabled"],
                    "mock_signer": mock_signer()})


def run() -> None:
    app.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    run()

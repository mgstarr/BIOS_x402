"""Signer Flask server: exposes POST /sign, GET /address, and GET /events."""

import os

from dotenv import load_dotenv
from eth_account import Account
from flask import Flask, jsonify, request
from pydantic import ValidationError

from contracts.events import Event, EventLog
from contracts.payment import PaymentRequirements
from signer.signer import sign_requirements

load_dotenv()

app = Flask(__name__)
log = EventLog()


def load_account():
    key = os.getenv("AGENT_PRIVATE_KEY")
    if not key:
        raise SystemExit("AGENT_PRIVATE_KEY is not set in .env")
    return Account.from_key(key)


ACCOUNT = load_account()


def error(message: str, code: int = 400):
    log.append(Event(service="signer", type="failed", data={"error": message}))
    return jsonify({"error": message}), code


@app.post("/sign")
def sign():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or "requirements" not in body:
        return error('body must be JSON like {"requirements": {...}}')
    try:
        req = PaymentRequirements.model_validate(body["requirements"])
        payload = sign_requirements(req, ACCOUNT)
    except ValidationError as exc:
        return error(f"invalid requirements: {exc.errors()[0]['loc']} {exc.errors()[0]['msg']}")
    except (ValueError, KeyError) as exc:
        return error(f"cannot sign these requirements: {exc}")
    auth = payload.payload["authorization"]
    log.append(Event(service="signer", type="signed", data={
        "to": auth["to"], "value": auth["value"], "nonce": auth["nonce"], "resource": req.resource,
    }))
    return jsonify(payload.model_dump(by_alias=True))


@app.get("/address")
def address():
    return jsonify({"address": ACCOUNT.address})


@app.get("/events")
def events():
    return jsonify([e.model_dump() for e in log.last(100)])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7001)

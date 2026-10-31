"""Facilitator Flask server: exposes POST /verify, POST /settle, and GET /events."""

import threading

from eth_account import Account
from eth_account.messages import encode_typed_data
from flask import Flask, jsonify, request
from pydantic import ValidationError

from chain import config
from contracts.events import Event, EventLog
from contracts.payment import (
    Authorization,
    PaymentPayload,
    PaymentRequirements,
    SettlementResponse,
    VerifyResponse,
)
from payment import settlement
from signer.signer import build_typed_data

app = Flask(__name__)
log = EventLog()
settle_lock = threading.Lock()


def same(a: str, b: str) -> bool:
    return a.lower() == b.lower()


def parse_body(body) -> tuple[PaymentPayload, PaymentRequirements, Authorization, str]:
    if not isinstance(body, dict) or "paymentPayload" not in body or "paymentRequirements" not in body:
        raise ValueError('body must be JSON like {"paymentPayload": {...}, "paymentRequirements": {...}}')
    try:
        payload = PaymentPayload.model_validate(body["paymentPayload"])
        req = PaymentRequirements.model_validate(body["paymentRequirements"])
        auth = Authorization.model_validate(payload.payload["authorization"])
        signature = payload.payload["signature"]
    except (ValidationError, KeyError, TypeError) as exc:
        raise ValueError(f"malformed payment payload or requirements: {exc}") from exc
    return payload, req, auth, signature


def signer_matches(auth: Authorization, req: PaymentRequirements, signature: str) -> bool:
    try:
        message = encode_typed_data(full_message=build_typed_data(auth, req))
        return same(Account.recover_message(message, signature=signature), auth.from_)
    except Exception:
        return False


def check(payload: PaymentPayload, req: PaymentRequirements, auth: Authorization, signature: str) -> str | None:
    """Return the first failing reason code, or None when the payment is valid."""
    if req.network != config.NETWORK or payload.accepted.network != req.network:
        return "bad_network"
    if not same(req.asset, config.USDC_ADDRESS) or not same(payload.accepted.asset, req.asset):
        return "bad_asset"
    if not signer_matches(auth, req, signature):
        return "bad_signature"
    if not same(auth.to, req.payTo):
        return "wrong_payee"
    if int(auth.value) != int(req.amount):
        return "wrong_amount"
    now = settlement.latest_timestamp()
    if now < int(auth.validAfter):
        return "not_yet_valid"
    if now >= int(auth.validBefore):
        return "expired"
    if settlement.auth_used(auth.from_, auth.nonce):
        return "nonce_used"
    if settlement.balance_of(auth.from_) < int(auth.value):
        return "insufficient_funds"
    return None


def verify(payload, req, auth, signature) -> VerifyResponse:
    reason = check(payload, req, auth, signature)
    if reason:
        log.append(Event(service="facilitator", type="failed", data={
            "stage": "verify", "reason": reason, "payer": auth.from_, "nonce": auth.nonce}))
        return VerifyResponse(isValid=False, invalidReason=reason, payer=auth.from_)
    log.append(Event(service="facilitator", type="verified", data={
        "payer": auth.from_, "to": auth.to, "value": auth.value, "nonce": auth.nonce}))
    return VerifyResponse(isValid=True, payer=auth.from_)


def bad_request(message: str):
    return jsonify({"error": message}), 400


def chain_down(exc: Exception):
    return jsonify({"error": f"chain unreachable or failed: {exc}"}), 502


@app.post("/verify")
def verify_route():
    try:
        payload, req, auth, signature = parse_body(request.get_json(silent=True))
        return jsonify(verify(payload, req, auth, signature).model_dump())
    except ValueError as exc:
        return bad_request(str(exc))
    except Exception as exc:
        return chain_down(exc)


@app.post("/settle")
def settle_route():
    try:
        payload, req, auth, signature = parse_body(request.get_json(silent=True))
    except ValueError as exc:
        return bad_request(str(exc))
    with settle_lock:
        try:
            result = verify(payload, req, auth, signature)
            if not result.isValid:
                return jsonify(SettlementResponse(
                    success=False, payer=auth.from_, errorReason=result.invalidReason).model_dump())
            tx = settlement.send_transfer_with_authorization(auth.model_dump(by_alias=True), signature)
        except Exception as exc:
            reason = f"settle_failed: {exc}"
            log.append(Event(service="facilitator", type="failed", data={
                "stage": "settle", "reason": reason, "nonce": auth.nonce}))
            return jsonify(SettlementResponse(success=False, payer=auth.from_, errorReason=reason).model_dump())
    log.append(Event(service="facilitator", type="settled", data={
        "transaction": tx, "payer": auth.from_, "to": auth.to, "value": auth.value}))
    return jsonify(SettlementResponse(success=True, transaction=tx, payer=auth.from_).model_dump())


@app.get("/events")
def events():
    return jsonify([e.model_dump() for e in log.last(100)])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7002)

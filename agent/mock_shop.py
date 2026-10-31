"""Mock shop (testing only): returns real x402 bills and accepts any well-formed payment. No chain."""

import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request

from contracts.payment import (
    PAYMENT_REQUIRED,
    PAYMENT_RESPONSE,
    PAYMENT_SIGNATURE,
    PaymentPayload,
    PaymentRequired,
    PaymentRequirements,
    SettlementResponse,
    decode_header,
    encode_header,
)

load_dotenv()
USDC_ADDRESS = os.getenv("USDC_ADDRESS", "0x036CbD53842c5426634e7929541eC2318f3dCF7e")
PAY_TO = os.getenv("SHOP_PAY_TO") or "0x000000000000000000000000000000000000bEEF"

app = Flask(__name__)
mock_tx_count = 0


def paid(amount, data):
    """No signature -> 402 bill. Well-formed signature for this bill -> 200 data + mock receipt."""
    global mock_tx_count
    req = PaymentRequirements(amount=amount, asset=USDC_ADDRESS, payTo=PAY_TO, resource=request.url)
    sig = request.headers.get(PAYMENT_SIGNATURE)
    if not sig:
        bill = PaymentRequired(accepts=[req])
        return jsonify(bill.model_dump()), 402, {PAYMENT_REQUIRED: encode_header(bill)}

    try:
        payload = decode_header(sig, PaymentPayload)
        auth = payload.payload["authorization"]
        ok = auth["value"] == amount and auth["to"] == PAY_TO and payload.payload["signature"]
    except Exception as e:
        return jsonify({"error": f"malformed payment: {e}"}), 402
    if not ok:
        return jsonify({"error": "payment does not match the bill"}), 402

    mock_tx_count += 1
    receipt = SettlementResponse(success=True, transaction=f"MOCK-not-on-chain-{mock_tx_count}", payer=auth["from"])
    return jsonify(data), 200, {PAYMENT_RESPONSE: encode_header(receipt)}


@app.get("/catalog")
def catalog():
    return jsonify([
        {"path": "/api/price?symbol=ETH", "price_usd": 0.01, "description": "Latest USD price of a crypto asset"},
        {"path": "/api/sentiment?symbol=ETH", "price_usd": 0.02, "description": "Market sentiment of a crypto asset"},
    ])


@app.get("/api/price")
def price():
    symbol = request.args.get("symbol", "ETH").upper()
    return paid("10000", {"symbol": symbol, "price_usd": 3120.55})


@app.get("/api/sentiment")
def sentiment():
    symbol = request.args.get("symbol", "ETH").upper()
    return paid("20000", {"symbol": symbol, "sentiment": "bullish", "score": 0.72})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)

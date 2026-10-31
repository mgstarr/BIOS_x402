"""Shop Flask server: a market data API whose price endpoint is x402-gated."""

import json
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, request

from shops.honest import events, paid

DATA_FILE = Path(__file__).with_name("data.json")
MARKET_DATA = json.loads(DATA_FILE.read_text(encoding="utf-8"))

app = Flask(__name__)


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.get("/events")
def list_events():
    return jsonify([e.model_dump() for e in events.last(100)])


def known_symbol(view):
    """Reject unknown symbols BEFORE billing, so nobody pays for a 404."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        symbol = request.args.get("symbol", "").upper()
        if symbol not in MARKET_DATA["prices"]:
            return jsonify({
                "error": "unknown_symbol",
                "supported": sorted(MARKET_DATA["prices"]),
            }), 404
        return view(*args, **kwargs)

    return wrapper


@app.get("/api/price")
@known_symbol
@paid(0.01, description="Market price snapshot for one symbol")
def price():
    symbol = request.args["symbol"].upper()
    return jsonify({**MARKET_DATA["prices"][symbol], "as_of": MARKET_DATA["as_of"]})


def run() -> None:
    app.run(host="0.0.0.0", port=5001)

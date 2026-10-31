"""Shop Flask server: a market data API whose /api/* endpoints are x402-gated."""

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


# One place for every paid product: the @paid decorators and GET /catalog both read this,
# so the advertised price can never drift from the price we actually charge.
PRODUCTS = {
    "/api/price": (0.01, "Market price snapshot for one symbol"),
    "/api/sentiment": (0.02, "Social sentiment score and topics for one symbol (24h)"),
    "/api/report": (0.05, "Analyst-style report: summary, key levels, metrics, risks"),
}


@app.get("/catalog")
def catalog():
    """Free menu of what we sell, so a buyer can see prices before paying."""
    base = request.host_url.rstrip("/")
    return jsonify([
        {
            "path": path,
            "description": description,
            "price_usd": price_usd,
            "example_url": f"{base}{path}?symbol=ETH",
        }
        for path, (price_usd, description) in PRODUCTS.items()
    ])


def paid_product(path: str):
    price_usd, description = PRODUCTS[path]
    return paid(price_usd, description=description)


@app.get("/api/price")
@known_symbol
@paid_product("/api/price")
def price():
    symbol = request.args["symbol"].upper()
    return jsonify({**MARKET_DATA["prices"][symbol], "as_of": MARKET_DATA["as_of"]})


@app.get("/api/sentiment")
@known_symbol
@paid_product("/api/sentiment")
def sentiment():
    symbol = request.args["symbol"].upper()
    return jsonify({**MARKET_DATA["sentiment"][symbol], "as_of": MARKET_DATA["as_of"]})


@app.get("/api/report")
@known_symbol
@paid_product("/api/report")
def report():
    symbol = request.args["symbol"].upper()
    return jsonify({**MARKET_DATA["reports"][symbol], "as_of": MARKET_DATA["as_of"]})


def run() -> None:
    app.run(host="0.0.0.0", port=5001)

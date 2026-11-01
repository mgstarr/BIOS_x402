"""NewShop: a small HONEST shop nobody has paid before (port 5003).

It charges 2 cents for a coin price (twice TickToll's 1 cent) to its own honest wallet. A guard that is
too strict about new shops would wrongly block it, which is exactly why it exists: honest pressure
for the guard's learning. Run: python app.py newshop
"""

import json
import os
from pathlib import Path

from flask import Flask, jsonify, request

from shops.honest import paid

# Anvil default account #5 -- NOT a real wallet. Its own honest wallet, different from TickToll's.
NEWSHOP_PAY_TO = os.getenv("NEWSHOP_PAY_TO", "0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc")
PRICE_USD = 0.02
MARKET = json.loads(Path(__file__).with_name("data.json").read_text(encoding="utf-8"))

app = Flask(__name__)


@app.get("/api/price")
@paid(PRICE_USD, pay_to=NEWSHOP_PAY_TO, description="Coin price from a brand-new honest shop")
def price():
    symbol = request.args.get("symbol", "ETH").upper()
    symbol = symbol if symbol in MARKET["prices"] else "ETH"
    return jsonify({**MARKET["prices"][symbol], "as_of": MARKET["as_of"]})


@app.get("/catalog")
def catalog():
    return jsonify([{"path": "/api/price", "price_usd": PRICE_USD, "pay_to": NEWSHOP_PAY_TO,
                     "description": "Coin price (honest new shop)"}])


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "newshop"})


def run() -> None:
    app.run(host="0.0.0.0", port=5003)

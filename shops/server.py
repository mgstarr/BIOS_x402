"""Shop Flask server: a market data API whose /api/* endpoints are x402-gated."""

import json
import secrets
import time
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from contracts.payment import (
    PaymentPayload,
    PaymentRequired,
    SettlementResponse,
    decode_header,
    encode_header,
)
from shops.honest import (
    HEADER_PAYMENT_REQUIRED,
    HEADER_PAYMENT_RESPONSE,
    HEADER_PAYMENT_SIGNATURE,
    NETWORK,
    USDC_DECIMALS,
    build_requirements,
    events,
    mock_facilitator_enabled,
    paid,
    pay_to_address,
    pay_to_problem,
    usd_to_atomic,
)
from shops.try_pay import build_fake_payment

DATA_FILE = Path(__file__).with_name("data.json")
MARKET_DATA = json.loads(DATA_FILE.read_text(encoding="utf-8"))

app = Flask(__name__)
# Re-read an HTML template when its file changes, so pages never mix an old cached
# template with new JS from static/ (that mismatch broke the Live page once).
app.config["TEMPLATES_AUTO_RELOAD"] = True


@app.get("/favicon.ico")
def favicon():
    """Browsers ask for /favicon.ico on their own; answer with our SVG badge instead of 404."""
    return app.send_static_file("favicon.svg")


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.get("/events")
def list_events():
    """Last N events (default 100, max 1000). The Live page asks for more so it can
    group a purchase's events into one row without losing the start of it."""
    limit = min(max(request.args.get("limit", 100, type=int), 1), 1000)
    return jsonify([e.model_dump() for e in events.last(limit)])


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


def catalog_items() -> list[dict]:
    """The menu as a list of dicts. Used by GET /catalog AND by the website's pricing cards."""
    base = request.host_url.rstrip("/")
    return [
        {
            "path": path,
            "description": description,
            "price_usd": price_usd,
            "example_url": f"{base}{path}?symbol=ETH",
        }
        for path, (price_usd, description) in PRODUCTS.items()
    ]


@app.get("/catalog")
def catalog():
    """Free menu of what we sell, so a buyer can see prices before paying."""
    return jsonify(catalog_items())


@app.get("/status")
def status():
    """Small, free status endpoint for the website: mock mode flag + running totals.

    Totals are counted over the WHOLE event log (not just the last 100 that /events shows),
    so the dashboard counters stay correct during a long demo.
    """
    all_events = events.last(10**9)  # "everything" (EventLog has no all() method)
    count = lambda t: sum(1 for e in all_events if e.type == t)  # noqa: E731
    return jsonify({
        "service": "shop",
        "mock_facilitator": mock_facilitator_enabled(),
        "network": NETWORK,
        "pay_to": pay_to_address(),
        "pay_to_problem": pay_to_problem(),  # None, "empty" or "invalid"
        "server_time": time.time(),  # lets the browser age bills by the SERVER's clock
        "totals": {
            "requests": count("request"),
            "payment_required": count("payment_required"),
            "settled": count("settled"),
            "failed": count("failed"),
            "earned_usd": round(sum(e.data.get("amount_usd") or 0
                                    for e in all_events if e.type == "settled"), 6),
        },
    })


# --- Website pages (HTML). The JSON routes above and below are unchanged. ----------------

BRAND = "TickToll"
SYMBOLS = sorted(MARKET_DATA["prices"])

# Plain-English names used ONLY by the website. The API, /catalog and the paywall don't
# read these, so renaming a product here can never change what we charge.
PRODUCT_INFO = {
    "/api/price": {"name": "Coin price", "blurb": "Today's price of a cryptocurrency"},
    "/api/sentiment": {"name": "Market mood",
                       "blurb": "Are people online feeling positive or negative about it today?"},
    "/api/report": {"name": "Analyst report",
                    "blurb": "A short expert-style summary: key numbers and risks"},
}
COIN_NAMES = {"BTC": "Bitcoin", "ETH": "Ether", "SOL": "Solana"}  # display order too


def plain_products() -> list[dict]:
    """Products for the website: plain name, one-line blurb, price in cents (from PRODUCTS)."""
    return [
        {"path": path, "cents": round(price_usd * 100), **PRODUCT_INFO[path]}
        for path, (price_usd, _description) in PRODUCTS.items()
    ]


def plain_coins() -> list[dict]:
    return [{"symbol": sym, "name": name, "label": f"{name} ({sym})"}
            for sym, name in COIN_NAMES.items() if sym in MARKET_DATA["prices"]]


@app.context_processor
def site_globals():
    products, coins = plain_products(), plain_coins()
    return {
        "brand": BRAND,
        "mock_mode": mock_facilitator_enabled(),
        "symbols": SYMBOLS,
        "pay_to": pay_to_address(),
        "pay_to_problem": pay_to_problem(),
        "products_plain": products,
        "coins": coins,
        # Same names for the page JavaScript (window.SITE), so pages and JS never disagree.
        "site_info": {
            "brand": BRAND,
            "mockMode": mock_facilitator_enabled(),
            "products": {p["path"]: {"name": p["name"], "cents": p["cents"]} for p in products},
            "coins": {c["symbol"]: c["name"] for c in coins},
        },
    }


@app.get("/")
def home():
    return render_template("index.html")


def pretty(model_or_dict) -> str:
    data = model_or_dict.model_dump(by_alias=True) if hasattr(model_or_dict, "model_dump") else model_or_dict
    return json.dumps(data, indent=2)


@app.get("/docs")
def docs():
    """Server-renders example bill/payment/receipt using the SAME code the paywall uses.

    Building the bill here does not go through the paid route, so opening the docs page
    does not add fake 402s to the live dashboard. The page's "Fetch live" button does a
    real HTTP call to /api/price for anyone who wants proof.
    """
    url = f"{request.host_url.rstrip('/')}/api/price?symbol=ETH"
    price_usd, description = PRODUCTS["/api/price"]
    with app.test_request_context(url):
        requirements = build_requirements(price_usd, description=description)
    bill = PaymentRequired(accepts=[requirements])

    now = int(time.time())
    example_payment = PaymentPayload(
        accepted=requirements,
        payload={
            "signature": "0x" + "ab" * 65,  # placeholder: 65-byte EIP-712 signature
            "authorization": {
                "from": "0xBuyerWalletAddress000000000000000000000000",
                "to": requirements.payTo,
                "value": requirements.amount,
                "validAfter": str(now - 60),
                "validBefore": str(now + requirements.maxTimeoutSeconds),
                "nonce": "0x" + secrets.token_hex(32),
            },
        },
    )
    example_receipt = SettlementResponse(
        success=True,
        transaction="0xmock" + "0" * 60,
        network=NETWORK,
        payer="0xBuyerWalletAddress000000000000000000000000",
    )
    units = [
        {"path": path, "price_usd": p, "atomic": usd_to_atomic(p)}
        for path, (p, _d) in PRODUCTS.items()
    ]
    return render_template(
        "docs.html",
        live_url=url,
        bill_json=pretty(bill),
        bill_b64=encode_header(bill),
        payment_json=pretty(example_payment),
        receipt_json=pretty(example_receipt),
        units=units,
        decimals=USDC_DECIMALS,
        h_required=HEADER_PAYMENT_REQUIRED,
        h_signature=HEADER_PAYMENT_SIGNATURE,
        h_response=HEADER_PAYMENT_RESPONSE,
    )


@app.get("/live")
def live():
    return render_template("live.html")


def run_purchase(path: str, symbol: str, value: str | None = None, to: str | None = None):
    """Run the WHOLE x402 flow from the server, through the real @paid paywall.

    Uses Flask's in-process test client, so these are normal requests to our own routes:
    the events, counters and Live page see them exactly like any other buyer.
      1) GET without payment -> 402 + bill
      2) build a dummy-signed PaymentPayload (same code as try_pay.py). value / to, if given,
         tamper with the SIGNED authorization only (used by the attack buttons).
      3) retry with PAYMENT-SIGNATURE -> 200 + receipt, or 402 + the rejection reason
    """
    client = app.test_client()
    base = request.host_url  # same host as the browser, so the bill's "resource" URL matches
    url = f"{path}?symbol={symbol}"
    full_url = base.rstrip("/") + url
    steps = []

    first = client.get(url, base_url=base)
    bill_b64 = first.headers.get(HEADER_PAYMENT_REQUIRED, "")
    steps.append({"step": "bill", "status": first.status_code, "header": bill_b64,
                  "body": first.get_json(silent=True)})
    if first.status_code != 402 or not bill_b64:
        return {"ok": False, "url": full_url, "steps": steps}

    bill = decode_header(bill_b64, PaymentRequired)
    payment = build_fake_payment(bill, value=value, to=to)
    payment_b64 = encode_header(payment)
    steps.append({"step": "payment", "header": payment_b64,
                  "payload": payment.model_dump(by_alias=True)})

    second = client.get(url, base_url=base, headers={HEADER_PAYMENT_SIGNATURE: payment_b64})
    receipt_b64 = second.headers.get(HEADER_PAYMENT_RESPONSE)
    receipt = decode_header(receipt_b64, SettlementResponse).model_dump() if receipt_b64 else None
    body = second.get_json(silent=True) or {}
    steps.append({"step": "paid", "status": second.status_code, "header": receipt_b64,
                  "receipt": receipt, "body": body})
    return {"ok": second.status_code == 200, "url": full_url, "steps": steps,
            "bill": bill.accepts[0].model_dump(by_alias=True),
            "status": second.status_code, "error": body.get("error")}


@app.post("/try/simulate")
def simulate_paid_call():
    """Home page "Simulate paid call (mock)": an honest purchase, run server-side.

    Only allowed with MOCK_FACILITATOR=true: a real facilitator rejects dummy signatures.
    """
    if not mock_facilitator_enabled():
        return jsonify({"error": "mock_mode_only"}), 403

    body = request.get_json(silent=True) or {}
    path, symbol = body.get("path"), str(body.get("symbol", "")).upper()
    if path not in PRODUCTS or symbol not in MARKET_DATA["prices"]:
        return jsonify({"error": "unknown_path_or_symbol"}), 400
    return jsonify(run_purchase(path, symbol))


# Docs page "attack" buttons: a cheating buyer copies our bill honestly into "accepted",
# but signs an authorization that moves less money, or sends it to another wallet.
ATTACKER_WALLET = "0x000000000000000000000000000000000000bEEF"  # obviously fake, not ours
ATTACKS = {
    "underpay": {"value": "1"},  # 1 atomic unit = $0.000001 instead of $0.01
    "wrong_wallet": {"to": ATTACKER_WALLET},
}


@app.post("/try/attack")
def try_attack():
    """Send a tampered payment (default: /api/price?symbol=ETH) through the normal paywall.

    It must be rejected (402 + reason) and logged as a "failed" event. Works in mock mode
    and with the real facilitator: our own terms check rejects it before any money moves.
    """
    body = request.get_json(silent=True) or {}
    kind = body.get("kind")
    if kind not in ATTACKS:
        return jsonify({"error": "unknown_attack", "supported": sorted(ATTACKS)}), 400
    # Optional product + coin (the Home page lets you choose); default: Coin price of ETH.
    path = body.get("path") or "/api/price"
    symbol = str(body.get("symbol") or "ETH").upper()
    if path not in PRODUCTS or symbol not in MARKET_DATA["prices"]:
        return jsonify({"error": "unknown_path_or_symbol"}), 400
    result = run_purchase(path, symbol, **ATTACKS[kind])
    result["kind"] = kind
    result["rejected"] = result.get("status") == 402
    return jsonify(result)


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

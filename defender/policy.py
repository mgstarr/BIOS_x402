"""Policy engine: decides whether a proposed payment is allowed under current rules.

Plain code only, no LLM. Every check returns (ok, plain-English reason). They run in this
order and the FIRST failure is the one reported as the block reason:

  origin -> asset -> payee -> cap -> price -> mandate -> expiry -> duplicate
"""

import re
import threading
import time

import requests

from chain.config import USDC_ADDRESS
from contracts.payment import PaymentRequirements
from defender.ledger import Ledger
from defender.mandate import check_mandate, money, parse_mandate
from defender.origin import check_origin, origin_of, path_of

USDC_DECIMALS = 6
NETWORK = "eip155:84532"
DRAIN_CAP_USD = 0.50
PRICE_MULTIPLIER = 3
MAX_TIMEOUT_S = 300
DUPLICATE_WINDOW_S = 60
CATALOG_TTL_S = 60

TICKTOLL_ORIGIN = "http://localhost:5001"
TICKTOLL_PAY_TO = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
TRUSTED_PAYEES = {TICKTOLL_ORIGIN: TICKTOLL_PAY_TO}
SHOP_NAMES = {TICKTOLL_ORIGIN: "TickToll", "http://localhost:5002": "EvilData"}

# (id used in API/stats, label shown on the dashboard -- matches Person B's names)
CHECKS = [
    ("origin", "origin"),
    ("asset", "asset"),
    ("payee", "payee"),
    ("cap", "drain cap"),
    ("price", "price"),
    ("mandate", "mandate"),
    ("expiry", "expiry"),
    ("duplicate", "duplicate"),
]

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def to_usd(amount: str | int) -> float:
    return int(amount) / 10**USDC_DECIMALS


def short(addr: str) -> str:
    return f"{addr[:6]}...{addr[-4:]}" if addr and len(addr) > 12 else str(addr)


def shop_origin(req: PaymentRequirements, context: dict) -> str | None:
    """The site we are paying: where the agent actually landed, else the bill's own resource."""
    return origin_of(context.get("final_url")) or origin_of(req.resource)


def shop_name(origin: str | None) -> str:
    return SHOP_NAMES.get(origin or "", origin or "unknown shop")


# --- payee memory -------------------------------------------------------------------------

def lookalike_of(pay_to: str, known: set[str]) -> str | None:
    """A known wallet that shares the first 8 and last 6 hex chars with pay_to but isn't it."""
    a = pay_to.lower().removeprefix("0x")
    for wallet in known:
        k = wallet.lower().removeprefix("0x")
        if a != k and a[:8] == k[:8] and a[-6:] == k[-6:]:
            return wallet
    return None


class PayeeBook:
    """Known wallets: fixed ones for trusted shops, plus the first payTo seen for any other shop."""

    def __init__(self, trusted: dict[str, str] | None = None) -> None:
        self.trusted = dict(TRUSTED_PAYEES if trusted is None else trusted)
        self.pinned: dict[str, str] = {}

    def expected(self, origin: str | None) -> str | None:
        return self.trusted.get(origin) or self.pinned.get(origin)

    def known_wallets(self) -> set[str]:
        return set(self.trusted.values()) | set(self.pinned.values())

    def pin(self, origin: str | None, pay_to: str) -> None:
        if origin and origin not in self.trusted and origin not in self.pinned:
            self.pinned[origin] = pay_to

    def reset(self) -> None:
        self.pinned.clear()


# --- catalog prices -----------------------------------------------------------------------

def fetch_catalog(origin: str) -> dict[str, float]:
    """GET <origin>/catalog -> {path: price_usd}. Accepts a list, or a dict with 'items'."""
    r = requests.get(f"{origin}/catalog", timeout=2)
    r.raise_for_status()
    data = r.json()
    items = data.get("items", []) if isinstance(data, dict) else data
    prices = {}
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and isinstance(item.get("price_usd"), (int, float)):
            prices[str(item.get("path"))] = float(item["price_usd"])
    return prices


class CatalogCache:
    """Per-origin catalog prices, cached for CATALOG_TTL_S (failures are cached too)."""

    def __init__(self, fetch=fetch_catalog, ttl_s: float = CATALOG_TTL_S) -> None:
        self.fetch, self.ttl_s = fetch, ttl_s
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict | None]] = {}

    def price(self, origin: str | None, path: str) -> float | None:
        if not origin:
            return None
        with self._lock:
            hit = self._cache.get(origin)
        if hit is None or time.time() - hit[0] > self.ttl_s:
            try:
                prices = self.fetch(origin)
            except Exception:  # noqa: BLE001 - shop down / not JSON -> price check is skipped
                prices = None
            hit = (time.time(), prices)
            with self._lock:
                self._cache[origin] = hit
        return None if hit[1] is None else hit[1].get(path)

    def reset(self) -> None:
        with self._lock:
            self._cache.clear()


# --- the checks ---------------------------------------------------------------------------

def check_asset(req: PaymentRequirements) -> tuple[bool, str]:
    if req.asset.lower() != USDC_ADDRESS.lower():
        return False, f"The bill wants to be paid in token {short(req.asset)}, which is not real USDC"
    if req.network != NETWORK:
        return False, f"The bill is for network {req.network}, not our test network {NETWORK}"
    return True, "real USDC on the test network"


def check_payee(origin: str | None, pay_to: str, book: PayeeBook) -> tuple[bool, str]:
    if not ADDRESS_RE.match(pay_to or ""):
        return False, f"'{pay_to}' is not a valid wallet address"
    like = lookalike_of(pay_to, book.known_wallets())
    if like:
        return False, f"Wallet {pay_to} only looks like the known wallet {like} (lookalike address)"
    expected = book.expected(origin)
    if expected is None:
        return True, f"new shop: remembering its wallet {short(pay_to)}"
    if pay_to.lower() != expected.lower():
        if origin in book.trusted:
            return False, f"{shop_name(origin)} is paid at {short(expected)}, but this bill pays {short(pay_to)}"
        return False, f"This shop first asked to be paid at {short(expected)}, now it wants {short(pay_to)}"
    return True, f"matches the known wallet {short(expected)}"


def check_cap(amount_usd: float) -> tuple[bool, str]:
    if amount_usd > DRAIN_CAP_USD + 1e-9:
        return False, f"This bill is {money(amount_usd)}, more than the {money(DRAIN_CAP_USD)} safety cap"
    return True, f"{money(amount_usd)} is under the {money(DRAIN_CAP_USD)} cap"


def ratio_text(r: float) -> str:
    return f"{r:.0f}x" if r >= 10 else f"{r:.1f}".rstrip("0").rstrip(".") + "x"


def check_price(amount_usd: float, catalog_price: float | None) -> tuple[bool, str]:
    if catalog_price is None:
        return True, "skipped: no catalog price for this item"
    if catalog_price <= 0:
        if amount_usd > 0:
            return False, f"This item is listed as free, but the bill asks for {money(amount_usd)}"
        return True, "free item"
    r = amount_usd / catalog_price
    if amount_usd > PRICE_MULTIPLIER * catalog_price + 1e-9:
        return False, f"Price is {ratio_text(r)} the usual {money(catalog_price)}"
    return True, f"{money(amount_usd)} vs usual {money(catalog_price)}"


def check_expiry(seconds: int) -> tuple[bool, str]:
    if seconds <= 0:
        return False, f"The bill's time limit ({seconds} s) makes no sense"
    if seconds > MAX_TIMEOUT_S:
        return False, (f"The payment would stay valid for {seconds:,} seconds; "
                       f"we allow at most {MAX_TIMEOUT_S} (5 minutes)")
    return True, f"valid for {seconds} s"


def check_duplicate(resource: str, ledger: Ledger) -> tuple[bool, str]:
    prev = ledger.paid_recently(resource, DUPLICATE_WINDOW_S)
    if prev:
        ago = max(0, round(time.time() - prev["ts"]))
        return False, f"We already paid for this exact item {ago} seconds ago"
    return True, "not paid for recently"


def evaluate(req: PaymentRequirements, context: dict, ledger: Ledger, book: PayeeBook,
             catalog_price: float | None) -> dict:
    """Run every check (so the dashboard can show them all); the first failure decides."""
    origin = shop_origin(req, context)
    amount_usd = to_usd(req.amount)
    mandate = parse_mandate(context)
    results = {
        "origin": check_origin(context.get("intended_url"), context.get("final_url")),
        "asset": check_asset(req),
        "payee": check_payee(origin, req.payTo, book),
        "cap": check_cap(amount_usd),
        "price": check_price(amount_usd, catalog_price),
        "mandate": check_mandate(amount_usd, mandate, ledger.spent(mandate["task"])),
        "expiry": check_expiry(req.maxTimeoutSeconds),
        "duplicate": check_duplicate(req.resource, ledger),
    }
    checks = [{"id": cid, "name": name, "ok": results[cid][0], "reason": results[cid][1]}
              for cid, name in CHECKS]
    failed = next((c for c in checks if not c["ok"]), None)
    return {"checks": checks, "failed": failed, "origin": origin, "path": path_of(req.resource),
            "amount_usd": amount_usd, "mandate": mandate}

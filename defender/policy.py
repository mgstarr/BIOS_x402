"""Policy engine: decides whether a proposed payment is allowed under current rules.

Plain code only, no LLM. Every check returns (ok, plain-English reason). All checks run once,
before anything is recorded; the headline reason is the first failure in this priority order:

  origin -> asset -> payee/lookalike -> price -> mandate -> spending cap -> expiry -> duplicate
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
REFERENCE_ITEM = "/api/price"  # TickToll's basic coin price: the yardstick for unknown items
SHOP_NAMES = {TICKTOLL_ORIGIN: "TickToll", "http://localhost:5002": "EvilData"}

# (id used in API/stats, label shown on the dashboard), in headline-priority order.
CHECKS = [
    ("origin", "origin"),
    ("asset", "asset"),
    ("payee", "payee"),
    ("price", "price"),
    ("mandate", "mandate"),
    ("cap", "spending cap"),
    ("expiry", "expiry"),
    ("duplicate", "duplicate"),
]

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


# --- owner rules ----------------------------------------------------------------------------
# What the wallet's owner allows. The Guard page edits these via GET/POST /rules. The defaults
# are the plan's values. A task's own mandate can only make the limits stricter, never looser.

def default_rules() -> dict:
    return {
        "checks": {cid: True for cid, _ in CHECKS},  # owner can switch single checks off
        "max_per_payment_usd": 0.10,
        "budget_usd": 0.50,
        "cap_usd": DRAIN_CAP_USD,
        "price_multiplier": PRICE_MULTIPLIER,
        "max_timeout_s": MAX_TIMEOUT_S,
        "duplicate_window_s": DUPLICATE_WINDOW_S,
        "known_shops_only": False,  # True: never pay a shop that isn't in trusted_payees
        "trusted_payees": dict(TRUSTED_PAYEES),
        "blocked_wallets": [],
    }


RULES = default_rules()
NUMBER_RULES = ("max_per_payment_usd", "budget_usd", "cap_usd", "price_multiplier",
                "max_timeout_s", "duplicate_window_s")


def update_rules(new: dict) -> dict:
    """Validate and apply the owner's edits. Raises ValueError with a plain message."""
    rules = {**RULES, "checks": dict(RULES["checks"])}
    for key in NUMBER_RULES:
        if key in new:
            value = float(new[key])
            if value <= 0:
                raise ValueError(f"{key} must be more than 0")
            rules[key] = value
    for cid, on in (new.get("checks") or {}).items():
        if cid in rules["checks"]:
            rules["checks"][cid] = bool(on)
    if "known_shops_only" in new:
        rules["known_shops_only"] = bool(new["known_shops_only"])
    if "trusted_payees" in new:
        payees = {origin_of(o): w for o, w in dict(new["trusted_payees"]).items()}
        if None in payees or not all(ADDRESS_RE.match(w or "") for w in payees.values()):
            raise ValueError("each trusted shop needs a URL like http://localhost:5001 and a 0x wallet")
        rules["trusted_payees"] = payees
    if "blocked_wallets" in new:
        wallets = [w.strip() for w in new["blocked_wallets"] if w.strip()]
        if not all(ADDRESS_RE.match(w) for w in wallets):
            raise ValueError("blocked wallets must be 0x addresses (42 characters)")
        rules["blocked_wallets"] = wallets
    RULES.clear()
    RULES.update(rules)
    return RULES


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

def lookalike_of(pay_to: str, known: set[str], head: int = 8, tail: int = 6) -> str | None:
    """A known wallet that shares the first `head` and last `tail` hex chars with pay_to but isn't it."""
    a = pay_to.lower().removeprefix("0x")
    for wallet in known:
        k = wallet.lower().removeprefix("0x")
        if a != k and a[:head] == k[:head] and a[-tail:] == k[-tail:]:
            return wallet
    return None


class PayeeBook:
    """Known wallets: fixed ones for trusted shops, plus the first payTo seen for any other shop."""

    def __init__(self, trusted: dict[str, str] | None = None) -> None:
        self.fixed = trusted  # None: use the owner's trusted_payees rule
        self.pinned: dict[str, str] = {}

    @property
    def trusted(self) -> dict[str, str]:
        return RULES["trusted_payees"] if self.fixed is None else self.fixed

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

    def reference_price(self, origin: str | None, path: str) -> float | None:
        """The 'usual' price, taken ONLY from a trusted shop's catalog (a scam shop's own catalog
        always agrees with its own bill). Trusted shop: its price for this path. Any other shop:
        the trusted shop's price for the same item name (/evil/report -> /api/report), else its
        basic coin-price item (/api/price)."""
        if origin in RULES["trusted_payees"]:
            return self.price(origin, path)
        name = path.rstrip("/").rsplit("/", 1)[-1]
        for ref_path in (f"/api/{name}", REFERENCE_ITEM):
            ref = self.price(TICKTOLL_ORIGIN, ref_path)
            if ref is not None:
                return ref
        return None


# --- the checks ---------------------------------------------------------------------------

def check_asset(req: PaymentRequirements) -> tuple[bool, str]:
    if req.asset.lower() != USDC_ADDRESS.lower():
        return False, f"The bill wants to be paid in token {short(req.asset)}, which is not real USDC"
    if req.network != NETWORK:
        return False, f"The bill is for network {req.network}, not our test network {NETWORK}"
    return True, "real USDC on the test network"


def check_payee(origin: str | None, pay_to: str, book: PayeeBook, look: tuple[int, int] = (8, 6),
                bad_wallets: dict | None = None) -> tuple[bool, str]:
    if not ADDRESS_RE.match(pay_to or ""):
        return False, f"'{pay_to}' is not a valid wallet address"
    if pay_to.lower() in {w.lower() for w in RULES["blocked_wallets"]}:
        return False, f"Wallet {short(pay_to)} is on the owner's blocked list"
    if bad_wallets and pay_to.lower() in bad_wallets:
        return False, f"Wallet {short(pay_to)} has received money from attacks before (learned)"
    like = lookalike_of(pay_to, book.known_wallets(), *look)
    if like:
        return False, f"Wallet {pay_to} only looks like the known wallet {like} (lookalike address)"
    expected = book.expected(origin)
    if expected is None:
        if RULES["known_shops_only"]:
            return False, f"{shop_name(origin)} is not one of the owner's trusted shops"
        return True, f"new shop: remembering its wallet {short(pay_to)}"
    if pay_to.lower() != expected.lower():
        if origin in book.trusted:
            return False, f"{shop_name(origin)} is paid at {short(expected)}, but this bill pays {short(pay_to)}"
        return False, f"This shop first asked to be paid at {short(expected)}, now it wants {short(pay_to)}"
    return True, f"matches the known wallet {short(expected)}"


def check_cap(amount_usd: float, new_shop_usd: float | None = None) -> tuple[bool, str]:
    """The owner's hard cap, plus (only for a shop the guard has never been told about) the learned
    new-shop limit."""
    if new_shop_usd is not None and amount_usd > new_shop_usd + 1e-9:
        return False, (f"This shop is new to us; it asks {money(amount_usd)} but new shops may charge "
                       f"at most {money(new_shop_usd)} (learned)")
    cap = RULES["cap_usd"]
    if amount_usd > cap + 1e-9:
        return False, f"This bill is {money(amount_usd)}, more than the {money(cap)} spending cap"
    return True, f"{money(amount_usd)} is under the {money(cap)} spending cap"


def ratio_text(r: float) -> str:
    return f"{r:.0f}x" if r >= 10 else f"{r:.1f}".rstrip("0").rstrip(".") + "x"


def check_price(amount_usd: float, catalog_price: float | None, multiplier: float | None = None) -> tuple[bool, str]:
    if catalog_price is None:
        return True, "skipped: no catalog price for this item"
    if catalog_price <= 0:
        if amount_usd > 0:
            return False, f"This item is listed as free, but the bill asks for {money(amount_usd)}"
        return True, "free item"
    r = amount_usd / catalog_price
    if amount_usd > (multiplier or RULES["price_multiplier"]) * catalog_price + 1e-9:
        return False, f"Price is {ratio_text(r)} the usual {money(catalog_price)}"
    return True, f"{money(amount_usd)} vs usual {money(catalog_price)}"


def check_expiry(seconds: int, max_s: float | None = None) -> tuple[bool, str]:
    if seconds <= 0:
        return False, f"The bill's time limit ({seconds} s) makes no sense"
    limit = int(max_s or RULES["max_timeout_s"])
    if seconds > limit:
        return False, (f"The payment would stay valid for {seconds:,} seconds; "
                       f"the guard allows at most {limit:,}")
    return True, f"valid for {seconds} s"


def check_duplicate(resource: str, ledger: Ledger) -> tuple[bool, str]:
    prev = ledger.paid_recently(resource, RULES["duplicate_window_s"])
    if prev:
        ago = max(0, round(time.time() - prev["ts"]))
        return False, f"We already paid for this exact item {ago} seconds ago"
    return True, "not paid for recently"


def effective(tuning: dict | None) -> dict:
    """What the guard learned, combined with the owner's rules: always the STRICTER of the two.
    The learner can tighten a rule, never loosen it."""
    t = tuning or {}
    return {"price_mult": min(t.get("price_mult", 1e9), RULES["price_multiplier"]),
            "expiry_s": min(t.get("expiry_s", 1e9), RULES["max_timeout_s"]),
            "look": tuple(min(a, b) for a, b in zip(t.get("look", (8, 6)), (8, 6))),
            "new_shop_usd": t.get("new_shop_usd"),
            "bad_wallets": t.get("bad_wallets") or {}}


def evaluate(req: PaymentRequirements, context: dict, ledger: Ledger, book: PayeeBook,
             catalog_price: float | None, tuning: dict | None = None) -> dict:
    """Run every check exactly once (call BEFORE recording the payment in the ledger).

    tuning = what the learner settled on for this shop (see defender/learner.py), or None.
    "failed" lists every failed check in priority order; "headline" is the first of them.
    """
    eff = effective(tuning)
    origin = shop_origin(req, context)
    amount_usd = to_usd(req.amount)
    mandate = parse_mandate(context, RULES)
    for key in ("max_per_payment_usd", "budget_usd"):  # the task may be stricter than the owner, never looser
        mandate[key] = min(mandate[key], RULES[key])
    results = {
        "origin": check_origin(context.get("intended_url"), context.get("final_url")),
        "asset": check_asset(req),
        "payee": check_payee(origin, req.payTo, book, eff["look"], eff["bad_wallets"]),
        "price": check_price(amount_usd, catalog_price, eff["price_mult"]),
        "mandate": check_mandate(amount_usd, mandate, ledger.spent(mandate["task"])),
        "cap": check_cap(amount_usd, eff["new_shop_usd"]),
        "expiry": check_expiry(req.maxTimeoutSeconds, eff["expiry_s"]),
        "duplicate": check_duplicate(req.resource, ledger),
    }
    for cid, on in RULES["checks"].items():
        if not on:
            results[cid] = (True, "switched off by the owner")
    checks = [{"id": cid, "name": name, "ok": results[cid][0], "reason": results[cid][1]}
              for cid, name in CHECKS]
    failed = [c for c in checks if not c["ok"]]
    return {"checks": checks, "failed": failed, "headline": failed[0] if failed else None,
            "origin": origin, "path": path_of(req.resource), "amount_usd": amount_usd, "mandate": mandate}

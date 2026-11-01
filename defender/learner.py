"""Blue learner: the guard teaches itself how strict to be, from attacks it has seen.

What can learn (each one a separate small bandit, per kind of shop):
  price       how far above the usual price a bill may be      3x   / 2x   / 1.25x
  expiry      how long a signed payment may stay valid          300s / 180s / 120s
  lookalike   how many matching characters make a wallet a fake 8+6  / 6+4  / 4+2
  new shop    the most a never-seen shop may charge             10c  / 3c   / 1.5c
and a wallet reputation list: any wallet the attacker engine says received an attack payment is blocked.

What never learns: the owner's rules (the learner can only make them stricter), real USDC + network,
the hard cap, mandate limits, origin and duplicate checks.

Kinds of shop ("bands"), from labelled history per shop address: new (fewer than 3 labels), trusted
(mostly honest), low (mostly attacks).

How it learns. Labels come only from the attacker engine's ground truth (POST /label with ARENA_TOKEN),
never from a shop. For every labelled bill we replay it under every level of every rule and score each
level: 1 if that level would have decided correctly, 0.4 if it would have blocked an honest bill,
0 if it would have let an attack through (counterfactual, full-information updates on Beta arms).

Safety gate. A level is only allowed for a band if every honest bill seen for that band still passes
under it. So the guard ends up as strict as it can be without blocking honest shops.

Explore vs use. While the arena trains, levels are Thompson-sampled (exploring). At all other times the
guard uses the best safe level for each rule: what it learned is what protects the live demo.
"""

import json
import os
import random
import threading
from pathlib import Path

from contracts.payment import PaymentRequirements
from defender import policy
from defender.ledger import Ledger

KNOBS = {
    "price_mult": [3.0, 2.0, 1.25],
    "expiry_s": [300, 180, 120],
    "look": [(8, 6), (6, 4), (4, 2)],
    "new_shop_usd": [0.10, 0.03, 0.015],
}
KNOB_TITLES = {"price_mult": "Price above usual", "expiry_s": "Signature lifetime",
               "look": "Look-alike wallets", "new_shop_usd": "New-shop limit"}
LEVEL_NAMES = ["relaxed", "tighter", "strictest"]
TUNABLE_CHECKS = {"price", "expiry", "payee", "cap"}  # the checks these knobs feed
BANDS = ["new", "low", "trusted"]
NEW_BELOW_N = 3
TRUSTED_MIN_MEAN = 0.6
REWARD = {"correct": 1.0, "false_block": 0.4, "missed": 0.0}
STATE_FILE = Path(os.getenv("LEARNER_STATE", Path(__file__).with_name("learner_state.json")))
MAX_PENDING, MAX_HONEST, HISTORY_WINDOW = 2000, 40, 10


def fresh_state() -> dict:
    return {
        "arms": {b: {k: [[1.0, 1.0] for _ in levels] for k, levels in KNOBS.items()} for b in BANDS},
        "trust": {},          # shop origin -> {"honest": n, "bad": n}
        "honest": {},         # honest bills seen, replayed by the safety gate
        "bad_wallets": {},    # wallet (lowercase) -> {"hits": n}
        "counts": {"labelled": 0, "correct": 0, "false_blocks": 0, "missed": 0},
        "history": [],        # accuracy per HISTORY_WINDOW labels
        "window": {"n": 0, "correct": 0},
    }


def kind_of(blocked: bool, truth: str) -> str:
    if truth == "attack":
        return "correct" if blocked else "missed"
    return "false_block" if blocked else "correct"


def level_value(knob: str, level: int):
    return KNOBS[knob][level]


class BlueLearner:
    def __init__(self, path: Path = STATE_FILE, rng: random.Random | None = None) -> None:
        self.path = Path(path)
        self.rng = rng or random.Random()
        self.lock = threading.RLock()
        self.exploring = False
        self.pending: dict[str, dict] = {}
        self.state = self._load()

    # --- persistence ---------------------------------------------------------------------
    def _load(self) -> dict:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            base = fresh_state()
            base.update({k: v for k, v in saved.items() if k in base})
            return base
        except (OSError, ValueError):
            return fresh_state()

    def save(self) -> None:
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def reset(self) -> None:
        with self.lock:
            self.state = fresh_state()
            self.pending.clear()
            self.save()

    # --- trust bands ---------------------------------------------------------------------
    def band(self, origin: str | None) -> str:
        t = self.state["trust"].get(origin or "", {"honest": 0, "bad": 0})
        if t["honest"] + t["bad"] < NEW_BELOW_N:
            return "new"
        return "trusted" if (1 + t["honest"]) / (2 + t["honest"] + t["bad"]) >= TRUSTED_MIN_MEAN else "low"

    # --- turning levels into the tuning the policy understands ---------------------------
    def tuning(self, band: str, levels: dict) -> dict:
        t = {k: level_value(k, i) for k, i in levels.items()}
        t["new_shop_usd"] = t["new_shop_usd"] if band == "new" else None  # only for never-seen shops
        t["bad_wallets"] = self.state["bad_wallets"]
        return t

    def _replay(self, item: dict, band: str, levels: dict, with_reputation: bool = True) -> bool:
        """Would this stored bill be blocked by the tunable checks under these levels?"""
        req = PaymentRequirements.model_validate(item["req"])
        t = self.tuning(band, levels)
        if not with_reputation:
            t["bad_wallets"] = {}
        d = policy.evaluate(req, item["context"], Ledger(), policy.PayeeBook(), item["catalog_price"], t)
        return any(c["id"] in TUNABLE_CHECKS for c in d["failed"])

    # --- the safety gate -----------------------------------------------------------------
    def safe_levels(self, band: str, knob: str, live: dict) -> list[int]:
        """Levels of `knob` under which every honest bill seen for this band still passes."""
        honest = [h for h in self.state["honest"].values() if self.band(h["origin"]) == band]
        return [i for i in range(len(KNOBS[knob]))
                if not any(self._replay(h, band, {**live, knob: i}) for h in honest)] or [0]

    def live_levels(self, band: str) -> dict:
        """For each rule: the best safe level by what has been learned so far (no exploring)."""
        levels = {k: 0 for k in KNOBS}
        for knob in KNOBS:
            arms = self.state["arms"][band][knob]
            safe = self.safe_levels(band, knob, levels)
            levels[knob] = max(safe, key=lambda i: (arms[i][0] / sum(arms[i]), -i))  # ties: the owner's level
        return levels

    def choose(self, origin: str | None) -> dict:
        """The settings for one bill: Thompson-sampled while exploring, the best safe ones otherwise."""
        with self.lock:
            band = self.band(origin)
            levels = self.live_levels(band)
            if self.exploring:
                for knob in KNOBS:
                    arms = self.state["arms"][band][knob]
                    safe = self.safe_levels(band, knob, levels)
                    levels[knob] = max(safe, key=lambda i: self.rng.betavariate(*arms[i]))
            return {"band": band, "levels": levels, "tuning": self.tuning(band, levels)}

    # --- labels --------------------------------------------------------------------------
    def remember(self, decision_id: str, info: dict) -> None:
        with self.lock:
            self.pending[decision_id] = info
            while len(self.pending) > MAX_PENDING:
                self.pending.pop(next(iter(self.pending)))

    def label(self, decision_id: str, truth: str) -> dict | None:
        """Ground truth from the attacker engine: truth is "attack" or "honest"."""
        with self.lock:
            info = self.pending.pop(decision_id, None)
            if info is None:
                return None
            st, band, origin = self.state, info["band"], info["origin"] or ""
            kind = kind_of(info["verdict"] == "blocked", truth)
            # Did a rule that never learns (origin, asset, mandate, duplicate) already block it?
            fixed_block = any(c not in TUNABLE_CHECKS for c in info["failed"])
            # Counterfactual: score every level of every rule on this very bill.
            for knob, levels in KNOBS.items():
                for i in range(len(levels)):
                    blocked = fixed_block or self._replay(info, band, {**info["levels"], knob: i}, with_reputation=False)
                    r = REWARD[kind_of(blocked, truth)]
                    arm = st["arms"][band][knob][i]
                    arm[0] += r
                    arm[1] += 1 - r
            if truth == "attack":  # reputation: this wallet takes attack money
                w = st["bad_wallets"].setdefault(info["req"]["payTo"].lower(), {"hits": 0})
                w["hits"] += 1
            t = st["trust"].setdefault(origin, {"honest": 0, "bad": 0})
            t["honest" if truth == "honest" else "bad"] += 1
            if truth == "honest":
                st["honest"][f"{origin}{info['path']}"] = {k: info[k] for k in ("origin", "req", "context", "catalog_price")}
                while len(st["honest"]) > MAX_HONEST:
                    st["honest"].pop(next(iter(st["honest"])))
            c = st["counts"]
            c["labelled"] += 1
            c[{"correct": "correct", "false_block": "false_blocks", "missed": "missed"}[kind]] += 1
            w = st["window"]
            w["n"] += 1
            w["correct"] += kind == "correct"
            if w["n"] >= HISTORY_WINDOW:
                st["history"].append({"labels": c["labelled"], "accuracy": round(w["correct"] / w["n"], 3)})
                st["window"] = {"n": 0, "correct": 0}
            self.save()
            return {"kind": kind, "reward": REWARD[kind], "band": band}

    # --- read-only view for the API ------------------------------------------------------
    def snapshot(self) -> dict:
        with self.lock:
            st = self.state
            bands = {}
            for b in BANDS:
                live = self.live_levels(b)
                bands[b] = {knob: {
                    "title": KNOB_TITLES[knob], "live": LEVEL_NAMES[live[knob]], "value": level_value(knob, live[knob]),
                    "levels": [{"name": LEVEL_NAMES[i], "value": v,
                                "score": round(a / (a + bb), 3), "evidence": round(a + bb - 2, 1),
                                "safe": i in self.safe_levels(b, knob, live)}
                               for i, (v, (a, bb)) in enumerate(zip(KNOBS[knob], st["arms"][b][knob]))],
                } for knob in KNOBS}
            trust = {o: {**t, "band": self.band(o)} for o, t in st["trust"].items()}
            c = st["counts"]
            return {"exploring": self.exploring, "bands": bands, "trust": trust, "counts": dict(c),
                    "bad_wallets": st["bad_wallets"], "history": list(st["history"]),
                    "accuracy": round(c["correct"] / c["labelled"], 3) if c["labelled"] else None,
                    "honest_seen": sorted(st["honest"])}

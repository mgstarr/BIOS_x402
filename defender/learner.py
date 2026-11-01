"""Blue learner (the guard's immune system).

Hard floors never learn (real USDC + network, drain cap, mandate limits, origin, duplicate).
What learns: which strictness PROFILE (lenient / standard / strict) to use for the tunable checks,
chosen by a 3-arm Thompson bandit per shop-trust band (new / low / trusted).

Labels come ONLY from the attacker engine's ground truth (POST /label with ARENA_TOKEN), never from
anything a shop sends. Reward per decision: 1 correct, 0.4 false block, 0 missed attack.

Conservative rule: a sampled profile only goes live for a band if every honest control we have seen
for that band still passes under it; otherwise we roll back to the last safe profile and the arm is
charged the false-block reward (0.4).
"""

import json
import os
import random
import threading
import time
from pathlib import Path

from defender import policy
from defender.ledger import Ledger

PROFILE_NAMES = ["lenient", "standard", "strict"]
BANDS = ["new", "low", "trusted"]
NEW_BELOW_N = 3          # fewer than this many labelled observations -> brand-new shop
TRUSTED_MIN_MEAN = 0.6   # Beta mean below this -> low trust
REWARD = {"correct": 1.0, "false_block": 0.4, "missed": 0.0}
STATE_FILE = Path(os.getenv("LEARNER_STATE", Path(__file__).with_name("learner_state.json")))
MAX_PENDING = 2000
MAX_HONEST = 40
HISTORY_WINDOW = 20      # labels per accuracy point


def fresh_state() -> dict:
    return {
        "bandits": {b: {p: [1.0, 1.0] for p in PROFILE_NAMES} for b in BANDS},
        "live": {b: "standard" for b in BANDS},
        "trust": {},            # origin -> {"honest": n, "bad": n}
        "honest": {},           # key -> {"origin","req","context","catalog_price"} (honest controls seen)
        "counts": {"labelled": 0, "correct": 0, "false_blocks": 0, "missed": 0, "rollbacks": 0},
        "by_profile": {b: {p: 0 for p in PROFILE_NAMES} for b in BANDS},
        "history": [],          # [{"labels": n, "accuracy": x}] one point per HISTORY_WINDOW labels
        "window": {"n": 0, "correct": 0},
        "version": 0,           # bumps whenever the honest set changes (invalidates the gate cache)
    }


def beta_mean(t: dict) -> float:
    return (1 + t["honest"]) / (2 + t["honest"] + t["bad"])


class BlueLearner:
    def __init__(self, path: Path = STATE_FILE, rng: random.Random | None = None) -> None:
        self.path = Path(path)
        self.rng = rng or random.Random()
        self.lock = threading.RLock()
        self.enabled = False
        self.pending: dict[str, dict] = {}
        self.gate_cache: dict = {}
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
            self.gate_cache.clear()
            self.save()

    # --- trust ---------------------------------------------------------------------------
    def trust_of(self, origin: str | None) -> dict:
        return self.state["trust"].get(origin or "", {"honest": 0, "bad": 0})

    def band(self, origin: str | None) -> str:
        t = self.trust_of(origin)
        if t["honest"] + t["bad"] < NEW_BELOW_N:
            return "new"
        return "trusted" if beta_mean(t) >= TRUSTED_MIN_MEAN else "low"

    # --- choosing a profile --------------------------------------------------------------
    def _gate_ok(self, band: str, profile: str, book) -> bool:
        """Would every honest control seen for this band still pass under `profile`?"""
        key = (band, profile, self.state["version"])
        if key in self.gate_cache:
            return self.gate_cache[key]
        ok = True
        for h in self.state["honest"].values():
            if self.band(h["origin"]) != band:
                continue
            from contracts.payment import PaymentRequirements
            req = PaymentRequirements.model_validate(h["req"])
            d = policy.evaluate(req, h["context"], Ledger(), book, h["catalog_price"],
                                policy.PROFILES[profile], band)
            if d["failed"]:
                ok = False
                break
        self.gate_cache[key] = ok
        return ok

    def choose(self, origin: str | None, book) -> dict:
        """Thompson-sample a profile for this shop's band, then apply the conservative gate."""
        with self.lock:
            band = self.band(origin)
            draws = {p: self.rng.betavariate(*self.state["bandits"][band][p]) for p in PROFILE_NAMES}
            sampled = max(draws, key=draws.get)
            used, rolled_back = sampled, False
            if not self._gate_ok(band, sampled, book):
                rolled_back = True
                used = self.state["live"][band]
                self.state["counts"]["rollbacks"] += 1
                self._update_arm(band, sampled, REWARD["false_block"])  # charged like a false block
            else:
                self.state["live"][band] = sampled
            return {"band": band, "profile": used, "sampled": sampled, "rolled_back": rolled_back}

    def _update_arm(self, band: str, profile: str, reward: float) -> None:
        win = 1.0 if self.rng.random() < reward else 0.0   # Bernoulli draw for fractional rewards
        a = self.state["bandits"][band][profile]
        a[0] += win
        a[1] += 1.0 - win

    # --- labels --------------------------------------------------------------------------
    def remember(self, decision_id: str, info: dict) -> None:
        with self.lock:
            self.pending[decision_id] = info
            while len(self.pending) > MAX_PENDING:
                self.pending.pop(next(iter(self.pending)))

    def label(self, decision_id: str, truth: str) -> dict | None:
        """Ground truth from the attacker engine: truth is 'attack' or 'honest'."""
        with self.lock:
            info = self.pending.pop(decision_id, None)
            if info is None:
                return None
            blocked = info["verdict"] == "blocked"
            if truth == "attack":
                kind = "correct" if blocked else "missed"
            else:
                kind = "false_block" if blocked else "correct"
            reward = REWARD[kind]
            band, profile, origin = info["band"], info["profile"], info["origin"] or ""
            self._update_arm(band, profile, reward)
            st = self.state
            st["by_profile"][band][profile] += 1
            c = st["counts"]
            c["labelled"] += 1
            c["correct"] += kind == "correct"
            c["false_blocks"] += kind == "false_block"
            c["missed"] += kind == "missed"
            t = st["trust"].setdefault(origin, {"honest": 0, "bad": 0})
            t["honest" if truth == "honest" else "bad"] += 1
            if truth == "honest":
                key = f"{origin}{info['path']}"
                if key not in st["honest"]:
                    st["version"] += 1
                st["honest"][key] = {"origin": origin, "req": info["req"], "context": info["context"],
                                     "catalog_price": info["catalog_price"]}
                while len(st["honest"]) > MAX_HONEST:
                    st["honest"].pop(next(iter(st["honest"])))
            w = st["window"]
            w["n"] += 1
            w["correct"] += kind == "correct"
            if w["n"] >= HISTORY_WINDOW:
                st["history"].append({"labels": c["labelled"], "accuracy": round(w["correct"] / w["n"], 3)})
                st["window"] = {"n": 0, "correct": 0}
            self.gate_cache.clear()  # trust bands may have moved
            self.save()
            return {"kind": kind, "reward": reward, "band": band, "profile": profile}

    # --- read-only view for the API ------------------------------------------------------
    def snapshot(self) -> dict:
        with self.lock:
            st = self.state
            bandits = {b: {p: {"alpha": a, "beta": bb, "mean": round(a / (a + bb), 3), "pulls": st["by_profile"][b][p]}
                           for p, (a, bb) in arms.items()} for b, arms in st["bandits"].items()}
            trust = {o: {**t, "score": round(beta_mean(t), 3), "band": self.band(o)} for o, t in st["trust"].items()}
            c = st["counts"]
            return {"enabled": self.enabled, "bandits": bandits, "live_profile": dict(st["live"]),
                    "profiles": policy.PROFILES, "trust": trust, "counts": dict(c),
                    "accuracy": round(c["correct"] / c["labelled"], 3) if c["labelled"] else None,
                    "history": list(st["history"]), "honest_controls": sorted(st["honest"])}

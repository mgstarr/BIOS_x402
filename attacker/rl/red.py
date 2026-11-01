"""Red learner: two-level Thompson sampling with discounted evidence.

Level 1 picks an attack family (3 arms). Level 2 picks a tactic inside that family. Each arm is a Beta
(alpha, beta). Rewards are fractional, so every update is a Bernoulli draw of the reward. Old evidence is
discounted (gamma ~0.97) on every update of a scope, so the learner moves on after a hole is patched.
"""

import json
import random
import threading
from pathlib import Path

from attacker.rl.tactics import FAMILIES, TACTICS

GAMMA = 0.97
STATE_FILE = Path(__file__).resolve().parent.parent / "rl_state.json"


def reward_for(breach: bool, passed_fraction: float) -> float:
    """1 if unpermitted USDC reached an attacker wallet (verified on chain), else 0.5 x checks passed."""
    return 1.0 if breach else 0.5 * max(0.0, min(1.0, passed_fraction))


class RedLearner:
    def __init__(self, path: Path = STATE_FILE, rng: random.Random | None = None, gamma: float = GAMMA) -> None:
        self.path, self.gamma = Path(path), gamma
        self.rng = rng or random.Random()
        self.lock = threading.RLock()
        self.arms = self._fresh()
        self._load()

    @staticmethod
    def _fresh() -> dict:
        return {"families": {f: [1.0, 1.0] for f in FAMILIES},
                "tactics": {f: {t: [1.0, 1.0] for t in ts} for f, ts in TACTICS.items()},
                "pulls": {f: {t: 0 for t in ts} for f, ts in TACTICS.items()},
                "breaches": {f: {t: 0 for t in ts} for f, ts in TACTICS.items()}}

    def _load(self) -> None:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            fresh = self._fresh()
            fresh["families"].update(saved.get("families", {}))
            for f in FAMILIES:
                for key in ("tactics", "pulls", "breaches"):
                    fresh[key][f].update(saved.get(key, {}).get(f, {}))
            self.arms = fresh
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.arms), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def reset(self) -> None:
        with self.lock:
            self.arms = self._fresh()
            self.save()

    def _pick(self, arms: dict) -> str:
        draws = {k: self.rng.betavariate(a, b) for k, (a, b) in arms.items()}
        return max(draws, key=draws.get)

    def choose(self) -> tuple[str, str]:
        with self.lock:
            family = self._pick(self.arms["families"])
            return family, self._pick(self.arms["tactics"][family])

    def _update(self, arms: dict, key: str, win: float) -> None:
        for k, ab in arms.items():          # discount all old evidence in this scope toward the prior
            ab[0] = 1.0 + self.gamma * (ab[0] - 1.0)
            ab[1] = 1.0 + self.gamma * (ab[1] - 1.0)
        arms[key][0] += win
        arms[key][1] += 1.0 - win

    def update(self, family: str, tactic: str, reward: float, breach: bool) -> float:
        win = 1.0 if self.rng.random() < reward else 0.0   # Bernoulli draw for fractional rewards
        with self.lock:
            self._update(self.arms["families"], family, win)
            self._update(self.arms["tactics"][family], tactic, win)
            self.arms["pulls"][family][tactic] += 1
            self.arms["breaches"][family][tactic] += bool(breach)
            self.save()
        return win

    def snapshot(self) -> dict:
        with self.lock:
            mean = lambda ab: round(ab[0] / (ab[0] + ab[1]), 3)  # noqa: E731
            return {"families": {f: {"alpha": round(a, 2), "beta": round(b, 2), "mean": mean((a, b))}
                                 for f, (a, b) in self.arms["families"].items()},
                    "tactics": {f: {t: {"mean": mean(ab), "pulls": self.arms["pulls"][f][t],
                                        "breaches": self.arms["breaches"][f][t]}
                                    for t, ab in ts.items()} for f, ts in self.arms["tactics"].items()},
                    "gamma": self.gamma}

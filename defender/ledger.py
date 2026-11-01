"""Ledger: records settled payments so spend can be tallied against mandates over time."""

import threading
import time


class Ledger:
    """In-memory list of payments the defender let through (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[dict] = []

    def record(self, task: str, resource: str, amount_usd: float, pay_to: str, origin: str | None) -> dict:
        entry = {"ts": time.time(), "task": task, "resource": resource,
                 "amount_usd": amount_usd, "payTo": pay_to, "origin": origin}
        with self._lock:
            self._entries.append(entry)
        return entry

    def remove(self, entry: dict) -> None:
        """Undo a record (used when the real signer fails after we reserved the spend)."""
        with self._lock:
            self._entries = [e for e in self._entries if e is not entry]

    def spent(self, task: str) -> float:
        with self._lock:
            return round(sum(e["amount_usd"] for e in self._entries if e["task"] == task), 6)

    def paid_recently(self, resource: str, window_s: float = 60) -> dict | None:
        """The most recent payment for exactly this resource within the window, if any."""
        cutoff = time.time() - window_s
        with self._lock:
            for e in reversed(self._entries):
                if e["resource"] == resource and e["ts"] >= cutoff:
                    return e
        return None

    def all(self) -> list[dict]:
        with self._lock:
            return list(self._entries)

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()

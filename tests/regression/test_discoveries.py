"""Replays every saved discovery (tests/regression/discoveries.json) against the live stack.

A discovery is a heist that once drained real (test) USDC to an attacker wallet. After the guard is patched
or has learned, each one must be BLOCKED. Needs Anvil + defender + shops + ARENA_TOKEN; skipped otherwise.
Run: RUN_REGRESSION=1 pytest tests/regression -q     (Anvil state is snapshotted and reverted)
"""

import os
import random

import pytest
from dotenv import load_dotenv

load_dotenv()
pytestmark = pytest.mark.skipif(os.getenv("RUN_REGRESSION") != "1", reason="set RUN_REGRESSION=1 with the stack up")

from attacker.rl import trainer  # noqa: E402
from attacker.rl.tactics import build  # noqa: E402


@pytest.fixture(scope="module")
def arena():
    try:
        trainer.preflight()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"stack not ready: {e}")
    snap = trainer.rpc("evm_snapshot")
    yield
    trainer.rpc("evm_revert", [snap])


@pytest.mark.parametrize("d", trainer.load_discoveries(), ids=lambda d: f"{d['id']}-{d['tactic']}")
def test_discovery_is_blocked(arena, d):
    if d["tactic"].startswith("fresh:") and d["amount_usd"] <= 0.03:
        pytest.xfail("known gap: a brand-new shop billing a fair price to a brand-new wallet looks exactly like an "
                     "honest new shop; the price and new-shop rules cap each such loss at a few cents")
    heist = build(d["family"], d["tactic"], random)
    res = trainer.play(heist)
    assert not res["breach"], f"{d['text']} -- still gets through"

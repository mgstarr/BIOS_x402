"""Unit tests for the red and blue learners (no network, no chain)."""

import random

import pytest

from attacker.rl.red import RedLearner, reward_for
from attacker.rl.tactics import TACTICS, build, lookalike_wallet
from attacker.attacks import TICKTOLL
from contracts.payment import PaymentRequirements
from defender import policy
from defender.learner import BlueLearner
from defender.policy import PayeeBook

HEX = TICKTOLL.lower().removeprefix("0x")


def red(tmp_path, seed=1):
    return RedLearner(path=tmp_path / "red.json", rng=random.Random(seed))


def blue(tmp_path, seed=1):
    return BlueLearner(path=tmp_path / "blue.json", rng=random.Random(seed))


def test_reward_rules():
    assert reward_for(True, 0.0) == 1.0
    assert reward_for(False, 1.0) == 0.5
    assert reward_for(False, 0.5) == 0.25
    assert reward_for(False, 0.0) == 0.0


def test_red_learns_the_rewarded_family(tmp_path):
    r = red(tmp_path)
    for _ in range(200):
        fam, tac = r.choose()
        r.update(fam, tac, 1.0 if fam == "wrong_party" else 0.0, fam == "wrong_party")
    snap = r.snapshot()["families"]
    assert snap["wrong_party"]["mean"] > snap["overpayment"]["mean"]
    picks = [r.choose()[0] for _ in range(100)]
    assert picks.count("wrong_party") > 70


def test_red_discounting_forgets_old_evidence(tmp_path):
    r = red(tmp_path)
    for _ in range(100):
        r.update("overpayment", "cat:overcharge", 1.0, True)
    high = r.snapshot()["tactics"]["overpayment"]["cat:overcharge"]["mean"]
    for _ in range(100):  # hole patched: nothing works any more
        r.update("overpayment", "cat:overcharge", 0.0, False)
    assert r.snapshot()["tactics"]["overpayment"]["cat:overcharge"]["mean"] < high - 0.5


def test_red_state_persists_and_resets(tmp_path):
    r = red(tmp_path)
    r.update("overpayment", "cat:drain", 1.0, True)
    again = red(tmp_path)
    assert again.snapshot()["tactics"]["overpayment"]["cat:drain"]["pulls"] == 1
    again.reset()
    assert red(tmp_path).snapshot()["tactics"]["overpayment"]["cat:drain"]["pulls"] == 0


def test_every_tactic_builds():
    rng = random.Random(3)
    for fam, tactics in TACTICS.items():
        for t in tactics:
            h = build(fam, t, rng)
            assert h["truth"] == "attack" and h["final"].startswith("http")


@pytest.mark.parametrize("h,t", [(8, 6), (7, 6), (6, 4), (4, 2)])
def test_lookalike_shares_exactly_h_and_t(h, t):
    w = lookalike_wallet(h, t, random.Random(5)).removeprefix("0x")
    assert w[:h] == HEX[:h] and w[-t:] == HEX[-t:]
    assert w[h] != HEX[h] and w[-t - 1] != HEX[-t - 1] and len(w) == 40


def test_profile_boundaries_match_the_rules():
    like = "0x" + lookalike_wallet(7, 6, random.Random(1)).removeprefix("0x")
    known = {TICKTOLL}
    p = policy.PROFILES
    assert policy.lookalike_of(like, known, p["standard"]["look_head"], p["standard"]["look_tail"]) is None
    assert policy.lookalike_of(like, known, p["strict"]["look_head"], p["strict"]["look_tail"]) == TICKTOLL
    assert policy.check_price(0.015, 0.01, p["standard"]["price_mult"])[0]
    assert not policy.check_price(0.015, 0.01, p["strict"]["price_mult"])[0]
    assert not policy.check_expiry(200, p["strict"]["expiry_s"])[0]
    assert policy.check_cap(0.02, p["standard"]["new_shop_usd"])[0]
    assert not policy.check_cap(0.02, p["strict"]["new_shop_usd"])[0]
    assert not policy.check_cap(0.6, None)[0]  # hard cap never learns


def honest_info(profile="strict", band="trusted"):
    req = PaymentRequirements(amount="15000", asset=policy.USDC_ADDRESS, payTo=policy.TICKTOLL_PAY_TO,
                              resource="http://localhost:5001/api/price-peak?symbol=ETH&n=1", maxTimeoutSeconds=60)
    ctx = {"task": "t", "mandate": {"max_per_payment_usd": 0.1, "budget_usd": 0.5}}
    return {"verdict": "blocked", "band": band, "profile": profile, "origin": policy.TICKTOLL_ORIGIN,
            "path": "/api/price-peak", "req": req.model_dump(), "context": ctx, "catalog_price": 0.01}


def make_trusted(b):
    b.state["trust"][policy.TICKTOLL_ORIGIN] = {"honest": 20, "bad": 0}


def test_trust_bands(tmp_path):
    b = blue(tmp_path)
    assert b.band("http://x") == "new"
    b.state["trust"]["http://x"] = {"honest": 10, "bad": 0}
    assert b.band("http://x") == "trusted"
    b.state["trust"]["http://x"] = {"honest": 1, "bad": 9}
    assert b.band("http://x") == "low"


def test_label_rewards(tmp_path):
    b = blue(tmp_path)
    b.remember("a", {**honest_info(), "verdict": "blocked"})
    assert b.label("a", "attack")["kind"] == "correct"
    b.remember("b", {**honest_info(), "verdict": "blocked"})
    assert b.label("b", "honest")["reward"] == 0.4
    b.remember("c", {**honest_info(), "verdict": "allowed"})
    assert b.label("c", "attack")["reward"] == 0.0
    b.remember("d", {**honest_info(), "verdict": "allowed"})
    assert b.label("d", "honest")["reward"] == 1.0
    assert b.label("zzz", "honest") is None


def test_gate_rolls_back_strict_after_honest_peak_seen(tmp_path):
    b = blue(tmp_path, seed=2)
    make_trusted(b)
    b.remember("h", honest_info())            # honest peak bill, wrongly blocked under strict
    b.label("h", "honest")
    for p in ("lenient", "standard"):         # make strict the favourite sample
        b.state["bandits"]["trusted"][p] = [1.0, 50.0]
    b.state["bandits"]["trusted"]["strict"] = [50.0, 1.0]
    book = PayeeBook()
    seen = {b.choose(policy.TICKTOLL_ORIGIN, book)["profile"] for _ in range(20)}
    assert "strict" not in seen               # never goes live: honest control would fail
    assert b.state["counts"]["rollbacks"] > 0


def test_strict_allowed_where_no_honest_control_conflicts(tmp_path):
    b = blue(tmp_path, seed=2)
    b.state["trust"]["http://evil"] = {"honest": 0, "bad": 9}   # low band, no honest evidence there
    b.state["bandits"]["low"]["strict"] = [60.0, 1.0]
    out = b.choose("http://evil", PayeeBook())
    assert out["band"] == "low" and out["profile"] == "strict" and not out["rolled_back"]


def test_blue_state_persists_and_resets(tmp_path):
    b = blue(tmp_path)
    b.remember("a", honest_info())
    b.label("a", "honest")
    assert blue(tmp_path).state["counts"]["labelled"] == 1
    b.reset()
    assert blue(tmp_path).state["counts"]["labelled"] == 0

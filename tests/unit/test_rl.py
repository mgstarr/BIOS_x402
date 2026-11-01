"""Unit tests for the learning attacker (red) and the learning guard (blue). No network, no chain."""

import random

import pytest

from attacker.attacks import ATTACKER, TICKTOLL
from attacker.rl.red import RedLearner, reward_for
from attacker.rl.tactics import TACTICS, build, lookalike_wallet
from contracts.payment import PaymentRequirements
from defender import policy
from defender.learner import KNOBS, BlueLearner

HEX = TICKTOLL.lower().removeprefix("0x")
NEW = "http://127.0.0.9:5002"
NEWSHOP = "http://127.0.0.7:5003"


@pytest.fixture(autouse=True)
def owner_defaults():
    policy.update_rules(policy.default_rules())
    yield
    policy.update_rules(policy.default_rules())


def red(tmp_path, seed=1):
    return RedLearner(path=tmp_path / "red.json", rng=random.Random(seed))


def blue(tmp_path, seed=1):
    return BlueLearner(path=tmp_path / "blue.json", rng=random.Random(seed))


# --- red ---------------------------------------------------------------------------------

def test_reward_rules():
    assert reward_for(True, 0.0) == 1.0
    assert reward_for(False, 1.0) == 0.5
    assert reward_for(False, 0.0) == 0.0


def test_red_learns_the_rewarded_family(tmp_path):
    r = red(tmp_path)
    for _ in range(200):
        fam, tac = r.choose()
        r.update(fam, tac, 1.0 if fam == "wrong_party" else 0.0, fam == "wrong_party")
    assert [r.choose()[0] for _ in range(100)].count("wrong_party") > 70


def test_red_forgets_a_patched_hole(tmp_path):
    r = red(tmp_path)
    for _ in range(100):
        r.update("overpayment", "cat:overcharge", 1.0, True)
    high = r.snapshot()["tactics"]["overpayment"]["cat:overcharge"]["mean"]
    for _ in range(100):
        r.update("overpayment", "cat:overcharge", 0.0, False)
    assert r.snapshot()["tactics"]["overpayment"]["cat:overcharge"]["mean"] < high - 0.5


def test_every_tactic_builds():
    rng = random.Random(3)
    for fam, tactics in TACTICS.items():
        for t in tactics:
            h = build(fam, t, rng)
            assert h["truth"] == "attack" and h["final"].startswith("http") and h["plain"]


@pytest.mark.parametrize("h,t", [(8, 6), (7, 5), (6, 4), (4, 2)])
def test_lookalike_shares_exactly_h_and_t(h, t):
    w = lookalike_wallet(h, t, random.Random(5)).removeprefix("0x")
    assert w[:h] == HEX[:h] and w[-t:] == HEX[-t:] and w[h] != HEX[h] and len(w) == 40


# --- blue --------------------------------------------------------------------------------

def bill(amount, pay_to, origin, path="/evil/custom", expiry=60):
    return PaymentRequirements(amount=str(amount), asset=policy.USDC_ADDRESS, payTo=pay_to,
                               resource=f"{origin}{path}?symbol=ETH", maxTimeoutSeconds=expiry)


def decide(b, req, origin, decision_id, catalog_price=0.01):
    """What the guard server does for one bill: choose settings, evaluate, remember."""
    choice = b.choose(origin)
    ctx = {"task": decision_id}
    d = policy.evaluate(req, ctx, policy.Ledger(), policy.PayeeBook(), catalog_price, choice["tuning"])
    b.remember(decision_id, {"verdict": "blocked" if d["failed"] else "allowed", "band": choice["band"],
                             "levels": choice["levels"], "origin": origin, "path": d["path"],
                             "req": req.model_dump(), "context": ctx, "catalog_price": catalog_price,
                             "failed": [c["id"] for c in d["failed"]]})
    return d


def test_starts_exactly_at_the_owner_rules(tmp_path):
    b = blue(tmp_path)
    assert all(level == 0 for level in b.live_levels("new").values())


def test_never_looser_than_the_owner():
    policy.update_rules({"price_multiplier": 1.5})
    assert policy.effective({"price_mult": 3.0})["price_mult"] == 1.5
    assert policy.effective({"look": (10, 8)})["look"] == (8, 6)


def test_learns_to_tighten_lookalike_but_not_against_honest_shops(tmp_path):
    b = blue(tmp_path)
    rng = random.Random(2)
    for i in range(12):  # 7+5 lookalikes slip past the owner's 8+6 rule
        w = lookalike_wallet(7, 5, rng)
        decide(b, bill(10000, w, NEW), NEW, f"a{i}")
        b.label(f"a{i}", "attack")
    assert b.live_levels("new")["look"] > 0
    w = lookalike_wallet(7, 5, rng)  # a new 7+5 lookalike, never seen: now blocked
    assert decide(b, bill(10000, w, NEW), NEW, "z")["failed"]


def test_safety_gate_keeps_honest_new_shop_allowed(tmp_path):
    b = blue(tmp_path)
    honest_wallet = "0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc"
    decide(b, bill(20000, honest_wallet, NEWSHOP, "/api/price"), NEWSHOP, "h")
    b.label("h", "honest")
    for i in range(15):  # attacks at 2.9c from new shops push the new-shop limit down...
        w = "0x" + "%040x" % (i + 7)
        decide(b, bill(29000, w, NEW), NEW, f"a{i}")
        b.label(f"a{i}", "attack")
    live = b.live_levels("new")
    assert KNOBS["new_shop_usd"][live["new_shop_usd"]] >= 0.02  # ...but never below the honest 2c shop
    assert not decide(b, bill(20000, honest_wallet, NEWSHOP, "/api/price"), NEWSHOP, "h2")["failed"]


def test_reputation_blocks_a_wallet_that_took_attack_money(tmp_path):
    b = blue(tmp_path)
    decide(b, bill(10000, ATTACKER, NEW), NEW, "a")
    b.label("a", "attack")
    d = decide(b, bill(10000, ATTACKER, "http://127.0.0.44:5002"), "http://127.0.0.44:5002", "b")
    assert [c["id"] for c in d["failed"]] == ["payee"] and "learned" in d["headline"]["reason"]


def test_trust_bands(tmp_path):
    b = blue(tmp_path)
    assert b.band("http://x") == "new"
    b.state["trust"]["http://x"] = {"honest": 10, "bad": 0}
    assert b.band("http://x") == "trusted"
    b.state["trust"]["http://x"] = {"honest": 1, "bad": 9}
    assert b.band("http://x") == "low"


def test_labels_need_a_known_decision_and_persist(tmp_path):
    b = blue(tmp_path)
    assert b.label("nope", "honest") is None
    decide(b, bill(10000, TICKTOLL, policy.TICKTOLL_ORIGIN, "/api/price"), policy.TICKTOLL_ORIGIN, "h")
    assert b.label("h", "honest")["kind"] == "correct"
    assert blue(tmp_path).state["counts"]["labelled"] == 1
    b.reset()
    assert blue(tmp_path).state["counts"]["labelled"] == 0

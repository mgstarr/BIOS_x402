import pytest

from attacker import evaluator, runner
from attacker.attacks import ATTACKS, KEYS, get


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setattr(runner, "MOCK", True)
    runner.MOCK_STATE["guard"] = True


def test_catalogue_shape():
    ids = [a["id"] for a in ATTACKS]
    assert len(ids) == len(set(ids))
    for a in ATTACKS:
        assert all(k in a for k in KEYS)
        assert a["defender_should"] in ("block", "allow")
    assert {"overcharge", "drain", "fake-token", "long-expiry", "pay-again", "wrong-wallet",
            "lookalike", "injection"} <= set(ids)
    assert sum(a["defender_should"] == "allow" for a in ATTACKS) == 2


@pytest.mark.parametrize("run,expected", [
    ({"blocked": True}, "blocked"),
    ({"decision": {"verdict": "blocked"}}, "blocked"),
    ({"lost_usd": 5.0, "payments": [{}]}, "stolen"),
    ({"payments": [], "mode": "agent"}, "refused"),
    ({"payments": [], "mode": "direct"}, "no_payment"),
    ({"payments": [], "error": "facilitator rejected"}, "no_payment"),
    ({"payments": [{}], "lost_usd": 0}, "paid"),
])
def test_outcome(run, expected):
    assert evaluator.outcome(run) == expected


def test_fake_token_guard_off_is_not_stolen():
    runner.MOCK_STATE["guard"] = False
    s = evaluator.score(get("fake-token"), runner.fire(get("fake-token")))
    assert s["outcome"] == "no_payment" and s["lost_usd"] == 0


def test_suite_scorecard():
    r = evaluator.suite()
    assert r["guard_on"]["lost_usd"] == 0 and r["guard_off"]["lost_usd"] > 0
    assert r["guard_on"]["honest_allowed"] == 2


def test_suite_restores_guard_on_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    calls = []
    monkeypatch.setattr(evaluator, "set_guard", lambda e: calls.append(e))
    monkeypatch.setattr(evaluator, "run_all", boom)
    with pytest.raises(RuntimeError):
        evaluator.suite()
    assert calls[-1] is True


def test_suite_restores_guard_when_second_phase_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(evaluator, "set_guard", lambda e: calls.append(e))
    n = {"i": 0}

    def flaky(player=None):
        n["i"] += 1
        if n["i"] == 2:
            raise RuntimeError("down")
        return []
    monkeypatch.setattr(evaluator, "run_all", flaky)
    with pytest.raises(RuntimeError):
        evaluator.suite()
    assert calls == [True, False, True]


def test_fire_endpoint():
    from attacker.server import app
    c = app.test_client()
    r = c.post("/fire", json={"attack_id": "overcharge"}).get_json()
    assert r["outcome"] == "blocked" and r["run"]["simulated"] is True
    assert c.post("/fire", json={"attack_id": "nope"}).status_code == 400

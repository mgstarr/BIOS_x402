import re

import pytest

from defender import policy, server
from defender.learner import BlueLearner
from defender.origin import origin_of

SHOP = "http://localhost:5001"
EVIL = "http://localhost:5002"
TICKTOLL = policy.TICKTOLL_PAY_TO
ATTACKER = "0x90F79bf6EB2c4f870365E785982E1f101E93b906"
LOOKALIKE = "0x3c44cDdd1B2C3D4e5F60718293a4b5c6d74293bC"
OTHER = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
CATALOGS = {SHOP: {"/api/price": 0.01, "/api/sentiment": 0.02, "/api/report": 0.05}}


def fake_fetch(origin):
    if origin not in CATALOGS:
        raise ConnectionError("no catalog")
    return CATALOGS[origin]


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("MOCK_SIGNER", "true")
    monkeypatch.setattr(server, "learner", BlueLearner(path=tmp_path / "learner.json"))  # nothing learned yet
    monkeypatch.setattr(server.catalog, "fetch", fake_fetch)
    server.reset_state()
    policy.update_rules(policy.default_rules())
    yield
    server.reset_state()
    policy.update_rules(policy.default_rules())


@pytest.fixture
def client():
    return server.app.test_client()


def bill(path="/api/price", amount="10000", pay_to=TICKTOLL, origin=SHOP, **kw):
    return {"scheme": "exact", "network": "eip155:84532", "amount": amount, "asset": USDC,
            "payTo": pay_to, "maxTimeoutSeconds": 60, "resource": f"{origin}{path}?symbol=ETH",
            "description": "test", **kw}


def sign(client, requirements, **context):
    body = {"requirements": requirements}
    if context:
        body["context"] = context
    return client.post("/sign", json=body)


def test_honest_ticktoll_bill_is_allowed(client):
    r = sign(client, bill(), task="price", intended_url=f"{SHOP}/api/price?symbol=ETH",
             final_url=f"{SHOP}/api/price?symbol=ETH")
    assert r.status_code == 200
    body = r.get_json()
    auth = body["payload"]["authorization"]
    assert re.fullmatch(r"0x[0-9a-f]{130}", body["payload"]["signature"])
    assert re.fullmatch(r"0x[0-9a-f]{64}", auth["nonce"])
    assert auth["to"] == TICKTOLL and auth["value"] == "10000" and "from" in auth
    assert int(auth["validAfter"]) < int(auth["validBefore"])
    assert body["accepted"]["payTo"] == TICKTOLL

    ev = client.get("/events").get_json()[-1]
    assert ev["type"] == "allowed" and ev["data"]["shop"] == "TickToll"
    assert len(ev["data"]["checks"]) == 8 and all(c["ok"] for c in ev["data"]["checks"])
    assert client.get("/stats").get_json()["allowed"] == 1


BLOCKED = [
    # (check id, requirements, context)
    ("origin", bill(origin=EVIL, pay_to=ATTACKER),
     {"intended_url": f"{SHOP}/api/price?symbol=ETH", "final_url": f"{EVIL}/api/price?symbol=ETH"}),
    ("asset", bill(asset="0x1111111111111111111111111111111111111111"), {}),
    ("asset", bill(network="eip155:1"), {}),
    ("payee", bill(pay_to=ATTACKER), {}),
    ("payee", bill(pay_to=LOOKALIKE), {}),
    ("price", bill(amount="50000"), {}),  # 5 cents for a 1-cent item
    ("mandate", bill(path="/api/report", amount="50000"),
     {"mandate": {"max_per_payment_usd": 0.02, "budget_usd": 0.50}}),
    ("expiry", bill(maxTimeoutSeconds=31536000), {}),
]


@pytest.mark.parametrize("check,requirements,context", BLOCKED,
                         ids=[f"{c}-{i}" for i, (c, _, _) in enumerate(BLOCKED)])
def test_bad_bill_is_blocked(client, check, requirements, context):
    r = sign(client, requirements, **context)
    assert r.status_code == 403
    body = r.get_json()
    assert body["blocked"] is True and body["check"] == check and body["reason"]

    ev = client.get("/events").get_json()[-1]
    assert ev["type"] == "blocked" and ev["data"]["check"] == check
    assert ev["data"]["bill"]["payTo"] == requirements["payTo"]
    stats = client.get("/stats").get_json()
    assert stats["blocked"] == 1 and stats["by_check"][check] == 1
    assert stats["saved_usd"] == int(requirements["amount"]) / 1_000_000


def test_duplicate_is_blocked(client):
    assert sign(client, bill()).status_code == 200
    r = sign(client, bill())
    assert r.status_code == 403 and r.get_json()["check"] == "duplicate"


def test_budget_is_blocked_after_running_total(client):
    mandate = {"max_per_payment_usd": 0.10, "budget_usd": 0.03}
    assert sign(client, bill("/api/price"), task="t", mandate=mandate).status_code == 200
    assert sign(client, bill("/api/sentiment", amount="20000"), task="t", mandate=mandate).status_code == 200
    r = sign(client, bill("/api/report", amount="10000"), task="t", mandate=mandate)
    assert r.status_code == 403 and r.get_json()["check"] == "mandate"
    # A different task has its own budget.
    assert sign(client, bill("/api/report", amount="10000"), task="other", mandate=mandate).status_code == 200


def test_price_reason_is_plain_english(client):
    CATALOGS[EVIL] = {"/evil/x": 0.01}
    try:
        r = sign(client, bill("/evil/x", amount="400000", origin=EVIL, pay_to=ATTACKER))
    finally:
        del CATALOGS[EVIL]
    assert r.get_json()["reason"] == "Price is 40x the usual 1¢"


def test_unknown_shop_is_pinned_to_first_wallet(client):
    assert sign(client, bill("/a", origin=EVIL, pay_to=ATTACKER)).status_code == 200  # no catalog: skipped
    r = sign(client, bill("/b", origin=EVIL, pay_to=OTHER))
    assert r.status_code == 403 and r.get_json()["check"] == "payee"
    assert sign(client, bill("/c", origin=EVIL, pay_to=ATTACKER)).status_code == 200


def test_guard_off_forwards_but_still_logs(client):
    assert client.post("/mode", json={"enabled": False}).get_json() == {"enabled": False}
    r = sign(client, bill(amount="900000", pay_to=ATTACKER))
    assert r.status_code == 200
    ev = client.get("/events").get_json()[-1]
    assert ev["type"] == "paid_unprotected" and ev["data"]["guard"] == "off"
    assert "payee" in ev["data"]["would_have_blocked"]
    assert client.get("/stats").get_json()["blocked"] == 0


def test_forwards_to_real_signer(client, monkeypatch):
    monkeypatch.setenv("MOCK_SIGNER", "false")
    monkeypatch.setenv("SIGNER_URL_REAL", "http://signer.test")
    seen = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"x402Version": 2, "signed": True}

    def fake_post(url, json, timeout):
        seen.update(url=url, body=json)
        return Resp()

    monkeypatch.setattr(server.requests, "post", fake_post)
    body = {"requirements": bill(), "context": {"task": "x"}}
    r = client.post("/sign", json=body)
    assert r.status_code == 200 and r.get_json()["signed"] is True
    assert seen["url"] == "http://signer.test/sign" and seen["body"] == body


def test_signer_down_releases_budget(client, monkeypatch):
    monkeypatch.setenv("MOCK_SIGNER", "false")
    monkeypatch.setenv("SIGNER_URL_REAL", "http://127.0.0.1:9")

    def boom(*a, **k):
        raise server.requests.ConnectionError("down")

    monkeypatch.setattr(server.requests, "post", boom)
    assert sign(client, bill()).status_code == 502
    monkeypatch.setenv("MOCK_SIGNER", "true")
    assert sign(client, bill()).status_code == 200  # not counted as a duplicate


def test_bad_requests(client):
    assert client.post("/sign", json={"nope": 1}).status_code == 400
    assert sign(client, bill(amount="-5")).status_code == 400
    assert sign(client, {"amount": "1"}).status_code == 400
    assert client.post("/mode", json={"enabled": "yes"}).status_code == 400


def test_reset_and_health(client):
    sign(client, bill(pay_to=ATTACKER))
    client.post("/mode", json={"enabled": False})
    assert client.post("/reset").get_json() == {"ok": True}
    stats = client.get("/stats").get_json()
    assert stats["checked"] == 0 and stats["enabled"] is True and client.get("/events").get_json() == []
    assert client.get("/health").get_json()["ok"] is True


def test_origin_normalisation():
    assert origin_of("HTTP://LocalHost:5001/x?y=1") == "http://localhost:5001"
    assert origin_of("https://a.example/x") == "https://a.example:443"
    assert origin_of("not a url") is None


def test_cap_blocks_when_owner_allows_big_payments(client):
    policy.update_rules({"max_per_payment_usd": 5, "budget_usd": 10})
    r = sign(client, bill("/api/unlisted", amount="900000"))  # no catalog price: only the cap stops it
    assert r.status_code == 403 and r.get_json()["check"] == "cap"


def test_task_mandate_cannot_loosen_owner_rules(client):
    r = sign(client, bill("/api/unlisted", amount="200000"), mandate={"max_per_payment_usd": 5, "budget_usd": 10})
    assert r.status_code == 403 and r.get_json()["check"] == "mandate"


def test_owner_rules_blocked_wallet_and_known_shops_only(client):
    policy.update_rules({"blocked_wallets": [TICKTOLL]})
    assert sign(client, bill()).get_json()["check"] == "payee"
    policy.update_rules({"blocked_wallets": [], "known_shops_only": True})
    r = sign(client, bill("/evil/x", origin=EVIL, pay_to=ATTACKER))
    assert r.status_code == 403 and "trusted shops" in r.get_json()["reason"]


def test_owner_can_switch_a_check_off(client):
    policy.update_rules({"checks": {"expiry": False}})
    assert sign(client, bill(maxTimeoutSeconds=31536000)).status_code == 200


def test_rules_endpoint_rejects_bad_values(client):
    assert client.post("/rules", json={"cap_usd": -1}).status_code == 400
    assert client.post("/rules", json={"blocked_wallets": ["nope"]}).status_code == 400
    assert client.post("/rules", json={"cap_usd": 0.25}).get_json()["cap_usd"] == 0.25

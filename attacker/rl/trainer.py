"""Training arena: the learning attacker (red) against the learning guard (blue), on the real Anvil
chain, paying directly through the guard (no LLM, so a round takes seconds, not minutes).

Per round: M red heists (tactics chosen by Thompson sampling) mixed with honest purchases. Every heist is
labelled by THIS engine (ground truth) and the label goes to the guard's token-protected /label.
Red's reward comes from the on-chain USDC balance of the attacker's wallets, never from a judge.
Before training we take an Anvil snapshot and always revert it afterwards, so training never costs the
demo wallet anything. What both sides learned is kept.
"""

import json
import math
import os
import random
import secrets
import threading
import time
from pathlib import Path

import requests

from attacker import runner
from attacker.attacks import ATTACKER, ATTACKS, LOOKALIKE
from attacker.rl.red import RedLearner, reward_for
from attacker.rl.tactics import FAMILY_TITLE, build, fresh_shop
from contracts.payment import (
    PAYMENT_REQUIRED, PAYMENT_RESPONSE, PAYMENT_SIGNATURE,
    PaymentPayload, PaymentRequired, SettlementResponse, decode_header, encode_header,
)

DISCOVERIES = Path(os.getenv("DISCOVERIES_FILE", Path(__file__).resolve().parents[2] / "tests" / "regression" / "discoveries.json"))
# Only the round-by-round history survives a restart (not a live run, which can't resume anyway):
# this is the "before vs after" story of a demo, so it must not vanish just because a service restarted.
TRAINING_LOG = Path(os.getenv("TRAINING_LOG", Path(__file__).resolve().parents[1] / "training_log.json"))
HONEST_IDS = [a["id"] for a in ATTACKS if a["defender_should"] == "allow"]
TIMEOUT = 20
red = RedLearner()
PLAIN: dict = {}
_lock = threading.RLock()
STATUS: dict = {}
_stop = threading.Event()


def fresh_status() -> dict:
    return {"running": False, "round": 0, "rounds": 0, "heists_per_round": 0, "done": 0, "error": None,
            "rounds_log": [], "recent": [], "started": None, "snapshot": None, "reverted": None}


def load_rounds_log() -> list:
    try:
        return json.loads(TRAINING_LOG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_rounds_log() -> None:
    try:
        tmp = TRAINING_LOG.with_suffix(".tmp")
        tmp.write_text(json.dumps(STATUS["rounds_log"]), encoding="utf-8")
        tmp.replace(TRAINING_LOG)
    except OSError:
        pass


STATUS.update(fresh_status())
STATUS["rounds_log"] = load_rounds_log()


# --- plumbing --------------------------------------------------------------------------

def token() -> str:
    return os.getenv("ARENA_TOKEN", "")


def arena(method: str, path: str, **kw):
    kw.setdefault("timeout", 10)
    return requests.request(method, f"{runner.DEFENDER_URL}{path}", headers={"X-Arena-Token": token()}, **kw)


def rpc(method: str, params=()):
    r = requests.post(runner.RPC_URL,
                      json={"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params)}, timeout=10)
    body = r.json()
    if "error" in body:
        raise RuntimeError(f"{method}: {body['error']}")
    return body["result"]


def preflight() -> None:
    if runner.MOCK:
        raise RuntimeError("MOCK=true: training needs the real chain")
    if not token():
        raise RuntimeError("ARENA_TOKEN is not set in .env")
    if not runner.chain_up():
        raise RuntimeError("Anvil is not reachable")
    r = arena("GET", "/health")
    if r.status_code != 200:
        raise RuntimeError("defender is not reachable")
    if arena("POST", "/arena/soft-reset").status_code != 200:
        raise RuntimeError("defender refused ARENA_TOKEN")


def shoot(heist: dict) -> dict:
    """Pay one heist's bill through the defender, like a buyer that trusts the shop. Never raises."""
    out = {"payments": [], "blocked": False, "decision_id": None, "failed": [], "total_checks": 8,
           "band": None, "error": None}
    ctx = {"task": heist["task"], "mandate": runner.mandate_for(heist["task"]),
           "intended_url": heist["intended"], "final_url": heist["final"]}
    url = heist["final"]
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        for _ in range(2):  # a dishonest shop may bill again after taking the money
            if resp.status_code != 402:
                break
            req = decode_header(resp.headers.get(PAYMENT_REQUIRED, ""), PaymentRequired).accepts[0]
            s = arena("POST", "/sign", json={"requirements": req.model_dump(), "context": ctx}, timeout=TIMEOUT)
            out["decision_id"] = s.headers.get("X-Decision-Id") or out["decision_id"]
            out["band"] = s.headers.get("X-Decision-Band") or out["band"]
            out["band"] = (s.json().get("band") if s.status_code == 403 else None) or out["band"]
            if s.status_code == 403:
                out["blocked"] = True
                body = s.json()
                out["failed"], out["total_checks"] = body.get("failed", []), body.get("total_checks", 8)
                return out
            if s.status_code != 200:
                out["error"] = f"signer {s.status_code}"
                return out
            payload = PaymentPayload.model_validate(s.json())
            resp = requests.get(url, headers={PAYMENT_SIGNATURE: encode_header(payload)}, timeout=TIMEOUT * 3)
            raw = resp.headers.get(PAYMENT_RESPONSE)
            if not raw:
                out["error"] = f"shop rejected the payment ({resp.status_code})"
                return out
            receipt = decode_header(raw, SettlementResponse)
            if not receipt.success:
                out["error"] = f"settlement failed: {receipt.errorReason}"
                return out
            out["payments"].append({"amount_usd": int(req.amount) / 1e6, "tx": receipt.transaction, "payTo": req.payTo})
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{e.__class__.__name__}: {e}"
    return out


def honest_heist(attack_id: str) -> dict:
    a = next(x for x in ATTACKS if x["id"] == attack_id)
    nonce = secrets.token_hex(4)
    url = a["url"]
    if attack_id == "honest-newshop":  # a shop nobody has seen: new alias every time
        url = f"{fresh_shop(5003)}/api/price?symbol=ETH"
    url += f"&n={nonce}"
    return {"intended": url, "final": url, "task": f"{a['task']} [{nonce}]", "watch": [], "family": "honest",
            "tactic": attack_id, "truth": "honest", "plain": a["plain"]}


def play(heist: dict) -> dict:
    """One heist end to end: reset per-heist guard state, fire, measure on chain, label."""
    arena("POST", "/arena/soft-reset")
    wallets = [ATTACKER, LOOKALIKE, *heist["watch"]]
    before = runner.balances(wallets)
    res = shoot(heist)
    after = runner.balances(wallets)
    if before is None or after is None:
        raise RuntimeError("lost the chain: cannot verify balances")
    gained = round(sum(after[w] - before[w] for w in wallets), 6)
    res["gained_usd"] = gained
    res["breach"] = heist["truth"] == "attack" and gained > 0
    res["tx"] = next((p["tx"] for p in res["payments"] if p.get("tx")), None)
    label = None
    if res["decision_id"]:
        r = arena("POST", "/label", json={"decision_id": res["decision_id"], "truth": heist["truth"]})
        label = r.json() if r.status_code == 200 else None
    res["label"] = label
    return res


# --- discoveries -----------------------------------------------------------------------

def load_discoveries() -> list:
    try:
        return json.loads(DISCOVERIES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_discovery(heist: dict, res: dict, rnd: int) -> dict | None:
    with _lock:
        found = load_discoveries()
        key = (heist["tactic"], res.get("band"))
        if any((d["tactic"], d.get("band")) == key for d in found):
            return None
        where = f"for a {res['band']} shop" if res.get("band") else ""
        d = {"id": f"d{len(found) + 1:03d}", "ts": round(time.time()), "round": rnd,
             "family": heist["family"], "tactic": heist["tactic"], "band": res.get("band"),
             "tx": res["tx"], "amount_usd": res["gained_usd"],
             "text": f"{heist['plain']} The guard let it through {where}: {res['gained_usd'] * 100:g}¢ of real test USDC "
                     f"reached an attacker wallet (tx {res['tx']}, rolled back after training).".replace("  ", " ")}
        found.append(d)
        DISCOVERIES.parent.mkdir(parents=True, exist_ok=True)
        DISCOVERIES.write_text(json.dumps(found, indent=2), encoding="utf-8")
        return d


# --- the training loop -----------------------------------------------------------------

def run_training(rounds: int, heists: int) -> None:
    snap = None
    try:
        preflight()
        snap = rpc("evm_snapshot")
        with _lock:  # the round log carries on across runs, so the race chart shows the whole history
            STATUS.update({**fresh_status(), "rounds_log": STATUS["rounds_log"]}, running=True, rounds=rounds,
                          heists_per_round=heists, started=time.time(), snapshot=snap)
            offset = len(STATUS["rounds_log"])
        runner_mode = requests.post(f"{runner.DEFENDER_URL}/mode", json={"enabled": True}, timeout=5)
        runner_mode.raise_for_status()
        arena("POST", "/learning/explore", json={"enabled": True}).raise_for_status()
        n_honest = max(len(HONEST_IDS), math.ceil(heists / 2))
        for rnd in range(1, rounds + 1):
            if _stop.is_set():
                break
            with _lock:
                STATUS["round"] = rnd
            plan = [("red", None)] * heists + [("honest", HONEST_IDS[i % len(HONEST_IDS)]) for i in range(n_honest)]
            random.shuffle(plan)
            m = {"round": offset + rnd, "attacks": 0, "breaches": 0, "honest": 0, "correct": 0, "false_blocks": 0,
                 "missed": 0, "labelled": 0, "red_reward": 0.0}
            for kind, hid in plan:
                if _stop.is_set():
                    break
                if kind == "red":
                    family, tactic = red.choose()
                    heist = build(family, tactic, random)
                else:
                    heist = honest_heist(hid)
                res = play(heist)
                if kind == "red":
                    frac = 1 - len(res["failed"]) / max(1, res["total_checks"]) if res["blocked"] else 1.0
                    r = reward_for(res["breach"], frac)
                    red.update(heist["family"], heist["tactic"], r, res["breach"])
                    m["attacks"] += 1
                    m["breaches"] += res["breach"]
                    m["red_reward"] += r
                    if res["breach"]:
                        save_discovery(heist, res, offset + rnd)
                else:
                    m["honest"] += 1
                if res["label"]:
                    m["labelled"] += 1
                    m["correct"] += res["label"]["kind"] == "correct"
                    m["false_blocks"] += res["label"]["kind"] == "false_block"
                    m["missed"] += res["label"]["kind"] == "missed"
                with _lock:
                    STATUS["done"] += 1
                    STATUS["recent"] = (STATUS["recent"] + [{
                        "round": rnd, "family": heist["family"], "tactic": heist["tactic"], "truth": heist["truth"],
                        "blocked": res["blocked"], "breach": res["breach"], "gained_usd": res["gained_usd"],
                        "band": res.get("band"), "error": res["error"], "plain": heist["plain"],
                        "tx": res.get("tx")}])[-30:]
            m["attacker_success_rate"] = round(m["breaches"] / m["attacks"], 3) if m["attacks"] else 0.0
            m["guard_accuracy"] = round(m["correct"] / m["labelled"], 3) if m["labelled"] else None
            m["red_reward"] = round(m["red_reward"], 2)
            m["red_families"] = {f: v["mean"] for f, v in red.snapshot()["families"].items()}
            m["blue_live"] = blue_live()
            with _lock:
                STATUS["rounds_log"].append(m)
            save_rounds_log()
    except Exception as e:  # noqa: BLE001
        with _lock:
            STATUS["error"] = f"{e.__class__.__name__}: {e}"
    finally:
        try:
            arena("POST", "/learning/explore", json={"enabled": False})
        except Exception:  # noqa: BLE001
            pass
        reverted = None
        if snap is not None:
            try:
                reverted = bool(rpc("evm_revert", [snap]))
            except Exception as e:  # noqa: BLE001
                reverted = False
                with _lock:
                    STATUS["error"] = (STATUS["error"] or "") + f" | evm_revert failed: {e}"
        with _lock:
            STATUS.update(running=False, reverted=reverted)
        _stop.clear()


def start(rounds: int, heists: int) -> tuple[bool, str]:
    with _lock:
        if STATUS["running"]:
            return False, "training is already running"
        STATUS["running"] = True  # claim the slot before the thread starts
    threading.Thread(target=run_training, args=(rounds, heists), daemon=True).start()
    return True, "started"


def stop() -> None:
    _stop.set()


def reset_all() -> None:
    red.reset()
    with _lock:
        STATUS.update(fresh_status())
    save_rounds_log()
    if token():
        try:
            arena("POST", "/learning/reset")
        except Exception:  # noqa: BLE001
            pass


def blue_live() -> dict | None:
    """The guard's current setting for each rule, per kind of shop (for the round log)."""
    try:
        bands = requests.get(f"{runner.DEFENDER_URL}/learning", timeout=3).json()["bands"]
        return {b: {k: v["live"] for k, v in knobs.items()} for b, knobs in bands.items()}
    except Exception:  # noqa: BLE001
        return None


def tactic_plain() -> dict:
    """One plain sentence per tactic, for the dashboard."""
    from attacker.rl.tactics import TACTICS
    rng = random.Random(0)
    return {t: build(f, t, rng)["plain"] for f, ts in TACTICS.items() for t in ts}


def learning_state() -> dict:
    blue = None
    try:
        blue = requests.get(f"{runner.DEFENDER_URL}/learning", timeout=3).json()
    except Exception:  # noqa: BLE001
        pass
    if not PLAIN:
        PLAIN.update(tactic_plain())
    with _lock:
        status = {k: v for k, v in STATUS.items()}
    return {"training": status, "red": red.snapshot(), "blue": blue, "discoveries": load_discoveries(),
            "family_titles": FAMILY_TITLE, "tactic_plain": PLAIN}

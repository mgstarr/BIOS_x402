# BIOS_x402

An AI agent that pays for data with USDC over [x402](https://x402.org), and a guard that checks
every bill before the wallet signs it. An attacker console tries to trick the agent into paying
the wrong price, the wrong wallet or the wrong token; the guard stops it with plain rules.

Everything runs on one laptop against a local fork of Base Sepolia (Anvil, chain id 84532),
with the real USDC contract, so every payment is a real on-chain transfer on that fork.

## Run the demo

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # then add the Phase 2 settings below
bash start_all.sh             # starts the chain and all services, logs in logs/
```

Open **http://localhost:8000**. It has three views, each meant for its own window:

| View | URL | What it shows |
| --- | --- | --- |
| Service provider | `/provider` | TickToll's shop: 9 data APIs (crypto, weather, temperature, air, shares, currencies, news) to try and buy, incoming requests, an invoice per payment |
| Guard (blue) | `/guard` | The latest block and its reason, the owner's rules (editable), and the AI agent: ask it anything and watch every step live, down to the block each payment lands in |
| Attacker (red) | `/attacker` | "Rob the Robot", a game for judges: pick a heist, tune it, see which rules would catch it (recon), launch it, and read the intel board |

`bash start_all.sh stop` stops everything. Ollama must be running with `qwen2.5:7b` for the
agent; attacks can also be sent "directly to the wallet", which skips the AI.

## How a payment flows

1. The **agent** (`agent/`, :7000) gets a task and asks a shop for data.
2. The **shop** answers HTTP 402 with a bill: TickToll (`shops/server.py`, :5001) is honest,
   EvilData (`shops/malicious.py`, :5002) is not.
3. The agent's wallet code (`payment/client.py`) sends the bill to the **guard**
   (`defender/`, :7003) with the task and the URL it meant to visit.
4. The guard runs eight checks (origin, asset, payee, price, mandate, spending cap, expiry,
   duplicate). One failure means HTTP 403 with a plain reason, and nothing is signed.
5. Allowed bills go to the **signer** (`signer/`, :7001), which holds the key. The shop has the
   **facilitator** (`payment/facilitator.py`, :7002) settle the transfer on **Anvil** (:8545).

The **attacker engine** (`attacker/`, :7004) fires the attacks and measures stolen USDC on-chain.
The **dashboard** (`dashboard/`, :8000) shows all of it.

## Folders

| Folder | What it is |
| --- | --- |
| `contracts/` | Shared data shapes (bill, payment, receipt, event, mandate). Team-owned. |
| `agent/` | The Ollama agent, its tools, and `demo.py` (prints one run step by step) |
| `payment/` | `client.py`: the agent's x402 wallet code. `facilitator.py`, `settlement.py`: settle on-chain |
| `signer/` | Holds the agent's private key, signs EIP-3009 transfers |
| `defender/` | The guard: checks, owner rules (`GET/POST /rules`), on/off switch, stats; `learner.py`: the learning guard |
| `shops/` | TickToll (honest), EvilData (dishonest) and NewShop (honest, brand-new, :5003) |
| `attacker/` | Attack catalogue, runner, evaluator, server; `rl/`: the learning attacker and the training arena |
| `dashboard/` | The three views at :8000 |
| `chain/` | Start the Anvil fork, sync its clock, set demo balances (`fund.sh`) |
| `tests/` | `unit/` (guard, attacker) and `integration/` (agent vs shop, full pipeline) |

## Reinforcement learning: the attacker and the guard teach each other

Train it from the Attacker page ("The attacker that teaches itself") or `POST :7004/train {"rounds", "heists"}`.

- **The arena** (`attacker/rl/trainer.py`) plays rounds on the real Anvil chain, paying straight through the
  guard (no LLM, so it is fast). Each round mixes attacks with honest purchases (TickToll, its honest
  peak-price route `/api/price-peak`, and NewShop on :5003, a brand-new honest shop). It takes an Anvil
  snapshot first and always rolls back afterwards: training never spends the demo wallet.
- **The attacker** (`attacker/rl/red.py`, `tactics.py`) is a two-level Thompson-sampling bandit: it picks a
  family (overpayment, wrong party, tricking the agent), then a tactic. Tactics are the catalogue attacks plus
  ones it generates near each rule's edge: prices just under the price rule, signatures just under the expiry
  rule, look-alike wallets sharing fewer characters, fresh wallets, hidden orders. Reward = USDC that really
  reached an attacker wallet (read on-chain), else half the share of guard rules it got past. Old evidence
  fades (discount 0.97), so it moves on once a hole is patched.
- **The guard** (`defender/learner.py`) learns per kind of shop (new / trusted / suspicious, from labelled
  history) how strict to set four rules: price above usual, signature lifetime, look-alike wallets, new-shop
  limit. Each is a small bandit with three levels, updated counterfactually: every labelled bill is replayed
  under every level. A safety gate only allows levels under which every honest bill seen still passes, and the
  owner's rules are a ceiling it can only tighten. Wallets that took attack money are refused from then on.
  Labels come only from the arena, through `ARENA_TOKEN`. What it learned is used live, in every payment.
- **Discoveries**: every hole the attacker finds is saved to `tests/regression/discoveries.json` and replayed by
  `RUN_REGRESSION=1 pytest tests/regression` with the stack running.

State is kept in `attacker/rl_state.json` and `defender/learner_state.json` (not committed).
"Forget all learning" on the Attacker page resets both.

## Phase 2 settings for `.env`

```
SIGNER_URL=http://localhost:7003        # the agent signs through the guard
SIGNER_URL_REAL=http://localhost:7001   # the guard forwards allowed bills here
DEFENDER_URL=http://localhost:7003
ATTACKER_URL=http://localhost:7004
EVIL_URL=http://localhost:5002
LOOKALIKE_ADDRESS=0x3c44cDdd1B2C3D4e5F60718293a4b5c6d74293bC
COMPROMISE_DEMO=true
RUN_MODE=agent
AGENT_PRIVATE_KEY=<Anvil account #0 key>       # agent wallet 0xf39F...2266
FACILITATOR_PRIVATE_KEY=<Anvil account #1 key> # pays gas only
MOCK_SIGNER=false
MOCK_FACILITATOR=false
ARENA_TOKEN=<any long random string>    # lets the arena label bills for the learning guard
NEWSHOP_URL=http://localhost:5003
```

Note: on public Base Sepolia, Anvil account #0 has EIP-7702 code attached by a stranger (its key
is public), which makes USDC reject its signatures. `start_all.sh` removes that code on the
local fork only, then `chain/fund.sh` gives the agent 100 USDC.

## Tests

```bash
.venv/bin/python -m pytest -q tests/unit
```

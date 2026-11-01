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
| `defender/` | The guard: checks, owner rules (`GET/POST /rules`), on/off switch, stats |
| `shops/` | TickToll (honest) and EvilData (dishonest) shops |
| `attacker/` | Attack catalogue, runner, evaluator, server |
| `dashboard/` | The three views at :8000 |
| `chain/` | Start the Anvil fork, sync its clock, set demo balances (`fund.sh`) |
| `tests/` | `unit/` (guard, attacker) and `integration/` (agent vs shop, full pipeline) |

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
```

Note: on public Base Sepolia, Anvil account #0 has EIP-7702 code attached by a stranger (its key
is public), which makes USDC reject its signatures. `start_all.sh` removes that code on the
local fork only, then `chain/fund.sh` gives the agent 100 USDC.

## Tests

```bash
.venv/bin/python -m pytest -q tests/unit
```

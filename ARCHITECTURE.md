# ARCHITECTURE

> Snapshot taken on branch `rl` (cut from `main`). **The real defender is not on `main`**: `defender/*.py` there are one-line
> stubs. The working defender (8 checks, `/mode`, `/stats`, `/reset`, defender dashboard page) lives only on `origin/defender`
> (4 commits ahead). Everything below about the defender describes `origin/defender`.

## Services

| Service | Folder | Port | Start |
| --- | --- | --- | --- |
| Anvil (Base Sepolia fork, chain 84532, 1 s blocks) | `chain/` | 8545 | `bash chain/start_chain.sh` |
| Agent (Ollama LLM loop, `/run`) | `agent/` | 7000 | `python app.py agent` |
| Signer (holds agent key, `/sign`) | `signer/` | 7001 | `python -m signer.server` |
| Facilitator (`/verify`, `/settle`, submits tx) | `payment/facilitator.py` | 7002 | `python -m payment.facilitator` |
| Defender (guard in front of signer, `/sign`) | `defender/` | 7003 | `python -m defender.server` (no `app.py` entry yet) |
| Attacker engine | `attacker/` | 7004 | `python app.py attacker` |
| TickToll honest shop (+ `/compromised/*`) | `shops/server.py` | 5001 | `python app.py shop` |
| EvilData malicious shop | `shops/malicious.py` | 5002 | `python app.py evil` |
| Dashboard | `dashboard/` | 8000 | `python app.py dashboard` |

Flow: shop 402 -> buyer POSTs `{requirements, context}` to **defender /sign** -> checks -> 403, or forward to signer -> buyer
retries shop with signature -> shop calls facilitator `/verify`+`/settle` -> `transferWithAuthorization` on Anvil.
Env: `MOCK` (attacker/dashboard simulate), `MOCK_SIGNER`, `MOCK_FACILITATOR`, `RUN_MODE` (`direct` default | `agent`),
`DEFENDER_URL` (default 7003), `SIGNER_URL_REAL` (defender -> signer, default 7001). There is **no ARENA_TOKEN** in `.env` yet.

## Defender checks (`defender/policy.py`, `evaluate()` runs all 8 once per bill)

Priority / ids: `origin, asset, payee, price, mandate, cap, expiry, duplicate`. Headline = first failure.

| id | rule | threshold |
| --- | --- | --- |
| origin | intended-URL origin == final-URL origin | exact (lowercased scheme/host/port) |
| asset | `req.asset == USDC_ADDRESS` and network `eip155:84532` | exact |
| payee | valid address; not a lookalike; matches trusted/pinned wallet for the origin | lookalike = same first **8** + last **6** hex chars as a known wallet but not equal; TickToll wallet fixed, other shops pinned to first payTo seen |
| price | amount <= `PRICE_MULTIPLIER` x reference catalog price | **3x**; reference only from TickToll's `/catalog` (`/api/<name>`, else `/api/price`); skipped if none |
| mandate | per-payment limit and task budget | from request context; default **$0.10** per payment, **$0.50** budget per task string |
| cap | absolute drain cap | **$0.50** (`DRAIN_CAP_USD`) |
| expiry | `maxTimeoutSeconds` | **<= 300 s** (`MAX_TIMEOUT_S`), > 0 |
| duplicate | same `resource` (full URL incl. query) paid in last window | **60 s** |

State: in-memory `Ledger` (payments let through, per task), `PayeeBook` (pinned wallets), `CatalogCache` (60 s TTL). Constants
are module globals today, not per-request parameters.

## Attacker engine (`attacker/`, port 7004)

- `attacks.py`: 8 attacks (`overcharge, drain, wrong-wallet, lookalike, fake-token, long-expiry, pay-again, injection`, all
  `defender_should=block`, each with `expected_check`) + 2 honest controls (`honest-price`, `honest-sentiment`, `allow`).
- `runner.fire(attack, player, custom)`: `RUN_MODE=direct` GETs the shop for the 402, POSTs `{requirements, context}` to the
  defender (`context` = task, mandate `{0.10, 0.50}`, `intended_url`, `final_url`), then retries the shop with the signature.
  `RUN_MODE=agent` posts the task to the agent (LLM). `MOCK=true` simulates. Injection in direct mode just jumps
  `intended_url` (news page) -> `final_url` (`/evil/report`). Loss = USDC balance delta of `ATTACKER` + `LOOKALIKE` on chain
  (fallback: sum of payments not to TickToll).
- **Custom heist**: `fire(..., custom={price_usd, pay_to, token, expiry_s, hidden_text})` hits `EvilData /evil/custom?...`, which
  builds a real bill from the query. `pay_to` is limited to the choices `attacker | lookalike | shop` (no arbitrary address);
  `token` is `real | fake`; `hidden_text` only echoes in the paid response (only matters in agent mode). Custom runs are scored
  as `defender_should=block`.
- `evaluator.py`: `outcome()` = blocked / stolen / paid / no_payment / refused; `suite()` runs all attacks with guard ON then OFF
  (`POST defender /mode`).
- `server.py` routes: `GET /health /attacks /scoreboard /wallet /events`, `POST /fire {attack_id|custom, player}`, `POST /suite`.
  Scoreboard = in-memory per attack and per player, split by guard state. Events: `attack`, `result`.

## Honest controls

`honest-price` (TickToll `/api/price`, 1c) and `honest-sentiment` (`/api/sentiment`, 2c). Nothing else. No "peak price" route
and no honest brand-new shop exist yet.

## Events, stats, /mode, scoreboard

- `contracts/events.py`: `Event{ts, service, type, data}`, in-memory `EventLog` (no lock, no clear), served at `GET /events` by each service.
- Defender events: `allowed | blocked | paid_unprotected | failed`; `data` has verdict, shop, item, amount, payee, `check` (headline),
  `failed` ids, all 8 `checks`, mandate, bill, context. No decision id.
- Defender `GET /stats`: checked, allowed, blocked, unprotected, saved_usd, by_check. `POST /reset` clears ledger, payees, catalog, stats.
- `/mode` GET/POST `{enabled}`: guard OFF -> verdict `paid_unprotected` (checks still run, nothing blocks).
- Scoreboard lives in the attacker (above); defender dashboard "Run full test" calls attacker `POST /suite`.

## Dashboard (port 8000, Flask, all assets local, no CDN)

Pages: `/` landing (3 big cards), `/attack` ("Rob the Robot" game: `dashboard/attacker.py`, `assets/attack.js`),
`/defend` (`dashboard/defender.py` blueprint, only on `origin/defender`; main shows a placeholder). Shared shell in `layout.py`.
Style: dark (`#0f1115` bg, `#1a1d24` panels), red/blue/green/amber accents, 18 px base, big type for a projector, rounded 18 px
panels, `theme-red` (attack) / `theme-blue` (defend). Defend page: guard on/off shield button, ON-vs-OFF scorecard, stat tiles,
filterable live feed, check legend. `MOCK=true` shows a SIMULATED badge.

## Chain facts relevant to RL

Attacker-controlled wallets watched: `ATTACKER` (Anvil #3) and `LOOKALIKE`. Facilitator reads block timestamp for expiry.
Anvil `evm_snapshot` / `evm_revert` are available on the running fork.

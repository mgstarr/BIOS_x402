# BIOS_x402

AI Agent Payment Security Layer using x402.

An LLM-driven agent pays for things over HTTP using the
[x402](https://x402.org) "exact" payment scheme on EVM (Base Sepolia,
`eip155:84532`), with a defender enforcing a spending mandate in between, and
an attacker harness probing the whole pipeline for weaknesses.

## Folder ownership

| Folder | Owner |
| --- | --- |
| `contracts/` | Shared — team-owned, changes need agreement from everyone |
| `agent/`, `payment/client.py` | Team A |
| `chain/`, `signer/`, `payment/facilitator.py`, `payment/settlement.py` | Team B |
| `shops/`, `app.py` | Team C |
| `attacker/`, `dashboard/` | Shared |

## Ports

| Service | Port |
| --- | --- |
| agent | 7000 |
| signer | 7001 |
| facilitator | 7002 |
| anvil (local chain) | 8545 |
| shop | 5001 |

## Running

```bash
cp .env.example .env     # fill in keys / addresses
pip install -r requirements.txt
python app.py <service>  # e.g. python app.py agent
```

Each service (`agent`, `signer`, `facilitator`, `shop`, `defender`, `attacker`,
`dashboard`) is started the same way, by name, via the root `app.py`.

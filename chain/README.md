# chain/

Local Base Sepolia fork (chain id 84532) that the signer and facilitator talk to. Owned by Team B.

## Start

```bash
bash chain/start_chain.sh      # fork pinned to the BLOCK set at the top of the script
```

Anvil serves on `http://127.0.0.1:8545`. Because the fork is pinned, balances and contract state are
the same on every restart. A fresh fork's clock is the pinned block's time, hours behind the laptop, so
`start_chain.sh` calls `chain/sync_time.sh` once Anvil is up. That script mines a block and, if chain time
differs from the laptop by more than 30 s, sets the next block timestamp to now and mines again. Run it
by hand any time against a running chain: `bash chain/sync_time.sh`.

`.env` needs `AGENT_ADDRESS` and `SHOP_PAY_TO`.

## Check

```bash
python -m chain.check_chain
```

Prints chain id, USDC name and version, and the agent and shop balances in USDC.

## Fund the agent

Route 1, faucet: request test USDC for the agent address from the Circle faucet (Base Sepolia).
The fork only sees it if you restart Anvil with a `BLOCK` after the faucet transaction.

Route 2, impersonation fallback (works on the pinned fork, no waiting). Pick any address that holds
USDC at the fork block, then:

```bash
HOLDER=0x...   # a USDC holder at the pinned block
USDC=0x036CbD53842c5426634e7929541eC2318f3dCF7e
AGENT=0xE573E712a863754Db86546E2C7b44F519B1c468A
cast rpc anvil_impersonateAccount $HOLDER --rpc-url http://127.0.0.1:8545
cast rpc anvil_setBalance $HOLDER 0xDE0B6B3A7640000 --rpc-url http://127.0.0.1:8545   # gas for the holder
cast send $USDC "transfer(address,uint256)" $AGENT 20000000 --from $HOLDER --unlocked --rpc-url http://127.0.0.1:8545
```

Then run the check again.

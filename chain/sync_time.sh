#!/usr/bin/env bash
# Moves the fork's clock to the laptop clock so validAfter/validBefore windows are real.
RPC="${RPC_URL:-http://127.0.0.1:8545}"
MAX_DRIFT=1

cast rpc evm_mine --rpc-url "$RPC" > /dev/null
chain_ts=$(cast block latest -f timestamp --rpc-url "$RPC")
now=$(date +%s)
drift=$(( now - chain_ts ))
echo "before: chain=$chain_ts laptop=$now drift=${drift}s"

if [ "${drift#-}" -gt "$MAX_DRIFT" ]; then
  cast rpc evm_setNextBlockTimestamp "$now" --rpc-url "$RPC" > /dev/null
  cast rpc evm_mine --rpc-url "$RPC" > /dev/null
  chain_ts=$(cast block latest -f timestamp --rpc-url "$RPC")
  now=$(date +%s)
  echo "after:  chain=$chain_ts laptop=$now drift=$(( now - chain_ts ))s"
fi

# Keep the clock moving: one block per second (no-op if Anvil already runs with --block-time 1).
cast rpc anvil_setIntervalMining 1 --rpc-url "$RPC" > /dev/null

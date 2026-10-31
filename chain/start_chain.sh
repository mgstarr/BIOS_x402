#!/usr/bin/env bash
# Starts a Base Sepolia fork pinned to a fixed block, then syncs its clock to the laptop.
BLOCK=52764660
DIR="$(cd "$(dirname "$0")" && pwd)"

anvil --fork-url https://sepolia.base.org --fork-block-number "$BLOCK" \
  --chain-id 84532 --host 0.0.0.0 --port 8545 --block-time 1 &
ANVIL_PID=$!
trap 'kill $ANVIL_PID 2>/dev/null' EXIT INT TERM

for _ in $(seq 1 60); do
  cast chain-id --rpc-url http://127.0.0.1:8545 > /dev/null 2>&1 && break
  sleep 1
done

bash "$DIR/sync_time.sh"
wait $ANVIL_PID

#!/usr/bin/env bash
# Start the whole Phase 2 demo on laptop A, each service in the background with its log in logs/.
# Usage: bash start_all.sh        (stop everything: bash start_all.sh stop)
cd "$(dirname "$0")"
PY=.venv/bin/python
mkdir -p logs

# name:port:command, in start order (the chain first, the dashboard last)
SERVICES=(
  "signer:7001:$PY -m signer.server"
  "facilitator:7002:$PY -m payment.facilitator"
  "defender:7003:$PY app.py defender"
  "shop:5001:$PY app.py shop"
  "evil:5002:$PY app.py evil"
  "agent:7000:$PY app.py agent"
  "attacker:7004:$PY app.py attacker"
  "dashboard:8000:$PY app.py dashboard"
)

for s in "${SERVICES[@]}"; do  # stop old copies so new code and .env are picked up
  IFS=: read -r _ port _ <<< "$s"
  kill $(lsof -t -i:"$port" -sTCP:LISTEN 2>/dev/null) 2>/dev/null
done
[ "$1" = stop ] && exit 0

if ! lsof -i:8545 -sTCP:LISTEN >/dev/null; then  # Anvil keeps running between restarts
  nohup bash chain/start_chain.sh > logs/anvil.log 2>&1 &
  sleep 5
fi
# The agent wallet (Anvil #0) has EIP-7702 code on real Base Sepolia (its key is public), so USDC
# rejects its signatures. Remove that code on our local fork only.
~/.foundry/bin/cast rpc --rpc-url http://localhost:8545 anvil_setCode \
  0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266 0x > /dev/null
bash chain/fund.sh  # demo start: agent wallet 100 USDC, attacker wallet 0

sleep 1
for s in "${SERVICES[@]}"; do
  IFS=: read -r name port cmd <<< "$s"
  nohup $cmd > "logs/$name.log" 2>&1 &
done
sleep 4

for s in "anvil:8545" "${SERVICES[@]}"; do
  IFS=: read -r name port _ <<< "$s"
  lsof -i:"$port" -sTCP:LISTEN >/dev/null && echo "up    $name :$port" || echo "DOWN  $name :$port (see logs/$name.log)"
done

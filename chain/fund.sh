#!/usr/bin/env bash
# Local fork only: set USDC balances for a clean demo start (agent 100 USDC, attacker 0).
# USDC v2.2 keeps each balance in storage slot 9 (mapping address => balance).
# Usage: bash chain/fund.sh [agent_usdc]
CAST=~/.foundry/bin/cast
RPC=${RPC_URL:-http://localhost:8545}
USDC=0x036CbD53842c5426634e7929541eC2318f3dCF7e
AGENT=0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
ATTACKER=0x90F79bf6EB2c4f870365E785982E1f101E93b906
LOOKALIKE=0x3c44cDdd1B2C3D4e5F60718293a4b5c6d74293bC

set_balance() {  # address, amount in whole USDC
  local value
  value=$(printf "0x%064x" $(($2 * 1000000)))
  $CAST rpc --rpc-url "$RPC" anvil_setStorageAt $USDC "$($CAST index address "$1" 9)" "$value" > /dev/null
}

set_balance $AGENT "${1:-100}"
set_balance $ATTACKER 0
set_balance $LOOKALIKE 0
echo "agent $($CAST call --rpc-url "$RPC" $USDC 'balanceOf(address)(uint256)' $AGENT | cut -d' ' -f1) units of USDC"

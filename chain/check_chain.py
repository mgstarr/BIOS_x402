"""Prints chain id, token name/version and the agent and shop USDC balances."""

import sys
import time

from web3 import Web3

from chain import config


def main() -> int:
    w3 = Web3(Web3.HTTPProvider(config.RPC_URL))
    if not w3.is_connected():
        print(f"Cannot reach {config.RPC_URL}. Start the chain with chain/start_chain.sh")
        return 1
    if not config.AGENT_ADDRESS or not config.SHOP_PAY_TO:
        print("AGENT_ADDRESS and SHOP_PAY_TO must be set in .env")
        return 1
    usdc = w3.eth.contract(address=Web3.to_checksum_address(config.USDC_ADDRESS), abi=config.USDC_ABI)
    scale = 10**config.USDC_DECIMALS
    print("chain id:", w3.eth.chain_id)
    print("block:", w3.eth.block_number)
    print("chain time minus laptop time:", w3.eth.get_block("latest")["timestamp"] - round(time.time()), "s")
    print("token name:", usdc.functions.name().call())
    print("token version:", usdc.functions.version().call())
    for role, addr in (("agent", config.AGENT_ADDRESS), ("shop", config.SHOP_PAY_TO)):
        raw = usdc.functions.balanceOf(Web3.to_checksum_address(addr)).call()
        print(f"{role} {addr} balance: {round(raw / scale, 6)} USDC")
    return 0


if __name__ == "__main__":
    sys.exit(main())

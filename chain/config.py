"""Chain config: shared network, chain ID, contract address, ABI and role constants."""

import os

from dotenv import load_dotenv

load_dotenv()

RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:8545")
CHAIN_ID = int(os.getenv("CHAIN_ID", "84532"))
NETWORK = f"eip155:{CHAIN_ID}"
USDC_ADDRESS = os.getenv("USDC_ADDRESS", "0x036CbD53842c5426634e7929541eC2318f3dCF7e")
USDC_DECIMALS = 6
FORK_BLOCK = 52764660

# Public addresses only. Private keys are never loaded here.
AGENT_ADDRESS = os.getenv("AGENT_ADDRESS", "")
SHOP_PAY_TO = os.getenv("SHOP_PAY_TO", "")
FACILITATOR_ADDRESS = os.getenv("FACILITATOR_ADDRESS", "")  # Anvil account #0

ROLES = {
    "agent": "payer: signs EIP-3009 authorizations, needs no ETH",
    "shop": "payee: receives USDC at SHOP_PAY_TO",
    "facilitator": "relayer: submits transferWithAuthorization and pays gas (Anvil #0)",
}


def _fn(name: str, inputs: list[tuple[str, str]], outputs: list[str], mutability: str) -> dict:
    return {
        "type": "function",
        "name": name,
        "stateMutability": mutability,
        "inputs": [{"name": n, "type": t} for n, t in inputs],
        "outputs": [{"name": "", "type": t} for t in outputs],
    }


USDC_ABI = [
    _fn("balanceOf", [("account", "address")], ["uint256"], "view"),
    _fn("name", [], ["string"], "view"),
    _fn("version", [], ["string"], "view"),
    _fn("authorizationState", [("authorizer", "address"), ("nonce", "bytes32")], ["bool"], "view"),
    _fn(
        "transferWithAuthorization",
        [
            ("from", "address"),
            ("to", "address"),
            ("value", "uint256"),
            ("validAfter", "uint256"),
            ("validBefore", "uint256"),
            ("nonce", "bytes32"),
            ("v", "uint8"),
            ("r", "bytes32"),
            ("s", "bytes32"),
        ],
        [],
        "nonpayable",
    ),
]

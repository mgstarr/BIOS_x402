"""Settlement logic: submits verified authorizations on-chain and reports the result."""

import os

from dotenv import load_dotenv
from web3 import Web3

from chain import config

load_dotenv()


def get_web3() -> Web3:
    return Web3(Web3.HTTPProvider(config.RPC_URL))


def usdc(w3: Web3):
    return w3.eth.contract(address=Web3.to_checksum_address(config.USDC_ADDRESS), abi=config.USDC_ABI)


def balance_of(address: str) -> int:
    w3 = get_web3()
    return usdc(w3).functions.balanceOf(Web3.to_checksum_address(address)).call()


def auth_used(authorizer: str, nonce: str) -> bool:
    w3 = get_web3()
    fn = usdc(w3).functions.authorizationState(Web3.to_checksum_address(authorizer), bytes.fromhex(nonce[2:]))
    return fn.call()


def latest_timestamp() -> int:
    return get_web3().eth.get_block("latest")["timestamp"]


def split_signature(signature: str) -> tuple[int, bytes, bytes]:
    raw = bytes.fromhex(signature[2:])
    if len(raw) != 65:
        raise ValueError("signature must be 65 bytes")
    v = raw[64]
    return (v + 27 if v < 27 else v), raw[:32], raw[32:64]


def send_transfer_with_authorization(auth: dict, signature: str) -> str:
    """Send transferWithAuthorization from the facilitator account. Returns the tx hash."""
    key = os.getenv("FACILITATOR_PRIVATE_KEY")
    if not key:
        raise RuntimeError("FACILITATOR_PRIVATE_KEY is not set in .env")
    w3 = get_web3()
    account = w3.eth.account.from_key(key)
    v, r, s = split_signature(signature)
    fn = usdc(w3).functions.transferWithAuthorization(
        Web3.to_checksum_address(auth["from"]),
        Web3.to_checksum_address(auth["to"]),
        int(auth["value"]),
        int(auth["validAfter"]),
        int(auth["validBefore"]),
        bytes.fromhex(auth["nonce"][2:]),
        v, r, s,
    )
    tx = fn.build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "chainId": config.CHAIN_ID,
    })
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    if receipt["status"] != 1:
        raise RuntimeError(f"transaction reverted: {tx_hash.hex()}")
    return "0x" + tx_hash.hex().removeprefix("0x")

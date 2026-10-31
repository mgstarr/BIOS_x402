"""Signing logic: builds and signs EIP-3009 authorizations for x402 payments."""

import secrets
import time

from eth_account.messages import encode_typed_data
from eth_account.signers.local import LocalAccount

from contracts.payment import Authorization, PaymentPayload, PaymentRequirements

TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ],
}


def build_typed_data(auth: Authorization, req: PaymentRequirements) -> dict:
    """EIP-712 message for an authorization. Domain comes from the requirements, never hardcoded."""
    return {
        "types": TYPES,
        "primaryType": "TransferWithAuthorization",
        "domain": {
            "name": req.extra["name"],
            "version": req.extra["version"],
            "chainId": int(req.network.split(":")[1]),
            "verifyingContract": req.asset,
        },
        "message": {
            "from": auth.from_,
            "to": auth.to,
            "value": int(auth.value),
            "validAfter": int(auth.validAfter),
            "validBefore": int(auth.validBefore),
            "nonce": bytes.fromhex(auth.nonce[2:]),
        },
    }


def new_authorization(req: PaymentRequirements, payer: str) -> Authorization:
    return Authorization(
        **{"from": payer},
        to=req.payTo,
        value=req.amount,
        validAfter="0",
        validBefore=str(int(time.time()) + req.maxTimeoutSeconds),
        nonce="0x" + secrets.token_hex(32),
    )


def sign_requirements(req: PaymentRequirements, account: LocalAccount) -> PaymentPayload:
    auth = new_authorization(req, account.address)
    signed = account.sign_message(encode_typed_data(full_message=build_typed_data(auth, req)))
    signature = signed.signature.hex()
    if not signature.startswith("0x"):
        signature = "0x" + signature
    return PaymentPayload(
        accepted=req,
        payload={"signature": signature, "authorization": auth.model_dump(by_alias=True)},
    )

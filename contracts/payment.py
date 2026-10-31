"""
Shared x402 (HTTP 402 Payment Required) data contracts, v2 "exact" scheme on EVM.

These models are the wire format every service in this repo speaks. Nobody should
redefine their own version of these shapes -- import them from here instead.

Service APIs that produce/consume these models:
  - signer:       POST /sign    {requirements: PaymentRequirements} -> PaymentPayload
                  GET  /address -> {"address": str}
  - facilitator:  POST /verify  {paymentPayload, paymentRequirements} -> VerifyResponse
                  POST /settle  {paymentPayload, paymentRequirements} -> SettlementResponse
  - shop:         GET  /catalog -> 402 PaymentRequired, or 200 catalog data once paid
  - agent:        POST /run     {task: str, mandate: Mandate} -> answer
  - every service: GET  /events -> recent Event log entries
"""

import base64
import json

from pydantic import BaseModel, ConfigDict, Field

# HTTP header names used to carry x402 payloads between client and server.
PAYMENT_REQUIRED = "PAYMENT-REQUIRED"
PAYMENT_SIGNATURE = "PAYMENT-SIGNATURE"
PAYMENT_RESPONSE = "PAYMENT-RESPONSE"


class PaymentRequirements(BaseModel):
    scheme: str = "exact"
    network: str = "eip155:84532"
    amount: str
    asset: str
    payTo: str
    maxTimeoutSeconds: int = 60
    resource: str
    description: str = ""
    extra: dict = Field(default_factory=lambda: {"name": "USDC", "version": "2"})


class PaymentRequired(BaseModel):
    x402Version: int = 2
    error: str = "payment_required"
    accepts: list[PaymentRequirements]


class Authorization(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    value: str
    validAfter: str
    validBefore: str
    nonce: str  # "0x" + 32 bytes hex


class PaymentPayload(BaseModel):
    x402Version: int = 2
    accepted: PaymentRequirements
    payload: dict  # {"signature": str, "authorization": Authorization}


class SettlementResponse(BaseModel):
    success: bool
    transaction: str | None = None
    network: str = "eip155:84532"
    payer: str | None = None
    errorReason: str | None = None


class VerifyResponse(BaseModel):
    isValid: bool
    invalidReason: str | None = None
    payer: str | None = None


def encode_header(model: BaseModel) -> str:
    """Serialize a model to the base64-encoded JSON string carried in an x402 header."""
    raw = model.model_dump_json(by_alias=True).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def decode_header(value: str, model_cls: type[BaseModel]):
    """Decode a base64-encoded JSON header value back into the given model class."""
    raw = base64.b64decode(value)
    return model_cls.model_validate(json.loads(raw))

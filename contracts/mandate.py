"""Shared Mandate contract: the spending limits an agent is allowed to operate under."""

from pydantic import BaseModel


class Mandate(BaseModel):
    task: str
    max_per_payment_usd: float
    budget_usd: float

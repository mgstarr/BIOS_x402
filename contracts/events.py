"""Shared Event contract and in-memory EventLog used by every service's GET /events."""

import time
from typing import Literal

from pydantic import BaseModel, Field


class Event(BaseModel):
    ts: float = Field(default_factory=time.time)
    service: str
    type: Literal[
        "request",
        "payment_required",
        "signed",
        "verified",
        "settled",
        "failed",
        "answer",
        "allowed",
        "blocked",
        "paid_unprotected",
        "attack",
        "result",
    ]
    data: dict = Field(default_factory=dict)


class EventLog:
    """A tiny in-memory ring of recent events for a service to expose over /events."""

    def __init__(self) -> None:
        self._events: list[Event] = []

    def append(self, event: Event) -> None:
        self._events.append(event)

    def last(self, n: int = 100) -> list[Event]:
        return self._events[-n:]

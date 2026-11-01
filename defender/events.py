"""Defender's EventLog instance and helpers for recording its own events."""

from typing import Literal

from contracts.events import Event, EventLog

SERVICE = "defender"


class DefenderEvent(Event):
    """The shared Event, plus "paid_unprotected": the guard was OFF and let a bill through unchecked."""

    type: Literal["allowed", "blocked", "paid_unprotected", "failed"]


class DefenderLog(EventLog):
    """EventLog plus a clear() for POST /reset."""

    def clear(self) -> None:
        self._events.clear()


log = DefenderLog()


def record(event_type: str, data: dict) -> Event:
    event = DefenderEvent(service=SERVICE, type=event_type, data=data)
    log.append(event)
    return event

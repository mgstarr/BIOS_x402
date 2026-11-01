"""Defender's EventLog instance and helpers for recording its own events."""

from contracts.events import Event, EventLog

SERVICE = "defender"


class DefenderLog(EventLog):
    """EventLog plus a clear() for POST /reset."""

    def clear(self) -> None:
        self._events.clear()


log = DefenderLog()


def record(event_type: str, data: dict) -> Event:
    event = Event(service=SERVICE, type=event_type, data=data)
    log.append(event)
    return event

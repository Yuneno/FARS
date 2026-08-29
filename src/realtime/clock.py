"""Clock abstraction so replay and tests do not use wall-clock time."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """Wall-clock UTC. Not for replay determinism."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock:
    """Fixed instant. Replay/tests advance time only by calling ``set``."""

    def __init__(self, instant: datetime) -> None:
        self.set(instant)

    def now(self) -> datetime:
        return self._instant

    def set(self, instant: datetime) -> None:
        if not isinstance(instant, datetime):
            raise ValueError(f"instant must be a datetime, got {type(instant).__name__}")
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("clock instant must be timezone-aware")
        self._instant = instant

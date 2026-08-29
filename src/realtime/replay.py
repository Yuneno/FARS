"""RT-4 replay engine: recorded events into the live canonical pipeline.

Uses FrozenClock so timing is deterministic. Strategy/Risk are unchanged
subscribers of the same bus.

Origin policy: reconstructed events keep the origin stored in the journal.
Replay is the session (FrozenClock + exclusive bus), not a rewrite of
historical provenance. New Strategy/Risk events minted during the session
choose their own origin; this engine does not emit them.
"""

from __future__ import annotations

from pathlib import Path

from src.realtime.clock import FrozenClock
from src.realtime.events import CanonicalEvent
from src.realtime.interfaces import AsyncEventBus
from src.realtime.recorder import reconstruct_events


class ReplayError(ValueError):
    """Replay cannot run with a wall clock or an unreadable journal."""


class ReplayEngine:
    """Publish reconstructed journal events in file order onto an async bus."""

    def __init__(self, path: str | Path, *, bus: AsyncEventBus, clock: FrozenClock) -> None:
        if not isinstance(clock, FrozenClock):
            raise ReplayError("replay requires FrozenClock for deterministic time")
        self._path = Path(path)
        self._bus = bus
        self._clock = clock

    async def play(self) -> int:
        events: tuple[CanonicalEvent, ...] = reconstruct_events(self._path)
        session = getattr(self._bus, "replay_session", None)
        if callable(session):
            async with session():  # type: ignore[misc]
                return await self._emit(events)
        await self._bus.wait_idle()
        return await self._emit(events)

    async def _emit(self, events: tuple[CanonicalEvent, ...]) -> int:
        count = 0
        for event in events:
            self._clock.set(event.timestamp)
            await self._bus.publish(event)
            await self._bus.wait_idle()
            count += 1
        return count

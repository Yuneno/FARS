"""RT-2 bounded asyncio event bus.

Only canonical events enter the bus. Duplicates are counted and not
re-delivered. Late/gap/conflict events are delivered without reordering.
Overflow never drops events silently.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

from src.realtime.events import (
    AccountSnapshot,
    Bar,
    CanonicalEvent,
    ExecutionReport,
    MarketTick,
    MarketTrade,
    OrderIntent,
    Quote,
    RiskDecision,
    Signal,
    SystemEvent,
)
from src.realtime.ordering import OrderingClass, SequenceTracker

_CANONICAL = (
    MarketTick,
    Quote,
    Bar,
    MarketTrade,
    AccountSnapshot,
    Signal,
    RiskDecision,
    OrderIntent,
    ExecutionReport,
    SystemEvent,
)

_SENTINEL = object()
_OVERFLOW = {"block", "error"}

Subscriber = Callable[[CanonicalEvent], Awaitable[None] | None]


class BusError(ValueError):
    """Illegal bus use, non-canonical input, or backpressure rejection."""


class AsyncIOEventBus:
    """Bounded asyncio.Queue bus. start() must run before publish()."""

    def __init__(
        self,
        *,
        maxsize: int = 256,
        overflow: Literal["block", "error"] = "block",
    ) -> None:
        if not isinstance(maxsize, int) or isinstance(maxsize, bool) or maxsize < 1:
            raise BusError("maxsize must be a positive integer")
        if overflow not in _OVERFLOW:
            raise BusError("overflow must be 'block' or 'error'")
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._overflow = overflow
        self._subs: list[Subscriber] = []
        self._tracker = SequenceTracker()
        self._task: asyncio.Task | None = None
        self._accepting = False
        self._error: BaseException | None = None
        self.received = 0
        self.delivered = 0
        self.duplicates = 0
        self.late = 0
        self.gaps = 0
        self.conflicts = 0
        self.backpressure_rejects = 0

    def subscribe(self, callback: Subscriber) -> None:
        if not callable(callback):
            raise BusError("subscriber must be callable")
        self._subs.append(callback)

    async def start(self) -> None:
        if self._task is not None:
            raise BusError("bus already started")
        self._accepting = True
        self._task = asyncio.create_task(self._run(), name="fars-event-bus")

    async def publish(self, event: object) -> None:
        if not isinstance(event, _CANONICAL):
            raise BusError(
                f"bus accepts canonical events only, got {type(event).__name__}"
            )
        if not self._accepting or self._task is None or self._task.done():
            raise BusError("bus is not running")
        self.received += 1
        kind = self._tracker.classify(event)
        if kind is OrderingClass.DUPLICATE:
            self.duplicates += 1
            return
        if kind is OrderingClass.LATE:
            self.late += 1
        elif kind is OrderingClass.SEQUENCE_GAP:
            self.gaps += 1
        elif kind is OrderingClass.CONFLICT:
            self.conflicts += 1
        if self._overflow == "error" and self._queue.full():
            self.backpressure_rejects += 1
            raise BusError("bounded queue is full")
        await self._queue.put(event)

    async def shutdown(self, *, drain: bool = True) -> None:
        self._accepting = False
        if self._task is None:
            return
        if self._task.done():
            self._reraise()
            return
        if drain and self._error is None:
            await self._queue.put(_SENTINEL)
            await self._task
        else:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._reraise()

    def _reraise(self) -> None:
        if self._error is not None:
            raise BusError("subscriber failed") from self._error

    async def _run(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                try:
                    if item is _SENTINEL:
                        return
                    await self._deliver(item)
                    self.delivered += 1
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._error = exc
            self._accepting = False

    async def _deliver(self, event: CanonicalEvent) -> None:
        for callback in list(self._subs):
            result = callback(event)
            if asyncio.iscoroutine(result):
                await result

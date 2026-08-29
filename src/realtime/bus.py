"""RT-2 bounded asyncio event bus.

Only canonical events enter the bus. Duplicates are counted and not
re-delivered. Late/gap/conflict events are delivered without reordering.
Overflow never drops events silently.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import astuple, dataclass
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
    identity_key,
    stream_key,
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


@dataclass
class _Inflight:
    fingerprint: tuple
    event_id: str
    completed: asyncio.Future


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
        self._lock = asyncio.Lock()
        self._pending: dict[tuple[str, str], _Inflight] = {}
        self._pending_seq: dict[tuple[str, int], str] = {}
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
        self.received += 1
        id_key = identity_key(event)
        seq_key = stream_key(event)
        fingerprint = astuple(event)
        while True:
            waiter: asyncio.Future | None = None
            owner = False
            async with self._lock:
                if not self._accepting or self._task is None or self._task.done():
                    raise BusError("bus is not running")
                inflight = self._pending.get(id_key)
                if inflight is not None:
                    if inflight.fingerprint != fingerprint:
                        self.conflicts += 1
                        self._accepting = False
                        raise BusError("critical identity conflict; bus halted")
                    waiter = inflight.completed
                else:
                    pending_id = self._pending_seq.get(seq_key)
                    if pending_id is not None and pending_id != id_key[1]:
                        self.conflicts += 1
                        self._accepting = False
                        raise BusError("critical identity conflict; bus halted")
                    kind = self._tracker.classify(event, commit=False)
                    if kind is OrderingClass.DUPLICATE:
                        self.duplicates += 1
                        return
                    if kind is OrderingClass.CONFLICT:
                        self.conflicts += 1
                        self._accepting = False
                        raise BusError("critical identity conflict; bus halted")
                    if self._overflow == "error" and self._queue.full():
                        self.backpressure_rejects += 1
                        raise BusError("bounded queue is full")
                    completed = asyncio.get_running_loop().create_future()
                    self._pending[id_key] = _Inflight(fingerprint, id_key[1], completed)
                    self._pending_seq[seq_key] = id_key[1]
                    owner = True
                    if self._overflow == "error":
                        self._queue.put_nowait(event)
                        committed = self._tracker.classify(event, commit=True)
                        self._finish_inflight(id_key, seq_key, True)
                        self._record_committed(committed)
                        return
            if waiter is not None:
                # Cancelling one duplicate must not cancel the shared inflight
                # result for every other publisher waiting on this identity.
                enqueued = await asyncio.shield(waiter)
                if enqueued:
                    self.duplicates += 1
                    return
                continue
            if not owner:
                continue
            queued = False
            try:
                try:
                    self._queue.put_nowait(event)
                except asyncio.QueueFull:
                    put_task = asyncio.create_task(self._queue.put(event))
                    dispatcher = self._task
                    if dispatcher is None:
                        put_task.cancel()
                        await asyncio.gather(put_task, return_exceptions=True)
                        raise BusError("bus is not running")
                    try:
                        done, _pending = await asyncio.wait(
                            {put_task, dispatcher},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    except BaseException:
                        put_task.cancel()
                        await asyncio.gather(put_task, return_exceptions=True)
                        # queue.put may have completed immediately before this
                        # task was cancelled. Commit that accepted event so a
                        # retry cannot enqueue it a second time.
                        queued = (
                            put_task.done()
                            and not put_task.cancelled()
                            and put_task.exception() is None
                        )
                        raise
                    if dispatcher in done:
                        if not put_task.done():
                            put_task.cancel()
                            await asyncio.gather(put_task, return_exceptions=True)
                        self._reraise()
                        raise BusError("bus is not running")
                    await put_task
                queued = True
                async with self._lock:
                    committed = self._tracker.classify(event, commit=True)
                    self._finish_inflight(id_key, seq_key, True)
                    self._record_committed(committed)
                return
            except BaseException:
                async with self._lock:
                    if queued:
                        if id_key in self._pending:
                            committed = self._tracker.classify(event, commit=True)
                            self._finish_inflight(id_key, seq_key, True)
                            self._record_committed(committed)
                    else:
                        self._finish_inflight(id_key, seq_key, False)
                raise

    def _record_committed(self, kind: OrderingClass) -> None:
        if kind is OrderingClass.LATE:
            self.late += 1
        elif kind is OrderingClass.SEQUENCE_GAP:
            self.gaps += 1
        elif kind is OrderingClass.CONFLICT:
            self.conflicts += 1
            self._accepting = False
            raise BusError("critical identity conflict; bus halted")

    def _finish_inflight(
        self, id_key: tuple[str, str], seq_key: tuple[str, int], enqueued: bool
    ) -> None:
        inflight = self._pending.pop(id_key, None)
        if self._pending_seq.get(seq_key) == id_key[1]:
            self._pending_seq.pop(seq_key, None)
        if inflight is not None and not inflight.completed.done():
            inflight.completed.set_result(enqueued)

    async def wait_idle(self) -> None:
        join = asyncio.ensure_future(self._queue.join())
        wait_for = {join}
        if self._task is not None:
            wait_for.add(self._task)
        _done, pending = await asyncio.wait(
            wait_for, return_when=asyncio.FIRST_COMPLETED
        )
        if join in pending:
            join.cancel()
            try:
                await join
            except asyncio.CancelledError:
                pass
        self._reraise()

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

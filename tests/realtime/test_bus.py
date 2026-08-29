"""RT-2 AsyncIOEventBus tests. Uses asyncio.run — no extra pytest plugin."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.realtime.bus import AsyncIOEventBus, BusError
from src.realtime.events import MarketTick
from src.realtime.interfaces import AsyncEventBus

TS = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


def _tick(event_id: str, sequence: int) -> MarketTick:
    return MarketTick(
        event_id=event_id,
        source="feed",
        timestamp=TS,
        sequence=sequence,
        symbol="MNQ",
        price=1.0,
        volume=1.0,
    )


def test_rejects_non_canonical_and_unbounded_config():
    with pytest.raises(BusError, match="positive"):
        AsyncIOEventBus(maxsize=0)
    async def _run():
        bus = AsyncIOEventBus(maxsize=2)
        assert isinstance(bus, AsyncEventBus)
        await bus.start()
        with pytest.raises(BusError, match="canonical"):
            await bus.publish({"type": "tick"})
        await bus.shutdown()
    asyncio.run(_run())


def test_publish_requires_start_and_duplicates_are_not_redelivered():
    async def _run():
        bus = AsyncIOEventBus(maxsize=8)
        seen: list[str] = []
        bus.subscribe(lambda event: seen.append(event.event_id))
        with pytest.raises(BusError, match="not running"):
            await bus.publish(_tick("e1", 1))
        await bus.start()
        await bus.publish(_tick("e1", 1))
        await bus.publish(_tick("e1", 1))
        await bus.publish(_tick("e2", 2))
        await bus.shutdown()
        assert seen == ["e1", "e2"]
        assert bus.duplicates == 1
        assert bus.delivered == 2
    asyncio.run(_run())


def test_backpressure_error_does_not_drop_silently():
    async def _run():
        hold = asyncio.Event()
        entered = asyncio.Event()
        bus = AsyncIOEventBus(maxsize=1, overflow="error")

        async def blocker(_event):
            entered.set()
            await hold.wait()

        bus.subscribe(blocker)
        await bus.start()
        await bus.publish(_tick("e1", 1))
        await entered.wait()
        await bus.publish(_tick("e2", 2))
        with pytest.raises(BusError, match="full"):
            await bus.publish(_tick("e3", 3))
        assert bus.backpressure_rejects == 1
        hold.set()
        for _ in range(50):
            if bus.delivered >= 2:
                break
            await asyncio.sleep(0.01)
        await bus.publish(_tick("e3", 3))
        await bus.shutdown()
        assert bus.delivered == 3
        assert bus.duplicates == 0
    asyncio.run(_run())


def test_concurrent_same_identity_is_idempotent():
    async def _run():
        bus = AsyncIOEventBus(maxsize=1, overflow="block")
        seen: list[str] = []
        bus.subscribe(lambda event: seen.append(event.event_id))
        await bus.start()
        event = _tick("e3", 3)
        await asyncio.gather(bus.publish(event), bus.publish(event), bus.publish(event))
        await bus.shutdown()
        assert seen == ["e3"]
        assert bus.duplicates == 2
        assert bus.delivered == 1
    asyncio.run(_run())


def test_concurrent_same_sequence_different_ids_conflicts():
    async def _run():
        hold = asyncio.Event()
        entered = asyncio.Event()
        bus = AsyncIOEventBus(maxsize=1, overflow="block")
        seen: list[tuple[str, int]] = []

        async def blocker(event):
            seen.append((event.event_id, event.sequence))
            entered.set()
            await hold.wait()

        bus.subscribe(blocker)
        await bus.start()
        await bus.publish(_tick("e1", 1))
        await entered.wait()
        await bus.publish(_tick("e2", 2))
        task_a = asyncio.create_task(bus.publish(_tick("e3-a", 3)))
        task_b = asyncio.create_task(bus.publish(_tick("e3-b", 3)))
        await asyncio.sleep(0.05)
        hold.set()
        results = await asyncio.gather(task_a, task_b, return_exceptions=True)
        try:
            await bus.shutdown()
        except BusError:
            pass
        assert bus.conflicts >= 1
        assert any(isinstance(item, BusError) for item in results)
        assert len([item for item in seen if item[1] == 3]) <= 1
    asyncio.run(_run())


def test_gap_is_delivered_without_reordering():
    async def _run():
        bus = AsyncIOEventBus(maxsize=8)
        seen: list[int] = []
        bus.subscribe(lambda event: seen.append(event.sequence))
        await bus.start()
        await bus.publish(_tick("e1", 1))
        await bus.publish(_tick("e3", 3))
        await bus.shutdown()
        assert seen == [1, 3]
        assert bus.gaps == 1
    asyncio.run(_run())


def test_subscriber_error_fails_closed():
    async def _run():
        bus = AsyncIOEventBus(maxsize=4)

        def boom(_event):
            raise RuntimeError("handler")

        bus.subscribe(boom)
        await bus.start()
        await bus.publish(_tick("e1", 1))
        await asyncio.sleep(0.02)
        with pytest.raises(BusError, match="subscriber failed"):
            await bus.shutdown()
    asyncio.run(_run())


def test_pending_duplicate_survives_owner_cancel():
    async def _run():
        hold = asyncio.Event()
        entered = asyncio.Event()
        bus = AsyncIOEventBus(maxsize=1, overflow="block")
        seen: list[str] = []

        async def blocker(event):
            seen.append(event.event_id)
            entered.set()
            await hold.wait()

        bus.subscribe(blocker)
        await bus.start()
        await bus.publish(_tick("e1", 1))
        await entered.wait()
        await bus.publish(_tick("e2", 2))
        owner = asyncio.create_task(bus.publish(_tick("e3", 3)))
        await asyncio.sleep(0.05)
        duplicate = asyncio.create_task(bus.publish(_tick("e3", 3)))
        await asyncio.sleep(0.05)
        owner.cancel()
        hold.set()
        await asyncio.gather(owner, duplicate, return_exceptions=True)
        await bus.shutdown()
        assert "e3" in seen
        assert bus.delivered == 3
    asyncio.run(_run())


def test_cancel_after_enqueue_does_not_redeliver():
    async def _run():
        bus = AsyncIOEventBus(maxsize=8)
        seen: list[str] = []
        bus.subscribe(lambda event: seen.append(event.event_id))
        await bus.start()
        original = bus._lock
        state = {"n": 0}

        class Gate:
            async def __aenter__(self):
                await original.acquire()
                state["n"] += 1
                if state["n"] == 2:
                    original.release()
                    raise asyncio.CancelledError()
                return None

            async def __aexit__(self, *args):
                original.release()
                return False

        bus._lock = Gate()
        with pytest.raises(asyncio.CancelledError):
            await bus.publish(_tick("e1", 1))
        bus._lock = original
        await bus.publish(_tick("e1", 1))
        await bus.shutdown()
        assert seen == ["e1"]
        assert bus.delivered == 1
        assert bus.duplicates == 1
    asyncio.run(_run())


def test_wait_idle_raises_if_dispatcher_dies_with_queued_events():
    async def _run():
        entered = asyncio.Event()
        bus = AsyncIOEventBus(maxsize=8)

        async def boom(event):
            if event.event_id == "e1":
                entered.set()
                await asyncio.sleep(0.05)
                raise RuntimeError("handler")

        bus.subscribe(boom)
        await bus.start()
        await bus.publish(_tick("e1", 1))
        await entered.wait()
        await bus.publish(_tick("e2", 2))
        with pytest.raises(BusError, match="subscriber failed"):
            await asyncio.wait_for(bus.wait_idle(), timeout=1)
        try:
            await bus.shutdown()
        except BusError:
            pass
    asyncio.run(_run())


def test_blocked_publisher_raises_if_dispatcher_dies():
    async def _run():
        hold = asyncio.Event()
        entered = asyncio.Event()
        bus = AsyncIOEventBus(maxsize=1, overflow="block")

        async def boom(event):
            if event.event_id == "e1":
                entered.set()
                await hold.wait()
                raise RuntimeError("handler")

        bus.subscribe(boom)
        await bus.start()
        await bus.publish(_tick("e1", 1))
        await entered.wait()
        await bus.publish(_tick("e2", 2))
        blocked = asyncio.create_task(bus.publish(_tick("e3", 3)))
        await asyncio.sleep(0.05)
        hold.set()
        with pytest.raises(BusError, match="subscriber failed"):
            await asyncio.wait_for(blocked, timeout=1)
        try:
            await bus.shutdown()
        except BusError:
            pass

    asyncio.run(_run())


def test_cancelling_duplicate_does_not_cancel_other_waiters():
    async def _run():
        hold = asyncio.Event()
        entered = asyncio.Event()
        bus = AsyncIOEventBus(maxsize=1, overflow="block")
        seen: list[str] = []

        async def blocker(event):
            seen.append(event.event_id)
            if event.event_id == "e1":
                entered.set()
                await hold.wait()

        bus.subscribe(blocker)
        await bus.start()
        await bus.publish(_tick("e1", 1))
        await entered.wait()
        await bus.publish(_tick("e2", 2))
        event = _tick("e3", 3)
        owner = asyncio.create_task(bus.publish(event))
        duplicate_a = asyncio.create_task(bus.publish(event))
        duplicate_b = asyncio.create_task(bus.publish(event))
        await asyncio.sleep(0.05)
        duplicate_a.cancel()
        hold.set()
        results = await asyncio.gather(
            owner, duplicate_a, duplicate_b, return_exceptions=True
        )
        await bus.shutdown()
        assert results[0] is None
        assert isinstance(results[1], asyncio.CancelledError)
        assert results[2] is None
        assert seen == ["e1", "e2", "e3"]
        assert bus.duplicates == 1

    asyncio.run(_run())


def test_pending_consecutive_sequences_do_not_report_false_gap():
    async def _run():
        hold = asyncio.Event()
        entered = asyncio.Event()
        bus = AsyncIOEventBus(maxsize=1, overflow="block")
        seen: list[int] = []

        async def blocker(event):
            seen.append(event.sequence)
            if event.event_id == "e1":
                entered.set()
                await hold.wait()

        bus.subscribe(blocker)
        await bus.start()
        await bus.publish(_tick("e1", 1))
        await entered.wait()
        await bus.publish(_tick("e2", 2))
        sequence_3 = asyncio.create_task(bus.publish(_tick("e3", 3)))
        await asyncio.sleep(0)
        sequence_4 = asyncio.create_task(bus.publish(_tick("e4", 4)))
        await asyncio.sleep(0.05)
        hold.set()
        await asyncio.gather(sequence_3, sequence_4)
        await bus.shutdown()
        assert seen == [1, 2, 3, 4]
        assert bus.gaps == 0
        assert bus.late == 0

    asyncio.run(_run())

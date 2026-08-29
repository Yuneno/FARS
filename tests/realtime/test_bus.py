"""RT-2 AsyncIOEventBus tests. Uses asyncio.run — no extra pytest plugin."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.realtime.bus import AsyncIOEventBus, BusError
from src.realtime.events import MarketTick

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
        await bus.shutdown()
        assert bus.delivered == 2
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

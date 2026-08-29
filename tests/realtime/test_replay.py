"""RT-4 replay engine tests."""

from datetime import datetime, timedelta, timezone

import asyncio
import pytest

from typing import Literal

from src.realtime.bus import AsyncIOEventBus, BusError
from src.realtime.clock import FrozenClock, SystemClock
from src.realtime.events import MarketTick, RiskDecision, Signal
from src.realtime.recorder import FileEventRecorder, RecorderError
from src.realtime.replay import ReplayEngine, ReplayError

TS = datetime(2024, 7, 1, 10, 0, tzinfo=timezone.utc)


def _tick(
    event_id: str,
    sequence: int,
    when: datetime,
    origin: Literal["live", "replay"] = "live",
    source: str = "feed",
) -> MarketTick:
    return MarketTick(
        event_id=event_id,
        source=source,
        timestamp=when,
        sequence=sequence,
        symbol="MNQ",
        price=1.0,
        volume=1.0,
        origin=origin,
    )


class _Strategy:
    def on_event(self, event):
        if isinstance(event, MarketTick):
            return Signal(
                event_id=f"sig-{event.event_id}",
                source="strategy",
                timestamp=event.timestamp,
                sequence=event.sequence,
                symbol=event.symbol,
                action="LONG",
                origin=event.origin,
            )
        return None


class _Risk:
    def evaluate(self, signal: Signal) -> RiskDecision:
        return RiskDecision(
            event_id=f"risk-{signal.event_id}",
            source="risk",
            timestamp=signal.timestamp,
            sequence=signal.sequence,
            signal_id=signal.event_id,
            signal_source=signal.source,
            approved=True,
            reason="ok",
            origin=signal.origin,
        )


def _run_pipeline(journal, clock: FrozenClock):
    bus = AsyncIOEventBus(maxsize=8)
    events: list[tuple[str, datetime, str]] = []
    signals: list[tuple[str, str]] = []
    decisions: list[tuple[str, str, bool]] = []
    strategy = _Strategy()
    risk = _Risk()

    def pipeline(event):
        events.append((event.event_id, clock.now(), event.origin))
        signal = strategy.on_event(event)
        if signal is None:
            return
        signals.append((signal.event_id, signal.origin))
        decision = risk.evaluate(signal)
        decisions.append((decision.event_id, decision.origin, decision.approved))

    async def _run():
        bus.subscribe(pipeline)
        await bus.start()
        played = await ReplayEngine(journal, bus=bus, clock=clock).play()
        await bus.shutdown()
        return played

    played = asyncio.run(_run())
    return played, events, signals, decisions


def test_rejects_wall_clock():
    bus = AsyncIOEventBus(maxsize=8)
    with pytest.raises(ReplayError, match="FrozenClock"):
        ReplayEngine("journal.jsonl", bus=bus, clock=SystemClock())  # type: ignore[arg-type]


def test_replay_preserves_order_and_clock(tmp_path):
    journal = tmp_path / "session.jsonl"
    recorder = FileEventRecorder(journal)
    t1 = _tick("e1", 1, TS)
    t2 = _tick("e2", 2, TS + timedelta(seconds=5))
    recorder.record(t1)
    recorder.record(t2)
    played, seen, _signals, _decisions = _run_pipeline(journal, FrozenClock(TS))
    assert played == 2
    assert [item[0] for item in seen] == ["e1", "e2"]
    assert seen[0][1] == t1.timestamp
    assert seen[1][1] == t2.timestamp


def test_missing_journal_fails_closed(tmp_path):
    bus = AsyncIOEventBus(maxsize=4)
    clock = FrozenClock(TS)

    async def _run():
        await bus.start()
        with pytest.raises(RecorderError):
            await ReplayEngine(tmp_path / "missing.jsonl", bus=bus, clock=clock).play()
        await bus.shutdown()

    asyncio.run(_run())


def test_replay_fails_if_subscriber_fails(tmp_path):
    journal = tmp_path / "session.jsonl"
    FileEventRecorder(journal).record(_tick("e1", 1, TS))
    bus = AsyncIOEventBus(maxsize=4)
    clock = FrozenClock(TS)

    def boom(_event):
        raise RuntimeError("handler")

    bus.subscribe(boom)

    async def _run():
        await bus.start()
        with pytest.raises(BusError, match="subscriber failed"):
            await ReplayEngine(journal, bus=bus, clock=clock).play()
        try:
            await bus.shutdown()
        except BusError:
            pass

    asyncio.run(_run())


def test_replay_waits_for_busy_bus_before_moving_clock(tmp_path):
    journal = tmp_path / "session.jsonl"
    replay_ts = TS + timedelta(seconds=5)
    FileEventRecorder(journal).record(_tick("e1", 1, replay_ts))
    clock = FrozenClock(TS)
    bus = AsyncIOEventBus(maxsize=8)
    hold = asyncio.Event()
    entered = asyncio.Event()
    observed: list[tuple[str, datetime]] = []

    async def handler(event):
        observed.append((event.event_id, clock.now()))
        if event.event_id == "prior":
            entered.set()
            await hold.wait()
            observed.append((event.event_id, clock.now()))

    async def _run():
        bus.subscribe(handler)
        await bus.start()
        await bus.publish(_tick("prior", 1, TS, origin="live", source="warmup"))
        await entered.wait()
        play = asyncio.create_task(ReplayEngine(journal, bus=bus, clock=clock).play())
        await asyncio.sleep(0.05)
        assert clock.now() == TS
        hold.set()
        assert await play == 1
        await bus.shutdown()

    asyncio.run(_run())
    assert observed[0] == ("prior", TS)
    assert observed[1] == ("prior", TS)
    assert observed[2] == ("e1", replay_ts)


def test_replay_rejects_concurrent_producers(tmp_path):
    journal = tmp_path / "session.jsonl"
    FileEventRecorder(journal).record(_tick("e1", 1, TS))
    clock = FrozenClock(TS)
    bus = AsyncIOEventBus(maxsize=8)
    hold = asyncio.Event()
    entered = asyncio.Event()

    async def handler(event):
        if event.event_id == "e1":
            entered.set()
            await hold.wait()

    async def _run():
        bus.subscribe(handler)
        await bus.start()
        play = asyncio.create_task(ReplayEngine(journal, bus=bus, clock=clock).play())
        await entered.wait()
        with pytest.raises(BusError, match="owns"):
            await bus.publish(_tick("sneak", 2, TS + timedelta(seconds=1)))
        hold.set()
        assert await play == 1
        await bus.shutdown()

    asyncio.run(_run())


def test_live_journal_keeps_recorded_origin(tmp_path):
    journal = tmp_path / "session.jsonl"
    FileEventRecorder(journal).record(_tick("e1", 1, TS, origin="live"))
    _played, events, signals, decisions = _run_pipeline(journal, FrozenClock(TS))
    assert events == [("e1", TS, "live")]
    assert signals == [("sig-e1", "live")]
    assert decisions == [("risk-sig-e1", "live", True)]


def test_replay_is_bit_for_bit_across_two_pipelines(tmp_path):
    journal = tmp_path / "session.jsonl"
    recorder = FileEventRecorder(journal)
    recorder.record(_tick("e1", 1, TS, origin="live"))
    recorder.record(_tick("e2", 2, TS + timedelta(seconds=5), origin="live"))
    first = _run_pipeline(journal, FrozenClock(TS))
    second = _run_pipeline(journal, FrozenClock(TS))
    assert first == second
    played, events, signals, decisions = first
    assert played == 2
    assert [item[0] for item in events] == ["e1", "e2"]
    assert [item[1] for item in events] == [TS, TS + timedelta(seconds=5)]
    assert signals == [("sig-e1", "live"), ("sig-e2", "live")]
    assert decisions == [
        ("risk-sig-e1", "live", True),
        ("risk-sig-e2", "live", True),
    ]


def test_recorded_decision_chain_is_replayed_as_stored(tmp_path):
    journal = tmp_path / "session.jsonl"
    recorder = FileEventRecorder(journal)
    tick = _tick("e1", 1, TS, origin="live")
    signal = _Strategy().on_event(tick)
    assert signal is not None
    decision = _Risk().evaluate(signal)
    recorder.record(tick)
    recorder.record(signal)
    recorder.record(decision)
    clock = FrozenClock(TS)
    bus = AsyncIOEventBus(maxsize=8)
    seen: list[tuple[str, str]] = []

    def handler(event):
        seen.append((type(event).__name__, event.origin))

    async def _run():
        bus.subscribe(handler)
        await bus.start()
        played = await ReplayEngine(journal, bus=bus, clock=clock).play()
        await bus.shutdown()
        return played

    assert asyncio.run(_run()) == 3
    assert seen == [
        ("MarketTick", "live"),
        ("Signal", "live"),
        ("RiskDecision", "live"),
    ]

"""Acceptance tests for the causal CRT 4H port."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.backtest.crt4h import Crt4hStrategy, _SessionAggregator, crt4h_config
from src.backtest.executor import run_backtest
from src.backtest.history import Bar
from src.backtest.markets import MNQ
from src.realtime.bus import AsyncIOEventBus
from src.realtime.clock import FrozenClock
from src.realtime.events import Bar as RealtimeBar, ORIGIN_REPLAY
from src.realtime.recorder import FileEventRecorder
from src.realtime.replay import ReplayEngine


def _bar(ts: datetime, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(ts, o, h, l, c, 10.0)


def _signal_bars() -> list[Bar]:
    """One long setup; C1 starts exactly at the 18:00 ET CME anchor."""
    start = datetime(2026, 1, 5, 23, 0, tzinfo=UTC)
    bars = [_bar(start + timedelta(minutes=5 * i), 100, 130, 90, 100) for i in range(48)]
    for i in range(48, 56):
        high = 105 if i == 54 else 101
        bars.append(_bar(start + timedelta(minutes=5 * i), 100, high, 99, 100))
    bars.append(_bar(start + timedelta(minutes=5 * 56), 95, 100, 89, 95))
    bars.append(_bar(start + timedelta(minutes=5 * 57), 95, 101, 94, 101))
    bars.append(_bar(start + timedelta(minutes=5 * 58), 100, 101, 99, 100))
    bars.append(_bar(start + timedelta(minutes=5 * 59), 103, 127, 102, 126))
    return bars


def _signal_tuple(signal):
    if signal is None:
        return None
    return signal.direction, signal.entry, signal.stop, signal.target


def test_crt4h_signal_uses_only_closed_bars_and_no_future_data():
    bars = _signal_bars()
    strategy = Crt4hStrategy(require_bias=0)
    assert strategy.evaluate(bars[:-1]) is None
    expected = _signal_tuple(strategy.evaluate(bars))
    assert expected == ("long", 101, 87.9, 130)

    altered_future = bars + [_bar(bars[-1].timestamp + timedelta(minutes=5), 500, 600, 1, 2)]
    replayed = Crt4hStrategy(require_bias=0)
    observed = None
    for i in range(len(bars)):
        observed = _signal_tuple(replayed.evaluate(altered_future[: i + 1]))
    assert observed == expected


def test_crt4h_replay_signals_equal_sequential_backtest_signals():
    bars = _signal_bars()
    reference = Crt4hStrategy(require_bias=0)
    expected = []
    for i in range(len(bars)):
        signal = reference.evaluate(bars[: i + 1])
        if signal is not None:
            expected.append((i, bars[i].timestamp, _signal_tuple(signal)))

    journal = Path("lab_artifacts/c2_protocol/.test_crt4h_replay.jsonl")
    journal.unlink(missing_ok=True)
    recorder = FileEventRecorder(journal)
    for i, bar in enumerate(bars):
        recorder.record(RealtimeBar(
            event_id=f"bar-{i}", source="test", timestamp=bar.timestamp,
            sequence=i + 1, symbol="MNQ", interval="5m", open=bar.open,
            high=bar.high, low=bar.low, close=bar.close, volume=bar.volume,
            origin=ORIGIN_REPLAY,
        ))

    clock = FrozenClock(bars[0].timestamp)
    bus = AsyncIOEventBus(maxsize=16)
    replay_strategy = Crt4hStrategy(require_bias=0)
    history: list[Bar] = []
    actual = []

    def consume(event):
        if isinstance(event, RealtimeBar):
            history.append(_bar(event.timestamp, event.open, event.high, event.low, event.close))
            signal = replay_strategy.evaluate(history)
            if signal is not None:
                actual.append((len(history) - 1, event.timestamp, _signal_tuple(signal)))

    async def play():
        bus.subscribe(consume)
        await bus.start()
        count = await ReplayEngine(journal, bus=bus, clock=clock).play()
        await bus.shutdown()
        return count

    try:
        assert asyncio.run(play()) == len(bars)
        assert actual == expected
        assert len(actual) == 1
    finally:
        journal.unlink(missing_ok=True)


def test_crt4h_stop_wins_tie_on_limit_fill_bar():
    bars = _signal_bars()
    fill_bar = _bar(bars[-1].timestamp + timedelta(minutes=5), 105, 135, 85, 100)
    result = run_backtest(
        bars + [fill_bar],
        Crt4hStrategy(require_bias=0),
        crt4h_config(fixed_quantity=1),
    )
    assert result.n_trades == 1
    assert result.trades[0].entry_price == 101
    assert result.trades[0].exit_reason == "stop_loss"
    assert result.trades[0].exit_time == fill_bar.timestamp


def test_crt4h_d1_and_h4_aggregation_are_anchored_to_18_et_across_dst():
    agg = _SessionAggregator(MNQ)
    timestamps = [
        datetime(2026, 3, 7, 22, 55, tzinfo=UTC),  # 17:55 EST
        datetime(2026, 3, 7, 23, 0, tzinfo=UTC),   # 18:00 EST
        datetime(2026, 3, 8, 2, 0, tzinfo=UTC),    # 21:00 EST, same H4
        datetime(2026, 3, 8, 3, 0, tzinfo=UTC),    # 22:00 EST, next H4
        datetime(2026, 3, 8, 22, 0, tzinfo=UTC),   # 18:00 EDT after DST
    ]
    for i, ts in enumerate(timestamps):
        agg.add(_bar(ts, 100 + i, 101 + i, 99 + i, 100 + i), i)

    assert agg.day_of == [0, 1, 1, 1, 2]
    assert agg.h4_of == [0, 1, 1, 2, 3]
    assert agg.days[1].start == 1 and agg.days[1].end == 4
    assert agg.h4[1].start == 1 and agg.h4[1].end == 3

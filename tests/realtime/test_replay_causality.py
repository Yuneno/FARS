"""Unit tests for chronological replay causality and signal parity with backtest (Task 4)."""

import asyncio
from datetime import UTC, datetime, timedelta
import math
import pytest

from src.backtest.executor import run_backtest
from src.backtest.history import Bar as BacktestBar
from src.backtest.smc_fvg import SmcFvgStrategy, smc_fvg_config
from src.realtime.bus import AsyncIOEventBus
from src.realtime.clock import FrozenClock
from src.realtime.events import Bar as RealtimeBar, ORIGIN_REPLAY
from src.realtime.recorder import FileEventRecorder
from src.realtime.replay import ReplayEngine


def _make_sample_bars(n: int = 40) -> list[BacktestBar]:
    base = datetime(2026, 1, 1, 9, 30, tzinfo=UTC)
    bars = []
    p = 15000.0
    for i in range(n):
        ts = base + timedelta(minutes=5 * i)
        # Produce a synthetic sequence with swing highs and lows and FVGs
        swing = math.sin(i * 0.4) * 20.0
        o = p + swing
        h = o + 8.0
        l = o - 8.0
        c = o + (2.0 if i % 2 == 0 else -2.0)
        bars.append(BacktestBar(timestamp=ts, open=o, high=h, low=l, close=c, volume=100.0))
    return bars


def test_chronological_replay_signals_match_backtest(tmp_path):
    """Verify that ReplayEngine streams closed bars causally, producing identical signals to backtest."""
    bars = _make_sample_bars(50)
    strategy_backtest = SmcFvgStrategy(swing_w=1, min_risk_pts=0.0)

    # 1. Backtest: evaluate sequentially at each closed bar
    backtest_signals = []
    for i in range(1, len(bars) + 1):
        history = bars[:i]
        sig = strategy_backtest.evaluate(history)
        if sig is not None:
            backtest_signals.append({
                "bar_index": i - 1,
                "timestamp": bars[i - 1].timestamp,
                "direction": sig.direction,
                "entry": sig.entry,
                "stop": sig.stop,
                "target": sig.target,
            })

    # 2. Record bars into a realtime journal
    journal_path = tmp_path / "replay_bars.jsonl"
    recorder = FileEventRecorder(journal_path)
    for i, b in enumerate(bars):
        rt_bar = RealtimeBar(
            event_id=f"bar-{i+1}",
            source="databento",
            timestamp=b.timestamp,
            sequence=i + 1,
            symbol="MNQ",
            interval="5m",
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
            origin=ORIGIN_REPLAY,
        )
        recorder.record(rt_bar)

    # 3. Realtime Replay with FrozenClock and AsyncIOEventBus
    clock = FrozenClock(datetime(2026, 1, 1, 0, 0, tzinfo=UTC))
    bus = AsyncIOEventBus(maxsize=16)
    strategy_replay = SmcFvgStrategy(swing_w=1, min_risk_pts=0.0)

    replay_signals = []
    observed_history: list[BacktestBar] = []
    arrival_times = []

    def on_bar_event(event):
        if isinstance(event, RealtimeBar):
            # Causality assertion: event timestamp cannot be in the future relative to frozen clock
            assert event.timestamp == clock.now(), "FrozenClock must match bar timestamp"
            arrival_times.append(clock.now())

            # Convert to backtest bar representation for strategy
            b_bar = BacktestBar(
                timestamp=event.timestamp,
                open=event.open,
                high=event.high,
                low=event.low,
                close=event.close,
                volume=event.volume,
            )
            observed_history.append(b_bar)

            # Strategy evaluates ONLY observed closed bars so far
            sig = strategy_replay.evaluate(list(observed_history))
            if sig is not None:
                replay_signals.append({
                    "bar_index": len(observed_history) - 1,
                    "timestamp": event.timestamp,
                    "direction": sig.direction,
                    "entry": sig.entry,
                    "stop": sig.stop,
                    "target": sig.target,
                })

    async def _run_replay():
        bus.subscribe(on_bar_event)
        await bus.start()
        engine = ReplayEngine(journal_path, bus=bus, clock=clock)
        count = await engine.play()
        await bus.shutdown()
        return count

    count = asyncio.run(_run_replay())
    assert count == len(bars), f"Expected {len(bars)} events played, got {count}"

    # 4. Strict equivalence verification
    assert len(arrival_times) == len(bars)
    # Verify strictly monotonic chronological order
    for i in range(1, len(arrival_times)):
        assert arrival_times[i] > arrival_times[i - 1], "Replay events must be strictly chronological"

    # Compare signals
    assert len(replay_signals) == len(backtest_signals), (
        f"Signal count mismatch: replay {len(replay_signals)} vs backtest {len(backtest_signals)}"
    )

    for rs, bs in zip(replay_signals, backtest_signals):
        assert rs["bar_index"] == bs["bar_index"]
        assert rs["timestamp"] == bs["timestamp"]
        assert rs["direction"] == bs["direction"]
        assert math.isclose(rs["entry"], bs["entry"], abs_tol=1e-6)
        assert math.isclose(rs["stop"], bs["stop"], abs_tol=1e-6)
        assert math.isclose(rs["target"], bs["target"], abs_tol=1e-6)

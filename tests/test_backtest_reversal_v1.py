from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import src.backtest.reversal_v1 as rv1
from src.backtest.executor import run_backtest
from src.backtest.history import Bar, synthetic_bars
from src.detectors.events import DiagnosticEvent
from src.zones.models import Zone, make_zone_id


def _bars(n: int = 25) -> list[Bar]:
    start = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    out = []
    for i in range(n):
        base = 100.0 + i * 0.1
        out.append(Bar(start + timedelta(minutes=5 * i), base, base + 2, base - 2, base + 1, 10))
    return out


def _zone(
    *,
    name: str,
    level: float,
    direction: str,
    state: str = "active",
    available_at: datetime,
) -> Zone:
    return Zone(
        zone_id=make_zone_id("liquidity", "MNQ", "M5", direction, available_at, (name,)),
        zone_type="liquidity",
        symbol="MNQ",
        timeframe="M5",
        lower=level - 0.25,
        upper=level + 0.25,
        midpoint=level,
        direction=direction,
        pattern_time=available_at,
        available_at=available_at,
        state=state,
        metadata={"available_bar_index": 0},
    )


class _Engine:
    def __init__(self, zones):
        self.zones = list(zones)

    def update(self, history):
        del history

    def active_zones(self):
        return tuple(zone for zone in self.zones if not zone.is_terminal())

    def all_zones(self):
        return tuple(self.zones)


def _fvg(direction: str, when: datetime, *, future_seconds: int = 0) -> DiagnosticEvent:
    return DiagnosticEvent(
        detector="fvg_v1",
        symbol="MNQ",
        timeframe="M5",
        source_bar_ids=(0, 1, 2),
        pattern_time=when + timedelta(seconds=future_seconds),
        available_at=when + timedelta(seconds=future_seconds),
        direction=direction,
        levels={"gap_low": 102.0, "gap_high": 104.0},
    )


def _armed(monkeypatch, *, use_cisd=False, use_ote=False, target=True):
    bars = _bars()
    bars[-1] = Bar(bars[-1].timestamp, 103, 110, 101, 106, 10)
    swept = _zone(
        name="swept-low", level=90, direction="short", state="swept", available_at=bars[5].timestamp
    )
    zones = [swept]
    if target:
        zones.append(
            _zone(name="target-high", level=120, direction="long", available_at=bars[6].timestamp)
        )
    strategy = rv1.ReversalV1Strategy(use_cisd=use_cisd, use_ote=use_ote)
    strategy._engine = _Engine(zones)
    strategy._bars = list(bars)
    strategy._high = [bar.high for bar in bars]
    strategy._low = [bar.low for bar in bars]
    strategy._seen = len(bars)
    strategy._sweep = {"zone": swept, "direction": "long", "index": 20, "extreme": 90.0}
    strategy._choch = {"index": 22, "level": 100.0}
    monkeypatch.setattr(rv1, "detect_fvg", lambda *args, **kwargs: [_fvg("long", bars[-1].timestamp)])
    monkeypatch.setattr(
        rv1,
        "detect_cisd",
        lambda *args, **kwargs: [
            DiagnosticEvent(
                detector="cisd_v1",
                symbol="MNQ",
                timeframe="M5",
                source_bar_ids=(0,),
                pattern_time=bars[-1].timestamp,
                available_at=bars[-1].timestamp,
                direction="long",
            )
        ],
    )
    return strategy, bars


def test_valid_sweep_choch_fvg_setup_is_deterministic(monkeypatch):
    first, bars = _armed(monkeypatch)
    signal_a = first._candidate(len(bars) - 1)
    second, _ = _armed(monkeypatch)
    signal_b = second._candidate(len(bars) - 1)
    assert signal_a == signal_b
    assert signal_a is not None
    assert (signal_a.direction, signal_a.entry, signal_a.stop, signal_a.target) == (
        "long", 104.0, 89.0, 120.0
    )
    assert first.decisions == second.decisions


def test_two_full_backtest_runs_have_identical_trades_r_timestamps_and_reasons():
    bars = synthetic_bars(160, seed=71, interval_seconds=300)
    first_strategy = rv1.ReversalV1Strategy()
    second_strategy = rv1.ReversalV1Strategy()
    config = rv1.reversal_v1_config()
    first = run_backtest(bars, first_strategy, config)
    second = run_backtest(bars, second_strategy, config)
    projection = lambda result: [
        (trade.r_result, trade.entry_time, trade.exit_time, trade.exit_reason)
        for trade in result.trades
    ]
    assert projection(first) == projection(second)
    assert first_strategy.decisions == second_strategy.decisions


def test_no_fvg_means_no_trade(monkeypatch):
    strategy, bars = _armed(monkeypatch)
    monkeypatch.setattr(rv1, "detect_fvg", lambda *args, **kwargs: [])
    assert strategy._candidate(len(bars) - 1) is None


def test_no_choch_means_no_trade(monkeypatch):
    strategy, bars = _armed(monkeypatch)
    strategy._choch = None
    assert strategy._candidate(len(bars) - 1) is None


def test_no_opposite_pool_means_no_trade(monkeypatch):
    strategy, bars = _armed(monkeypatch, target=False)
    assert strategy._candidate(len(bars) - 1) is None


def test_pending_invalidation_is_recorded_and_evaluate_returns_none(monkeypatch):
    strategy, bars = _armed(monkeypatch)
    assert strategy._candidate(len(bars) - 1) is not None
    strategy._pending_active = True
    invalidating = Bar(bars[-1].timestamp + timedelta(minutes=5), 101, 102, 98, 99, 10)
    strategy._invalidate_pending(invalidating)
    assert strategy.decisions[-1].reason == "invalidated"
    assert strategy.evaluate(bars) is None


def test_cisd_flag_changes_candidates_deterministically(monkeypatch):
    without, bars = _armed(monkeypatch, use_cisd=False)
    monkeypatch.setattr(rv1, "detect_cisd", lambda *args, **kwargs: [])
    assert without._candidate(len(bars) - 1) is not None
    with_filter, _ = _armed(monkeypatch, use_cisd=True)
    monkeypatch.setattr(rv1, "detect_cisd", lambda *args, **kwargs: [])
    assert with_filter._candidate(len(bars) - 1) is None


def test_ote_flag_changes_candidates_deterministically(monkeypatch):
    without, bars = _armed(monkeypatch, use_ote=False)
    assert without._candidate(len(bars) - 1) is not None
    with_filter, _ = _armed(monkeypatch, use_ote=True)
    assert with_filter._candidate(len(bars) - 1) is None


def test_future_fvg_and_pool_are_rejected_explicitly(monkeypatch):
    strategy, bars = _armed(monkeypatch)
    monkeypatch.setattr(
        rv1, "detect_fvg", lambda *args, **kwargs: [_fvg("long", bars[-1].timestamp, future_seconds=1)]
    )
    assert strategy._candidate(len(bars) - 1) is None

    strategy, bars = _armed(monkeypatch)
    future_target = _zone(
        name="future", level=120, direction="long", available_at=bars[-1].timestamp + timedelta(seconds=1)
    )
    strategy._engine.zones = [strategy._sweep["zone"], future_target]
    assert strategy._candidate(len(bars) - 1) is None


def test_append_only_and_closed_bar_contract():
    bars = _bars(10)
    strategy = rv1.ReversalV1Strategy()
    assert strategy.evaluate(bars[:5]) is None
    divergent = list(bars[:5])
    divergent[-1] = Bar(divergent[-1].timestamp + timedelta(seconds=1), 1, 2, 0.5, 1, 1)
    with pytest.raises(ValueError, match="append-only"):
        strategy.evaluate(divergent)


def test_config_declares_limit_primary_and_market_neighbor():
    limit = rv1.reversal_v1_config(wait=48, max_hold_bars=96, entry_mode="limit")
    market = rv1.reversal_v1_config(wait=48, max_hold_bars=96, entry_mode="market")
    assert limit.pending_limit_entry is True
    assert limit.pending_order_wait_bars == 48
    assert limit.max_bars_held == 95
    assert market.pending_limit_entry is False
    assert market.pending_order_wait_bars == 0


def test_import_is_opt_in_and_exports_no_registry_mutation():
    assert rv1.ReversalV1Strategy.__module__ == "src.backtest.reversal_v1"
    assert rv1.ReversalV1Strategy().parameters()["session_pools"] is True

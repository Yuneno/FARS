"""Tests for the backtest executor and pipeline (FASE B/C/D)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.backtest.executor import (
    BacktestConfig,
    _entry_price,
    _quantity,
    _stop_fill,
    run_backtest,
)
from src.backtest.history import Bar, synthetic_bars
from src.backtest.pipeline import (
    chronological_split,
    run_pipeline,
    write_report,
    write_trades_csv,
)
from src.backtest.strategy import BreakoutStrategy, Signal


def _bar(ts, o, h, l, c, v=10):
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def _min(i):
    return datetime(2026, 9, 1, 9, 0, tzinfo=UTC) + timedelta(minutes=i)


class _OneLong:
    """Fires exactly one long signal (stop/target 5/10 points off the signal close)."""

    def __init__(self):
        self.fired = False

    def evaluate(self, history):
        if self.fired or not history:
            return None
        self.fired = True
        last = history[-1]
        return Signal("long", last.close, last.close - 5.0, last.close + 10.0)


class _NoSignal:
    def evaluate(self, history):
        return None


def test_mnq_quantity_sizing_rounds_to_contracts():
    config = BacktestConfig(initial_balance=50_000, risk_per_trade=0.01)  # $500 risk
    # 5 point stop * $2/point = $10/contract -> 50 contracts, capped at 10
    assert _quantity(config, stop_distance=5.0) == 10
    # 50 point stop -> $100/contract -> 5 contracts
    assert _quantity(config, stop_distance=50.0) == 5


def test_entry_and_stop_slippage_against_position():
    config = BacktestConfig(slippage_points=0.25)
    assert _entry_price(config, "long", 100.0) == 100.25
    assert _entry_price(config, "short", 100.0) == 99.75
    assert _stop_fill(config, "long", 95.0) == 94.75
    assert _stop_fill(config, "short", 105.0) == 105.25


@pytest.mark.parametrize(
    "field",
    [
        "initial_balance",
        "risk_per_trade",
        "dollar_per_point",
        "tick_size",
        "commission_per_side",
        "slippage_points",
        "profit_target_pct",
        "max_drawdown_pct",
        "daily_loss_limit_pct",
    ],
)
def test_backtest_config_rejects_negative_economic_magnitudes(field):
    with pytest.raises(ValueError, match=field):
        BacktestConfig(**{field: -1.0})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("risk_per_trade", 0.0),
        ("risk_per_trade", 1.0),
        ("profit_target_pct", 0.0),
        ("profit_target_pct", 1.0),
        ("max_drawdown_pct", 0.0),
        ("max_drawdown_pct", 1.0),
        ("daily_loss_limit_pct", 0.0),
        ("daily_loss_limit_pct", 1.0),
    ],
)
def test_backtest_config_requires_rule_percentages_strictly_between_zero_and_one(
    field, value
):
    with pytest.raises(ValueError, match=field):
        BacktestConfig(**{field: value})


def test_entry_is_next_bar_open_not_signal_close():
    bars = [
        _bar(_min(0), 90, 91, 89, 100),   # signal bar closes at 100
        _bar(_min(1), 105, 106, 104, 105),  # next bar opens at 105
        _bar(_min(2), 105, 116, 104, 115),  # hits target 110
    ]
    config = BacktestConfig(slippage_points=0.0, commission_per_side=0.0)
    result = run_backtest(bars, _OneLong(), config)
    assert len(result.trades) == 1
    assert result.trades[0].entry_price == 105.0
    assert result.trades[0].entry_time == _min(1)


def test_tp_sl_same_bar_resolves_conservatively_to_stop():
    bars = [
        _bar(_min(0), 90, 91, 89, 100),   # signal (stop 95, target 110)
        _bar(_min(1), 100, 101, 99, 100),  # entry bar
        _bar(_min(2), 100, 111, 94, 105),  # BOTH target(110) and stop(95) in range
    ]
    config = BacktestConfig(slippage_points=0.0, commission_per_side=0.0)
    result = run_backtest(bars, _OneLong(), config)
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "stop_loss"


def test_long_mnq_pnl_and_commission_slippage():
    bars = [
        _bar(_min(0), 90, 91, 89, 100),
        _bar(_min(1), 100, 101, 99, 100),
        _bar(_min(2), 100, 111, 99, 110),  # high 111 hits target 110
    ]
    config = BacktestConfig(slippage_points=0.0, commission_per_side=0.0)
    result = run_backtest(bars, _OneLong(), config)
    t = result.trades[0]
    # long: entry 100, exit 110 -> +10 points * $2/point * qty
    assert t.direction == "long"
    assert t.exit_reason == "take_profit"
    assert abs(t.gross_pnl - (10.0 * 2.0 * t.quantity)) < 1e-9
    assert t.net_pnl == t.gross_pnl  # zero commission/slippage


def test_commission_reduces_net_pnl():
    bars = [
        _bar(_min(0), 90, 91, 89, 100),
        _bar(_min(1), 100, 101, 99, 100),
        _bar(_min(2), 100, 111, 99, 110),  # high reaches target 110
    ]
    config = BacktestConfig(slippage_points=0.0, commission_per_side=0.62)
    result = run_backtest(bars, _OneLong(), config)
    t = result.trades[0]
    assert t.commission == pytest.approx(0.62 * 2 * t.quantity)
    assert t.net_pnl == pytest.approx(t.gross_pnl - t.commission)


def test_entry_bar_target_is_evaluated():
    # Signal on bar0; bar1 is the entry bar AND reaches the target intraday.
    bars = [
        _bar(_min(0), 90, 91, 89, 100),   # signal (stop 95, target 110)
        _bar(_min(1), 100, 112, 99, 105),  # entry bar hits target 110
    ]
    config = BacktestConfig(slippage_points=0.0, commission_per_side=0.0)
    result = run_backtest(bars, _OneLong(), config)
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "take_profit"
    assert result.trades[0].exit_time == _min(1)  # closed on the entry bar


def test_entry_bar_stop_is_evaluated():
    bars = [
        _bar(_min(0), 90, 91, 89, 100),   # signal (stop 95)
        _bar(_min(1), 100, 101, 94, 99),   # entry bar breaches stop 95
    ]
    config = BacktestConfig(slippage_points=0.0, commission_per_side=0.0)
    result = run_backtest(bars, _OneLong(), config)
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "stop_loss"
    assert result.trades[0].exit_time == _min(1)


def test_entry_bar_both_levels_is_conservative_stop():
    bars = [
        _bar(_min(0), 90, 91, 89, 100),   # signal (stop 95, target 110)
        _bar(_min(1), 100, 112, 94, 105),  # entry bar reaches BOTH
    ]
    config = BacktestConfig(slippage_points=0.0, commission_per_side=0.0)
    result = run_backtest(bars, _OneLong(), config)
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "stop_loss"  # conservative: stop first


def test_strategy_with_no_signals_produces_zero_trades():
    bars = synthetic_bars(500, seed=1)
    result = run_backtest(bars, _NoSignal(), BacktestConfig())
    assert result.n_trades == 0
    assert result.net_pnl == 0.0


def test_legacy_absolute_price_signal_closes_at_last_close():
    bars = [
        _bar(_min(0), 90, 91, 89, 100),
        _bar(_min(1), 100, 101, 99, 100),
        _bar(_min(2), 103, 104, 102, 103),
    ]
    result = run_backtest(
        bars,
        _OneLong(),
        BacktestConfig(slippage_points=0.0, commission_per_side=0.0),
    )

    assert result.unresolved_positions == 0
    assert result.n_trades == 1
    assert result.trades[0].exit_reason == "end_of_data"
    assert result.trades[0].exit_price == 103.0


def test_reproducible_with_seed():
    cfg = BacktestConfig()
    strat_a = BreakoutStrategy()
    strat_b = BreakoutStrategy()
    a = run_backtest(synthetic_bars(3000, seed=42), strat_a, cfg)
    b = run_backtest(synthetic_bars(3000, seed=42), strat_b, cfg)
    assert [t.net_pnl for t in a.trades] == [t.net_pnl for t in b.trades]


def test_chronological_split_is_order_preserving_and_reproducible():
    bars = synthetic_bars(2000, seed=5)
    split = chronological_split(bars, train_fraction=0.7)
    assert split.in_sample + split.out_of_sample == bars
    assert split.in_sample[-1].timestamp < split.out_of_sample[0].timestamp
    split2 = chronological_split(bars, train_fraction=0.7)
    assert split.in_sample == split2.in_sample
    assert split.out_of_sample == split2.out_of_sample


def test_chronological_split_rejects_single_session():
    bars = [
        _bar(_min(index * 5), 100, 101, 99, 100)
        for index in range(8)
    ]

    with pytest.raises(ValueError, match="session boundary"):
        chronological_split(bars, train_fraction=0.5)


def test_pipeline_and_report_persistence(tmp_path):
    bars = synthetic_bars(2000, seed=3)
    report = run_pipeline(bars, BreakoutStrategy(), BacktestConfig(), train_fraction=0.7)
    assert "in_sample" in report and "out_of_sample" in report
    write_report(report, tmp_path)
    assert (tmp_path / "backtest_summary.json").exists()
    result = run_backtest(bars, BreakoutStrategy(), BacktestConfig())
    write_trades_csv(result.trades, tmp_path / "trades.csv")
    assert (tmp_path / "trades.csv").exists()

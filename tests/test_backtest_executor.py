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
from src.backtest.strategy import BreakoutStrategy, Signal, Strategy


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


class _OneLimit:
    def __init__(self):
        self.fired = False

    def evaluate(self, history):
        if self.fired or not history:
            return None
        self.fired = True
        return Signal("long", 95.0, 90.0, 105.0)


class _FixedDistanceLong:
    def __init__(self):
        self.fired = False

    def evaluate(self, history):
        if self.fired or not history:
            return None
        self.fired = True
        return Signal("long", 0.0, 10.0, 20.0, stop_target_as_points=True)


class _EveryEligibleBar:
    def evaluate(self, history):
        return Signal("long", 0.0, 1.0, 1.0, stop_target_as_points=True)

    def observe(self, history):
        del history


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


def test_partial_tp_exact_half_then_break_even_stop_is_half_r():
    bars = [
        _bar(_min(0), 100, 101, 99, 100),
        _bar(_min(1), 100, 111, 99, 110),  # TP1 at 110; original SL 90 untouched
        _bar(_min(2), 100, 101, 99, 100),  # remaining half stops at entry
    ]
    config = BacktestConfig(
        initial_balance=1_000.0,
        risk_per_trade=0.02,  # $20 budget = 10 points * $2 * one contract
        dollar_per_point=2.0,
        fixed_quantity=1,
        commission_per_side=0.0,
        slippage_points=0.0,
        partial_take_profit_fraction=0.5,
        move_stop_to_break_even=True,
    )
    result = run_backtest(bars, _FixedDistanceLong(), config)

    trade = result.trades[0]
    assert trade.stop_price == trade.entry_price == 100.0
    assert trade.gross_pnl == 10.0
    assert trade.r_result == 0.5
    assert trade.exit_reason == "break_even_stop"


def test_pending_limit_expires_after_exact_wait_without_trade():
    bars = [
        _bar(_min(0), 100, 101, 99, 100),
        _bar(_min(1), 100, 101, 96, 100),
        _bar(_min(2), 100, 101, 96, 100),
    ]
    config = BacktestConfig(
        commission_per_side=0.0,
        slippage_points=0.0,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
    )

    result = run_backtest(bars, _OneLimit(), config)

    assert result.n_trades == 0
    assert result.unresolved_positions == 0


def test_pending_limit_fills_on_fvg_retracement_and_trades():
    bars = [
        _bar(_min(0), 100, 101, 99, 100),
        _bar(_min(1), 100, 106, 94, 100),
    ]
    config = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.0,
        slippage_points=0.0,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
    )

    result = run_backtest(bars, _OneLimit(), config)

    assert result.n_trades == 1
    assert result.trades[0].entry_price == 95.0
    assert result.trades[0].exit_price == 105.0
    assert result.trades[0].exit_reason == "take_profit"


def test_cooldown_rearms_from_final_closed_cooldown_bar():
    bars = [
        _bar(_min(index), 100, 102, 98, 100)
        for index in range(5)
    ]
    config = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.0,
        slippage_points=0.0,
        partial_take_profit_fraction=0.5,
        move_stop_to_break_even=True,
        cooldown_bars=2,
    )

    result = run_backtest(bars, _EveryEligibleBar(), config)

    assert [trade.entry_time for trade in result.trades] == [_min(1), _min(4)]


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


def test_time_exit_mode_flat_vs_market():
    # Construct bars where a trade opens and neither TP nor SL is hit before max_bars_held
    from src.backtest.strategy import Signal

    class SingleTradeStrategy:
        def __init__(self):
            self.fired = False

        def evaluate(self, history):
            if not self.fired and len(history) >= 2:
                self.fired = True
                return Signal(direction="long", entry=100.0, stop=90.0, target=120.0)
            return None

    # Entry bar at t=10 opens at 100.0. Subsequent bars stay flat around 105.0.
    bars = [
        _bar(_min(0), 100.0, 101.0, 99.0, 100.0),
        _bar(_min(5), 100.0, 101.0, 99.0, 100.0),
        _bar(_min(10), 100.0, 102.0, 98.0, 101.0),  # entry filled here at open 100.0
        _bar(_min(15), 101.0, 106.0, 101.0, 105.0), # bar 1 held
        _bar(_min(20), 105.0, 107.0, 104.0, 106.0), # bar 2 held -> exit at max_bars_held=2
        _bar(_min(25), 106.0, 107.0, 105.0, 106.0),
    ]

    # Mode 1: market (default) -> exits at bar.close (106.0) -> gross_pnl = (106 - 100) * 2 = $12.00
    cfg_market = BacktestConfig(
        max_bars_held=2,
        fixed_quantity=1,
        time_exit_mode="market",
        commission_per_side=0.0,
        slippage_points=0.0,
    )
    res_market = run_backtest(bars, SingleTradeStrategy(), cfg_market)
    assert len(res_market.trades) == 1
    assert res_market.trades[0].exit_reason == "time_exit"
    assert res_market.trades[0].exit_price == 106.0
    assert res_market.trades[0].gross_pnl == 12.0

    # Mode 2: flat -> exits at entry price (100.0) -> gross_pnl = $0.00
    cfg_flat = BacktestConfig(
        max_bars_held=2,
        fixed_quantity=1,
        time_exit_mode="flat",
        commission_per_side=0.0,
        slippage_points=0.0,
    )
    res_flat = run_backtest(bars, SingleTradeStrategy(), cfg_flat)
    assert len(res_flat.trades) == 1
    assert res_flat.trades[0].exit_reason == "time_exit"
    assert res_flat.trades[0].exit_price == 100.0
    assert res_flat.trades[0].gross_pnl == 0.0


def test_end_of_data_policy_close_and_unresolved():
    t0 = datetime(2026, 9, 1, 9, 30)
    bars = [
        _bar(t0, 100, 101, 99, 100),
        _bar(t0 + timedelta(minutes=5), 100, 102, 98, 101),
        _bar(t0 + timedelta(minutes=10), 101, 103, 99, 102.5),
    ]

    class _PointsHoldStrategy(Strategy):
        def __init__(self):
            self.fired = False

        def evaluate(self, history):
            if not self.fired:
                self.fired = True
                return Signal("long", history[-1].close, 50.0, 50.0, stop_target_as_points=True)
            return None

    # 1. Policy "close": closes open position at last bar close with exit_reason="end_of_data"
    cfg_close = BacktestConfig(
        slippage_points=0.0,
        commission_per_side=0.0,
        fixed_quantity=1,
        end_of_data_policy="close",
    )
    res_close = run_backtest(bars, _PointsHoldStrategy(), cfg_close)
    assert res_close.unresolved_positions == 0
    assert res_close.open_position is None
    assert res_close.n_trades == 1
    assert res_close.trades[0].exit_reason == "end_of_data"
    assert res_close.trades[0].exit_price == 102.5

    # 2. Policy "unresolved": leaves position open without emitting executed trade
    cfg_unres = BacktestConfig(
        slippage_points=0.0,
        commission_per_side=0.0,
        fixed_quantity=1,
        end_of_data_policy="unresolved",
    )
    res_unres = run_backtest(bars, _PointsHoldStrategy(), cfg_unres)
    assert res_unres.unresolved_positions == 1
    assert res_unres.n_trades == 0
    assert res_unres.open_position is not None
    assert res_unres.open_position["state"] == "open"
    assert res_unres.open_position["last_price"] == 102.5



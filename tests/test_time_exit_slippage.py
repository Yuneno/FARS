"""Tests for time exit slippage in BacktestConfig and executor."""
from __future__ import annotations

import math
from datetime import datetime, timedelta
import pytest

from src.backtest.executor import BacktestConfig, run_backtest
from src.backtest.history import Bar
from src.backtest.strategy import Signal, Strategy


def _bar(ts: datetime, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=100.0)


class _SingleTradeStrategy(Strategy):
    def __init__(self, direction: str, stop: float, target: float) -> None:
        self.direction = direction
        self.stop = stop
        self.target = target
        self._fired = False

    def evaluate(self, history: list[Bar]) -> Signal | None:
        if not self._fired:
            self._fired = True
            return Signal(
                direction=self.direction,
                entry=history[-1].close,
                stop=self.stop,
                target=self.target,
                stop_target_as_points=False,
            )
        return None


def test_time_exit_slippage_validation():
    # Valid non-negative float
    cfg = BacktestConfig(time_exit_slippage_points=0.5)
    assert cfg.time_exit_slippage_points == 0.5

    # Negative rejected
    with pytest.raises(ValueError, match="must be >= 0"):
        BacktestConfig(time_exit_slippage_points=-0.25)

    # Non-finite rejected
    with pytest.raises(ValueError, match="must be finite"):
        BacktestConfig(time_exit_slippage_points=float("nan"))
    with pytest.raises(ValueError, match="must be finite"):
        BacktestConfig(time_exit_slippage_points=float("inf"))


def test_time_exit_slippage_bit_by_bit_regression():
    t0 = datetime(2026, 9, 1, 9, 30)
    bars = [
        _bar(t0, 100, 101, 99, 100),
        _bar(t0 + timedelta(minutes=5), 100, 102, 98, 101),
        _bar(t0 + timedelta(minutes=10), 101, 103, 99, 102),
        _bar(t0 + timedelta(minutes=15), 102, 104, 100, 103),
    ]
    # Default time_exit_slippage_points is 0.0
    cfg_default = BacktestConfig(
        slippage_points=0.0,
        commission_per_side=0.0,
        fixed_quantity=1,
        max_bars_held=2,
        time_exit_mode="market",
    )
    cfg_explicit_zero = BacktestConfig(
        slippage_points=0.0,
        commission_per_side=0.0,
        fixed_quantity=1,
        max_bars_held=2,
        time_exit_mode="market",
        time_exit_slippage_points=0.0,
    )

    strat1 = _SingleTradeStrategy("long", stop=50.0, target=150.0)
    res_def = run_backtest(bars, strat1, cfg_default)

    strat2 = _SingleTradeStrategy("long", stop=50.0, target=150.0)
    res_zero = run_backtest(bars, strat2, cfg_explicit_zero)

    assert res_def.n_trades == res_zero.n_trades == 1
    t_def, t_zero = res_def.trades[0], res_zero.trades[0]
    assert t_def.exit_reason == t_zero.exit_reason == "time_exit"
    assert t_def.exit_price == t_zero.exit_price == 103.0
    assert t_def.gross_pnl == t_zero.gross_pnl
    assert t_def.net_pnl == t_zero.net_pnl
    assert t_def.r_result == t_zero.r_result
    assert res_def.win_rate == res_zero.win_rate
    assert res_def.profit_factor == res_zero.profit_factor


def test_time_exit_slippage_applied_adverse_long():
    t0 = datetime(2026, 9, 1, 9, 30)
    bars = [
        _bar(t0, 100, 101, 99, 100),
        _bar(t0 + timedelta(minutes=5), 100, 102, 98, 101),
        _bar(t0 + timedelta(minutes=10), 101, 103, 99, 102),  # hold limit bar: close=102
    ]
    # Long trade: adverse slippage of 0.25 should reduce exit price from 102.0 to 101.75
    cfg = BacktestConfig(
        slippage_points=0.0,
        commission_per_side=0.0,
        fixed_quantity=2,
        max_bars_held=1,
        time_exit_mode="market",
        time_exit_slippage_points=0.25,
    )
    strat = _SingleTradeStrategy("long", stop=50.0, target=150.0)
    res = run_backtest(bars, strat, cfg)

    assert res.n_trades == 1
    trade = res.trades[0]
    assert trade.exit_reason == "time_exit"
    assert trade.exit_price == 101.75
    # Move: 101.75 - 100 = 1.75 pts. dollar_per_point=2.0, qty=2 -> gross_pnl = 1.75 * 2 * 2 = 7.0
    assert trade.gross_pnl == 7.0
    assert trade.net_pnl == 7.0
    # Slippage cost: 0.25 pts * $2 * 2 qty = $1.0
    assert trade.slippage_cost == 1.0


def test_time_exit_slippage_applied_adverse_short():
    t0 = datetime(2026, 9, 1, 9, 30)
    bars = [
        _bar(t0, 100, 101, 99, 100),
        _bar(t0 + timedelta(minutes=5), 100, 102, 98, 101),
        _bar(t0 + timedelta(minutes=10), 101, 103, 99, 102),  # hold limit bar: close=102
    ]
    # Short trade: adverse slippage of 0.50 should increase exit fill price from 102.0 to 102.50
    cfg = BacktestConfig(
        slippage_points=0.0,
        commission_per_side=0.0,
        fixed_quantity=1,
        max_bars_held=1,
        time_exit_mode="market",
        time_exit_slippage_points=0.50,
    )
    strat = _SingleTradeStrategy("short", stop=150.0, target=50.0)
    res = run_backtest(bars, strat, cfg)

    assert res.n_trades == 1
    trade = res.trades[0]
    assert trade.exit_reason == "time_exit"
    assert trade.exit_price == 102.50
    # Short move: 100 - 102.50 = -2.50 pts. dollar_per_point=2.0, qty=1 -> gross_pnl = -5.0
    assert trade.gross_pnl == -5.0
    assert trade.net_pnl == -5.0
    # Slippage cost: 0.50 * 2.0 * 1 = 1.0
    assert trade.slippage_cost == 1.0


def test_time_exit_slippage_not_applied_in_flat_mode():
    t0 = datetime(2026, 9, 1, 9, 30)
    bars = [
        _bar(t0, 100, 101, 99, 100),
        _bar(t0 + timedelta(minutes=5), 100, 102, 98, 101),
        _bar(t0 + timedelta(minutes=10), 101, 103, 99, 102),
    ]
    # Even if time_exit_slippage_points > 0, mode "flat" exits at entry price with zero slippage
    cfg = BacktestConfig(
        slippage_points=0.0,
        commission_per_side=0.0,
        fixed_quantity=1,
        max_bars_held=1,
        time_exit_mode="flat",
        time_exit_slippage_points=0.50,
    )
    strat = _SingleTradeStrategy("long", stop=50.0, target=150.0)
    res = run_backtest(bars, strat, cfg)

    assert res.n_trades == 1
    trade = res.trades[0]
    assert trade.exit_reason == "time_exit"
    assert trade.exit_price == 100.0
    assert trade.gross_pnl == 0.0
    assert trade.slippage_cost == 0.0


def test_time_exit_slippage_enhanced_execution_path():
    """Test time exit slippage in the enhanced executor path (partial TP, discrete contracts, break-even stop)."""
    t0 = datetime(2026, 9, 1, 9, 30)
    bars = [
        _bar(t0, 100.0, 101.0, 99.0, 100.0),                         # Bar 0: signal emitted
        _bar(t0 + timedelta(minutes=5), 100.0, 115.0, 98.0, 105.0),  # Bar 1: entry at 100.0, TP1 (110.0) hit -> partial taken
        _bar(t0 + timedelta(minutes=10), 104.0, 106.0, 103.0, 104.0), # Bar 2: max_bars_held=1 expires -> time_exit at close=104.0
    ]

    base_kwargs = dict(
        slippage_points=0.0,
        commission_per_side=0.0,
        fixed_quantity=2,
        max_bars_held=1,
        time_exit_mode="market",
        partial_take_profit_fraction=0.5,
        discrete_partial_contracts=True,
        move_stop_to_break_even=True,
    )

    # 1. Enhanced path with slippage: 0.50 pts slippage on time exit
    cfg_slip = BacktestConfig(
        **base_kwargs,
        time_exit_slippage_points=0.50,
    )
    strat_slip = _SingleTradeStrategy("long", stop=90.0, target=130.0)
    res_slip = run_backtest(bars, strat_slip, cfg_slip)

    assert res_slip.n_trades == 1
    t_slip = res_slip.trades[0]
    assert t_slip.exit_reason == "time_exit"
    # Adverse slippage on remaining fraction:
    # 2 contracts initial, 1 contract exited at TP1 (risk_pts=10, pnl = 1 * 10 * $2 = $20.00).
    # Remaining 1 contract exits at bar.close=104.0 with 0.50 pts adverse slippage -> fill = 103.50.
    # rem_pnl = 1 * (103.50 - 100.0) * $2 = $7.00.
    # gross_pnl = $20.00 + $7.00 = $27.00.
    # total_move = $27.00 / ($2 * 2 qty) = 6.75 pts.
    # equivalent exit price = 100.0 + 6.75 = 106.75.
    assert t_slip.exit_price == 106.75
    assert t_slip.gross_pnl == 27.0
    assert t_slip.net_pnl == 27.0
    # Slippage cost: remaining_frac = 1/2 = 0.5; exit_slip_pts = 0.50 * 0.5 = 0.25 pts.
    # slippage_cost = 0.25 * $2.0/pt * 2 qty = $1.00 (exactly $1.00 on the 1 remaining contract).
    assert t_slip.slippage_cost == 1.0

    # 2. Bit-by-bit regression: time_exit_slippage_points=0.0 vs default (unspecified)
    cfg_default = BacktestConfig(**base_kwargs)
    cfg_zero = BacktestConfig(**base_kwargs, time_exit_slippage_points=0.0)

    strat_def = _SingleTradeStrategy("long", stop=90.0, target=130.0)
    res_def = run_backtest(bars, strat_def, cfg_default)

    strat_zero = _SingleTradeStrategy("long", stop=90.0, target=130.0)
    res_zero = run_backtest(bars, strat_zero, cfg_zero)

    assert res_def.n_trades == res_zero.n_trades == 1
    t_def, t_zero = res_def.trades[0], res_zero.trades[0]
    assert t_def.exit_reason == t_zero.exit_reason == "time_exit"
    assert t_def.exit_price == t_zero.exit_price == 107.0
    assert t_def.gross_pnl == t_zero.gross_pnl == 28.0
    assert t_def.net_pnl == t_zero.net_pnl == 28.0
    assert t_def.r_result == t_zero.r_result
    assert t_def.slippage_cost == t_zero.slippage_cost == 0.0
    assert res_def.win_rate == res_zero.win_rate
    assert res_def.profit_factor == res_zero.profit_factor

    # 3. Exact delta between slippage run and zero-slippage run
    assert t_def.exit_price - t_slip.exit_price == 0.25  # 0.50 slip * 0.5 remaining_frac
    assert t_def.gross_pnl - t_slip.gross_pnl == 1.00    # $1.00 slippage cost


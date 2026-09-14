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

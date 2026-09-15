"""Unit tests for causal MAE computation (Block D3)."""

from datetime import datetime, timezone, timedelta
import pytest

from src.backtest.executor import ExecutedTrade
from src.backtest.history import Bar
from src.backtest.mae import compute_trade_mae, TradeMaeResult


def test_compute_trade_mae_long_causal():
    t_entry = datetime(2026, 9, 1, 9, 35, tzinfo=timezone.utc)
    t_exit = datetime(2026, 9, 1, 9, 39, tzinfo=timezone.utc)

    # Long trade entered at 100.0, exited at 105.0 (winner)
    trade = ExecutedTrade(
        trade_id="t_long_1",
        direction="long",
        entry_time=t_entry,
        exit_time=t_exit,
        entry_price=100.0,
        exit_price=105.0,
        stop_price=90.0,
        target_price=105.0,
        quantity=1,
        gross_pnl=10.0,
        commission=1.0,
        net_pnl=9.0,
        r_result=0.9,
        exit_reason="take_profit",
    )

    # M1 bars:
    # 09:34: low 95 (BEFORE entry -> must be ignored!)
    # 09:35: open 100, high 101, low 98, close 99 (in trade -> adverse excursion 2.0)
    # 09:36: open 99, high 102, low 97, close 101 (in trade -> adverse excursion 3.0)
    # 09:37: open 101, high 104, low 100, close 103 (in trade)
    # 09:38: open 103, high 105, low 102, close 105 (in trade)
    # 09:39: open 105, high 106, low 105, close 105 (exit bar)
    # 09:40: low 92 (AFTER exit -> must be ignored!)
    m1_bars = [
        Bar(t_entry - timedelta(minutes=1), 99.0, 100.0, 95.0, 99.0, 10.0),
        Bar(t_entry, 100.0, 101.0, 98.0, 99.0, 10.0),
        Bar(t_entry + timedelta(minutes=1), 99.0, 102.0, 97.0, 101.0, 10.0),
        Bar(t_entry + timedelta(minutes=2), 101.0, 104.0, 100.0, 103.0, 10.0),
        Bar(t_entry + timedelta(minutes=3), 103.0, 105.0, 102.0, 105.0, 10.0),
        Bar(t_exit, 105.0, 106.0, 105.0, 105.0, 10.0),
        Bar(t_exit + timedelta(minutes=1), 105.0, 106.0, 92.0, 93.0, 10.0),
    ]

    res = compute_trade_mae(trade, m1_bars, dollar_per_point=2.0)
    assert res.trade_id == "t_long_1"
    # Max adverse excursion should be 100.0 - 97.0 = 3.0 points (not 5.0 from 09:34 and not 8.0 from 09:40)
    assert res.mae_points == 3.0
    assert res.mae_dollars_per_contract == 6.0  # 3.0 * 2.0
    assert res.mae_timestamp == t_entry + timedelta(minutes=1)
    assert res.mfe_points == 6.0  # 106.0 - 100.0
    assert res.m1_bars_evaluated == 5  # 09:35, 09:36, 09:37, 09:38, 09:39
    assert res.resolution_mode == "m1_causal"


def test_compute_trade_mae_short_causal():
    t_entry = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    t_exit = datetime(2026, 9, 1, 10, 3, tzinfo=timezone.utc)

    # Short trade entered at 200.0, exited at 195.0
    trade = ExecutedTrade(
        trade_id="t_short_1",
        direction="short",
        entry_time=t_entry,
        exit_time=t_exit,
        entry_price=200.0,
        exit_price=195.0,
        stop_price=210.0,
        target_price=195.0,
        quantity=1,
        gross_pnl=10.0,
        commission=1.0,
        net_pnl=9.0,
        r_result=0.9,
        exit_reason="take_profit",
    )

    m1_bars = [
        Bar(t_entry, 200.0, 204.0, 199.0, 202.0, 10.0),  # adverse 4.0
        Bar(t_entry + timedelta(minutes=1), 202.0, 202.5, 198.0, 199.0, 10.0),
        Bar(t_entry + timedelta(minutes=2), 199.0, 200.0, 196.0, 197.0, 10.0),
        Bar(t_exit, 197.0, 197.0, 195.0, 195.0, 10.0),
    ]

    res = compute_trade_mae(trade, m1_bars, dollar_per_point=2.0)
    assert res.mae_points == 4.0  # 204.0 - 200.0
    assert res.mae_dollars_per_contract == 8.0
    assert res.mae_timestamp == t_entry
    assert res.m1_bars_evaluated == 4
    assert res.resolution_mode == "m1_causal"


def test_compute_trade_mae_fallback_when_no_m1():
    t_entry = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    t_exit = datetime(2026, 9, 1, 10, 5, tzinfo=timezone.utc)

    trade = ExecutedTrade(
        trade_id="t_fallback",
        direction="long",
        entry_time=t_entry,
        exit_time=t_exit,
        entry_price=100.0,
        exit_price=105.0,
        stop_price=92.0,
        target_price=105.0,
        quantity=1,
        gross_pnl=10.0,
        commission=1.0,
        net_pnl=9.0,
        r_result=0.9,
        exit_reason="take_profit",
    )

    # Without any bars provided: falls back to stop distance
    res = compute_trade_mae(trade, m1_bars=None, dollar_per_point=2.0)
    assert res.mae_points == 8.0  # 100 - 92
    assert res.mae_dollars_per_contract == 16.0
    assert res.resolution_mode == "stop_bound_conservative"

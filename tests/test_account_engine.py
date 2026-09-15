"""Unit tests for the single funded account engine (Block D2, D3, D5)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.account_engine import (
    AccountEngineConfig,
    run_account_simulation,
    run_multi_account_portfolio,
)
from src.backtest.executor import ExecutedTrade
from src.backtest.mae import TradeMaeResult
from src.funded_profiles import apex_25k_profile, apex_50k_profile


def _make_trade(
    trade_id: str,
    entry_time: datetime,
    exit_time: datetime,
    entry_price: float,
    exit_price: float,
    stop_price: float,
    target_price: float,
    net_pnl: float,
    direction: str = "long",
    quantity: int = 1,
) -> ExecutedTrade:
    return ExecutedTrade(
        trade_id=trade_id,
        direction=direction,
        entry_time=entry_time,
        exit_time=exit_time,
        entry_price=entry_price,
        exit_price=exit_price,
        stop_price=stop_price,
        target_price=target_price,
        quantity=quantity,
        gross_pnl=net_pnl + 2.0,
        commission=1.0,
        net_pnl=net_pnl,
        r_result=net_pnl / 200.0,
        exit_reason="take_profit" if net_pnl > 0 else "stop_loss",
        budgeted_risk_dollars=200.0,
        effective_risk_dollars=200.0,
    )


def test_account_engine_pass_on_profit_target():
    """Account reaches +$1,500 target and passes without breaches."""
    profile = apex_25k_profile()
    t0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)

    # 3 trades of +$600 each = +$1,800 total (target is $1,500)
    trades = [
        _make_trade("t1", t0, t0 + timedelta(minutes=10), 100.0, 106.0, 98.0, 106.0, net_pnl=600.0),
        _make_trade("t2", t0 + timedelta(hours=1), t0 + timedelta(hours=1, minutes=10), 105.0, 111.0, 103.0, 111.0, net_pnl=600.0),
        _make_trade("t3", t0 + timedelta(hours=2), t0 + timedelta(hours=2, minutes=10), 110.0, 116.0, 108.0, 116.0, net_pnl=600.0),
    ]

    cfg = AccountEngineConfig(risk_pct=0.01, trailing_mode="closed_trade")
    res = run_account_simulation(profile, trades, cfg)

    assert res.status == "passed"
    assert res.termination_reason == "profit_target_reached"
    assert res.final_balance >= 26500.0


def test_account_engine_blown_on_closed_breach():
    """Account hits trailing floor on closed trades and blows."""
    profile = apex_25k_profile()
    t0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)

    # Losses totaling -$1,600 (trailing DD is $1,500 -> blows)
    trades = [
        _make_trade("t1", t0, t0 + timedelta(minutes=10), 100.0, 92.0, 92.0, 110.0, net_pnl=-800.0),
        _make_trade("t2", t0 + timedelta(hours=1), t0 + timedelta(hours=1, minutes=10), 95.0, 87.0, 87.0, 105.0, net_pnl=-800.0),
    ]

    cfg = AccountEngineConfig(risk_pct=0.03, trailing_mode="closed_trade")
    res = run_account_simulation(profile, trades, cfg)

    assert res.status == "blown"
    assert "breach" in res.termination_reason


def test_account_engine_risk_blocked_when_margin_buffer_insufficient():
    """Account enters blocked status when remaining buffer cannot fit 1 contract."""
    profile = apex_25k_profile()
    t0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)

    # Trade 1 sizes to 1 contract with unit risk $1,450 (stop distance 725 pts * $2 = $1,450)
    # Loss is -$1,450 -> balance drops to 23,550 (floor is 23,500, buffer is $50).
    # Trade 2 has unit risk $100 (stop distance 50 pts * $2 = $100).
    # 75% of buffer is $37.50. qty_max_buffer = floor(37.50 / 100) = 0 -> BLOCKED!
    trades = [
        _make_trade("t1", t0, t0 + timedelta(minutes=10), 1000.0, 275.0, 275.0, 1500.0, net_pnl=-1450.0),
        _make_trade("t2", t0 + timedelta(hours=1), t0 + timedelta(hours=1, minutes=10), 100.0, 110.0, 50.0, 150.0, net_pnl=500.0),
    ]

    cfg = AccountEngineConfig(risk_pct=0.05, trailing_mode="closed_trade")
    res = run_account_simulation(profile, trades, cfg)

    assert res.status == "blocked"
    assert res.termination_reason == "risk-blocked:dd-buffer"
    assert res.trades_executed == 1  # trade 1 blocked immediately due to wide stop


def test_trade_winner_that_blows_on_intraday_mae_decisive():
    """THE DECISIVE TEST (Acceptance Criteria #3 & #4):

    A trade that closes as a winner (+10 points = +$500 per contract),
    BUT experienced an intraday adverse excursion (MAE) of 40 points (-$1,600 on 20 contracts).
    Under closed_trade trailing: SURVIVES and passes!
    Under intraday_event trailing: BURNS THE ACCOUNT at the intraday excursion point!
    """
    profile = apex_25k_profile()
    t0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
    t_exit = t0 + timedelta(minutes=30)

    # Trade enters at 100.0, exits at 110.0 (+10 pts winner)
    # Stop distance = 5 pts. With 25k account and 1% risk ($250), unit risk = 5 * 2 = $10 -> qty = 20 contracts.
    trade = _make_trade(
        trade_id="t_swing_winner",
        entry_time=t0,
        exit_time=t_exit,
        entry_price=100.0,
        exit_price=110.0,
        stop_price=95.0,
        target_price=110.0,
        net_pnl=400.0,
        quantity=1,
    )

    # Precomputed MAE: during the trade, price dipped to 92.0 (8.0 points adverse excursion)
    # On 20 micro contracts: 8.0 pts * $2/pt * 20 contracts = -$320 intraday dip.
    # Wait, let's make the dip touch the $1,500 trailing floor!
    # For a starting balance of 25,000, floor is 23,500 (max loss = 1,500).
    # If 20 contracts are traded: 1,500 / (20 * 2) = 37.5 points adverse excursion!
    # With 38.0 points adverse excursion: dip = 38 * 2 * 20 = $1,520 -> equity drops to 23,480 <= 23,500 floor!
    precomputed_maes = {
        "t_swing_winner": TradeMaeResult(
            trade_id="t_swing_winner",
            mae_points=38.0,
            mae_dollars_per_contract=76.0,
            mae_timestamp=t0 + timedelta(minutes=15),
            mfe_points=10.0,
            mfe_timestamp=t_exit,
            m1_bars_evaluated=30,
            resolution_mode="m1_causal",
        )
    }

    # Case A: Trailing on closed trades (reference method)
    cfg_closed = AccountEngineConfig(risk_pct=0.008, trailing_mode="closed_trade")
    res_closed = run_account_simulation(profile, [trade], cfg_closed, precomputed_maes=precomputed_maes)

    # Under closed trades: it only sees the exit price (+400 net), so it NEVER burns!
    assert res_closed.status != "blown"
    assert res_closed.final_balance > 25000.0

    # Case B: Trailing intraday with MAE (FARS method)
    cfg_intraday = AccountEngineConfig(risk_pct=0.008, trailing_mode="intraday_event")
    res_intraday = run_account_simulation(profile, [trade], cfg_intraday, precomputed_maes=precomputed_maes)

    # Under intraday trailing: it detects the dip touching the floor at 09:45 and BURNS immediately!
    assert res_intraday.status == "blown"
    assert res_intraday.termination_reason == "maximum_loss_intraday_breach"


def test_multi_account_portfolio_correlation_warning():
    """Multi-account portfolio reports independent states and explicit correlation warning."""
    p25 = apex_25k_profile()
    p50 = apex_50k_profile()
    t0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)

    trades = [
        _make_trade("t1", t0, t0 + timedelta(minutes=10), 100.0, 105.0, 98.0, 105.0, net_pnl=300.0),
    ]

    configs = {
        "apex-25k-evaluation": AccountEngineConfig(risk_pct=0.01),
        "apex-50k-evaluation": AccountEngineConfig(risk_pct=0.01),
    }

    res = run_multi_account_portfolio([p25, p50], trades, configs)
    assert res.total_accounts == 2
    assert res.correlation_coefficient == 1.0
    assert "ADVERTENCIA DE CORRELACION" in res.simultaneous_quema_risk_warning

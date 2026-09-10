"""Tests for joining causal AMD+CRT decisions to executor outcomes."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from src.backtest.amd_crt import ET, AmdCrtDecision
from src.backtest.decisions import join_decisions_to_outcomes
from src.backtest.executor import BacktestConfig, ExecutedTrade, run_backtest
from src.backtest.history import Bar
from src.backtest.strategy import Signal


def _decision(
    day: date,
    decision: str = "accepted",
    *,
    hour: int = 10,
) -> AmdCrtDecision:
    return AmdCrtDecision(
        day=day,
        weekday=day.weekday(),
        direction="long",
        crt_confirmed=decision != "crt_not_confirmed",
        ema_regime="long",
        atr=4.5,
        sl_pts=9.0,
        tp_pts=18.0,
        pre_ny_amplitude=20.0,
        median_amplitude=30.0,
        decision=decision,  # type: ignore[arg-type]
        timestamp=datetime(day.year, day.month, day.day, hour, tzinfo=ET),
    )


def _trade(
    entry_time: datetime,
    *,
    r_result: float = 2.0,
    exit_reason: str = "target",
    trade_id: str = "T1",
) -> ExecutedTrade:
    return ExecutedTrade(
        trade_id=trade_id,
        direction="long",
        entry_time=entry_time,
        exit_time=entry_time + timedelta(hours=1),
        entry_price=100.0,
        exit_price=118.0,
        stop_price=91.0,
        target_price=118.0,
        quantity=1,
        gross_pnl=36.0,
        commission=0.0,
        net_pnl=36.0,
        r_result=r_result,
        exit_reason=exit_reason,
    )


def test_accepted_decision_gets_same_session_trade_outcome():
    day = date(2026, 9, 7)
    decision = _decision(day)
    trade = _trade(datetime(2026, 9, 7, 10, 5, tzinfo=ET), r_result=-1.0,
                   exit_reason="stop")

    rows = join_decisions_to_outcomes([decision], [trade])

    assert len(rows) == 1
    assert rows[0].decision == "accepted"
    assert rows[0].executed is True
    assert rows[0].r_result == -1.0
    assert rows[0].exit_reason == "stop"
    assert rows[0].atr == decision.atr


def test_accepted_decision_without_trade_is_not_executed():
    row = join_decisions_to_outcomes([_decision(date(2026, 9, 7))], [])[0]

    assert row.decision == "not_executed"
    assert row.executed is False
    assert row.r_result is None
    assert row.exit_reason is None


def test_executor_gap_rejection_is_retained_as_not_executed():
    day = date(2026, 9, 7)
    decision = _decision(day)

    class AcceptedThenGap:
        def __init__(self):
            self.decisions = [decision]
            self.fired = False

        def evaluate(self, history):
            if self.fired:
                return None
            self.fired = True
            return Signal("long", 0.0, 5.0, 10.0, stop_target_as_points=True)

    bars = [
        Bar(decision.timestamp, 100.0, 101.0, 99.0, 100.0, 10.0),
        Bar(decision.timestamp + timedelta(minutes=10), 100.0, 101.0, 99.0, 100.0, 10.0),
    ]
    strategy = AcceptedThenGap()
    result = run_backtest(
        bars,
        strategy,
        BacktestConfig(bar_interval_seconds=300),
    )

    assert result.gap_rejections == 1
    assert result.trades == ()
    row = join_decisions_to_outcomes(strategy.decisions, result.trades)[0]
    assert row.decision == "not_executed"
    assert row.executed is False


def test_rejected_decision_has_no_outcome():
    row = join_decisions_to_outcomes(
        [_decision(date(2026, 9, 7), "crt_not_confirmed")], []
    )[0]

    assert row.decision == "crt_not_confirmed"
    assert row.executed is False
    assert row.r_result is None
    assert row.exit_reason is None


def test_multi_day_join_uses_session_day_not_calendar_day():
    first = date(2026, 9, 7)
    second = date(2026, 9, 8)
    decisions = [_decision(first), _decision(second)]
    # With an 18:00 ET session start, 17:00 on Sep 8 still belongs to Sep 7.
    trades = [
        _trade(datetime(2026, 9, 8, 17, 0, tzinfo=ET), r_result=1.25, trade_id="T1"),
        _trade(datetime(2026, 9, 9, 17, 0, tzinfo=ET), r_result=-0.75, trade_id="T2"),
    ]
    decisions[0] = _decision(first, hour=19)
    decisions[1] = _decision(second, hour=19)

    rows = join_decisions_to_outcomes(
        decisions, trades, session_tz=ET, session_start=time(18, 0)
    )

    assert [(row.day, row.r_result) for row in rows] == [
        (first, 1.25),
        (second, -0.75),
    ]


def test_multiple_trades_in_one_session_is_reported():
    day = date(2026, 9, 7)
    trades = [
        _trade(datetime(2026, 9, 7, 10, 5, tzinfo=ET), trade_id="T1"),
        _trade(datetime(2026, 9, 7, 10, 10, tzinfo=ET), trade_id="T2"),
    ]

    with pytest.raises(ValueError, match="multiple trades.*2026-09-07"):
        join_decisions_to_outcomes([_decision(day)], trades)


def test_trade_before_decision_is_rejected_as_noncausal():
    day = date(2026, 9, 7)
    trade = _trade(datetime(2026, 9, 7, 9, 55, tzinfo=ET))

    with pytest.raises(ValueError, match="entry is not after"):
        join_decisions_to_outcomes([_decision(day)], [trade])

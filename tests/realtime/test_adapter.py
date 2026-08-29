"""RT-5 Realtime FARS adapter: Core analytics stay behind a completed-trade gate."""

from datetime import datetime, timezone
from math import isnan

import pytest

from src.metrics import compute_metrics
from src.realtime.adapter import AdapterError, require_core_trades, run_core_metrics
from src.realtime.events import (
    EXEC_FILLED,
    AccountSnapshot,
    Bar,
    ExecutionReport,
    MarketTick,
    MarketTrade,
    OrderIntent,
    Quote,
    RiskDecision,
    Signal,
    SystemEvent,
    SYSTEM_HALTED,
)
from src.types import Trade

TS = datetime(2024, 8, 1, 14, 0, tzinfo=timezone.utc)


def test_market_and_decision_events_are_not_core_trades():
    events = [
        MarketTick("e1", "feed", TS, 1, "MNQ", 1.0, 1.0),
        Quote("e2", "feed", TS, 2, "MNQ", 1.0, 1.1, 1.0, 1.0),
        Bar("e3", "feed", TS, 3, "MNQ", "1m", 1.0, 1.2, 0.9, 1.1, 10.0),
        MarketTrade("e4", "feed", TS, 4, "MNQ", 1.0, 1.0),
        AccountSnapshot("e5", "acct", TS, 5),
        Signal("e6", "strat", TS, 6, "MNQ", "LONG"),
        RiskDecision("e7", "risk", TS, 7, "e6", "strat", True, "ok"),
        OrderIntent("e8", "risk", TS, 8, "MNQ", "LONG", "e7"),
        ExecutionReport("e9", "exec", TS, 9, "e8", EXEC_FILLED),
        SystemEvent("e10", "feed/system", TS, 1, SYSTEM_HALTED),
    ]
    for event in events:
        with pytest.raises(AdapterError, match="cannot convert realtime"):
            require_core_trades([event])
        with pytest.raises(AdapterError, match="cannot convert realtime"):
            run_core_metrics([event])


def test_non_trade_objects_and_empty_input_fail_closed():
    with pytest.raises(AdapterError, match="Trade only"):
        require_core_trades([{"r_result": 1.0}])
    with pytest.raises(AdapterError, match="at least one"):
        require_core_trades([])
    with pytest.raises(AdapterError, match="at least one"):
        run_core_metrics([])


def test_mixed_realtime_and_core_is_rejected():
    with pytest.raises(AdapterError, match="cannot convert realtime"):
        require_core_trades([Trade(r_result=1.0), MarketTick("e1", "feed", TS, 1, "MNQ", 1.0, 1.0)])


def test_non_finite_r_result_is_rejected():
    with pytest.raises(AdapterError, match="finite"):
        require_core_trades([Trade(r_result=float("nan"))])
    with pytest.raises(AdapterError, match="finite"):
        require_core_trades([Trade(r_result=float("inf"))])


def test_completed_core_trades_reach_metrics_unchanged():
    trades = (Trade(r_result=1.0, trade_id="a"), Trade(r_result=-1.0, trade_id="b"))
    gated = require_core_trades(trades)
    assert gated == trades
    assert gated[0] is trades[0]
    metrics = run_core_metrics(trades)
    expected = compute_metrics([1.0, -1.0])
    assert metrics.n_trades == expected.n_trades == 2
    assert metrics.win_rate == expected.win_rate == 0.5
    assert metrics.expectancy_r == expected.expectancy_r
    assert isnan(metrics.skewness) or metrics.skewness == expected.skewness

"""Unit tests for M1 intrabar ambiguity resolution (D4/A2.4),
end-of-dataset policy (D2/A2.2), and R/equity ledger definitions (A2.5).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.backtest.executor import BacktestConfig, run_backtest
from src.backtest.history import Bar
from src.backtest.intrabar import (
    IntrabarAudit,
    index_m1_bars,
    is_m5_ambiguous,
    resolve_intrabar_with_m1,
)
from src.backtest.strategy import Signal, Strategy


def _bar(ts: datetime, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(
        timestamp=ts,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=100.0,
    )


class _SignalOnce(Strategy):
    def __init__(self, direction: str, stop: float, target: float) -> None:
        self.direction = direction
        self.stop = stop
        self.target = target
        self._fired = False

    def evaluate(self, history: list[Bar]) -> Signal | None:
        if not self._fired:
            self._fired = True
            return Signal(
                direction=self.direction,  # type: ignore[arg-type]
                entry=history[-1].close,
                stop=self.stop,
                target=self.target,
                stop_target_as_points=False,
            )
        return None


def test_is_m5_ambiguous():
    # Long: stop=95, target=105
    assert is_m5_ambiguous("long", stop=95.0, target=105.0, high=106.0, low=94.0)
    assert not is_m5_ambiguous("long", stop=95.0, target=105.0, high=104.0, low=94.0)
    assert not is_m5_ambiguous("long", stop=95.0, target=105.0, high=106.0, low=96.0)

    # Short: stop=105, target=95
    assert is_m5_ambiguous("short", stop=105.0, target=95.0, high=106.0, low=94.0)
    assert not is_m5_ambiguous("short", stop=105.0, target=95.0, high=104.0, low=94.0)
    assert not is_m5_ambiguous("short", stop=105.0, target=95.0, high=106.0, low=96.0)


def test_resolve_intrabar_with_m1_order():
    t0 = datetime(2026, 9, 1, 9, 30)
    # M1 minute 0: stays between 98 and 102
    # M1 minute 1: touches target 105 (high=106, low=99)
    # M1 minute 2: touches stop 95 (high=100, low=94)
    m1_target_first = [
        _bar(t0, 100, 102, 98, 101),
        _bar(t0 + timedelta(minutes=1), 101, 106, 99, 104),
        _bar(t0 + timedelta(minutes=2), 104, 104, 94, 95),
    ]
    reason, status = resolve_intrabar_with_m1("long", 95.0, 105.0, m1_target_first)
    assert reason == "take_profit"
    assert status == "resolved_by_m1_target"

    # Reverse order: stop touched first
    m1_stop_first = [
        _bar(t0, 100, 102, 98, 101),
        _bar(t0 + timedelta(minutes=1), 101, 102, 94, 95),
        _bar(t0 + timedelta(minutes=2), 95, 106, 95, 105),
    ]
    reason, status = resolve_intrabar_with_m1("long", 95.0, 105.0, m1_stop_first)
    assert reason == "stop_loss"
    assert status == "resolved_by_m1_stop"


def test_resolve_intrabar_with_m1_residual_ambiguity_conservative():
    t0 = datetime(2026, 9, 1, 9, 30)
    # Both stop 95 and target 105 touched in the exact same M1 bar
    m1_same_bar = [
        _bar(t0, 100, 106, 94, 100),
    ]
    reason, status = resolve_intrabar_with_m1("long", 95.0, 105.0, m1_same_bar)
    assert reason == "stop_loss"
    assert status == "m1_residual_ambiguity_conservative_stop"


def test_resolve_intrabar_with_m1_empty_fallback():
    reason, status = resolve_intrabar_with_m1("long", 95.0, 105.0, [])
    assert reason == "stop_loss"
    assert status == "no_m1_data_conservative_stop"


def test_index_m1_bars():
    t0 = datetime(2026, 9, 1, 9, 30)
    bars = [
        _bar(t0 + timedelta(minutes=i), 100, 101, 99, 100)
        for i in range(12)
    ]
    indexed = index_m1_bars(bars)
    m5_bucket_1 = datetime(2026, 9, 1, 9, 30)
    m5_bucket_2 = datetime(2026, 9, 1, 9, 35)
    m5_bucket_3 = datetime(2026, 9, 1, 9, 40)
    assert len(indexed[m5_bucket_1]) == 5
    assert len(indexed[m5_bucket_2]) == 5
    assert len(indexed[m5_bucket_3]) == 2


def test_backtest_intrabar_m1_resolves_target():
    t0 = datetime(2026, 9, 1, 9, 30)
    m5_bars = [
        _bar(t0, 100, 101, 99, 100),  # signal bar
        _bar(t0 + timedelta(minutes=5), 100, 106, 94, 102),  # fills at 100, touches stop 95 & target 105
        _bar(t0 + timedelta(minutes=10), 102, 103, 101, 102),
    ]
    # M1 slice for the second bar (9:35): touches target first
    m1_bars = [
        _bar(t0 + timedelta(minutes=5), 100, 101, 99, 100),
        _bar(t0 + timedelta(minutes=6), 100, 106, 99, 105),  # hits 105 target
        _bar(t0 + timedelta(minutes=7), 105, 105, 94, 95),   # hits 95 stop later
        _bar(t0 + timedelta(minutes=8), 95, 96, 94, 95),
        _bar(t0 + timedelta(minutes=9), 95, 102, 95, 102),
    ]

    config = BacktestConfig(slippage_points=0.0, commission_per_side=0.0, fixed_quantity=1)
    strat = _SignalOnce("long", stop=95.0, target=105.0)

    # Without M1: falls back to conservative stop
    res_no_m1 = run_backtest(m5_bars, strat, config)
    assert res_no_m1.n_trades == 1
    assert res_no_m1.trades[0].exit_reason == "stop_loss"
    assert res_no_m1.intrabar_audit.ambiguous_bars_count == 1
    assert res_no_m1.intrabar_audit.no_m1_data_fallback == 1

    # With M1: correctly resolved to take_profit
    strat_m1 = _SignalOnce("long", stop=95.0, target=105.0)
    res_m1 = run_backtest(m5_bars, strat_m1, config, m1_bars=m1_bars)
    assert res_m1.n_trades == 1
    assert res_m1.trades[0].exit_reason == "take_profit"
    assert res_m1.trades[0].exit_price == 105.0
    assert res_m1.intrabar_audit.ambiguous_bars_count == 1
    assert res_m1.intrabar_audit.resolved_by_m1_target == 1
    assert res_m1.intrabar_audit.resolution_rate_pct == 100.0


def test_end_of_data_policy_close_vs_unresolved():
    t0 = datetime(2026, 9, 1, 9, 30)
    bars = [
        _bar(t0, 100, 101, 99, 100),  # signal
        _bar(t0 + timedelta(minutes=5), 100, 102, 98, 101),  # enters, doesn't hit far stop/target
        _bar(t0 + timedelta(minutes=10), 101, 103, 99, 102.5),  # last bar closes at 102.5
    ]

    # Policy 'close': closes at last bar close
    cfg_close = BacktestConfig(
        slippage_points=0.0,
        commission_per_side=0.0,
        fixed_quantity=1,
        end_of_data_policy="close",
    )
    res_close = run_backtest(bars, _SignalOnce("long", stop=50.0, target=150.0), cfg_close)
    assert res_close.unresolved_positions == 0
    assert res_close.n_trades == 1
    assert res_close.trades[0].exit_reason == "end_of_data"
    assert res_close.trades[0].exit_price == 102.5
    assert res_close.open_position is None

    # Policy 'unresolved': keeps position open and reports price & state
    cfg_unres = BacktestConfig(
        slippage_points=0.0,
        commission_per_side=0.0,
        fixed_quantity=1,
        end_of_data_policy="unresolved",
    )
    class _PointsSignal(Strategy):
        def __init__(self):
            self.fired = False
        def evaluate(self, history):
            if not self.fired:
                self.fired = True
                return Signal("long", history[-1].close, 50.0, 50.0, stop_target_as_points=True)
            return None

    res_unres = run_backtest(bars, _PointsSignal(), cfg_unres)
    assert res_unres.unresolved_positions == 1
    assert res_unres.n_trades == 0
    assert res_unres.open_position is not None
    assert res_unres.open_position["state"] == "open"
    assert res_unres.open_position["last_price"] == 102.5
    assert res_unres.open_position["unrealized_gross_pnl"] == (102.5 - 100.0) * 2.0 * 1  # MNQ dollar_per_point=2


def test_r_and_equity_ledger_fields():
    t0 = datetime(2026, 9, 1, 9, 30)
    bars = [
        _bar(t0, 100, 101, 99, 100),
        _bar(t0 + timedelta(minutes=5), 100, 115, 99, 112),  # enters at 100, hits 110 target
    ]
    cfg = BacktestConfig(
        initial_balance=50_000.0,
        risk_per_trade=0.01,  # $500 budgeted risk
        dollar_per_point=2.0,
        fixed_quantity=1,
        slippage_points=0.0,
        commission_per_side=0.0,
    )
    strat = _SignalOnce("long", stop=90.0, target=110.0)
    res = run_backtest(bars, strat, cfg)
    assert res.n_trades == 1
    t = res.trades[0]
    # Move is 10 points * $2/pt * 1 = $20
    assert t.net_pnl == 20.0
    # Budgeted risk is 1% of $50,000 = $500
    assert t.budgeted_risk_dollars == 500.0
    assert t.budgeted_r == 20.0 / 500.0
    # Effective risk to stop is 10 points * $2/pt * 1 = $20
    assert t.effective_risk_dollars == 20.0
    assert t.effective_r == 20.0 / 20.0

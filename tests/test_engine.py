"""
Phase 3-4 tests: single simulation engine.

Tests cover all terminal conditions, edge cases, and deterministic
behavior. Uses hand-crafted Trade objects — no randomness.
"""

import math

import pytest

from src.account import AccountState
from src.engine import SimulationResult, run_simulation
from src.types import FundedAccountRules, Trade


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trades(r_results: list[float], start_date: str = "2024-01-01") -> list[Trade]:
    """Create minimal Trade objects with sequential dates (one per trade)."""
    trades = []
    for i, r in enumerate(r_results):
        trades.append(Trade(
            r_result=r,
            trade_id=f"t{i:04d}",
            date=f"2024-01-{i+1:02d}",
        ))
    return trades


def _standard_rules(**kwargs) -> FundedAccountRules:
    defaults = dict(
        initial_balance=100_000.0,
        profit_target_pct=0.10,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.05,
        risk_per_trade=0.01,
        max_trades=None,
    )
    defaults.update(kwargs)
    return FundedAccountRules(**defaults)


# ---------------------------------------------------------------------------
# Terminal conditions
# ---------------------------------------------------------------------------


def test_stops_on_profit_target():
    """Simulation stops immediately when profit target is reached."""
    trades = _make_trades([10.0, -50.0])  # 10R win → target; 50R loss would kill it
    result = run_simulation(trades, _standard_rules())
    assert result.terminal_condition == "profit_target"
    assert result.passed
    assert result.trades_executed == 1
    assert result.final_equity == 110_000.0


def test_stops_on_max_drawdown():
    """Simulation stops when max drawdown is violated."""
    # 2R win → peak 102K, then -12R → equity 90K, DD=12K/102K≈11.76% > 10%
    trades = _make_trades([2.0, -12.0])
    result = run_simulation(trades, _standard_rules())
    assert result.terminal_condition == "max_drawdown"
    assert not result.passed
    assert result.trades_executed == 2
    assert result.max_drawdown_hit >= 0.10
    assert result.max_drawdown_historical >= 0.10


def test_stops_on_daily_loss():
    """Simulation stops when daily loss limit is violated."""
    # Both on same day: -6R → 6% daily loss > 5%
    trades = [
        Trade(r_result=-3.0, trade_id="t0", date="2024-01-01"),
        Trade(r_result=-3.0, trade_id="t1", date="2024-01-01"),
    ]
    result = run_simulation(trades, _standard_rules())
    assert result.terminal_condition == "daily_loss"
    assert not result.passed
    assert result.trades_executed == 2


def test_stops_on_max_trades():
    """Simulation stops at max_trades if no other condition triggers."""
    trades = _make_trades([0.5, 0.5, 0.5, 0.5, 0.5])
    result = run_simulation(trades, _standard_rules(max_trades=3))
    assert result.terminal_condition == "max_trades"
    assert not result.passed
    assert result.trades_executed == 3


def test_runs_all_trades_when_no_condition():
    """Without triggers, all trades are applied."""
    trades = _make_trades([0.5, -0.3, 0.2, -0.1, 0.4])
    result = run_simulation(trades, _standard_rules())
    assert result.terminal_condition == "completed"
    assert result.trades_executed == 5


# ---------------------------------------------------------------------------
# Impact verification (FARS_SPEC §16 tests 4-5)
# ---------------------------------------------------------------------------


def test_minus_1r_with_1pct_risk():
    """-1R trade with 1% risk → approximately -1% account impact."""
    trades = _make_trades([-1.0])
    result = run_simulation(trades, _standard_rules(risk_per_trade=0.01))
    assert result.final_equity == 99_000.0  # exactly -1%


def test_plus_2r_with_half_pct_risk():
    """+2R trade with 0.5% risk → approximately +1% account impact."""
    trades = _make_trades([2.0])
    result = run_simulation(trades, _standard_rules(risk_per_trade=0.005))
    assert result.final_equity == 101_000.0  # exactly +1%


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_same_inputs_same_result():
    """Same trades + same rules → identical SimulationResult."""
    trades = _make_trades([1.0, -0.5, 2.0, -1.0, 0.5])
    rules = _standard_rules()
    r1 = run_simulation(trades, rules)
    r2 = run_simulation(trades, rules)
    assert r1 == r2


# ---------------------------------------------------------------------------
# Priority: profit target checked before drawdown
# ---------------------------------------------------------------------------


def test_profit_target_takes_priority():
    """
    A trade that simultaneously hits profit target AND would violate
    drawdown should stop at profit target (checked first).
    """
    # First: win big → equity near target
    # Second: win that pushes over target even though DD from intra-trade
    # is irrelevant since we check target first
    trades = _make_trades([9.0, 1.0])  # First: 109K, second: 110K = target
    result = run_simulation(trades, _standard_rules())
    assert result.terminal_condition == "profit_target"


def test_drawdown_checked_before_max_trades():
    """If both drawdown and max_trades trigger on same trade, drawdown wins."""
    trades = _make_trades([-12.0])
    result = run_simulation(trades, _standard_rules(max_trades=1))
    assert result.terminal_condition == "max_drawdown"
    assert result.trades_executed == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_trades():
    """Empty trade list → completed with 0 trades."""
    result = run_simulation([], _standard_rules())
    assert result.terminal_condition == "completed"
    assert result.trades_executed == 0
    assert result.final_equity == 100_000.0
    assert result.equity_curve == []


def test_single_trade_win():
    result = run_simulation(_make_trades([1.0]), _standard_rules())
    assert result.terminal_condition == "completed"
    assert result.trades_executed == 1
    assert result.final_equity == 101_000.0


def test_single_trade_loss():
    result = run_simulation(_make_trades([-1.0]), _standard_rules())
    assert result.terminal_condition == "completed"
    assert result.trades_executed == 1
    assert result.final_equity == 99_000.0


def test_exact_profit_target():
    """Hitting target exactly should trigger it (>= not just >)."""
    trades = _make_trades([10.0])
    result = run_simulation(trades, _standard_rules())
    assert result.terminal_condition == "profit_target"
    assert result.final_equity == 110_000.0


def test_exact_drawdown_limit():
    """Hitting drawdown exactly at limit should be a violation."""
    trades = _make_trades([-10.0])  # equity 90K, DD = 10K/100K = 10%
    result = run_simulation(trades, _standard_rules())
    assert result.terminal_condition == "max_drawdown"


def test_exact_daily_loss_limit():
    """Hitting daily loss exactly at 5% should violate."""
    trades = [
        Trade(r_result=-5.0, trade_id="t0", date="2024-01-01"),
    ]
    result = run_simulation(trades, _standard_rules())
    assert result.terminal_condition == "daily_loss"


def test_zero_r_trade():
    """0R trade should not trigger any condition."""
    trades = _make_trades([0.0, 0.0, 0.0])
    result = run_simulation(trades, _standard_rules())
    assert result.terminal_condition == "completed"
    assert result.final_equity == 100_000.0


def test_nan_trade_rejected():
    """NaN r_result should raise ValueError."""
    trades = [Trade(r_result=float("nan"), trade_id="bad", date="2024-01-01")]
    with pytest.raises(ValueError, match="non-finite"):
        run_simulation(trades, _standard_rules())


def test_inf_trade_rejected():
    """inf r_result should raise ValueError."""
    trades = [Trade(r_result=float("inf"), trade_id="bad", date="2024-01-01")]
    with pytest.raises(ValueError, match="non-finite"):
        run_simulation(trades, _standard_rules())


# ---------------------------------------------------------------------------
# SimulationResult
# ---------------------------------------------------------------------------


def test_result_passed_property():
    result = SimulationResult(
        final_equity=110_000, terminal_condition="profit_target",
        trades_executed=1, max_drawdown_hit=0.0,
        max_drawdown_historical=0.0, daily_loss_hit=0.0,
    )
    assert result.passed


def test_result_not_passed():
    result = SimulationResult(
        final_equity=90_000, terminal_condition="max_drawdown",
        trades_executed=1, max_drawdown_hit=0.10,
        max_drawdown_historical=0.10, daily_loss_hit=0.0,
    )
    assert not result.passed


def test_result_invalid_terminal_condition():
    with pytest.raises(ValueError, match="terminal_condition"):
        SimulationResult(
            final_equity=100_000, terminal_condition="bogus",
            trades_executed=0, max_drawdown_hit=0.0,
            max_drawdown_historical=0.0, daily_loss_hit=0.0,
        )


def test_result_frozen():
    result = SimulationResult(
        final_equity=100_000, terminal_condition="completed",
        trades_executed=0, max_drawdown_hit=0.0,
        max_drawdown_historical=0.0, daily_loss_hit=0.0,
    )
    with pytest.raises(Exception):
        result.final_equity = 200_000  # type: ignore


# ---------------------------------------------------------------------------
# Multiple days: daily loss resets
# ---------------------------------------------------------------------------


def test_daily_loss_does_not_violate_across_days():
    """3% loss day 1 + 3% loss day 2 → neither violates 5% limit alone."""
    trades = [
        Trade(r_result=-3.0, trade_id="t0", date="2024-01-01"),
        Trade(r_result=-3.0, trade_id="t1", date="2024-01-02"),
    ]
    result = run_simulation(trades, _standard_rules())
    assert result.terminal_condition == "completed"


def test_daily_loss_accumulates_same_day_violation():
    """2% + 4% on same day = 6% → violation."""
    trades = [
        Trade(r_result=-2.0, trade_id="t0", date="2024-01-01"),
        Trade(r_result=-4.0, trade_id="t1", date="2024-01-01"),
    ]
    result = run_simulation(trades, _standard_rules())
    assert result.terminal_condition == "daily_loss"


# ---------------------------------------------------------------------------
# max_drawdown_hit and daily_loss_hit track worst case
# ---------------------------------------------------------------------------


def test_max_drawdown_hit_worst_case():
    """Even if simulation ends on profit target, max_drawdown_historical records peak DD."""
    trades = _make_trades([-3.0, 15.0])  # DD of 3% then big win
    result = run_simulation(trades, _standard_rules())
    assert result.terminal_condition == "profit_target"
    assert result.max_drawdown_historical > 0.0  # the 3% peak-to-trough DD


def test_historical_dd_diverges_from_rule_dd():
    """Historical peak-to-trough DD can be large while static rule DD is zero."""
    # Static mode: equity 100K→109K→100K, spread across days to avoid daily loss
    trades = [
        Trade(r_result=9.0, trade_id="t0", date="2024-01-01"),
        Trade(r_result=-3.0, trade_id="t1", date="2024-01-02"),
        Trade(r_result=-3.0, trade_id="t2", date="2024-01-03"),
        Trade(r_result=-3.0, trade_id="t3", date="2024-01-04"),
    ]
    result = run_simulation(trades, _standard_rules(drawdown_mode="static"))
    assert result.max_drawdown_hit == pytest.approx(0.0)  # rule: never below initial
    assert result.max_drawdown_historical > 0.05  # historical peak-to-trough
    assert result.terminal_condition == "completed"


def test_daily_loss_hit_worst_case():
    """Daily loss hit tracks the worst intra-day loss seen."""
    trades = [
        Trade(r_result=-4.0, trade_id="t0", date="2024-01-01"),
        Trade(r_result=2.0, trade_id="t1", date="2024-01-01"),
    ]
    result = run_simulation(trades, _standard_rules())
    assert result.daily_loss_hit == pytest.approx(0.04)


# ---------------------------------------------------------------------------
# Date validation
# ---------------------------------------------------------------------------


def test_rejects_backwards_dates():
    """Non-chronological dates should raise ValueError."""
    trades = [
        Trade(r_result=1.0, trade_id="t0", date="2024-01-05"),
        Trade(r_result=1.0, trade_id="t1", date="2024-01-03"),  # backwards!
    ]
    with pytest.raises(ValueError, match="non-decreasing"):
        run_simulation(trades, _standard_rules())


def test_accepts_same_date():
    """Same date on consecutive trades is fine (intra-day)."""
    trades = [
        Trade(r_result=1.0, trade_id="t0", date="2024-01-01"),
        Trade(r_result=1.0, trade_id="t1", date="2024-01-01"),
    ]
    result = run_simulation(trades, _standard_rules())
    assert result.trades_executed == 2


# ---------------------------------------------------------------------------
# Floating-point edge: profit target
# ---------------------------------------------------------------------------


def test_profit_target_exact_floating_point_engine():
    """10% target at 100K with +10R should pass despite FP imprecision."""
    trades = _make_trades([10.0])
    result = run_simulation(trades, _standard_rules(profit_target_pct=0.10))
    assert result.terminal_condition == "profit_target"
    assert result.passed


def test_drawdown_exact_floating_point_engine():
    """10% DD with -10R should be caught despite any FP noise."""
    trades = _make_trades([-10.0])
    result = run_simulation(trades, _standard_rules(max_drawdown_pct=0.10))
    assert result.terminal_condition == "max_drawdown"


def test_profit_target_7pct():
    """7% target (not binary-representable) should not fail."""
    trades = _make_trades([7.0])
    result = run_simulation(trades, _standard_rules(profit_target_pct=0.07))
    assert result.terminal_condition == "profit_target"


# ---------------------------------------------------------------------------
# Invalid date format
# ---------------------------------------------------------------------------


def test_rejects_invalid_date_format():
    """Non-ISO dates should raise ValueError."""
    trades = [
        Trade(r_result=1.0, trade_id="t0", date="01/15/2024"),
    ]
    with pytest.raises(ValueError, match="invalid date"):
        run_simulation(trades, _standard_rules())


def test_rejects_nonsense_date():
    trades = [
        Trade(r_result=1.0, trade_id="t0", date="not-a-date"),
    ]
    with pytest.raises(ValueError, match="invalid date"):
        run_simulation(trades, _standard_rules())


# ---------------------------------------------------------------------------
# Small account: relative tolerance prevents false positives
# ---------------------------------------------------------------------------


def test_small_account_profit_target():
    """$10 account with 10% target: equity $10.99 should NOT trigger $11 target."""
    rules = FundedAccountRules(
        initial_balance=10.0, profit_target_pct=0.10,
        max_drawdown_pct=0.10, daily_loss_limit_pct=0.05,
        risk_per_trade=0.01,
    )
    # +9.9R at 1% of $10 = +$0.10 per R → +$0.99
    # equity = 10.99, target = 11.0 → NOT reached
    trades = _make_trades([9.9])
    result = run_simulation(trades, rules)
    assert result.terminal_condition != "profit_target"

    # +10.1R → equity = 10 + 10.1*0.1 = 11.01 → reached
    trades2 = _make_trades([10.1])
    result2 = run_simulation(trades2, rules)
    assert result2.terminal_condition == "profit_target"

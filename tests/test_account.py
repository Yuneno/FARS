"""
Phase 3 tests: account state tracking.

Tests cover equity updates, drawdown calculation, daily loss tracking,
and rule-constraint evaluation. Uses known Trade sequences — no
randomness, no synthetic data dependency.
"""

import math

import pytest

from src.account import AccountState
from src.types import FundedAccountRules


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def standard_rules() -> FundedAccountRules:
    """Typical prop-firm rules: 100K account, 10% target, 10% max DD, 5% daily loss."""
    return FundedAccountRules(
        initial_balance=100_000.0,
        profit_target_pct=0.10,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.05,
        risk_per_trade=0.01,  # 1% risk
        daily_loss_base="initial",
    )


@pytest.fixture
def fresh_account(standard_rules: FundedAccountRules) -> AccountState:
    return AccountState(rules=standard_rules)


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


def test_initial_equity(fresh_account: AccountState):
    assert fresh_account.equity == 100_000.0
    assert fresh_account.peak_equity == 100_000.0
    assert fresh_account.start_of_day_equity == 100_000.0
    assert fresh_account.trades_applied == 0
    assert fresh_account.equity_curve == []


def test_initial_no_violations(fresh_account: AccountState):
    assert not fresh_account.is_profit_target_reached()
    assert not fresh_account.is_max_drawdown_violated()
    assert not fresh_account.is_daily_loss_violated()
    assert fresh_account.current_drawdown() == 0.0
    assert fresh_account.daily_loss_today() == 0.0


# ---------------------------------------------------------------------------
# Equity updates
# ---------------------------------------------------------------------------


def test_apply_win_increases_equity(fresh_account: AccountState):
    """+1R with 1% risk → equity +1% of initial = +1000."""
    fresh_account.apply_trade(r_result=1.0, date="2024-01-01")
    assert fresh_account.equity == 101_000.0


def test_apply_loss_decreases_equity(fresh_account: AccountState):
    """-1R with 1% risk → equity -1% of initial = -1000."""
    fresh_account.apply_trade(r_result=-1.0, date="2024-01-01")
    assert fresh_account.equity == 99_000.0


def test_apply_2r_win(fresh_account: AccountState):
    """+2R with 0.5% risk → equity +1% of initial."""
    rules = FundedAccountRules(
        initial_balance=100_000, profit_target_pct=0.10,
        max_drawdown_pct=0.10, daily_loss_limit_pct=0.05,
        risk_per_trade=0.005,
    )
    account = AccountState(rules=rules)
    account.apply_trade(r_result=2.0, date="2024-01-01")
    assert account.equity == 101_000.0


def test_apply_half_r_loss(fresh_account: AccountState):
    """-0.5R with 1% risk → equity -0.5% of initial."""
    fresh_account.apply_trade(r_result=-0.5, date="2024-01-01")
    assert fresh_account.equity == 99_500.0


# ---------------------------------------------------------------------------
# Peak equity and drawdown
# ---------------------------------------------------------------------------


def test_peak_equity_updates(fresh_account: AccountState):
    fresh_account.apply_trade(r_result=2.0, date="2024-01-01")
    assert fresh_account.peak_equity == 102_000.0
    fresh_account.apply_trade(r_result=-0.5, date="2024-01-01")
    assert fresh_account.peak_equity == 102_000.0  # unchanged
    fresh_account.apply_trade(r_result=3.0, date="2024-01-01")
    assert fresh_account.peak_equity == 104_500.0  # new peak (101500 + 3000)


def test_drawdown_simple(fresh_account: AccountState):
    """Static DD: 100K init → 99K equity → DD = 1K/100K = 1% from initial."""
    fresh_account.apply_trade(r_result=-1.0, date="2024-01-01")  # → 99K
    assert fresh_account.current_drawdown() == pytest.approx(0.01)


def test_drawdown_static_vs_trailing():
    """Static drawdown uses initial_balance, trailing uses peak_equity."""
    # --- Static mode ---
    rules_static = FundedAccountRules(
        initial_balance=100_000, profit_target_pct=0.10,
        max_drawdown_pct=0.10, daily_loss_limit_pct=0.05,
        drawdown_mode="static", risk_per_trade=0.01,
    )
    acc_s = AccountState(rules=rules_static)
    acc_s.apply_trade(r_result=50.0, date="2024-01-01")   # → 150K
    acc_s.apply_trade(r_result=-30.0, date="2024-01-01")  # → 120K
    # Static: (100K - 120K) / 100K = 0? No, equity > initial → DD = 0
    assert acc_s.current_drawdown() == 0.0
    acc_s.apply_trade(r_result=-30.0, date="2024-01-01")  # → 90K
    # Static: (100K - 90K) / 100K = 10%
    assert acc_s.current_drawdown() == pytest.approx(0.10)
    assert acc_s.is_max_drawdown_violated()

    # --- Trailing mode ---
    rules_trail = FundedAccountRules(
        initial_balance=100_000, profit_target_pct=0.10,
        max_drawdown_pct=0.10, daily_loss_limit_pct=0.05,
        drawdown_mode="trailing", risk_per_trade=0.01,
    )
    acc_t = AccountState(rules=rules_trail)
    acc_t.apply_trade(r_result=50.0, date="2024-01-01")   # → 150K peak
    acc_t.apply_trade(r_result=-30.0, date="2024-01-01")  # → 120K
    # Trailing: (150K - 120K) / 150K = 20%
    dd = acc_t.current_drawdown()
    assert dd == pytest.approx(30000 / 150000)
    assert dd > 0.10  # Already violated in trailing mode!


def test_drawdown_no_losses(fresh_account: AccountState):
    fresh_account.apply_trade(r_result=1.0, date="2024-01-01")
    fresh_account.apply_trade(r_result=2.0, date="2024-01-01")
    assert fresh_account.current_drawdown() == 0.0


def test_drawdown_violation(fresh_account: AccountState):
    """Static DD: equity to 90K = 10% from initial."""
    fresh_account.apply_trade(r_result=-10.0, date="2024-01-01")  # → 90K
    assert fresh_account.is_max_drawdown_violated()


def test_drawdown_below_limit(fresh_account: AccountState):
    """Drawdown below 10% should NOT violate."""
    fresh_account.apply_trade(r_result=-9.0, date="2024-01-01")  # DD = 9% < 10%
    assert not fresh_account.is_max_drawdown_violated()


# ---------------------------------------------------------------------------
# Daily loss
# ---------------------------------------------------------------------------


def test_daily_loss_reset_new_day(fresh_account: AccountState):
    """Daily loss must reset when date changes."""
    fresh_account.apply_trade(r_result=-4.0, date="2024-01-01")  # 4% loss
    assert fresh_account.daily_loss_today() == pytest.approx(0.04)

    # New day: daily loss resets to 0 before this trade
    fresh_account.apply_trade(r_result=-2.0, date="2024-01-02")
    # start_of_day = 96K (equity after day 1)
    # This trade: -2R at 1% = -2000 → equity = 94K
    # loss from start_of_day = 2000 / 96K for "initial" base → 2000/100K = 2%
    assert fresh_account.daily_loss_today() == pytest.approx(0.02)


def test_daily_loss_accumulates_same_day(fresh_account: AccountState):
    """Two losses on same day accumulate."""
    fresh_account.apply_trade(r_result=-2.0, date="2024-01-01")  # -2K
    fresh_account.apply_trade(r_result=-1.5, date="2024-01-01")  # -1.5K
    # Total loss: 3.5K on 100K base = 3.5%
    assert fresh_account.daily_loss_today() == pytest.approx(0.035)


def test_daily_loss_violation(fresh_account: AccountState):
    """5% daily loss should trigger violation."""
    fresh_account.apply_trade(r_result=-5.0, date="2024-01-01")
    assert fresh_account.is_daily_loss_violated()


def test_daily_loss_below_limit(fresh_account: AccountState):
    """4% daily loss should NOT trigger 5% limit."""
    fresh_account.apply_trade(r_result=-4.0, date="2024-01-01")
    assert not fresh_account.is_daily_loss_violated()


def test_daily_loss_positive_day(fresh_account: AccountState):
    """Winning day → daily loss = 0."""
    fresh_account.apply_trade(r_result=3.0, date="2024-01-01")
    assert fresh_account.daily_loss_today() == 0.0


def test_daily_loss_after_win_then_loss(fresh_account: AccountState):
    """Win then loss on same day: daily loss tracks from start_of_day."""
    fresh_account.apply_trade(r_result=3.0, date="2024-01-01")  # → 103K
    fresh_account.apply_trade(r_result=-4.0, date="2024-01-01")  # → 99K
    # start_of_day = 100K, current = 99K, loss = 1K / 100K = 1%
    assert fresh_account.daily_loss_today() == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# Profit target
# ---------------------------------------------------------------------------


def test_profit_target_reached(fresh_account: AccountState):
    """10% profit target requires 110K."""
    fresh_account.apply_trade(r_result=10.0, date="2024-01-01")  # +10R → +10K
    assert fresh_account.equity == 110_000.0
    assert fresh_account.is_profit_target_reached()


def test_profit_target_not_reached(fresh_account: AccountState):
    fresh_account.apply_trade(r_result=9.0, date="2024-01-01")
    assert fresh_account.equity == 109_000.0
    assert not fresh_account.is_profit_target_reached()


def test_profit_target_exceeded(fresh_account: AccountState):
    """Going above target should also register as reached."""
    fresh_account.apply_trade(r_result=15.0, date="2024-01-01")  # → 115K
    assert fresh_account.is_profit_target_reached()


# ---------------------------------------------------------------------------
# Max trades
# ---------------------------------------------------------------------------


def test_max_trades_not_reached(fresh_account: AccountState):
    fresh_account.apply_trade(r_result=1.0, date="2024-01-01")
    fresh_account.apply_trade(r_result=1.0, date="2024-01-01")
    assert not fresh_account.is_max_trades_reached()  # max_trades=None = unlimited


def test_max_trades_reached():
    rules = FundedAccountRules(
        initial_balance=100_000, profit_target_pct=0.10,
        max_drawdown_pct=0.10, daily_loss_limit_pct=0.05,
        max_trades=2, risk_per_trade=0.01,
    )
    account = AccountState(rules=rules)
    account.apply_trade(r_result=1.0, date="2024-01-01")
    account.apply_trade(r_result=1.0, date="2024-01-01")
    assert account.is_max_trades_reached()


# ---------------------------------------------------------------------------
# Equity curve
# ---------------------------------------------------------------------------


def test_equity_curve_builds(fresh_account: AccountState):
    fresh_account.apply_trade(r_result=1.0, date="2024-01-01")
    fresh_account.apply_trade(r_result=-0.5, date="2024-01-01")
    fresh_account.apply_trade(r_result=2.0, date="2024-01-01")
    curve = fresh_account.equity_curve
    assert len(curve) == 3
    assert curve[0] == 101_000.0
    assert curve[1] == 100_500.0
    assert curve[2] == 102_500.0


def test_equity_curve_is_copy(fresh_account: AccountState):
    """Modifying the returned list should not affect internal state."""
    fresh_account.apply_trade(r_result=1.0, date="2024-01-01")
    curve = fresh_account.equity_curve
    curve.append(999_999.0)
    assert fresh_account.equity_curve[-1] == 101_000.0


# ---------------------------------------------------------------------------
# apply_trade rejects NaN/inf directly
# ---------------------------------------------------------------------------


def test_apply_trade_rejects_nan(fresh_account: AccountState):
    with pytest.raises(ValueError, match="finite"):
        fresh_account.apply_trade(r_result=float("nan"), date="2024-01-01")


def test_apply_trade_rejects_inf(fresh_account: AccountState):
    with pytest.raises(ValueError, match="finite"):
        fresh_account.apply_trade(r_result=float("inf"), date="2024-01-01")


# ---------------------------------------------------------------------------
# Floating-point edge: non-binary-representable percentages
# ---------------------------------------------------------------------------


def test_profit_target_floating_point():
    """Profit target with 0.07 (not binary-representable) should not fail FP."""
    rules = FundedAccountRules(
        initial_balance=100_000, profit_target_pct=0.07,
        max_drawdown_pct=0.10, daily_loss_limit_pct=0.05,
        risk_per_trade=0.01,
    )
    account = AccountState(rules=rules)
    # 7R at 1% = +7K → equity 107K, target = 107K exactly?
    account.apply_trade(r_result=7.0, date="2024-01-01")
    assert account.is_profit_target_reached()
    assert account.equity == 107_000.0


def test_daily_loss_floating_point():
    """Daily loss at limit with 0.07 should not fail FP."""
    rules = FundedAccountRules(
        initial_balance=100_000, profit_target_pct=0.10,
        max_drawdown_pct=0.10, daily_loss_limit_pct=0.07,
        risk_per_trade=0.01,
    )
    account = AccountState(rules=rules)
    account.apply_trade(r_result=-7.0, date="2024-01-01")
    assert account.is_daily_loss_violated()


# ---------------------------------------------------------------------------
# daily_loss_base: eod mode
# ---------------------------------------------------------------------------


def test_daily_loss_eod_mode():
    """eod mode: daily loss is % of start_of_day_equity, not initial_balance."""
    rules = FundedAccountRules(
        initial_balance=100_000, profit_target_pct=0.10,
        max_drawdown_pct=0.10, daily_loss_limit_pct=0.05,
        risk_per_trade=0.01, daily_loss_base="eod",
    )
    account = AccountState(rules=rules)
    # Day 1: win → equity grows
    account.apply_trade(r_result=5.0, date="2024-01-01")  # → 105K
    # Day 2: loss from 105K
    account.apply_trade(r_result=-4.0, date="2024-01-02")  # → 101K
    # start_of_day = 105K, loss = 4K / 105K ≈ 3.81%
    expected = 4000 / 105000
    assert account.daily_loss_today() == pytest.approx(expected)

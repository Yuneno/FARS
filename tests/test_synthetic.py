"""
Phase 1 tests: synthetic trade data generation.

Tests follow fars-testing skill requirements:
  - Fixed seeds for reproducibility tests
  - Loose tolerances for statistical tests (large N approximations)
  - Edge cases: zero std, single trade, validation, NaN/inf
"""

import math
import warnings

import numpy as np
import pytest

from src.synthetic import generate_trades, generate_trade_sequences
from src.types import SyntheticConfig, FundedAccountRules


# =========================================================================
# Reproducibility
# =========================================================================

def test_same_seed_same_sequence():
    """Same SyntheticConfig + same seed → identical r_result sequence."""
    config1 = SyntheticConfig(seed=42, n_trades=100)
    config2 = SyntheticConfig(seed=42, n_trades=100)
    trades1 = generate_trades(config1)
    trades2 = generate_trades(config2)
    assert [t.r_result for t in trades1] == [t.r_result for t in trades2]


def test_different_seeds_different_sequence():
    """Different seeds → different r_result sequences."""
    config1 = SyntheticConfig(seed=42, n_trades=100)
    config2 = SyntheticConfig(seed=99, n_trades=100)
    trades1 = generate_trades(config1)
    trades2 = generate_trades(config2)
    assert [t.r_result for t in trades1] != [t.r_result for t in trades2]


def test_no_seed_produces_different_sequences():
    """seed=None → each call produces a different sequence."""
    config = SyntheticConfig(seed=None, n_trades=50)
    seq1 = generate_trades(config)
    seq2 = generate_trades(config)
    assert [t.r_result for t in seq1] != [t.r_result for t in seq2]


# =========================================================================
# Output structure
# =========================================================================

def test_correct_number_of_trades():
    """len(generate_trades(config)) == config.n_trades."""
    for n in [1, 10, 100, 1000]:
        config = SyntheticConfig(seed=42, n_trades=n)
        trades = generate_trades(config)
        assert len(trades) == n


def test_unique_days_correct():
    """trades_per_day produces correct number of unique trade dates."""
    config = SyntheticConfig(seed=42, n_trades=100, trades_per_day=5)
    trades = generate_trades(config)
    unique_dates = {t.date for t in trades}
    expected_days = 100 // 5
    assert len(unique_dates) == expected_days


def test_dates_are_sequential():
    """Trade dates are ordered and advancing."""
    config = SyntheticConfig(seed=42, n_trades=100, trades_per_day=5)
    trades = generate_trades(config)
    dates = [t.date for t in trades]
    assert dates == sorted(dates)
    assert len(set(dates)) == 20


def test_trade_ids_unique():
    """Every trade has a unique trade_id."""
    config = SyntheticConfig(seed=42, n_trades=100)
    trades = generate_trades(config)
    ids = [t.trade_id for t in trades]
    assert len(ids) == len(set(ids))


# =========================================================================
# DGP correctness: win_rate and means (regression tests for CRITICAL #1, #2)
# =========================================================================

def test_win_rate_equals_positive_fraction():
    """
    win_rate controls P(r_result > 0) directly.
    This is the regression test for Codex CRITICAL #2.
    """
    config = SyntheticConfig(
        seed=42, n_trades=50000, win_rate=0.50,
        avg_win_r=0.1, avg_loss_r=0.1,
        std_win_r=2.0, std_loss_r=2.0,
    )
    trades = generate_trades(config)
    positive_frac = sum(1 for t in trades if t.r_result > 0) / len(trades)

    # Should be close to configured win_rate even with high std
    assert 0.47 <= positive_frac <= 0.53


def test_avg_win_r_matches_configured():
    """
    The empirical mean of positive trades equals avg_win_R.
    Regression test for Codex CRITICAL #1.
    """
    config = SyntheticConfig(
        seed=42, n_trades=50000, win_rate=0.50,
        avg_win_r=0.1, avg_loss_r=0.1,
        std_win_r=2.0, std_loss_r=2.0,
    )
    trades = generate_trades(config)
    wins = [t.r_result for t in trades if t.r_result > 0]
    empirical_mean = sum(wins) / len(wins)

    # Should be ~0.1, not ~1.6 as with truncated normal
    assert 0.07 <= empirical_mean <= 0.13


def test_avg_loss_r_matches_configured():
    """
    The empirical mean of |negative trades| equals avg_loss_R.
    Regression test for Codex CRITICAL #1.
    """
    config = SyntheticConfig(
        seed=42, n_trades=50000, win_rate=0.50,
        avg_win_r=0.1, avg_loss_r=0.1,
        std_win_r=2.0, std_loss_r=2.0,
    )
    trades = generate_trades(config)
    losses = [abs(t.r_result) for t in trades if t.r_result < 0]
    empirical_mean = sum(losses) / len(losses)

    assert 0.07 <= empirical_mean <= 0.13


def test_empirical_win_rate_approximate():
    """Large N → empirical P(r>0) close to configured win_rate (default params)."""
    config = SyntheticConfig(seed=42, n_trades=10000, win_rate=0.45)
    trades = generate_trades(config)
    n_pos = sum(1 for t in trades if t.r_result > 0)
    empirical_wr = n_pos / len(trades)
    assert 0.43 <= empirical_wr <= 0.47


def test_avg_win_r_approximate_defaults():
    """Large N with default params → empirical avg win R ~= configured."""
    config = SyntheticConfig(
        seed=42, n_trades=50000, win_rate=0.50,
        avg_win_r=2.0, avg_loss_r=1.0,
        std_win_r=0.5, std_loss_r=0.3,
    )
    trades = generate_trades(config)
    wins = [t.r_result for t in trades if t.r_result > 0]
    empirical_avg = sum(wins) / len(wins)
    assert 1.90 <= empirical_avg <= 2.10


def test_avg_loss_r_approximate_defaults():
    """Large N with default params → empirical avg loss R ~= configured."""
    config = SyntheticConfig(
        seed=42, n_trades=50000, win_rate=0.50,
        avg_win_r=2.0, avg_loss_r=1.0,
        std_win_r=0.5, std_loss_r=0.3,
    )
    trades = generate_trades(config)
    losses = [abs(t.r_result) for t in trades if t.r_result < 0]
    empirical_avg = sum(losses) / len(losses)
    assert 0.90 <= empirical_avg <= 1.10


# =========================================================================
# Sign correctness (NOT tautological)
# =========================================================================

def test_all_trades_nonzero():
    """
    With lognormal DGP and non-degenerate parameters, all trades
    should have strictly non-zero r_result.
    """
    config = SyntheticConfig(seed=42, n_trades=500, win_rate=0.60)
    trades = generate_trades(config)
    for t in trades:
        assert t.r_result != 0.0, f"Trade {t.trade_id} has r_result=0"


def test_no_trade_is_nan():
    """No trade should have NaN r_result."""
    config = SyntheticConfig(seed=42, n_trades=500)
    trades = generate_trades(config)
    for t in trades:
        assert not math.isnan(t.r_result)


# =========================================================================
# Edge cases
# =========================================================================

def test_single_trade():
    """n_trades=1 should work without errors."""
    config = SyntheticConfig(seed=42, n_trades=1)
    trades = generate_trades(config)
    assert len(trades) == 1
    assert isinstance(trades[0].r_result, float)


def test_zero_std_all_deterministic():
    """std=0 → all wins are exactly avg_win_R, all losses exactly -avg_loss_R."""
    config = SyntheticConfig(
        seed=42, n_trades=100, win_rate=0.50,
        avg_win_r=2.0, avg_loss_r=1.0,
        std_win_r=0.0, std_loss_r=0.0,
    )
    trades = generate_trades(config)

    wins = [t for t in trades if t.r_result > 0]
    losses = [t for t in trades if t.r_result < 0]

    for t in wins:
        assert t.r_result == pytest.approx(2.0)
    for t in losses:
        assert t.r_result == pytest.approx(-1.0)


def test_n_trades_not_divisible_by_trades_per_day():
    """n_trades not divisible by trades_per_day → correct unique days."""
    config = SyntheticConfig(seed=42, n_trades=17, trades_per_day=5)
    trades = generate_trades(config)
    unique_dates = {t.date for t in trades}
    # ceil(17 / 5) = 4 days
    assert len(unique_dates) == 4
    assert len(trades) == 17


# =========================================================================
# generate_trade_sequences
# =========================================================================

def test_generate_sequences_count():
    """n_sequences=N → N independent sequences returned."""
    config = SyntheticConfig(seed=42, n_trades=50)
    sequences = generate_trade_sequences(config, n_sequences=5)
    assert len(sequences) == 5
    for seq in sequences:
        assert len(seq) == 50


def test_sequences_are_different():
    """Each sequence in a batch should be different (derived seeds)."""
    config = SyntheticConfig(seed=42, n_trades=100)
    sequences = generate_trade_sequences(config, n_sequences=3)
    r0 = [t.r_result for t in sequences[0]]
    r1 = [t.r_result for t in sequences[1]]
    r2 = [t.r_result for t in sequences[2]]
    assert r0 != r1
    assert r1 != r2
    assert r0 != r2


def test_sequences_seed_none_random():
    """
    seed=None → each call to generate_trade_sequences produces
    different sequences (regression test for Codex WARNING #3).
    """
    config = SyntheticConfig(seed=None, n_trades=100)
    seq_a = generate_trade_sequences(config, n_sequences=2)
    seq_b = generate_trade_sequences(config, n_sequences=2)

    r_a0 = [t.r_result for t in seq_a[0]]
    r_b0 = [t.r_result for t in seq_b[0]]
    assert r_a0 != r_b0, "seed=None should produce different sequences each call"


def test_n_sequences_zero_raises():
    """n_sequences=0 should raise ValueError."""
    config = SyntheticConfig(seed=42, n_trades=50)
    with pytest.raises(ValueError, match="n_sequences"):
        generate_trade_sequences(config, n_sequences=0)


def test_n_sequences_negative_raises():
    """n_sequences < 0 should raise ValueError."""
    config = SyntheticConfig(seed=42, n_trades=50)
    with pytest.raises(ValueError, match="n_sequences"):
        generate_trade_sequences(config, n_sequences=-1)


# =========================================================================
# Warnings for unimplemented features (Codex WARNING #2)
# =========================================================================

def test_streak_factor_warns():
    """streak_factor > 0 should emit a UserWarning."""
    config = SyntheticConfig(seed=42, n_trades=10, streak_factor=0.3)
    with pytest.warns(UserWarning, match="streak_factor"):
        generate_trades(config)


def test_distribution_warns():
    """distribution != 'lognormal' is rejected at construction (not just warned)."""
    # This test verifies that unsupported values are caught early.
    # Since Literal['lognormal'] is enforced at the type level,
    # runtime rejection happens via the type system + validation.
    # We test that the valid value works fine.
    config = SyntheticConfig(seed=42, n_trades=10, distribution="lognormal")
    trades = generate_trades(config)
    assert len(trades) == 10


# =========================================================================
# Validation: SyntheticConfig
# =========================================================================

def test_config_rejects_invalid_win_rate():
    """SyntheticConfig must reject win_rate outside (0, 1)."""
    with pytest.raises(ValueError, match="win_rate"):
        SyntheticConfig(win_rate=0.0)
    with pytest.raises(ValueError, match="win_rate"):
        SyntheticConfig(win_rate=1.0)
    with pytest.raises(ValueError, match="win_rate"):
        SyntheticConfig(win_rate=-0.1)


def test_config_rejects_negative_r_params():
    """avg_win_R and avg_loss_R must be positive."""
    with pytest.raises(ValueError, match="avg_win_r"):
        SyntheticConfig(avg_win_r=0)
    with pytest.raises(ValueError, match="avg_loss_r"):
        SyntheticConfig(avg_loss_r=0)
    with pytest.raises(ValueError, match="avg_loss_r"):
        SyntheticConfig(avg_loss_r=-1)


def test_config_rejects_negative_std():
    """std_win_R and std_loss_R cannot be negative."""
    with pytest.raises(ValueError, match="std_win_r"):
        SyntheticConfig(std_win_r=-0.1)
    with pytest.raises(ValueError, match="std_loss_r"):
        SyntheticConfig(std_loss_r=-0.5)


def test_config_rejects_invalid_n_trades():
    """n_trades must be positive."""
    with pytest.raises(ValueError, match="n_trades"):
        SyntheticConfig(n_trades=0)
    with pytest.raises(ValueError, match="n_trades"):
        SyntheticConfig(n_trades=-5)


def test_config_rejects_invalid_trades_per_day():
    """trades_per_day must be positive."""
    with pytest.raises(ValueError, match="trades_per_day"):
        SyntheticConfig(trades_per_day=0)
    with pytest.raises(ValueError, match="trades_per_day"):
        SyntheticConfig(trades_per_day=-1)


def test_config_rejects_nan_win_rate():
    """NaN win_rate should be rejected."""
    with pytest.raises(ValueError, match="win_rate"):
        SyntheticConfig(win_rate=float("nan"))


def test_config_rejects_inf_win_rate():
    """inf win_rate should be rejected."""
    with pytest.raises(ValueError, match="win_rate"):
        SyntheticConfig(win_rate=float("inf"))


def test_config_rejects_nan_avg_win_r():
    """NaN avg_win_r should be rejected (regression for WARNING #4)."""
    with pytest.raises(ValueError, match="avg_win_r"):
        SyntheticConfig(avg_win_r=float("nan"))


# =========================================================================
# Validation: FundedAccountRules
# =========================================================================

def test_far_rejects_max_trades_zero():
    """max_trades=0 should be rejected (regression for WARNING #4)."""
    with pytest.raises(ValueError, match="max_trades"):
        FundedAccountRules(
            initial_balance=100_000,
            profit_target_pct=0.10,
            max_drawdown_pct=0.10,
            daily_loss_limit_pct=0.05,
            max_trades=0,
        )


def test_far_rejects_max_trades_negative():
    """max_trades=-5 should be rejected (regression for WARNING #4)."""
    with pytest.raises(ValueError, match="max_trades"):
        FundedAccountRules(
            initial_balance=100_000,
            profit_target_pct=0.10,
            max_drawdown_pct=0.10,
            daily_loss_limit_pct=0.05,
            max_trades=-5,
        )


def test_far_rejects_nan_initial_balance():
    """NaN initial_balance should be rejected."""
    with pytest.raises(ValueError, match="initial_balance"):
        FundedAccountRules(
            initial_balance=float("nan"),
            profit_target_pct=0.10,
            max_drawdown_pct=0.10,
            daily_loss_limit_pct=0.05,
        )


def test_far_rejects_nan_risk_per_trade():
    """NaN risk_per_trade should be rejected."""
    with pytest.raises(ValueError, match="risk_per_trade"):
        FundedAccountRules(
            initial_balance=100_000,
            profit_target_pct=0.10,
            max_drawdown_pct=0.10,
            daily_loss_limit_pct=0.05,
            risk_per_trade=float("nan"),
        )


def test_far_accepts_valid_defaults():
    """Valid FundedAccountRules should construct without error."""
    far = FundedAccountRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.05,
    )
    assert far.initial_balance == 100_000
    assert far.risk_per_trade == 0.01  # default
    assert far.max_trades is None


def test_far_includes_risk_per_trade():
    """risk_per_trade is present (regression for Codex WARNING #1)."""
    far = FundedAccountRules(
        initial_balance=50_000,
        profit_target_pct=0.08,
        max_drawdown_pct=0.12,
        daily_loss_limit_pct=0.04,
        risk_per_trade=0.025,
    )
    assert far.risk_per_trade == 0.025


# =========================================================================
# W4 regression: integer validation
# =========================================================================

def test_config_rejects_float_n_trades():
    """n_trades must be a strict int, not float."""
    with pytest.raises(ValueError, match="n_trades"):
        SyntheticConfig(n_trades=3.5)


def test_config_rejects_bool_n_trades():
    """n_trades must not be bool (bool is subclass of int)."""
    with pytest.raises(ValueError, match="n_trades"):
        SyntheticConfig(n_trades=True)


def test_config_rejects_float_trades_per_day():
    """trades_per_day must be a strict int."""
    with pytest.raises(ValueError, match="trades_per_day"):
        SyntheticConfig(trades_per_day=2.0)


def test_far_rejects_float_max_trades():
    """max_trades must be a strict int or None."""
    with pytest.raises(ValueError, match="max_trades"):
        FundedAccountRules(
            initial_balance=100_000,
            profit_target_pct=0.10,
            max_drawdown_pct=0.10,
            daily_loss_limit_pct=0.05,
            max_trades=10.5,
        )


def test_far_rejects_bool_max_trades():
    """max_trades must not be bool."""
    with pytest.raises(ValueError, match="max_trades"):
        FundedAccountRules(
            initial_balance=100_000,
            profit_target_pct=0.10,
            max_drawdown_pct=0.10,
            daily_loss_limit_pct=0.05,
            max_trades=True,
        )


# =========================================================================
# W5 regression: streak_factor validation
# =========================================================================

def test_config_rejects_nan_streak_factor():
    """NaN streak_factor should be rejected."""
    with pytest.raises(ValueError, match="streak_factor"):
        SyntheticConfig(streak_factor=float("nan"))


def test_config_rejects_inf_streak_factor():
    """inf streak_factor should be rejected."""
    with pytest.raises(ValueError, match="streak_factor"):
        SyntheticConfig(streak_factor=float("inf"))


def test_config_rejects_negative_streak_factor():
    """streak_factor must be in [0, 1]."""
    with pytest.raises(ValueError, match="streak_factor"):
        SyntheticConfig(streak_factor=-0.5)


def test_config_rejects_streak_factor_above_one():
    """streak_factor must be in [0, 1]."""
    with pytest.raises(ValueError, match="streak_factor"):
        SyntheticConfig(streak_factor=1.5)


def test_config_accepts_streak_factor_zero():
    """streak_factor=0 is valid (IID)."""
    config = SyntheticConfig(seed=42, n_trades=10, streak_factor=0.0)
    assert config.streak_factor == 0.0


# =========================================================================
# C1 regression: distribution runtime validation
# =========================================================================

def test_far_rejects_invalid_daily_loss_base():
    """daily_loss_base must be 'initial' or 'eod'."""
    with pytest.raises(ValueError, match="daily_loss_base"):
        FundedAccountRules(
            initial_balance=100_000,
            profit_target_pct=0.10,
            max_drawdown_pct=0.10,
            daily_loss_limit_pct=0.05,
            daily_loss_base="bogus",
        )


def test_far_rejects_invalid_drawdown_mode():
    """drawdown_mode must be 'static' or 'trailing'."""
    with pytest.raises(ValueError, match="drawdown_mode"):
        FundedAccountRules(
            initial_balance=100_000,
            profit_target_pct=0.10,
            max_drawdown_pct=0.10,
            daily_loss_limit_pct=0.05,
            drawdown_mode="bogus",
        )


def test_far_accepts_valid_daily_loss_base():
    """Valid daily_loss_base values should work."""
    far = FundedAccountRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.05,
        daily_loss_base="eod",
    )
    assert far.daily_loss_base == "eod"


def test_far_accepts_valid_drawdown_mode():
    """Valid drawdown_mode values should work."""
    far = FundedAccountRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.05,
        drawdown_mode="trailing",
    )
    assert far.drawdown_mode == "trailing"


def test_config_rejects_invalid_distribution():
    """distribution must be 'lognormal', reject anything else at runtime."""
    with pytest.raises(ValueError, match="distribution"):
        SyntheticConfig(distribution="normal")

    with pytest.raises(ValueError, match="distribution"):
        SyntheticConfig(distribution="student_t")

    with pytest.raises(ValueError, match="distribution"):
        SyntheticConfig(distribution="bogus")


# =========================================================================
# C2 regression: n_sequences strict int validation
# =========================================================================

def test_n_sequences_rejects_float():
    """n_sequences must be a strict int, not float."""
    config = SyntheticConfig(seed=42, n_trades=10)
    with pytest.raises(ValueError, match="n_sequences"):
        generate_trade_sequences(config, n_sequences=1.5)


def test_n_sequences_rejects_bool():
    """n_sequences must not be bool."""
    config = SyntheticConfig(seed=42, n_trades=10)
    with pytest.raises(ValueError, match="n_sequences"):
        generate_trade_sequences(config, n_sequences=True)


# =========================================================================
# S2 regression: seed=None produces unique batch IDs
# =========================================================================

def test_seed_none_unique_trade_ids_across_calls():
    """Different generate_trades(seed=None) calls produce non-overlapping IDs."""
    trades1 = generate_trades(SyntheticConfig(seed=None, n_trades=20))
    trades2 = generate_trades(SyntheticConfig(seed=None, n_trades=20))

    ids1 = {t.trade_id for t in trades1}
    ids2 = {t.trade_id for t in trades2}

    assert ids1.isdisjoint(ids2), (
        f"seed=None should produce unique IDs across calls, "
        f"but found overlap"
    )


# =========================================================================
# Numeric safety: FundedAccountRules config-level guards (Phase 4)
# =========================================================================

def test_far_rejects_underflow_dollar_risk():
    """initial_balance=5e-324 × risk=0.01 → dollar_risk underflows to 0."""
    with pytest.raises(ValueError, match="dollar_risk"):
        FundedAccountRules(
            initial_balance=5e-324,
            profit_target_pct=0.10,
            max_drawdown_pct=0.10,
            daily_loss_limit_pct=0.05,
            risk_per_trade=0.01,
        )


def test_far_rejects_one_plus_pct_rounding():
    """profit_target_pct below float64 epsilon → target == balance → reject."""
    with pytest.raises(ValueError, match="profit target"):
        FundedAccountRules(
            initial_balance=1e200,
            profit_target_pct=1e-20,
            max_drawdown_pct=0.10,
            daily_loss_limit_pct=0.05,
            risk_per_trade=0.01,
        )


def test_far_rejects_profit_target_overflow():
    """initial_balance=1e308 × 1.9 → target overflows to inf → reject."""
    with pytest.raises(ValueError, match="profit target"):
        FundedAccountRules(
            initial_balance=1e308,
            profit_target_pct=0.9,
            max_drawdown_pct=0.10,
            daily_loss_limit_pct=0.05,
            risk_per_trade=0.01,
        )


def test_far_rejects_absorbing_max_drawdown_pct():
    """max_drawdown_pct=5e-324 → balance*pct absorbs into balance, so a zero-loss
    trade would be flagged as a violation (threshold rounds back to balance)."""
    with pytest.raises(ValueError, match="max_drawdown_pct"):
        FundedAccountRules(
            initial_balance=100_000.0,
            profit_target_pct=0.10,
            max_drawdown_pct=5e-324,
            daily_loss_limit_pct=0.05,
            risk_per_trade=0.01,
        )


def test_far_rejects_absorbing_daily_loss_limit_pct():
    """daily_loss_limit_pct=5e-324 → balance*pct absorbs into balance."""
    with pytest.raises(ValueError, match="daily_loss_limit_pct"):
        FundedAccountRules(
            initial_balance=100_000.0,
            profit_target_pct=0.10,
            max_drawdown_pct=0.10,
            daily_loss_limit_pct=5e-324,
            risk_per_trade=0.01,
        )

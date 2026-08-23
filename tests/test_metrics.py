"""
Phase 2 tests: core statistical metrics.

Tests are designed to be self-contained with known sequences —
no reliance on synthetic data generation or randomness.
"""

import math

import numpy as np
import pytest

from src.metrics import (
    Metrics,
    avg_loss_r,
    avg_win_r,
    compute_metrics,
    expectancy_r,
    kurtosis,
    losing_streak_distribution,
    max_drawdown_r,
    max_losing_streak,
    n_trades,
    skewness,
    std_r,
    win_rate,
)


# =========================================================================
# n_trades
# =========================================================================

def test_n_trades_basic():
    assert n_trades([1.0, -0.5, 2.0]) == 3
    assert n_trades([]) == 0
    assert n_trades([0.5]) == 1


# =========================================================================
# win_rate
# =========================================================================

def test_win_rate_empty():
    assert win_rate([]) == 0.0


def test_win_rate_all_wins():
    assert win_rate([1.0, 2.0, 0.5]) == 1.0


def test_win_rate_all_losses():
    assert win_rate([-1.0, -0.5, -2.0]) == 0.0


def test_win_rate_mixed():
    assert win_rate([2.0, -1.0, 3.0, -0.5]) == pytest.approx(0.5)


def test_win_rate_zero_not_counted_as_win():
    """r_result == 0 should not count as a win."""
    assert win_rate([0.0, -1.0, 0.0]) == 0.0


# =========================================================================
# avg_win_r / avg_loss_r
# =========================================================================

def test_avg_win_r_empty():
    assert avg_win_r([]) == 0.0


def test_avg_win_r_no_wins():
    assert avg_win_r([-1.0, -2.0, -0.5]) == 0.0


def test_avg_win_r_basic():
    assert avg_win_r([2.0, -1.0, 3.0, -0.5]) == pytest.approx(2.5)


def test_avg_loss_r_empty():
    assert avg_loss_r([]) == 0.0


def test_avg_loss_r_no_losses():
    assert avg_loss_r([1.0, 2.0]) == 0.0


def test_avg_loss_r_basic():
    # Losses: -1.0, -0.5 → |values| = 1.0, 0.5 → mean = 0.75
    assert avg_loss_r([2.0, -1.0, 3.0, -0.5]) == pytest.approx(0.75)


def test_avg_loss_r_returns_positive():
    """avg_loss_r returns the mean of absolute values, always non-negative."""
    assert avg_loss_r([-3.0, -1.0]) == pytest.approx(2.0)


# =========================================================================
# expectancy_r
# =========================================================================

def test_expectancy_empty():
    assert expectancy_r([]) == 0.0


def test_expectancy_basic():
    assert expectancy_r([2.0, -1.0]) == pytest.approx(0.5)


def test_expectancy_breakeven():
    assert expectancy_r([1.0, -1.0, 2.0, -2.0]) == pytest.approx(0.0)


def test_expectancy_negative():
    assert expectancy_r([1.0, -2.0, -2.0]) == pytest.approx(-1.0)


def test_expectancy_matches_formula():
    """E = wr * aw - (1-wr) * al must match arithmetic mean on finite data."""
    r = [2.0, -1.0, 3.0, -0.5, -1.0, 4.0, -2.0, 1.5]
    direct = sum(r) / len(r)
    wr = win_rate(r)
    aw = avg_win_r(r)
    al = avg_loss_r(r)
    formula = wr * aw - (1 - wr) * al
    assert direct == pytest.approx(formula)


# =========================================================================
# std_r
# =========================================================================

def test_std_empty():
    assert math.isnan(std_r([]))


def test_std_single():
    assert math.isnan(std_r([5.0]))


def test_std_constant():
    assert std_r([2.0, 2.0, 2.0]) == pytest.approx(0.0)


def test_std_basic():
    # np.std([2, 4, 4, 4, 5, 5, 7, 9], ddof=1) = 2.138...
    # but let's use a simple known sequence
    result = std_r([0.0, 2.0])
    assert result == pytest.approx(np.sqrt(2), rel=1e-9)  # sqrt(((0-1)² + (2-1)²) / 1)


def test_std_uses_ddof1():
    """Verify sample std (ddof=1), not population."""
    r = [1.0, 2.0, 3.0]
    expected_sample = np.std(r, ddof=1)
    expected_pop = np.std(r, ddof=0)
    assert std_r(r) == pytest.approx(expected_sample)
    assert std_r(r) != pytest.approx(expected_pop)


# =========================================================================
# skewness
# =========================================================================

def test_skew_empty():
    assert math.isnan(skewness([]))


def test_skew_too_few():
    assert math.isnan(skewness([1.0]))
    assert math.isnan(skewness([1.0, 2.0]))


def test_skew_symmetric():
    """Symmetric distribution → skew ≈ 0."""
    r = [-3.0, -1.0, 0.0, 1.0, 3.0]
    assert skewness(r) == pytest.approx(0.0, abs=0.01)


def test_skew_positive():
    """Right-skewed: big wins, small losses."""
    r = [-0.5, -0.3, -1.0, -0.2, 5.0, 8.0]
    assert skewness(r) > 0.5


def test_skew_negative():
    """Left-skewed: small wins, big losses."""
    r = [0.5, 0.3, 1.0, 0.2, -5.0, -8.0]
    assert skewness(r) < -0.5


def test_skew_constant_is_nan():
    """Constant sequence → skewness is undefined → NaN."""
    assert math.isnan(skewness([2.0, 2.0, 2.0, 2.0]))


# =========================================================================
# kurtosis
# =========================================================================

def test_kurtosis_empty():
    assert math.isnan(kurtosis([]))


def test_kurtosis_too_few():
    assert math.isnan(kurtosis([1.0]))
    assert math.isnan(kurtosis([1.0, 2.0]))
    assert math.isnan(kurtosis([1.0, 2.0, 3.0]))


def test_kurtosis_normal_like():
    """Excess kurtosis of normal-like data ≈ 0."""
    rng = np.random.default_rng(42)
    data = rng.normal(0, 1, 10000).tolist()
    assert kurtosis(data) == pytest.approx(0.0, abs=0.1)


def test_kurtosis_fat_tails():
    """Fat-tailed distribution → positive excess kurtosis."""
    # Cauchy has very fat tails (infinite kurtosis, finite sample is huge)
    rng = np.random.default_rng(42)
    data = rng.standard_cauchy(10000).tolist()
    assert kurtosis(data) > 2.0  # Cauchy has very high kurtosis


def test_kurtosis_constant_is_nan():
    """Constant sequence → kurtosis is undefined → NaN."""
    r = [2.0, 2.0, 2.0, 2.0, 2.0]
    assert math.isnan(kurtosis(r))


def test_expectancy_formula_with_zero_r():
    """
    Regression: expectancy formula with zero-R trades.

    For [1, -1, 0]: mean = 0, but wr*aw - (1-wr)*al = -1/3.
    The correct formula uses loss_rate, not (1 - win_rate).
    """
    r = [1.0, -1.0, 0.0]
    direct = expectancy_r(r)
    assert direct == pytest.approx(0.0)

    # Wrong: (1 - win_rate) ignores breakevens
    wr = win_rate(r)
    aw = avg_win_r(r)
    al = avg_loss_r(r)
    wrong = wr * aw - (1 - wr) * al
    assert wrong != pytest.approx(0.0)  # This would give -1/3

    # Correct: use explicit loss_rate
    loss_rate = sum(1 for x in r if x < 0) / len(r)
    correct = wr * aw - loss_rate * al
    assert correct == pytest.approx(direct)


def test_compute_metrics_rejects_nan():
    """NaN in r_results should raise ValueError."""
    with pytest.raises(ValueError, match="finite"):
        compute_metrics([1.0, float("nan"), -1.0])


def test_compute_metrics_rejects_inf():
    """inf in r_results should raise ValueError."""
    with pytest.raises(ValueError, match="finite"):
        compute_metrics([1.0, float("inf"), -1.0])

    with pytest.raises(ValueError, match="finite"):
        compute_metrics([1.0, float("-inf"), -1.0])


@pytest.mark.parametrize("invalid", [True, "1.0", 1 + 2j, None])
def test_compute_metrics_rejects_non_real_values(invalid):
    with pytest.raises(ValueError, match="finite real number"):
        compute_metrics([1.0, invalid, -1.0])


def test_compute_metrics_normalizes_unrepresentable_real_to_value_error():
    with pytest.raises(ValueError, match="float64-compatible"):
        compute_metrics([10**1000])


# =========================================================================
# max_drawdown_r
# =========================================================================

def test_max_drawdown_empty():
    assert max_drawdown_r([]) == 0.0


def test_max_drawdown_all_wins():
    assert max_drawdown_r([1.0, 2.0, 3.0]) == 0.0


def test_max_drawdown_simple():
    """Peak at t=1 (cumulative=3), trough at t=3 (cumulative=1) → DD=2R."""
    r = [2.0, 1.0, -2.0, -1.0, 3.0]
    # cumsum: [2, 3, 1, 0, 3]
    # peak:   [2, 3, 3, 3, 3]
    # DD:     [0, 0, 2, 3, 0] → max = 3
    assert max_drawdown_r(r) == pytest.approx(3.0)


def test_max_drawdown_multiple_peaks():
    """Drawdown from first peak should be captured even if later peaks are higher."""
    r = [3.0, -2.0, -2.0, 5.0, -1.0]
    # cumsum: [3, 1, -1, 4, 3]
    # peak:   [3, 3,  3, 4, 4]
    # DD:     [0, 2,  4, 0, 1] → max = 4
    assert max_drawdown_r(r) == pytest.approx(4.0)


def test_max_drawdown_single_trade():
    assert max_drawdown_r([-1.0]) == pytest.approx(1.0)


def test_max_drawdown_recovery():
    """Drawdown ends when cumulative exceeds previous peak."""
    r = [5.0, -3.0, 5.0]
    # cumsum: [5, 2, 7]
    # peak:   [5, 5, 7]
    # DD:     [0, 3, 0] → max = 3
    assert max_drawdown_r(r) == pytest.approx(3.0)


# =========================================================================
# max_losing_streak
# =========================================================================

def test_max_losing_streak_empty():
    assert max_losing_streak([]) == 0


def test_max_losing_streak_no_losses():
    assert max_losing_streak([1.0, 2.0, 0.5]) == 0


def test_max_losing_streak_all_losses():
    assert max_losing_streak([-1.0, -2.0, -0.5]) == 3


def test_max_losing_streak_basic():
    assert max_losing_streak([1.0, -1.0, -2.0, 3.0, -0.5, -1.0, -2.0, 4.0]) == 3


def test_max_losing_streak_at_end():
    """Streak at end of sequence should be counted."""
    assert max_losing_streak([1.0, -1.0, -2.0, -3.0]) == 3


def test_max_losing_streak_zero_not_loss():
    """r_result=0 should break a losing streak."""
    assert max_losing_streak([-1.0, 0.0, -2.0]) == 1


# =========================================================================
# losing_streak_distribution
# =========================================================================

def test_losing_streak_distribution_empty():
    assert losing_streak_distribution([]) == {}


def test_losing_streak_distribution_no_losses():
    assert losing_streak_distribution([1.0, 2.0]) == {}


def test_losing_streak_distribution_basic():
    """Sequence with streaks of length 1, 2, 1, 3."""
    r = [1.0, -1.0, 1.0, -1.0, -2.0, 3.0, -0.5, 4.0, -1.0, -2.0, -3.0]
    dist = losing_streak_distribution(r)
    assert dist == {1: 2, 2: 1, 3: 1}


def test_losing_streak_distribution_at_end():
    """Streak at end of sequence appears in distribution."""
    r = [1.0, -1.0, -2.0]
    dist = losing_streak_distribution(r)
    assert dist == {2: 1}


def test_losing_streak_distribution_single_streak():
    r = [-1.0, -2.0, -3.0]
    dist = losing_streak_distribution(r)
    assert dist == {3: 1}


# =========================================================================
# compute_metrics — integration
# =========================================================================

def test_compute_metrics_returns_metrics_object():
    m = compute_metrics([2.0, -1.0])
    assert isinstance(m, Metrics)
    assert m.n_trades == 2


def test_compute_metrics_empty():
    m = compute_metrics([])
    assert m.n_trades == 0
    assert m.win_rate == 0.0
    assert m.avg_win_r == 0.0
    assert m.avg_loss_r == 0.0
    assert m.expectancy_r == 0.0
    assert math.isnan(m.std_r)
    assert math.isnan(m.skewness)
    assert math.isnan(m.kurtosis)
    assert m.max_drawdown_r == 0.0
    assert m.max_losing_streak == 0
    assert m.losing_streak_distribution == {}


def test_compute_metrics_all_wins():
    m = compute_metrics([1.0, 2.0, 3.0])
    assert m.win_rate == 1.0
    assert m.avg_win_r == pytest.approx(2.0)
    assert m.avg_loss_r == 0.0
    assert m.max_losing_streak == 0
    assert m.losing_streak_distribution == {}


def test_compute_metrics_all_losses():
    m = compute_metrics([-1.0, -2.0, -0.5])
    assert m.win_rate == 0.0
    assert m.avg_win_r == 0.0
    assert m.avg_loss_r == pytest.approx((1 + 2 + 0.5) / 3)
    assert m.max_losing_streak == 3


def test_compute_metrics_single_trade():
    m = compute_metrics([1.5])
    assert m.n_trades == 1
    assert m.win_rate == 1.0
    assert math.isnan(m.std_r)       # undefined with 1 trade
    assert math.isnan(m.skewness)   # undefined with 1 trade
    assert math.isnan(m.kurtosis)   # undefined with 1 trade


def test_compute_metrics_frozen():
    """Metrics should be immutable."""
    m = compute_metrics([1.0, -1.0])
    with pytest.raises(Exception):
        m.win_rate = 0.5  # type: ignore


# =========================================================================
# Cross-verification with synthetic data
# =========================================================================

def test_metrics_on_synthetic_data():
    """Sanity check: metrics computed on synthetic data are internally consistent."""
    from src.synthetic import generate_trades
    from src.types import SyntheticConfig

    config = SyntheticConfig(
        seed=42, n_trades=5000, win_rate=0.50,
        avg_win_r=2.0, avg_loss_r=1.0,
        std_win_r=0.5, std_loss_r=0.3,
    )
    trades = generate_trades(config)
    r = [t.r_result for t in trades]
    m = compute_metrics(r)

    # Win rate should be close to 0.50
    assert 0.47 <= m.win_rate <= 0.53

    # avg_win_r should be close to 2.0
    assert 1.90 <= m.avg_win_r <= 2.10

    # avg_loss_r should be close to 1.0
    assert 0.90 <= m.avg_loss_r <= 1.10

    # formula consistency
    formula = m.win_rate * m.avg_win_r - (1 - m.win_rate) * m.avg_loss_r
    assert m.expectancy_r == pytest.approx(formula)

    # Positive expectancy expected with these params
    assert m.expectancy_r > 0.25  # theoretical: 0.5*2 - 0.5*1 = 0.5

    # max_drawdown_r should be positive with 5000 trades
    assert m.max_drawdown_r > 0

    # max_losing_streak should be > 0
    assert m.max_losing_streak > 0


# =========================================================================
# Derived-overflow rejection (regression: extreme finite inputs)
# =========================================================================


def test_compute_metrics_rejects_overflowing_extreme_values():
    with pytest.raises(ValueError, match="overflow"):
        compute_metrics([1e308, 1e308])


def test_compute_metrics_rejects_infinite_std_from_extreme_spread():
    with pytest.raises(ValueError, match="overflow"):
        compute_metrics([1e308, -1e308])


def test_compute_metrics_rejects_unexpected_nan_from_overflow():
    # n=3 non-constant: skewness is statistically defined, so a NaN result
    # means intermediate moments overflowed.
    with pytest.raises(ValueError, match="unexpectedly NaN"):
        compute_metrics([1e150, -1e150, 2e150])


def test_compute_metrics_rejects_nan_kurtosis_from_overflow():
    with pytest.raises(ValueError, match="unexpectedly NaN"):
        compute_metrics([1e150, -1e150, 2e150, 0.5e150])


def test_compute_metrics_rejects_std_underflow_for_nonconstant_values():
    with pytest.raises(ValueError, match="underflowed or overflowed"):
        compute_metrics([1e-162, -1e-162, 2e-162, -0.5e-162])


def test_compute_metrics_allows_truly_constant_tiny_values():
    m = compute_metrics([1e-200, 1e-200, 1e-200, 1e-200])
    assert m.std_r == 0.0
    assert math.isnan(m.skewness)
    assert math.isnan(m.kurtosis)


def test_compute_metrics_large_but_safe_values_still_work():
    m = compute_metrics([1e50, -1e50, 2e50, -0.5e50])
    assert math.isfinite(m.expectancy_r)
    assert math.isfinite(m.std_r)
    assert math.isfinite(m.skewness)
    assert math.isfinite(m.kurtosis)

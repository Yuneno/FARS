"""
Core statistical metrics for trade sequences.

All functions operate on a sequence of R-multiple outcomes (list[float])
and are stateless. The Metrics dataclass bundles all metrics into a
single immutable result.

IMPORTANT: Individual metric functions (win_rate, expectancy_r, std_r,
etc.) assume their inputs are finite real numbers. For validated input,
use compute_metrics() which rejects NaN/inf before computing anything.

FARS_SPEC §6 defines the required metrics:
  - number_of_trades
  - win_rate
  - average_win_R
  - average_loss_R
  - expectancy_R
  - standard_deviation_R
  - skewness
  - kurtosis
  - historical_max_drawdown_R
  - maximum_losing_streak
  - losing_streak_distribution

ASSUMPTION: These metrics are descriptive statistics on a fixed,
    complete trade sequence. No forward-looking or distributional
    assumptions are made beyond the empirical data.
JUSTIFICATION: v0.1 needs baseline statistical characterization
    before building simulations. All metrics are computed from
    observed R outcomes only.
"""

from dataclasses import dataclass, field
import math

import numpy as np
from scipy import stats as sp_stats


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Metrics:
    """Immutable bundle of all core statistical metrics for a trade sequence.

    All fields are immutable except losing_streak_distribution, which is
    a plain dict (intentionally mutable for downstream analysis)."""

    n_trades: int
    win_rate: float
    avg_win_r: float
    avg_loss_r: float
    expectancy_r: float
    std_r: float
    skewness: float
    kurtosis: float
    max_drawdown_r: float
    max_losing_streak: int
    losing_streak_distribution: dict[int, int] = field(default_factory=dict)

    def __post_init__(self):
        if self.n_trades < 0:
            raise ValueError(f"n_trades must be >= 0, got {self.n_trades}")
        if not 0.0 <= self.win_rate <= 1.0:
            raise ValueError(f"win_rate must be in [0, 1], got {self.win_rate}")


# ---------------------------------------------------------------------------
# Individual metric functions
# ---------------------------------------------------------------------------


def n_trades(r_results: list[float]) -> int:
    """Number of trades in the sequence."""
    return len(r_results)


def win_rate(r_results: list[float]) -> float:
    """
    Fraction of trades with positive R outcome.

    Returns 0.0 for empty sequences.
    """
    if len(r_results) == 0:
        return 0.0
    return sum(1 for r in r_results if r > 0) / len(r_results)


def avg_win_r(r_results: list[float]) -> float:
    """
    Mean R outcome of winning trades (r_result > 0).

    Returns 0.0 if there are no winning trades.
    """
    wins = [r for r in r_results if r > 0]
    if len(wins) == 0:
        return 0.0
    return float(np.mean(wins))


def avg_loss_r(r_results: list[float]) -> float:
    """
    Mean absolute R outcome of losing trades (r_result < 0).

    Returns the mean of |r_result| for negative trades, so the
    returned value is always non-negative. Returns 0.0 if there
    are no losing trades.
    """
    losses = [abs(r) for r in r_results if r < 0]
    if len(losses) == 0:
        return 0.0
    return float(np.mean(losses))


def expectancy_r(r_results: list[float]) -> float:
    """
    Expected R per trade (arithmetic mean of all r_results).

    E[R] = (1/N) * Σ r_i

    This is mathematically equivalent to:
        win_rate * avg_win_R - loss_rate * avg_loss_R

    where loss_rate = P(r < 0). Note that (1 - win_rate) is NOT the
    correct weight when zero-R trades exist — use loss_rate explicitly.
    """
    if len(r_results) == 0:
        return 0.0
    return float(np.mean(r_results))


def std_r(r_results: list[float]) -> float:
    """
    Sample standard deviation of R outcomes (ddof=1).

    Returns math.nan for sequences with fewer than 2 trades
    (sample std is mathematically undefined for n < 2).
    """
    if len(r_results) < 2:
        return math.nan
    return float(np.std(r_results, ddof=1))


def skewness(r_results: list[float]) -> float:
    """
    Sample skewness of R outcomes (bias-corrected).

    Positive skew → right tail (big wins more extreme than big losses).
    Negative skew → left tail (big losses more extreme than big wins).

    Returns math.nan for sequences with fewer than 3 trades or zero
    variance (skewness is mathematically undefined for constant data).
    """
    if len(r_results) < 3:
        return math.nan
    # Constant data → variance = 0 → skew undefined → return NaN
    if np.std(r_results, ddof=1) == 0.0:
        return math.nan
    return float(sp_stats.skew(r_results, bias=False))


def kurtosis(r_results: list[float]) -> float:
    """
    Excess kurtosis of R outcomes (Fisher definition, normal = 0).

    Positive → fatter tails than normal (more extreme outcomes).
    Negative → thinner tails than normal.

    Returns math.nan for sequences with fewer than 4 trades or zero
    variance (kurtosis is mathematically undefined for constant data).
    """
    if len(r_results) < 4:
        return math.nan
    # Constant data → variance = 0 → kurtosis undefined → return NaN
    if np.std(r_results, ddof=1) == 0.0:
        return math.nan
    return float(sp_stats.kurtosis(r_results, fisher=True, bias=False))


def max_drawdown_r(r_results: list[float]) -> float:
    """
    Maximum peak-to-trough decline in cumulative R.

    Tracks the cumulative sum of r_results and records the largest
    decline from a running peak. This is a unitless drawdown measured
    in R multiples — it represents how many R the strategy gave back
    from its highest cumulative R level.

    Returns 0.0 for empty sequences (no drawdown possible).
    For all-winning sequences, drawdown is 0.0.
    """
    if len(r_results) == 0:
        return 0.0

    # Prepend initial cumulative = 0 (account state before any trades)
    cumulative = np.cumsum(r_results)
    cumulative = np.insert(cumulative, 0, 0.0)
    running_peak = np.maximum.accumulate(cumulative)
    drawdowns = running_peak - cumulative
    return float(np.max(drawdowns))


def max_losing_streak(r_results: list[float]) -> int:
    """
    Length of the longest consecutive sequence of losing trades (r < 0).
    """
    current = 0
    best = 0
    for r in r_results:
        if r < 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def losing_streak_distribution(r_results: list[float]) -> dict[int, int]:
    """
    Frequency distribution of losing streak lengths.

    Returns a dict mapping streak length → count of occurrences.
    Only losing streaks of length >= 1 are included.
    """
    dist: dict[int, int] = {}
    current = 0

    for r in r_results:
        if r < 0:
            current += 1
        else:
            if current > 0:
                dist[current] = dist.get(current, 0) + 1
                current = 0

    # Don't forget a streak at the end of the sequence
    if current > 0:
        dist[current] = dist.get(current, 0) + 1

    return dist


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------


def compute_metrics(
    r_results: list[float],
) -> Metrics:
    """
    Compute all core statistical metrics for a trade sequence.

    Accepts a list of R-multiple outcomes (r_result from Trade objects).

    Raises ValueError if any r_result is NaN or infinite.
    All input values must be finite real numbers.

    Returns an immutable Metrics dataclass with all values populated.
    All metrics are computed from the empirical data only — no
    distributional assumptions are applied.

    Edge cases:
      - Empty list: all rates/means are 0, n_trades = 0
      - All wins: avg_loss_r = 0, max_losing_streak = 0
      - All losses: win_rate = 0, avg_win_r = 0
      - Single trade: std = 0, skewness = NaN, kurtosis = NaN
      - Constant values: std = 0, skewness = NaN, kurtosis = NaN
    """
    for i, r in enumerate(r_results):
        if not math.isfinite(r):
            raise ValueError(
                f"r_results[{i}] must be a finite real number, got {r!r}"
            )
    return Metrics(
        n_trades=n_trades(r_results),
        win_rate=win_rate(r_results),
        avg_win_r=avg_win_r(r_results),
        avg_loss_r=avg_loss_r(r_results),
        expectancy_r=expectancy_r(r_results),
        std_r=std_r(r_results),
        skewness=skewness(r_results),
        kurtosis=kurtosis(r_results),
        max_drawdown_r=max_drawdown_r(r_results),
        max_losing_streak=max_losing_streak(r_results),
        losing_streak_distribution=losing_streak_distribution(r_results),
    )

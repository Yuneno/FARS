"""
Monte Carlo simulation engine (Phase 5).

Core of FARS v0.1 (FARS_SPEC §7). Runs many independent funded-account
simulations for ONE fixed risk_per_trade (embedded in FundedAccountRules)
and aggregates the outcomes into pass/fail probabilities, drawdown
quantiles, trades-to-pass statistics, and confidence intervals.

Phase 6 (optimization) will call run_monte_carlo() once per candidate
risk level. Phase 5 deliberately scopes a single risk level per call.

Input modes (exactly one must be provided)
-------------------------------------------
1. Parametric ("synthetic"): a SyntheticConfig generates a fresh trade
   sequence for every simulation via generate_trade_sequences().
   Sequence i uses derived seed = seed + i (existing convention), which
   gives the prefix property: the first k simulations of an n > k run
   are identical to a k-simulation run with the same seed.

2. Resampled: a fixed list of observed trades provides the empirical
   distribution. Each simulation draws n_trades values WITH replacement
   and places them on the ORIGINAL date scaffolding (positions keep
   their dates), so daily-loss day structure is preserved.

   ASSUMPTION: Resampled trade outcomes are IID-exchangeable.
   JUSTIFICATION: For synthetic data this holds by construction. Real
       trade data has not been audited yet (Phase 8), so resampling
       real data is out of scope for v0.1.
   FAILURE MODE: If the source sequence exhibits temporal dependence
       (streaks, volatility clustering), IID resampling destroys that
       structure and misstates drawdown/pass probabilities — typically
       understating the chance of long losing streaks.
   ROBUST ALTERNATIVE: When real data arrives, test for autocorrelation
       first (FARS_SPEC §11). If dependence is found, use block
       bootstrap or regime-aware resampling instead.

Terminal-condition accounting
-----------------------------
Each simulation ends in one of five engine terminal conditions. They
are mapped to the three mutually exclusive buckets required by
FARS_SPEC §8 (probability_pass, probability_fail, timeout_probability):

  profit_target          -> PASS
  max_drawdown           -> FAIL
  daily_loss             -> FAIL
  max_trades, completed  -> TIMEOUT

POLICY: "completed" (all generated trades applied without reaching the
target or violating a rule) is folded into TIMEOUT. The spec requires
P_pass + P_fail + P_timeout = 1, and both max_trades and completed mean
"the evaluation ended without a pass/fail verdict". They are still
reported separately in terminal_counts for transparency.

Failure attribution
-------------------
A single trade can violate multiple rules at once (e.g. -10R blowing
through both max_drawdown and daily_loss). The engine records ALL of
them in SimulationResult.violated_conditions (Phase 4). This module
reports BOTH views:

  - INCLUSIVE (failure_probability_max_drawdown / _daily_loss):
    fraction of simulations whose violated_conditions contains that
    rule. These can overlap: one simulation may count toward both.
  - PRIMARY (failure_probability_max_drawdown_primary /
    _daily_loss_primary): fraction of simulations whose
    terminal_condition is that rule. Primary causes are mutually
    exclusive and sum EXACTLY to probability_fail.

Drawdown statistics
-------------------
median/P95/P99 max drawdown are computed from
SimulationResult.max_drawdown_historical (peak-to-trough, independent
of drawdown_mode), NOT the rule-based max_drawdown_hit. In static mode
the rule DD can be 0% while the account actually drew down from a gain
peak; statistical reporting must reflect what really happened.

Uncertainty
-----------
Every probability reports a Wilson binomial confidence interval
(better coverage than the normal/Wald interval near 0 or 1). Drawdown
quantiles report bootstrap CIs (configurable, disable with
n_bootstrap=0). P(PASS) reports its standard error; convergence mode
uses it as the stopping criterion.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from math import isfinite, sqrt
from types import MappingProxyType

import numpy as np
from scipy import stats as sp_stats

from src.engine import SimulationResult, run_simulation
from src.synthetic import generate_trade_sequences
from src.types import FundedAccountRules, SyntheticConfig, Trade

# ---------------------------------------------------------------------------
# Terminal-condition coding
# ---------------------------------------------------------------------------

#: Canonical terminal conditions, indexed by their integer code.
TERMINAL_CONDITIONS: tuple[str, ...] = (
    "profit_target",  # 0
    "max_drawdown",   # 1
    "daily_loss",     # 2
    "max_trades",     # 3
    "completed",      # 4
)

_TERMINAL_TO_CODE = {name: i for i, name in enumerate(TERMINAL_CONDITIONS)}

CODE_PROFIT_TARGET = 0
CODE_MAX_DRAWDOWN = 1
CODE_DAILY_LOSS = 2
CODE_MAX_TRADES = 3
CODE_COMPLETED = 4


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MonteCarloConfig:
    """
    Configuration for a Monte Carlo run.

    Two execution modes:

    1. Fixed-n (default): run exactly n_simulations.
    2. Convergence (n_simulations=None): run batches of batch_size until
       SE(P_pass) <= se_target, with min_simulations as a safety floor
       and max_simulations as a hard cap.

    seed: master seed. None -> non-deterministic run. With a fixed seed,
        the entire run (simulation draws AND bootstrap CIs) is
        reproducible bit-for-bit.

    confidence_level: coverage for Wilson probability CIs and bootstrap
        quantile CIs.

    n_bootstrap: number of bootstrap resamples for drawdown-quantile CIs.
        0 disables them (the CI fields become None).
    """

    n_simulations: int | None = 10_000
    seed: int | None = None

    # Convergence-mode parameters (ignored when n_simulations is set)
    se_target: float = 0.005
    min_simulations: int = 1_000
    max_simulations: int = 100_000
    batch_size: int = 1_000

    # Inference
    confidence_level: float = 0.95
    n_bootstrap: int = 1_000

    def __post_init__(self):
        if self.n_simulations is not None:
            if (
                not isinstance(self.n_simulations, int)
                or isinstance(self.n_simulations, bool)
                or self.n_simulations <= 0
            ):
                raise ValueError(
                    f"n_simulations must be a positive integer or None, "
                    f"got {self.n_simulations!r}"
                )
        if self.seed is not None and (
            not isinstance(self.seed, int) or isinstance(self.seed, bool)
        ):
            raise ValueError(f"seed must be an integer or None, got {self.seed!r}")
        if not isfinite(self.se_target) or not 0.0 < self.se_target < 1.0:
            raise ValueError(
                f"se_target must be finite and in (0, 1), got {self.se_target}"
            )
        for name in ("min_simulations", "max_simulations", "batch_size"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(
                    f"{name} must be a positive integer, got {value!r}"
                )
        if self.max_simulations < self.min_simulations:
            raise ValueError(
                f"max_simulations ({self.max_simulations}) must be >= "
                f"min_simulations ({self.min_simulations})"
            )
        if not isfinite(self.confidence_level) or not 0.0 < self.confidence_level < 1.0:
            raise ValueError(
                f"confidence_level must be finite and in (0, 1), "
                f"got {self.confidence_level}"
            )
        if (
            not isinstance(self.n_bootstrap, int)
            or isinstance(self.n_bootstrap, bool)
            or self.n_bootstrap < 0
        ):
            raise ValueError(
                f"n_bootstrap must be a non-negative integer (0 disables), "
                f"got {self.n_bootstrap!r}"
            )


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MonteCarloResult:
    """
    Aggregated outcome of a Monte Carlo run for one risk_per_trade.

    Probability fields are point estimates; *_ci are Wilson intervals at
    the configured confidence_level. median/P95/P99 max drawdown use the
    HISTORICAL peak-to-trough drawdown across all simulations (see module
    docstring). median/mean_trades_to_pass are math.nan when no
    simulation passed (undefined statistic, not 0).

    Per-simulation raw data is exposed as fixed-size numpy arrays
    (treat as read-only):
      terminal_codes          int8 codes into TERMINAL_CONDITIONS
      final_equities          float64
      trades_executed         int64
      max_drawdowns_historical float64, in [0, ∞)
      violated_max_drawdown   bool — rule present in violated_conditions
      violated_daily_loss     bool — rule present in violated_conditions

    terminal_counts maps every terminal condition name to its count
    (zeros included), so "completed" vs "max_trades" stays visible even
    though both fold into timeout_probability.
    """

    # Setup echo
    n_simulations: int
    seed: int | None
    source: str  # "synthetic" | "resampled"
    risk_per_trade: float
    convergence_mode: bool
    converged: bool
    se_target: float

    # Outcome probabilities (mutually exclusive partition)
    probability_pass: float
    probability_fail: float
    timeout_probability: float

    # Failure attribution — inclusive (can overlap)
    failure_probability_max_drawdown: float
    failure_probability_daily_loss: float
    # Failure attribution — primary cause (sums exactly to probability_fail)
    failure_probability_max_drawdown_primary: float
    failure_probability_daily_loss_primary: float

    # Wilson confidence intervals for the three partition probabilities
    pass_ci: tuple[float, float]
    fail_ci: tuple[float, float]
    timeout_ci: tuple[float, float]

    # Trades to pass (over passing simulations only)
    median_trades_to_pass: float
    mean_trades_to_pass: float

    # Drawdown quantiles (historical peak-to-trough, all simulations)
    median_max_drawdown: float
    p95_max_drawdown: float
    p99_max_drawdown: float
    median_max_drawdown_ci: tuple[float, float] | None
    p95_max_drawdown_ci: tuple[float, float] | None
    p99_max_drawdown_ci: tuple[float, float] | None

    # Uncertainty of the headline estimate
    se_probability_pass: float

    # Per-simulation raw data (read-only)
    terminal_codes: np.ndarray = field(repr=False)
    final_equities: np.ndarray = field(repr=False)
    trades_executed: np.ndarray = field(repr=False)
    max_drawdowns_historical: np.ndarray = field(repr=False)
    violated_max_drawdown: np.ndarray = field(repr=False)
    violated_daily_loss: np.ndarray = field(repr=False)
    terminal_counts: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self):
        if (
            not isinstance(self.n_simulations, int)
            or isinstance(self.n_simulations, bool)
            or self.n_simulations <= 0
        ):
            raise ValueError(
                f"n_simulations must be a positive integer, got {self.n_simulations!r}"
            )
        if self.source not in ("synthetic", "resampled"):
            raise ValueError(
                f"source must be 'synthetic' or 'resampled', got {self.source!r}"
            )

        n = self.n_simulations
        probs = (
            ("probability_pass", self.probability_pass),
            ("probability_fail", self.probability_fail),
            ("timeout_probability", self.timeout_probability),
            ("failure_probability_max_drawdown", self.failure_probability_max_drawdown),
            ("failure_probability_daily_loss", self.failure_probability_daily_loss),
            ("failure_probability_max_drawdown_primary",
             self.failure_probability_max_drawdown_primary),
            ("failure_probability_daily_loss_primary",
             self.failure_probability_daily_loss_primary),
        )
        for name, value in probs:
            if not isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(
                    f"{name} must be finite and in [0, 1], got {value!r}"
                )

        # The three partition probabilities must sum to 1. They are
        # computed from integer counts, so any deviation is a bug.
        total = self.probability_pass + self.probability_fail + self.timeout_probability
        if abs(total - 1.0) > 1e-9:
            raise ValueError(
                f"probability_pass + probability_fail + timeout_probability "
                f"must equal 1, got {total!r}"
            )

        # Primary causes decompose probability_fail exactly.
        primary_sum = (
            self.failure_probability_max_drawdown_primary
            + self.failure_probability_daily_loss_primary
        )
        if abs(primary_sum - self.probability_fail) > 1e-9:
            raise ValueError(
                f"primary failure probabilities must sum to probability_fail: "
                f"{primary_sum!r} != {self.probability_fail!r}"
            )

        if not isfinite(self.se_probability_pass) or self.se_probability_pass < 0.0:
            raise ValueError(
                f"se_probability_pass must be finite and >= 0, "
                f"got {self.se_probability_pass!r}"
            )

        for name in ("median_max_drawdown", "p95_max_drawdown", "p99_max_drawdown"):
            value = getattr(self, name)
            if not isfinite(value) or value < 0.0:
                raise ValueError(
                    f"{name} must be finite and >= 0, got {value!r}"
                )

        for name in ("median_trades_to_pass", "mean_trades_to_pass"):
            value = getattr(self, name)
            if not isfinite(value) and not (isinstance(value, float) and value != value):
                # allow NaN (no passes); reject inf
                raise ValueError(f"{name} must be finite or NaN, got {value!r}")
            if isfinite(value) and value < 0:
                raise ValueError(f"{name} must be >= 0, got {value!r}")

        _validate_ci(self.pass_ci, "pass_ci")
        _validate_ci(self.fail_ci, "fail_ci")
        _validate_ci(self.timeout_ci, "timeout_ci")
        for name in (
            "median_max_drawdown_ci",
            "p95_max_drawdown_ci",
            "p99_max_drawdown_ci",
        ):
            ci = getattr(self, name)
            if ci is not None:
                _validate_ci(ci, name, upper_bound=None)

        # Array coherence
        arrays = {
            "terminal_codes": self.terminal_codes,
            "final_equities": self.final_equities,
            "trades_executed": self.trades_executed,
            "max_drawdowns_historical": self.max_drawdowns_historical,
            "violated_max_drawdown": self.violated_max_drawdown,
            "violated_daily_loss": self.violated_daily_loss,
        }
        for name, arr in arrays.items():
            if not isinstance(arr, np.ndarray) or arr.shape != (n,):
                raise ValueError(
                    f"{name} must be a numpy array of shape ({n},), "
                    f"got {type(arr).__name__} "
                    f"shape={getattr(arr, 'shape', None)!r}"
                )
        if not np.all(np.isfinite(self.final_equities)):
            raise ValueError("final_equities must be finite")
        if not np.all(np.isfinite(self.max_drawdowns_historical)):
            raise ValueError("max_drawdowns_historical must be finite")
        if np.any(self.max_drawdowns_historical < 0.0):
            raise ValueError("max_drawdowns_historical must be >= 0")
        if np.any(self.terminal_codes < 0) or np.any(
            self.terminal_codes >= len(TERMINAL_CONDITIONS)
        ):
            raise ValueError("terminal_codes contains an invalid code")

        # terminal_counts must agree with terminal_codes
        if set(self.terminal_counts) != set(TERMINAL_CONDITIONS):
            raise ValueError(
                f"terminal_counts must have exactly the keys "
                f"{TERMINAL_CONDITIONS}, got {sorted(self.terminal_counts)!r}"
            )
        for name, count in self.terminal_counts.items():
            expected = int(np.count_nonzero(self.terminal_codes == _TERMINAL_TO_CODE[name]))
            if count != expected:
                raise ValueError(
                    f"terminal_counts[{name!r}] = {count} disagrees with "
                    f"terminal_codes ({expected})"
                )

        # Enforce read-only guarantees for exposed numpy arrays and mappings
        for arr in (
            self.terminal_codes,
            self.final_equities,
            self.trades_executed,
            self.max_drawdowns_historical,
            self.violated_max_drawdown,
            self.violated_daily_loss,
        ):
            if isinstance(arr, np.ndarray):
                arr.flags.writeable = False

        object.__setattr__(
            self,
            "terminal_counts",
            MappingProxyType(dict(self.terminal_counts)),
        )


def _validate_ci(ci, name: str, upper_bound: float | None = 1.0) -> None:
    """Validate a (lo, hi) confidence-interval tuple."""
    if (
        not isinstance(ci, tuple)
        or len(ci) != 2
        or not all(isfinite(v) for v in ci)
    ):
        raise ValueError(f"{name} must be a tuple of two finite floats, got {ci!r}")
    lo, hi = ci
    if lo < 0.0 or lo > hi or (upper_bound is not None and hi > upper_bound):
        raise ValueError(
            f"{name} must satisfy 0 <= lo <= hi"
            + (f" <= {upper_bound}" if upper_bound is not None else "")
            + f", got {ci!r}"
        )


# ---------------------------------------------------------------------------
# Confidence intervals
# ---------------------------------------------------------------------------


def wilson_ci(
    successes: int,
    n: int,
    confidence_level: float = 0.95,
) -> tuple[float, float]:
    """
    Wilson score interval for a binomial proportion.

    Preferred over the Wald interval: much better coverage near p = 0
    or p = 1 and for small n, while remaining cheap to compute.

    Returns (lo, hi) clipped to [0, 1].
    """
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        raise ValueError(f"n must be a positive integer, got {n!r}")
    if not isinstance(successes, int) or isinstance(successes, bool):
        raise ValueError(f"successes must be an integer, got {successes!r}")
    if not 0 <= successes <= n:
        raise ValueError(f"successes must be in [0, {n}], got {successes!r}")
    if not isfinite(confidence_level) or not 0.0 < confidence_level < 1.0:
        raise ValueError(
            f"confidence_level must be finite and in (0, 1), got {confidence_level}"
        )

    z = float(sp_stats.norm.ppf(1.0 - (1.0 - confidence_level) / 2.0))
    p_hat = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p_hat + z2 / (2.0 * n)) / denom
    half = z * sqrt(p_hat * (1.0 - p_hat) / n + z2 / (4.0 * n * n)) / denom
    lo = max(0.0, center - half)
    hi = min(1.0, center + half)
    if successes == 0:
        lo = 0.0
    if successes == n:
        hi = 1.0
    return lo, hi


def wilson_se(
    successes: int,
    n: int,
    confidence_level: float = 0.95,
) -> float:
    """
    Standard error proxy for a binomial proportion derived from the Wilson score interval.

    Unlike the naive Wald formula sqrt(p_hat * (1 - p_hat) / n), which collapses to 0.0
    at p_hat = 0 or p_hat = 1 (falsely declaring certainty and premature convergence),
    the Wilson SE proxy is half_width / z, which stays strictly positive and scales
    as O(1/n) at boundaries.
    """
    lo, hi = wilson_ci(successes, n, confidence_level)
    z = float(sp_stats.norm.ppf(1.0 - (1.0 - confidence_level) / 2.0))
    return (hi - lo) / (2.0 * z)


def bootstrap_quantiles_ci(
    values: np.ndarray,
    quantiles_pct: list[float] | tuple[float, ...],
    n_bootstrap: int,
    confidence_level: float,
    rng: np.random.Generator,
    batch_size: int = 100,
) -> list[tuple[float, float]]:
    """
    Nonparametric bootstrap CI for multiple sample quantiles computed simultaneously
    in memory-safe batches.

    Avoids allocating massive matrices (e.g. 100,000 x 1,000 floats = 800 MB) by
    processing bootstrap resamples in manageable batches and evaluating all quantiles
    in a single pass.
    """
    if not isinstance(values, np.ndarray) or values.ndim != 1 or len(values) == 0:
        raise ValueError("values must be a non-empty 1-D numpy array")
    if not quantiles_pct:
        raise ValueError("quantiles_pct must be non-empty")
    for q in quantiles_pct:
        if not isfinite(q) or not 0.0 <= q <= 100.0:
            raise ValueError(f"quantiles_pct entries must be in [0, 100], got {q}")
    if not isinstance(n_bootstrap, int) or isinstance(n_bootstrap, bool) or n_bootstrap <= 0:
        raise ValueError(f"n_bootstrap must be a positive integer, got {n_bootstrap!r}")
    if not isfinite(confidence_level) or not 0.0 < confidence_level < 1.0:
        raise ValueError(
            f"confidence_level must be finite and in (0, 1), got {confidence_level}"
        )
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size <= 0:
        raise ValueError(f"batch_size must be a positive integer, got {batch_size!r}")

    n = len(values)
    n_q = len(quantiles_pct)
    boot_quantiles = np.empty((n_bootstrap, n_q), dtype=np.float64)

    generated = 0
    while generated < n_bootstrap:
        chunk = min(batch_size, n_bootstrap - generated)
        indices = rng.integers(0, n, size=(chunk, n))
        chunk_vals = values[indices]
        res = np.percentile(chunk_vals, quantiles_pct, axis=1)
        if n_q == 1:
            boot_quantiles[generated : generated + chunk, 0] = res
        else:
            boot_quantiles[generated : generated + chunk, :] = res.T
        generated += chunk

    alpha = (1.0 - confidence_level) / 2.0
    cis: list[tuple[float, float]] = []
    for j in range(n_q):
        lo = float(np.percentile(boot_quantiles[:, j], 100.0 * alpha))
        hi = float(np.percentile(boot_quantiles[:, j], 100.0 * (1.0 - alpha)))
        cis.append((lo, hi))
    return cis


def bootstrap_quantile_ci(
    values: np.ndarray,
    quantile_pct: float,
    n_bootstrap: int,
    confidence_level: float,
    rng: np.random.Generator,
) -> tuple[float, float]:
    """
    Nonparametric bootstrap CI for a sample quantile.

    Resamples `values` with replacement n_bootstrap times, recomputes
    the quantile each time, and returns the central confidence_level
    interval of those bootstrap estimates.

    quantile_pct is in [0, 100] (numpy percentile convention).
    """
    return bootstrap_quantiles_ci(
        values, [quantile_pct], n_bootstrap, confidence_level, rng
    )[0]


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------


def _prepare_resampling_source(
    trades: list[Trade],
    rules: FundedAccountRules,
) -> tuple[np.ndarray, list[str]]:
    """
    Validate a resampling source and split it into R values and dates.

    Raises ValueError for: empty list, non-finite r_result, invalid or
    non-chronological dates, or any r_result whose dollar P&L at this
    rule's risk level would overflow float64.
    """
    if not isinstance(trades, list) or len(trades) == 0:
        raise ValueError("trades must be a non-empty list of Trade objects")

    r_values = np.empty(len(trades), dtype=np.float64)
    dates: list[str] = []
    prev_date: str | None = None

    for i, trade in enumerate(trades):
        if not isinstance(trade, Trade):
            raise ValueError(f"trades[{i}] must be a Trade, got {type(trade).__name__}")
        if not isfinite(trade.r_result):
            raise ValueError(
                f"trades[{i}].r_result must be finite, got {trade.r_result!r}"
            )
        r_values[i] = trade.r_result

        try:
            normalized = datetime.strptime(trade.date, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            raise ValueError(
                f"trades[{i}].date {trade.date!r} is invalid. "
                f"Expected format: YYYY-MM-DD."
            )
        if prev_date is not None and normalized < prev_date:
            raise ValueError(
                f"trades dates must be non-decreasing: got {normalized!r} "
                f"after {prev_date!r} at index {i}"
            )
        prev_date = normalized
        dates.append(normalized)

    # Reject values whose single-trade dollar P&L would overflow. Without
    # this, the run dies mid-flight inside the engine with a less
    # actionable error. Equity accumulation overflow is still caught
    # per-trade by the engine and propagates as ValueError.
    dollar_risk = rules.risk_per_trade * rules.initial_balance
    with np.errstate(over="ignore"):
        pnl = r_values * dollar_risk
    bad = np.flatnonzero(~np.isfinite(pnl))
    if bad.size > 0:
        raise ValueError(
            f"trades[{int(bad[0])}].r_result={r_values[int(bad[0])]!r} would "
            f"overflow float64 P&L at risk_per_trade={rules.risk_per_trade} "
            f"and initial_balance={rules.initial_balance}"
        )

    return r_values, dates


# ---------------------------------------------------------------------------
# Monte Carlo driver
# ---------------------------------------------------------------------------


def run_monte_carlo(
    rules: FundedAccountRules,
    *,
    synthetic_config: SyntheticConfig | None = None,
    trades: list[Trade] | None = None,
    assume_iid: bool = False,
    config: MonteCarloConfig | None = None,
) -> MonteCarloResult:
    """
    Run a Monte Carlo study for one funded-account rule set.

    Exactly one of synthetic_config (parametric generation) or trades
    (IID resampling) must be provided.

    When trades is provided, assume_iid=True is required to acknowledge
    the IID exchangeability assumption (FARS_SPEC §11).

    Fixed-n mode (config.n_simulations set): runs exactly that many
    simulations.

    Convergence mode (config.n_simulations=None): runs batches of
    config.batch_size until Wilson SE(P_pass) <= config.se_target, always at
    least config.min_simulations and never more than
    config.max_simulations.

    Determinism: with a fixed config.seed the full result — including
    per-simulation arrays and bootstrap CIs — is reproducible. In
    synthetic mode the first k simulations of any run equal a k-run
    with the same seed (derived-seed prefix property). In resampled
    mode draws come from one sequential RNG stream with the same prefix
    property.

    Raises ValueError for invalid inputs. Engine-level numeric errors
    (overflow mid-run) propagate as ValueError from run_simulation.
    """
    if config is None:
        config = MonteCarloConfig()

    if (synthetic_config is None) == (trades is None):
        raise ValueError(
            "exactly one of synthetic_config or trades must be provided"
        )

    if trades is not None and not assume_iid:
        raise ValueError(
            "Resampling historical/observed trades assumes IID exchangeability. "
            "Pass assume_iid=True to explicitly confirm this assumption after "
            "testing for autocorrelation and temporal dependence (FARS_SPEC §11)."
        )

    if synthetic_config is not None:
        source = "synthetic"
        r_values = dates = None
    else:
        assert trades is not None
        source = "resampled"
        r_values, dates = _prepare_resampling_source(trades, rules)

    convergence_mode = config.n_simulations is None

    # --- Pre-allocate / collect ---
    code_chunks: list[np.ndarray] = []
    equity_chunks: list[np.ndarray] = []
    trades_chunks: list[np.ndarray] = []
    dd_chunks: list[np.ndarray] = []
    viol_dd_chunks: list[np.ndarray] = []
    viol_dl_chunks: list[np.ndarray] = []

    total = 0
    total_passes = 0

    # Resampling RNG: one sequential stream, deterministic in seed.
    resample_rng = (
        np.random.default_rng(config.seed) if source == "resampled" else None
    )

    def _run_batch(batch_n: int, start_index: int) -> None:
        nonlocal total, total_passes

        codes, equities, n_trades_exec, dds, viol_dd, viol_dl = _simulate_batch(
            rules=rules,
            batch_n=batch_n,
            start_index=start_index,
            synthetic_config=synthetic_config,
            r_values=r_values,
            dates=dates,
            resample_rng=resample_rng,
            master_seed=config.seed,
        )

        code_chunks.append(codes)
        equity_chunks.append(equities)
        trades_chunks.append(n_trades_exec)
        dd_chunks.append(dds)
        viol_dd_chunks.append(viol_dd)
        viol_dl_chunks.append(viol_dl)

        total += batch_n
        total_passes += int(np.count_nonzero(codes == CODE_PROFIT_TARGET))

    if not convergence_mode:
        assert config.n_simulations is not None  # narrowed by convergence_mode
        _run_batch(config.n_simulations, 0)
        converged = True
    else:
        while True:
            batch_n = min(config.batch_size, config.max_simulations - total)
            if batch_n <= 0:
                break
            _run_batch(batch_n, total)
            se = wilson_se(total_passes, total, config.confidence_level)
            if total >= config.min_simulations:
                if se <= config.se_target or total >= config.max_simulations:
                    break
        se_final = wilson_se(total_passes, total, config.confidence_level)
        converged = se_final <= config.se_target

    # --- Aggregate ---
    terminal_codes = np.concatenate(code_chunks)
    final_equities = np.concatenate(equity_chunks)
    trades_executed = np.concatenate(trades_chunks)
    max_drawdowns = np.concatenate(dd_chunks)
    violated_max_drawdown = np.concatenate(viol_dd_chunks)
    violated_daily_loss = np.concatenate(viol_dl_chunks)

    n = total
    pass_count = int(np.count_nonzero(terminal_codes == CODE_PROFIT_TARGET))
    fail_dd_primary = int(np.count_nonzero(terminal_codes == CODE_MAX_DRAWDOWN))
    fail_dl_primary = int(np.count_nonzero(terminal_codes == CODE_DAILY_LOSS))
    fail_count = fail_dd_primary + fail_dl_primary
    timeout_count = int(
        np.count_nonzero(
            (terminal_codes == CODE_MAX_TRADES) | (terminal_codes == CODE_COMPLETED)
        )
    )

    probability_pass = pass_count / n
    probability_fail = fail_count / n
    timeout_probability = timeout_count / n
    se_pass = wilson_se(pass_count, n, config.confidence_level)

    # Drawdown quantiles — historical peak-to-trough, all simulations.
    # numpy default linear interpolation.
    median_dd, p95_dd, p99_dd = (
        float(q) for q in np.percentile(max_drawdowns, [50.0, 95.0, 99.0])
    )

    # Bootstrap CIs for drawdown quantiles (deterministic stream derived
    # from the master seed via SeedSequence.spawn — never collides with
    # the seed+i simulation stream). Computed simultaneously in batches.
    median_dd_ci = p95_dd_ci = p99_dd_ci = None
    if config.n_bootstrap > 0:
        if config.seed is None:
            boot_rng = np.random.default_rng(None)
        else:
            boot_rng = np.random.Generator(
                np.random.PCG64(np.random.SeedSequence(config.seed).spawn(1)[0])
            )
        cis = bootstrap_quantiles_ci(
            max_drawdowns,
            [50.0, 95.0, 99.0],
            config.n_bootstrap,
            config.confidence_level,
            boot_rng,
        )
        median_dd_ci, p95_dd_ci, p99_dd_ci = cis[0], cis[1], cis[2]

    # Trades to pass — undefined (NaN) when nothing passed.
    trades_of_passes = trades_executed[terminal_codes == CODE_PROFIT_TARGET]
    if trades_of_passes.size == 0:
        median_trades_to_pass = float("nan")
        mean_trades_to_pass = float("nan")
    else:
        median_trades_to_pass = float(np.median(trades_of_passes))
        mean_trades_to_pass = float(np.mean(trades_of_passes))

    terminal_counts = {
        name: int(np.count_nonzero(terminal_codes == _TERMINAL_TO_CODE[name]))
        for name in TERMINAL_CONDITIONS
    }

    return MonteCarloResult(
        n_simulations=n,
        seed=config.seed,
        source=source,
        risk_per_trade=rules.risk_per_trade,
        convergence_mode=convergence_mode,
        converged=converged,
        se_target=config.se_target,
        probability_pass=probability_pass,
        probability_fail=probability_fail,
        timeout_probability=timeout_probability,
        failure_probability_max_drawdown=float(np.count_nonzero(violated_max_drawdown)) / n,
        failure_probability_daily_loss=float(np.count_nonzero(violated_daily_loss)) / n,
        failure_probability_max_drawdown_primary=fail_dd_primary / n,
        failure_probability_daily_loss_primary=fail_dl_primary / n,
        pass_ci=wilson_ci(pass_count, n, config.confidence_level),
        fail_ci=wilson_ci(fail_count, n, config.confidence_level),
        timeout_ci=wilson_ci(timeout_count, n, config.confidence_level),
        median_trades_to_pass=median_trades_to_pass,
        mean_trades_to_pass=mean_trades_to_pass,
        median_max_drawdown=median_dd,
        p95_max_drawdown=p95_dd,
        p99_max_drawdown=p99_dd,
        median_max_drawdown_ci=median_dd_ci,
        p95_max_drawdown_ci=p95_dd_ci,
        p99_max_drawdown_ci=p99_dd_ci,
        se_probability_pass=se_pass,
        terminal_codes=terminal_codes,
        final_equities=final_equities,
        trades_executed=trades_executed,
        max_drawdowns_historical=max_drawdowns,
        violated_max_drawdown=violated_max_drawdown,
        violated_daily_loss=violated_daily_loss,
        terminal_counts=terminal_counts,
    )


def _simulate_batch(
    *,
    rules: FundedAccountRules,
    batch_n: int,
    start_index: int,
    synthetic_config: SyntheticConfig | None,
    r_values: np.ndarray | None,
    dates: list[str] | None,
    resample_rng: np.random.Generator | None,
    master_seed: int | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Simulate batch_n funded-account runs and record per-run summaries.

    Returns (terminal_codes, final_equities, trades_executed,
    max_drawdowns_historical, violated_max_drawdown, violated_daily_loss).

    Exactly one source must be active: synthetic_config (parametric) or
    r_values/dates/resample_rng (resampled).
    """
    codes = np.empty(batch_n, dtype=np.int8)
    equities = np.empty(batch_n, dtype=np.float64)
    n_trades_exec = np.empty(batch_n, dtype=np.int64)
    dds = np.empty(batch_n, dtype=np.float64)
    viol_dd = np.zeros(batch_n, dtype=bool)
    viol_dl = np.zeros(batch_n, dtype=bool)

    sequences: list[list[Trade]]
    if synthetic_config is not None:
        batch_config = synthetic_config
        effective_seed = (
            master_seed if master_seed is not None else synthetic_config.seed
        )
        if effective_seed is not None:
            # Derived-seed convention (seed + i) keeps the prefix
            # property across batches: sequence i of the whole run is
            # generated from seed + i regardless of batch boundaries.
            batch_config = replace(
                synthetic_config, seed=effective_seed + start_index
            )
        sequences = generate_trade_sequences(batch_config, batch_n)
    else:
        assert r_values is not None and dates is not None and resample_rng is not None
        sequences = _resample_batch(resample_rng, r_values, dates, batch_n, start_index)

    for k, sequence in enumerate(sequences):
        result = run_simulation(sequence, rules)
        codes[k] = _TERMINAL_TO_CODE[result.terminal_condition]
        equities[k] = result.final_equity
        n_trades_exec[k] = result.trades_executed
        dds[k] = result.max_drawdown_historical
        violated = result.violated_conditions
        viol_dd[k] = "max_drawdown" in violated
        viol_dl[k] = "daily_loss" in violated

    return codes, equities, n_trades_exec, dds, viol_dd, viol_dl


def _resample_batch(
    rng: np.random.Generator,
    r_values: np.ndarray,
    dates: list[str],
    batch_n: int,
    start_index: int,
) -> list[list[Trade]]:
    """
    Draw batch_n IID bootstrap sequences of length len(dates).

    Values are sampled with replacement from r_values; each position
    keeps its original date (the date scaffolding), preserving the
    trades-per-day structure that drives daily-loss aggregation.
    """
    length = len(dates)
    m = len(r_values)
    sequences: list[list[Trade]] = []
    for k in range(batch_n):
        idx = rng.integers(0, m, size=length)
        sim_index = start_index + k
        # Position keeps its original date (the scaffolding); only the
        # R value is resampled. This preserves chronological order and
        # the trades-per-day structure that drives daily-loss resets.
        sequence = [
            Trade(
                r_result=float(r_values[j]),
                date=dates[pos],
                trade_id=f"mc-{sim_index}-{pos:06d}",
            )
            for pos, j in enumerate(idx)
        ]
        sequences.append(sequence)
    return sequences

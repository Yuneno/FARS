"""
Risk-per-trade optimization and FRES scoring engine (Phase 6).

Implements FARS_SPEC §8 (Candidate Risk Levels), §9 (Risk-Adjusted Optimization /
FRES), and §10 (Tail Risk: VaR & CVaR / Expected Shortfall).

Key responsibilities:
---------------------
1. Grid Search across Candidate Risk Levels with Common Random Numbers (CRN):
   Evaluates a configurable spectrum of risk_per_trade levels (e.g. 0.10% to 2.00%
   in 0.05% increments, or custom risk levels) using the Phase 5 Monte Carlo engine.
   Uses Common Random Numbers (CRN) across candidate levels to reduce between-scenario
   variance and perform paired comparison on identical trade sequences.

2. Tail Risk Analysis (VaR and CVaR / Expected Shortfall):
   Calculates Value at Risk (VaR_95, VaR_99) and Conditional Value at Risk
   (CVaR_95, CVaR_99 / Expected Shortfall) on historical peak-to-trough max drawdowns.
   CVaR is evaluated on the exact upper tail (worst (1 - alpha) fraction of paths),
   making it robust to discrete empirical samples with tied VaR values.

3. Stochastic Curve Smoothing:
   Applies Nadaraya-Watson Gaussian kernel regression to P(PASS | r) across candidate
   risk levels as a sensitivity view less dominated by pointwise Monte Carlo noise.
   Bandwidth defaults dynamically to 2x the median grid step of the evaluated levels.

4. Funded Risk Efficiency Score (FRES):
   Computes the multi-objective risk-adjusted efficiency score:
     FRES(r) = P_pass - λ * P_fail - γ * DD_P95_norm - δ * CVaR_norm
   where drawdown terms are expressed as multiples of the account drawdown budget.

5. Optimal Risk Identification:
   Identifies:
     - r*_raw: argmax of un-smoothed P(PASS | r)
     - r*_smooth: argmax of smoothed P(PASS | r)
     - r*_FRES: argmax of risk-adjusted FRES score
"""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from math import floor, isfinite
from numbers import Real

import numpy as np
from scipy import stats as sp_stats

from src.monte_carlo import (
    CODE_PROFIT_TARGET,
    MonteCarloConfig,
    MonteCarloResult,
    run_monte_carlo,
)
from src.types import FundedAccountRules, SyntheticConfig, Trade


def _as_real_float64_array(values: object, name: str) -> np.ndarray:
    """Convert numeric real-valued input without silently coercing invalid data."""
    try:
        raw = np.asarray(values)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain only real numeric values") from exc

    if np.iscomplexobj(raw) or raw.dtype.kind == "b":
        raise ValueError(f"{name} must contain only real numeric values")
    if raw.dtype.kind == "O":
        if any(
            isinstance(value, (bool, np.bool_)) or not isinstance(value, Real)
            for value in raw.flat
        ):
            raise ValueError(f"{name} must contain only real numeric values")
    elif not np.issubdtype(raw.dtype, np.number):
        raise ValueError(f"{name} must contain only real numeric values")

    try:
        return np.asarray(raw, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must contain only real numeric values") from exc


# ---------------------------------------------------------------------------
# Tail Risk Functions (VaR & CVaR / Expected Shortfall)
# ---------------------------------------------------------------------------


def compute_var_cvar(
    drawdowns: Sequence[float] | np.ndarray,
    alpha: float = 0.95,
) -> tuple[float, float]:
    """
    Compute Value at Risk (VaR) and Conditional Value at Risk (CVaR) for max drawdowns.

    Parameters
    ----------
    drawdowns : Sequence[float] | np.ndarray
        One-dimensional sequence of non-negative historical peak-to-trough
        maximum drawdowns across simulations.
    alpha : float
        Confidence level for tail risk (e.g. 0.95 for worst 5%, 0.99 for worst 1%).
        Must be in (0, 1).

    Returns
    -------
    tuple[float, float]
        (VaR_alpha, CVaR_alpha), where VaR is the alpha-quantile and CVaR is the
        expected shortfall (mean loss in the worst (1 - alpha) tail).

    ASSUMPTION: Tail risk is measured on empirical peak-to-trough max drawdown
        outcomes across Monte Carlo paths.
    JUSTIFICATION: Drawdown distributions in trading strategies exhibit heavy tails
        and skewness; non-parametric empirical quantile and conditional expectation
        capture fat tails without forcing a normality assumption.
    FAILURE MODE: For very small sample sizes (N < 100), extreme quantiles (P99)
        have high sampling variance. Account paths stop when a rule is breached,
        so this measures realized drawdown through the terminal trade (including
        overshoot), not the latent drawdown of an unconstrained continuation.
    ROBUST ALTERNATIVE: Use large Monte Carlo sample size (N >= 10,000) or
        Extreme Value Theory (EVT / Generalized Pareto Distribution) for tail fitting.
    """
    if (
        isinstance(alpha, (bool, np.bool_))
        or not isinstance(alpha, Real)
        or not isfinite(alpha)
        or not 0.0 < alpha < 1.0
    ):
        raise ValueError(f"alpha must be finite and in (0, 1), got {alpha!r}")

    drawdowns = _as_real_float64_array(drawdowns, "drawdowns")

    if drawdowns.ndim != 1:
        raise ValueError(
            f"drawdowns must be one-dimensional, got shape {drawdowns.shape}"
        )
    if drawdowns.size == 0:
        raise ValueError("drawdowns array must not be empty")
    if not np.all(np.isfinite(drawdowns)):
        raise ValueError("drawdowns array must contain only finite numbers")
    if np.any(drawdowns < 0.0):
        raise ValueError("drawdowns array must contain only non-negative values")

    # Empirical inverse-CDF quantile, coherent with the empirical quantile
    # integral used for Expected Shortfall below.
    var_val = float(np.quantile(drawdowns, alpha, method="inverted_cdf"))

    # Expected Shortfall is the integral of the empirical quantile function over
    # [alpha, 1].  When (1-alpha)*N is non-integral, the boundary observation
    # contributes only the fractional probability mass needed for the requested
    # tail.  Taking ceil(...) complete observations would make CVaR depend on an
    # arbitrary sample-size rounding convention and bias small empirical tails.
    sorted_dd = np.sort(drawdowns)
    tail_mass = (1.0 - float(alpha)) * len(sorted_dd)
    nearest_integer = round(tail_mass)
    if nearest_integer >= 1 and np.isclose(
        tail_mass, nearest_integer, rtol=0.0, atol=1e-12
    ):
        tail_mass = float(nearest_integer)

    if tail_mass <= 1.0:
        # The requested empirical tail is contained wholly within the single
        # worst observation. This branch also avoids underflow in
        # tail_mass * max(drawdown) when alpha is extremely close to one.
        cvar_val = float(sorted_dd[-1])
    else:
        full_count = int(floor(tail_mass))
        fractional_count = tail_mass - full_count
        # Divide before summing so a finite weighted mean does not overflow
        # merely because the unnormalized sum of large finite observations does.
        cvar_val = float(np.sum(sorted_dd[-full_count:] / tail_mass))
        if fractional_count > 0.0:
            boundary_index = len(sorted_dd) - full_count - 1
            cvar_val += (
                fractional_count / tail_mass * float(sorted_dd[boundary_index])
            )

    if not isfinite(var_val) or not isfinite(cvar_val):
        raise ValueError("VaR/CVaR calculation overflowed float64 precision")

    return var_val, cvar_val


# ---------------------------------------------------------------------------
# Kernel Smoothing Function
# ---------------------------------------------------------------------------


def smooth_curve_gaussian(
    x: np.ndarray,
    y: np.ndarray,
    bandwidth: float,
) -> np.ndarray:
    """
    Smooth a 1D response curve using Nadaraya-Watson Gaussian kernel regression.

    Parameters
    ----------
    x : np.ndarray
        Independent variable coordinates (e.g. risk_per_trade levels). Strictly increasing.
    y : np.ndarray
        Observed response values (e.g. estimated P(PASS)).
    bandwidth : float
        Kernel standard deviation (bandwidth h > 0).

    Returns
    -------
    np.ndarray
        Smoothed response values evaluated at coordinates x, bounded to [0, 1].

    ASSUMPTION: The true response function P(PASS | r) is smooth with respect to risk r.
    JUSTIFICATION: Discrete MC estimates exhibit random binomial sampling noise. Kernel
        smoothing prevents selecting a suboptimal risk level purely due to a positive
        stochastic fluctuation.
    FAILURE MODE: If bandwidth is set excessively large, sharp optimal peaks will be over-smoothed;
        if set too small, noise is preserved.
    ROBUST ALTERNATIVE: Cross-validated bandwidth selection or Gaussian Process regression.
    """
    if (
        isinstance(bandwidth, (bool, np.bool_))
        or not isinstance(bandwidth, Real)
        or not isfinite(bandwidth)
        or bandwidth <= 0.0
    ):
        raise ValueError(f"bandwidth must be finite and positive, got {bandwidth!r}")

    x = _as_real_float64_array(x, "x")
    y = _as_real_float64_array(y, "y")

    if x.ndim != 1 or y.ndim != 1:
        raise ValueError(
            f"x and y must be one-dimensional, got shapes {x.shape} and {y.shape}"
        )
    if x.size != y.size:
        raise ValueError(f"x and y must have same length, got {x.size} vs {y.size}")
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise ValueError("x and y must contain only finite values")
    if np.any((y < 0.0) | (y > 1.0)):
        raise ValueError("y probabilities must be in [0, 1]")

    if x.size == 0:
        return np.array([], dtype=np.float64)

    if x.size == 1:
        return np.clip(y.copy(), 0.0, 1.0)

    if np.any(x[1:] <= x[:-1]):
        raise ValueError("x must be strictly increasing")

    # Pairwise squared distances: (x_i - x_j)^2 / (2 * h^2)
    with np.errstate(over="ignore", invalid="ignore"):
        diffs = (x[:, np.newaxis] - x[np.newaxis, :]) / bandwidth
        weights = np.exp(-0.5 * (diffs**2))

    # Normalize weights across rows
    weight_sums = np.sum(weights, axis=1, keepdims=True)
    weight_sums[weight_sums == 0.0] = 1.0

    smoothed = np.sum(weights * y[np.newaxis, :], axis=1) / weight_sums.squeeze(axis=1)
    return np.clip(smoothed, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OptimizationConfig:
    """
    Configuration for risk-per-trade grid search and optimization.

    Attributes
    ----------
    min_risk : float
        Minimum risk-per-trade level (default 0.001 = 0.10%).
    max_risk : float
        Maximum risk-per-trade level (default 0.02 = 2.00%).
    step_size : float
        Grid step size (default 0.0005 = 0.05%).
    risk_levels : tuple[float, ...] | None
        Explicit tuple of candidate risk levels. If provided, overrides
        min_risk / max_risk / step_size.
    smoothing_bandwidth : float | None
        Bandwidth for Gaussian kernel smoothing. If None, defaults to 2 * median grid step.
    fres_lambda : float
        Weight on failure probability in FRES (default 1.0).
    fres_gamma : float
        Weight on normalized P95 drawdown in FRES (default 0.5).
    fres_delta : float
        Weight on normalized CVaR_95 tail risk in FRES (default 0.5).
    confidence_level : float
        Confidence level for Monte Carlo confidence intervals (default 0.95).
    """

    min_risk: float = 0.001
    max_risk: float = 0.02
    step_size: float = 0.0005
    risk_levels: tuple[float, ...] | None = None
    smoothing_bandwidth: float | None = None

    fres_lambda: float = 1.0
    fres_gamma: float = 0.5
    fres_delta: float = 0.5
    confidence_level: float = 0.95

    def __post_init__(self):
        # Validate boolean types explicitly
        for attr, val in (
            ("min_risk", self.min_risk),
            ("max_risk", self.max_risk),
            ("step_size", self.step_size),
            ("fres_lambda", self.fres_lambda),
            ("fres_gamma", self.fres_gamma),
            ("fres_delta", self.fres_delta),
            ("confidence_level", self.confidence_level),
        ):
            if isinstance(val, (bool, np.bool_)) or not isinstance(val, Real):
                raise ValueError(f"{attr} must be a float/int, got {val!r}")
            if not isfinite(val):
                raise ValueError(f"{attr} must be finite, got {val!r}")

        if self.min_risk <= 0.0 or self.min_risk >= 1.0:
            raise ValueError(f"min_risk must be in (0, 1), got {self.min_risk}")
        if self.max_risk <= 0.0 or self.max_risk >= 1.0:
            raise ValueError(f"max_risk must be in (0, 1), got {self.max_risk}")
        if self.step_size <= 0.0:
            raise ValueError(f"step_size must be positive, got {self.step_size}")

        if self.confidence_level <= 0.0 or self.confidence_level >= 1.0:
            raise ValueError(
                f"confidence_level must be in (0, 1), got {self.confidence_level}"
            )

        for attr, val in (
            ("fres_lambda", self.fres_lambda),
            ("fres_gamma", self.fres_gamma),
            ("fres_delta", self.fres_delta),
        ):
            if val < 0.0:
                raise ValueError(f"{attr} must be non-negative, got {val}")

        if self.smoothing_bandwidth is not None:
            if (
                isinstance(self.smoothing_bandwidth, (bool, np.bool_))
                or not isinstance(self.smoothing_bandwidth, Real)
                or not isfinite(self.smoothing_bandwidth)
                or self.smoothing_bandwidth <= 0.0
            ):
                raise ValueError(
                    "smoothing_bandwidth must be finite and positive, got "
                    f"{self.smoothing_bandwidth!r}"
                )

        if self.risk_levels is not None:
            if not isinstance(self.risk_levels, (tuple, list)):
                raise ValueError("risk_levels must be a sequence of floats")
            if len(self.risk_levels) == 0:
                raise ValueError("risk_levels must not be empty")

            normalized_levels = []
            prev = -1.0
            for r in self.risk_levels:
                if (
                    isinstance(r, (bool, np.bool_))
                    or not isinstance(r, Real)
                    or not isfinite(r)
                ):
                    raise ValueError(f"risk level must be finite float, got {r!r}")
                if not 0.0 < r < 1.0:
                    raise ValueError(f"risk level must be in (0, 1), got {r}")
                if r <= prev:
                    raise ValueError(
                        f"risk_levels must be strictly increasing, got {r} after {prev}"
                    )
                prev = float(r)
                normalized_levels.append(prev)
            object.__setattr__(self, "risk_levels", tuple(normalized_levels))
        else:
            if self.min_risk >= self.max_risk:
                raise ValueError(
                    "min_risk must be strictly less than max_risk, got "
                    f"{self.min_risk} >= {self.max_risk}"
                )
            grid_span = self.max_risk - self.min_risk
            if self.step_size > grid_span and not np.isclose(
                self.step_size, grid_span, rtol=1e-12, atol=0.0
            ):
                raise ValueError(
                    "step_size must be positive and no greater than grid span, got "
                    f"{self.step_size}"
                )

    def get_risk_levels(self) -> tuple[float, ...]:
        """Return the resolved tuple of candidate risk levels."""
        if self.risk_levels is not None:
            return self.risk_levels

        # Decimal arithmetic interprets the user's decimal-valued configuration
        # without binary accumulation artifacts. max_risk is included only when
        # it lies on the configured step grid. Unlike fixed decimal rounding,
        # this also preserves legitimately tiny positive risk levels.
        min_decimal = Decimal(str(self.min_risk))
        max_decimal = Decimal(str(self.max_risk))
        step_decimal = Decimal(str(self.step_size))
        decimal_span_steps = (max_decimal - min_decimal) / step_decimal
        float_span_steps = (self.max_risk - self.min_risk) / self.step_size
        nearest_step_count = round(float_span_steps)
        if np.isclose(
            float_span_steps, nearest_step_count, rtol=1e-12, atol=1e-12
        ):
            interval_count = int(nearest_step_count)
        else:
            interval_count = int(floor(decimal_span_steps))
        num_steps = interval_count + 1
        grid = [float(min_decimal + i * step_decimal) for i in range(num_steps)]
        if np.isclose(grid[-1], self.max_risk, rtol=1e-12, atol=0.0):
            grid[-1] = float(self.max_risk)
        return tuple(grid)


# ---------------------------------------------------------------------------
# Result Structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskLevelEvaluation:
    """
    Detailed evaluation and metrics for a single candidate risk level.

    Attributes
    ----------
    risk_per_trade : float
        The candidate risk level evaluated.
    probability_pass : float
        Point estimate P(PASS | r).
    probability_fail : float
        Point estimate P(FAIL | r).
    timeout_probability : float
        Point estimate P(TIMEOUT | r).
    failure_probability_max_drawdown : float
        Inclusive failure rate due to max drawdown violation.
    failure_probability_daily_loss : float
        Inclusive failure rate due to daily loss violation.
    failure_probability_max_drawdown_primary : float
        Primary failure rate due to max drawdown violation.
    failure_probability_daily_loss_primary : float
        Primary failure rate due to daily loss violation.
    median_trades_to_pass : float
        Median trades executed among passed runs (NaN if no passes).
    mean_trades_to_pass : float
        Mean trades executed among passed runs (NaN if no passes).
    median_max_drawdown : float
        50th percentile of historical peak-to-trough max drawdown.
    p95_max_drawdown : float
        95th percentile of historical peak-to-trough max drawdown.
    p99_max_drawdown : float
        99th percentile of historical peak-to-trough max drawdown.
    var_95 : float
        Value at Risk at 95% confidence (worst 5% threshold).
    cvar_95 : float
        Conditional Value at Risk at 95% confidence (Expected Shortfall).
    var_99 : float
        Value at Risk at 99% confidence (worst 1% threshold).
    cvar_99 : float
        Conditional Value at Risk at 99% confidence (Expected Shortfall).
    pass_ci : tuple[float, float]
        Wilson confidence interval for P(PASS).
    fail_ci : tuple[float, float]
        Wilson confidence interval for P(FAIL).
    timeout_ci : tuple[float, float]
        Wilson confidence interval for P(TIMEOUT).
    median_max_drawdown_ci : tuple[float, float] | None
        Bootstrap confidence interval for median max drawdown.
    p95_max_drawdown_ci : tuple[float, float] | None
        Bootstrap confidence interval for P95 max drawdown.
    p99_max_drawdown_ci : tuple[float, float] | None
        Bootstrap confidence interval for P99 max drawdown.
    se_probability_pass : float
        Standard error of P(PASS) estimate.
    smoothed_probability_pass : float
        Kernel-smoothed P(PASS) estimate.
    fres_score : float
        Funded Risk Efficiency Score.
    mc_result : MonteCarloResult
        Underlying Monte Carlo simulation result for full auditability.
    """

    risk_per_trade: float
    probability_pass: float
    probability_fail: float
    timeout_probability: float
    failure_probability_max_drawdown: float
    failure_probability_daily_loss: float
    failure_probability_max_drawdown_primary: float
    failure_probability_daily_loss_primary: float
    median_trades_to_pass: float
    mean_trades_to_pass: float
    median_max_drawdown: float
    p95_max_drawdown: float
    p99_max_drawdown: float
    var_95: float
    cvar_95: float
    var_99: float
    cvar_99: float
    pass_ci: tuple[float, float]
    fail_ci: tuple[float, float]
    timeout_ci: tuple[float, float]
    median_max_drawdown_ci: tuple[float, float] | None
    p95_max_drawdown_ci: tuple[float, float] | None
    p99_max_drawdown_ci: tuple[float, float] | None
    se_probability_pass: float
    smoothed_probability_pass: float
    fres_score: float
    mc_result: MonteCarloResult


@dataclass(frozen=True)
class OptimizationResult:
    """
    Comprehensive output of a risk optimization search across candidate risk levels.

    Attributes
    ----------
    config : OptimizationConfig
        The configuration used for the optimization run.
    rules_template : FundedAccountRules
        The account rules template (with original base risk).
    evaluations : tuple[RiskLevelEvaluation, ...]
        Ordered tuple of evaluations for each candidate risk level.
    optimal_risk_raw : float
        Risk level maximizing raw empirical P(PASS | r).
    optimal_risk_smoothed : float
        Risk level maximizing kernel-smoothed P(PASS | r).
    optimal_risk_fres : float
        Risk level maximizing the multi-objective FRES score.
    max_probability_pass_raw : float
        Highest empirical P(PASS).
    max_probability_pass_smoothed : float
        Highest smoothed P(PASS).
    max_fres_score : float
        Highest FRES score achieved.
    plausible_risk_levels : tuple[float, ...]
        Exact candidate levels not distinguishable from the raw empirical winner
        under simultaneous exact paired tests. The min/max fields are only the
        envelope of this tuple and need not imply every intervening candidate is
        plausible.
    risk_levels : np.ndarray
        Array of candidate risk levels (read-only).
    probabilities_pass : np.ndarray
        Array of empirical P(PASS) values (read-only).
    probabilities_pass_smoothed : np.ndarray
        Array of smoothed P(PASS) values (read-only).
    probabilities_fail : np.ndarray
        Array of P(FAIL) values (read-only).
    p95_drawdowns : np.ndarray
        Array of P95 max drawdowns (read-only).
    cvar_95_values : np.ndarray
        Array of CVaR_95 values (read-only).
    fres_scores : np.ndarray
        Array of FRES scores (read-only).
    """

    config: OptimizationConfig
    rules_template: FundedAccountRules
    evaluations: tuple[RiskLevelEvaluation, ...]
    optimal_risk_raw: float
    optimal_risk_smoothed: float
    optimal_risk_fres: float
    max_probability_pass_raw: float
    max_probability_pass_smoothed: float
    max_fres_score: float
    plausible_risk_levels: tuple[float, ...]
    plausible_risk_min: float
    plausible_risk_max: float

    # Read-only numpy arrays for vectorized querying and plotting
    risk_levels: np.ndarray = field(repr=False)
    probabilities_pass: np.ndarray = field(repr=False)
    probabilities_pass_smoothed: np.ndarray = field(repr=False)
    probabilities_fail: np.ndarray = field(repr=False)
    p95_drawdowns: np.ndarray = field(repr=False)
    cvar_95_values: np.ndarray = field(repr=False)
    fres_scores: np.ndarray = field(repr=False)

    def __post_init__(self):
        # Enforce read-only guarantees on exposed numpy arrays
        for arr_name in (
            "risk_levels",
            "probabilities_pass",
            "probabilities_pass_smoothed",
            "probabilities_fail",
            "p95_drawdowns",
            "cvar_95_values",
            "fres_scores",
        ):
            arr = getattr(self, arr_name)
            if isinstance(arr, np.ndarray):
                arr.flags.writeable = False


def _normalize_drawdown_risk(
    p95_drawdowns: np.ndarray,
    cvar_95_values: np.ndarray,
    drawdown_limit: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Express drawdown statistics as finite multiples of the account budget."""
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        p95_normalized = p95_drawdowns / drawdown_limit
        cvar_normalized = cvar_95_values / drawdown_limit
    if not np.all(np.isfinite(p95_normalized)) or not np.all(
        np.isfinite(cvar_normalized)
    ):
        raise ValueError("drawdown normalization overflowed float64 precision")
    return p95_normalized, cvar_normalized


# ---------------------------------------------------------------------------
# Main Optimization Engine
# ---------------------------------------------------------------------------


def optimize_risk_per_trade(
    rules: FundedAccountRules,
    synthetic_config: SyntheticConfig | None = None,
    trades: list[Trade] | None = None,
    assume_iid: bool = False,
    mc_config: MonteCarloConfig | None = None,
    opt_config: OptimizationConfig | None = None,
) -> OptimizationResult:
    """
    Perform full risk-per-trade grid search, smoothing, tail risk, and FRES optimization.

    Uses Common Random Numbers (CRN): every candidate risk level is evaluated
    against the exact same stream of simulated or resampled trade sequences.
    This isolates the effect of risk scaling and reduces the sampling variance
    of candidate differences; it does not remove Monte Carlo uncertainty.

    Parameters
    ----------
    rules : FundedAccountRules
        Base account rules configuration. The `risk_per_trade` field is replaced
        sequentially across candidate levels.
    synthetic_config : SyntheticConfig | None
        Configuration for synthetic parametric trade generation.
    trades : list[Trade] | None
        Observed trade sequence for empirical resampling.
    assume_iid : bool
        Must be explicitly True when providing `trades` to confirm IID assumption.
    mc_config : MonteCarloConfig | None
        Monte Carlo engine configuration (simulation count, seed, etc.).
    opt_config : OptimizationConfig | None
        Optimization grid, smoothing bandwidth, and FRES weight configuration.

    Returns
    -------
    OptimizationResult
        Immutable optimization result containing all candidate evaluations,
        tail risk metrics, smoothed estimates, and optimal risk levels.

    ASSUMPTION: Common Random Numbers (CRN) are used across candidate risk levels,
        and the same fixed simulation count is used for every candidate.
    JUSTIFICATION: Evaluating all candidate risk levels against the identical simulated
        trade sequences isolates the sensitivity of account survival to position sizing,
        reducing Monte Carlo sampling variance in candidate comparisons.
    FAILURE MODE: If simulations were independently seeded across levels, or adaptive
        stopping produced different sample sizes, sampling noise could artificially
        shift the argmax to an inferior risk level.
    ROBUST ALTERNATIVE: Independent random streams can be used with larger N and
        uncertainty intervals for unpaired candidate differences.
    """
    if opt_config is None:
        opt_config = OptimizationConfig()
    if mc_config is None:
        mc_config = MonteCarloConfig(confidence_level=opt_config.confidence_level)
    elif mc_config.confidence_level != opt_config.confidence_level:
        # OptimizationConfig is authoritative for all intervals reported by
        # one optimization result.
        mc_config = replace(mc_config, confidence_level=opt_config.confidence_level)

    if (synthetic_config is None) == (trades is None):
        raise ValueError("exactly one of synthetic_config or trades must be provided")
    if mc_config.n_simulations is None:
        raise ValueError(
            "risk optimization requires a fixed n_simulations so every candidate "
            "uses the same number of Common Random Number paths"
        )

    # CRN must also hold when callers request a non-deterministic run. Generate
    # one run-local master seed and reuse it for every candidate. If the synthetic
    # source already has a seed, preserve that reproducible stream.
    if mc_config.seed is None:
        if synthetic_config is not None and synthetic_config.seed is not None:
            master_seed = synthetic_config.seed
        else:
            master_seed = int(
                np.random.SeedSequence().generate_state(1, dtype=np.uint64)[0]
            )
        mc_config = replace(mc_config, seed=master_seed)

    risk_levels_tuple = opt_config.get_risk_levels()
    risk_levels_arr = np.array(risk_levels_tuple, dtype=np.float64)

    # 1. Run Monte Carlo simulations for each candidate risk level using CRN
    mc_results: list[MonteCarloResult] = []
    raw_pass_probs = []
    raw_fail_probs = []
    p95_dds = []
    cvar_95s = []
    var_95s = []
    cvar_99s = []
    var_99s = []

    for r in risk_levels_tuple:
        # Build derived rules with candidate risk
        candidate_rules = replace(rules, risk_per_trade=r)

        # Use identical mc_config (CRN: same seed across candidates)
        res = run_monte_carlo(
            rules=candidate_rules,
            synthetic_config=synthetic_config,
            trades=trades,
            assume_iid=assume_iid,
            config=mc_config,
        )
        mc_results.append(res)

        # Standard tail risk calculations:
        # VaR_95 / CVaR_95 are strictly at alpha=0.95 (worst 5%)
        # VaR_99 / CVaR_99 are strictly at alpha=0.99 (worst 1%)
        v95, cv95 = compute_var_cvar(res.max_drawdowns_historical, alpha=0.95)
        v99, cv99 = compute_var_cvar(res.max_drawdowns_historical, alpha=0.99)

        raw_pass_probs.append(res.probability_pass)
        raw_fail_probs.append(res.probability_fail)
        p95_dds.append(res.p95_max_drawdown)
        var_95s.append(v95)
        cvar_95s.append(cv95)
        var_99s.append(v99)
        cvar_99s.append(cv99)

    raw_pass_arr = np.array(raw_pass_probs, dtype=np.float64)
    raw_fail_arr = np.array(raw_fail_probs, dtype=np.float64)
    p95_dd_arr = np.array(p95_dds, dtype=np.float64)
    cvar_95_arr = np.array(cvar_95s, dtype=np.float64)

    # 2. Kernel smoothing on P(PASS | r)
    # Derive bandwidth dynamically from the evaluated grid spacing if not specified
    if opt_config.smoothing_bandwidth is not None:
        bandwidth = opt_config.smoothing_bandwidth
    else:
        if len(risk_levels_arr) > 1:
            median_step = float(np.median(np.diff(risk_levels_arr)))
            bandwidth = 2.0 * median_step
        else:
            # Smoothing a single point is the identity; the positive value is
            # only needed to satisfy the public smoother's input contract.
            bandwidth = 1.0

    smoothed_pass_arr = smooth_curve_gaussian(
        risk_levels_arr, raw_pass_arr, bandwidth=bandwidth
    )

    # 3. FRES calculation
    # Normalize relative to the maximum allowable account drawdown limit so
    # DD == limit maps to 1.0. Do not clip: an overshoot above the funded-account
    # budget is worse than merely reaching the limit and FRES must retain that
    # tail-severity information.
    dd_limit = rules.max_drawdown_pct
    dd_p95_norm, cvar_norm = _normalize_drawdown_risk(
        p95_dd_arr, cvar_95_arr, dd_limit
    )

    with np.errstate(over="ignore", invalid="ignore"):
        fres_arr = (
            raw_pass_arr
            - opt_config.fres_lambda * raw_fail_arr
            - opt_config.fres_gamma * dd_p95_norm
            - opt_config.fres_delta * cvar_norm
        )
    if not np.all(np.isfinite(fres_arr)):
        raise ValueError("FRES calculation overflowed float64 precision")

    # 4. Construct RiskLevelEvaluation objects
    evaluations: list[RiskLevelEvaluation] = []
    for i, r in enumerate(risk_levels_tuple):
        res = mc_results[i]
        eval_item = RiskLevelEvaluation(
            risk_per_trade=r,
            probability_pass=res.probability_pass,
            probability_fail=res.probability_fail,
            timeout_probability=res.timeout_probability,
            failure_probability_max_drawdown=res.failure_probability_max_drawdown,
            failure_probability_daily_loss=res.failure_probability_daily_loss,
            failure_probability_max_drawdown_primary=res.failure_probability_max_drawdown_primary,
            failure_probability_daily_loss_primary=res.failure_probability_daily_loss_primary,
            median_trades_to_pass=res.median_trades_to_pass,
            mean_trades_to_pass=res.mean_trades_to_pass,
            median_max_drawdown=res.median_max_drawdown,
            p95_max_drawdown=res.p95_max_drawdown,
            p99_max_drawdown=res.p99_max_drawdown,
            var_95=var_95s[i],
            cvar_95=cvar_95s[i],
            var_99=var_99s[i],
            cvar_99=cvar_99s[i],
            pass_ci=res.pass_ci,
            fail_ci=res.fail_ci,
            timeout_ci=res.timeout_ci,
            median_max_drawdown_ci=res.median_max_drawdown_ci,
            p95_max_drawdown_ci=res.p95_max_drawdown_ci,
            p99_max_drawdown_ci=res.p99_max_drawdown_ci,
            se_probability_pass=res.se_probability_pass,
            smoothed_probability_pass=float(smoothed_pass_arr[i]),
            fres_score=float(fres_arr[i]),
            mc_result=res,
        )
        evaluations.append(eval_item)

    # 5. Determine optimal risk levels
    idx_raw = int(np.argmax(raw_pass_arr))
    idx_smooth = int(np.argmax(smoothed_pass_arr))
    idx_fres = int(np.argmax(fres_arr))

    opt_raw = risk_levels_tuple[idx_raw]
    opt_smooth = risk_levels_tuple[idx_smooth]
    opt_fres = risk_levels_tuple[idx_fres]

    max_p_raw = float(raw_pass_arr[idx_raw])
    max_p_smooth = float(smoothed_pass_arr[idx_smooth])
    max_fres = float(fres_arr[idx_fres])

    # Uncertainty-aware candidate set. CRN makes pass indicators path-aligned,
    # so compare each candidate with the raw empirical winner using the exact
    # conditional sign/McNemar test on discordant path outcomes. This avoids the
    # false certainty of a normal standard error equal to zero when a small
    # sample happens to contain only one direction of discordance. Bonferroni
    # correction over both directions of every possible candidate pair protects
    # the post-selection comparison at no less than the configured family-wise
    # confidence level. Retention means insufficient evidence of inferiority,
    # not proof that the candidates are equivalent.
    best_pass = mc_results[idx_raw].terminal_codes == CODE_PROFIT_TARGET
    n_candidates = len(risk_levels_tuple)
    n_pairs = max(1, n_candidates * (n_candidates - 1) // 2)
    alpha_family = 1.0 - opt_config.confidence_level
    alpha_directional = alpha_family / (2.0 * n_pairs)
    plausible_indices_list: list[int] = []
    for i, result in enumerate(mc_results):
        candidate_pass = result.terminal_codes == CODE_PROFIT_TARGET
        best_only = int(np.count_nonzero(best_pass & ~candidate_pass))
        candidate_only = int(np.count_nonzero(~best_pass & candidate_pass))
        discordant = best_only + candidate_only
        if discordant == 0:
            plausible_indices_list.append(i)
            continue

        # Under equal pass probabilities, either member of a discordant pair is
        # the passing candidate with probability 1/2. The survival function is
        # the exact one-sided probability of observing at least best_only wins.
        p_value = float(sp_stats.binom.sf(best_only - 1, discordant, 0.5))
        if p_value > alpha_directional:
            plausible_indices_list.append(i)

    # The empirical winner always has a zero paired gap and therefore belongs.
    plausible_levels = tuple(
        risk_levels_tuple[i] for i in plausible_indices_list
    )
    plausible_min = float(min(plausible_levels))
    plausible_max = float(max(plausible_levels))

    return OptimizationResult(
        config=opt_config,
        rules_template=rules,
        evaluations=tuple(evaluations),
        optimal_risk_raw=opt_raw,
        optimal_risk_smoothed=opt_smooth,
        optimal_risk_fres=opt_fres,
        max_probability_pass_raw=max_p_raw,
        max_probability_pass_smoothed=max_p_smooth,
        max_fres_score=max_fres,
        plausible_risk_levels=plausible_levels,
        plausible_risk_min=plausible_min,
        plausible_risk_max=plausible_max,
        risk_levels=risk_levels_arr,
        probabilities_pass=raw_pass_arr,
        probabilities_pass_smoothed=smoothed_pass_arr,
        probabilities_fail=raw_fail_arr,
        p95_drawdowns=p95_dd_arr,
        cvar_95_values=cvar_95_arr,
        fres_scores=fres_arr,
    )


# ---------------------------------------------------------------------------
# FRES Sensitivity
# ---------------------------------------------------------------------------


def compute_fres_sensitivity(
    opt_result: OptimizationResult,
    lambdas: Sequence[float] = (0.5, 1.0, 1.5, 2.0),
    gammas: Sequence[float] = (0.0, 0.5, 1.0, 1.5),
    deltas: Sequence[float] = (0.0, 0.5, 1.0, 1.5),
) -> dict[tuple[float, float, float], float]:
    """
    Compute optimal risk level r*_FRES across a spectrum of risk-aversion weights.

    Parameters
    ----------
    opt_result : OptimizationResult
        Pre-computed optimization result.
    lambdas : Sequence[float]
        Sequence of failure penalty weights lambda >= 0.
    gammas : Sequence[float]
        Sequence of P95 drawdown penalty weights gamma >= 0.
    deltas : Sequence[float]
        Sequence of CVaR95 penalty weights delta >= 0.

    Returns
    -------
    dict[tuple[float, float, float], float]
        Mapping (lambda, gamma, delta) -> optimal_risk_fres.
    """
    dd_limit = opt_result.rules_template.max_drawdown_pct
    dd_p95_norm, cvar_norm = _normalize_drawdown_risk(
        opt_result.p95_drawdowns, opt_result.cvar_95_values, dd_limit
    )
    pass_probs = opt_result.probabilities_pass
    fail_probs = opt_result.probabilities_fail
    risk_levels = opt_result.risk_levels

    def _validate_weights(name: str, values: Sequence[float]) -> tuple[float, ...]:
        if isinstance(values, (str, bytes)):
            raise ValueError(f"{name} must be a non-empty sequence of weights")
        try:
            materialized = tuple(values)
        except TypeError as exc:
            raise ValueError(f"{name} must be a non-empty sequence of weights") from exc
        if not materialized:
            raise ValueError(f"{name} must not be empty")

        validated: list[float] = []
        for value in materialized:
            if (
                isinstance(value, (bool, np.bool_))
                or not isinstance(value, Real)
                or not isfinite(value)
                or value < 0.0
            ):
                raise ValueError(
                    f"{name} weights must be finite and non-negative, got {value!r}"
                )
            validated.append(float(value))
        return tuple(validated)

    valid_lambdas = _validate_weights("lambdas", lambdas)
    valid_gammas = _validate_weights("gammas", gammas)
    valid_deltas = _validate_weights("deltas", deltas)

    sensitivity: dict[tuple[float, float, float], float] = {}

    for lam in valid_lambdas:
        for gam in valid_gammas:
            for dlt in valid_deltas:
                with np.errstate(over="ignore", invalid="ignore"):
                    fres_scores = (
                        pass_probs
                        - lam * fail_probs
                        - gam * dd_p95_norm
                        - dlt * cvar_norm
                    )
                if not np.all(np.isfinite(fres_scores)):
                    raise ValueError(
                        "FRES sensitivity calculation overflowed float64 precision "
                        f"for weights {(lam, gam, dlt)!r}"
                    )
                best_idx = int(np.argmax(fres_scores))
                sensitivity[(float(lam), float(gam), float(dlt))] = float(risk_levels[best_idx])

    return sensitivity

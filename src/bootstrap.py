"""FARS 1.2 Phase 10A bootstrap framework.

Implements ``FARS_1_2_PHASE_10_BOOTSTRAP.md`` v6 exactly:

- IID Bootstrap and Circular Block Bootstrap (CBB) resampling engines.
- Temporal-dependence diagnostics (rank-portmanteau Ljung-Box, runs test,
  heuristic regime screens, descriptive raw-R ACF) with a
  Bonferroni-controlled family and a three-state eligibility classifier. Per
  v5 section 0.2, dependence tests (valid under the IID null) take
  classification precedence over regime screens, whose p-values assume
  within-group independence.
- V6 replaces raw-return Ljung-Box inputs with Gaussian rank scores and their
  squares. This rank-portmanteau construction preserves temporal order while
  avoiding the severe finite-sample size distortion of raw squared returns
  under highly skewed IID marginals.
- 95% uncertainty intervals for expectancy, win rate, and standard deviation.
  Per v5 section 0.3, the IID standard-deviation interval is studentized
  (bootstrap-t with plug-in delta-method SE); percentile intervals undercover
  variance-type estimands under skew.

These are estimator-uncertainty statements at the observed sample size n, never
forecasts. CBB intervals are exploratory: stationary short-memory dependence is
assumed, not established. No result contains NaN or infinity; undefined
quantities use the ``not_estimable`` contract with a stable reason code.
"""

from __future__ import annotations

import math
from typing import Any

import arch
import numpy as np
import scipy
from arch.bootstrap import optimal_block_length
from scipy import stats as sp_stats

from src.ingestion import CanonicalTradeDataset

ALGORITHM_VERSION = "fars-1.2-phase10a-v6"
RESULT_SCHEMA_VERSION = "fars-1.2-bootstrap-result-v1"

STATE_IID = "iid_eligible"
STATE_DEPENDENT = "dependent_resampling_candidate"
STATE_UNSUPPORTED = "unsupported_or_inconclusive"

VALIDITY_IID = "conditional_on_approximate_iid"
VALIDITY_DEPENDENT = "exploratory_dependent"
VALIDITY_UNSUPPORTED = "unsupported"

MIN_N_DIAGNOSTICS = 50
ALPHA_FAMILY = 0.05
CONFIDENCE_LEVEL = 0.95
QUANTILE_METHOD = "linear"
QUANTILE_LO = 0.025
QUANTILE_HI = 0.975
DISCREPANCY_CENTER_FRACTION = 0.25
BLOCK_LENGTH_REPORT_SIGNIFICANT_DIGITS = 12

_ESTIMAND_ORDER = ("expectancy", "win_rate", "std")
_REQUIRED_CAPABILITIES = ("core_metrics", "temporal_analysis")


# ---------------------------------------------------------------------------
# Estimators (observed point estimates)
# ---------------------------------------------------------------------------


def _estimate(name: str, r: np.ndarray) -> float:
    with np.errstate(over="raise", invalid="raise", divide="raise"):
        if name == "expectancy":
            value = float(np.mean(r))
        elif name == "win_rate":
            value = float(np.mean(r > 0.0))
        elif name == "std":
            value = float(np.std(r, ddof=1))
        else:
            raise ValueError(f"unknown estimand {name!r}")
    if not math.isfinite(value):
        raise FloatingPointError(f"non-finite {name} point estimate")
    return value


def _influence_series(name: str, r: np.ndarray) -> np.ndarray:
    """Estimand-specific influence series for block-length selection (6.2)."""
    with np.errstate(over="raise", invalid="raise", divide="raise"):
        r_bar = float(np.mean(r))
        if name == "expectancy":
            influence = r - r_bar
        elif name == "win_rate":
            w = (r > 0.0).astype(np.float64)
            influence = w - float(np.mean(w))
        elif name == "std":
            squared_deviations = np.square(r - r_bar)
            m2 = float(np.mean(squared_deviations))
            if not math.isfinite(m2) or m2 <= 0.0:
                raise FloatingPointError("invalid second moment for std influence")
            influence = (squared_deviations - m2) / (2.0 * math.sqrt(m2))
        else:
            raise ValueError(f"unknown estimand {name!r}")
    if not bool(np.all(np.isfinite(influence))) or _is_constant(influence):
        raise FloatingPointError(f"invalid influence series for {name}")
    return influence


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def _acf(x: np.ndarray, max_lag: int) -> list[float]:
    """Unadjusted sample autocorrelation for k = 1..max_lag (section 5.2)."""
    with np.errstate(over="raise", invalid="raise", divide="raise"):
        xc = x - float(np.mean(x))
        denom = float(np.sum(np.square(xc)))
        if not math.isfinite(denom) or denom <= 0.0:
            raise FloatingPointError("invalid ACF denominator")
        values = [
            float(np.sum(xc[k:] * xc[:-k]) / denom)
            for k in range(1, max_lag + 1)
        ]
    if not all(math.isfinite(value) for value in values):
        raise FloatingPointError("non-finite ACF")
    return values


def _ljung_box(x: np.ndarray, n: int, lags: list[int]) -> dict[int, tuple[float, float]]:
    acf = _acf(x, max(lags))
    out: dict[int, tuple[float, float]] = {}
    for h in lags:
        q = n * (n + 2) * sum(acf[k - 1] ** 2 / (n - k) for k in range(1, h + 1))
        p = float(sp_stats.chi2.sf(q, df=h))
        if not (math.isfinite(q) and math.isfinite(p)):
            raise FloatingPointError("non-finite Ljung-Box statistic")
        out[h] = (q, p)
    return out


def _is_constant(x: np.ndarray) -> bool:
    return bool(np.all(x == x[0]))


def _normal_scores(x: np.ndarray) -> np.ndarray:
    """Van der Waerden scores from average empirical ranks (v6 section 0.9)."""
    ranks = sp_stats.rankdata(x, method="average")
    probabilities = (ranks - 0.5) / x.shape[0]
    scores = np.asarray(sp_stats.norm.ppf(probabilities), dtype=np.float64)
    if not bool(np.all(np.isfinite(scores))):
        raise FloatingPointError("non-finite normal scores")
    return scores


_DEPENDENCE_KINDS = ("ljung_box", "runs")
_REGIME_KINDS = ("welch", "levene")


def _classify(
    tests: list[dict[str, Any]], alpha_b: float
) -> tuple[str, list[str], list[str]]:
    """Three-state eligibility classification (v5 section 0.2).

    Dependence tests (Ljung-Box, runs) are valid under the IID null and take
    precedence: any dependence rejection means the regime screens' p-values
    are uninterpretable, so they are demoted to descriptive evidence and
    cannot veto. Regime screens decide only when no dependence test rejects,
    which is exactly where their within-group-independence assumption holds.
    Returns (state, reasons, rejecting_ids).
    """
    rejecting = [t["id"] for t in tests if t["p_value"] < alpha_b]
    dep = [
        t["id"]
        for t in tests
        if t["p_value"] < alpha_b and t["kind"] in _DEPENDENCE_KINDS
    ]
    regime = [
        t["id"]
        for t in tests
        if t["p_value"] < alpha_b and t["kind"] in _REGIME_KINDS
    ]
    if dep:
        reasons = ["dependence_detected"]
        if regime:
            reasons.append("regime_rejection_descriptive_only")
        return STATE_DEPENDENT, reasons, rejecting
    if regime:
        return STATE_UNSUPPORTED, ["structural_change_evidence"], rejecting
    return STATE_IID, [], rejecting


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def analyze_bootstrap(
    dataset: CanonicalTradeDataset,
    *,
    master_seed: int,
    B: int = 2000,
) -> dict[str, Any]:
    """Run the Phase 10A pipeline on one accepted CanonicalTradeDataset.

    The result is a plain dict (JSON-serializable) whose structure is defined
    by the Phase 10A v6 design. No value in it is NaN or infinite.
    """
    if isinstance(master_seed, bool) or not isinstance(master_seed, int):
        raise TypeError("master_seed must be an integer")
    if master_seed < 0:
        raise ValueError("master_seed must be non-negative")
    if isinstance(B, bool) or not isinstance(B, int) or B < 2000:
        raise ValueError("B must be an integer >= 2000")

    # RNG tree: exactly three children in fixed estimand order, always spawned.
    root = np.random.SeedSequence(master_seed)
    children = root.spawn(3)
    generators = {
        name: np.random.Generator(np.random.PCG64(child))
        for name, child in zip(_ESTIMAND_ORDER, children)
    }
    rng_record = {
        "master_entropy": master_seed,
        "spawn_keys": [list(child.spawn_key) for child in children],
        "bit_generator": "PCG64",
        "B": B,
        "estimand_order": list(_ESTIMAND_ORDER),
    }

    r = np.asarray([trade.r_result for trade in dataset.trades], dtype=np.float64)
    n = int(r.shape[0])

    limitations: list[str] = []
    timestamps = [trade.timestamp for trade in dataset.trades]
    if all(ts is not None for ts in timestamps) and any(
        timestamps[i] == timestamps[i - 1] for i in range(1, n)
    ):
        limitations.append("equal_timestamp_order_retained")

    labels = {
        "assets": sorted({t.asset for t in dataset.trades if t.asset}),
        "strategies": sorted({t.strategy for t in dataset.trades if t.strategy}),
    }

    provenance = {
        "source_sha256": dataset.provenance.source_sha256,
        "schema_version": dataset.provenance.schema_version,
        "resolved_mapping": dict(dataset.provenance.resolved_mapping),
        "capabilities": {
            name: {
                "available": bool(status.available),
                "reasons": list(status.reasons),
            }
            for name, status in dataset.capabilities.items()
        },
        "versions": {
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "arch": arch.__version__,
        },
        "algorithm_version": ALGORITHM_VERSION,
    }

    diagnostics: dict[str, Any] = {
        "n": n,
        "tests": [],
        "omissions": [],
        "acf": None,
        "alpha_family": ALPHA_FAMILY,
        "m": None,
        "alpha_b": None,
    }

    def unsupported(reasons: list[str], rejecting: list[str] | None = None) -> dict[str, Any]:
        return {
            "schema_version": RESULT_SCHEMA_VERSION,
            "eligibility": {
                "state": STATE_UNSUPPORTED,
                "reasons": reasons,
                "rejecting_tests": list(rejecting or []),
            },
            "diagnostics": diagnostics,
            "estimands": {
                name: {
                    "status": "not_estimable",
                    "value": None,
                    "reason": reasons[0],
                    "validity": VALIDITY_UNSUPPORTED,
                    "intervals": [],
                    "interval_omissions": [],
                    "block_length": None,
                    "warnings": [],
                }
                for name in _ESTIMAND_ORDER
            },
            "rng": rng_record,
            "parameters": {
                "B": B,
                "confidence_level": CONFIDENCE_LEVEL,
                "quantile_method": QUANTILE_METHOD,
                "alpha_family": ALPHA_FAMILY,
                "min_n_diagnostics": MIN_N_DIAGNOSTICS,
                "block_length_report_significant_digits": (
                    BLOCK_LENGTH_REPORT_SIGNIFICANT_DIGITS
                ),
            },
            "provenance": provenance,
            "labels": labels,
            "limitations": limitations,
        }

    # Section 2: capability contract (inspected, not raised).
    missing = [
        f"capability_unavailable:{name}"
        for name in _REQUIRED_CAPABILITIES
        if not dataset.capabilities[name].available
    ]
    if missing:
        return unsupported(missing)

    # Section 5.1: numeric preparation.
    if n < MIN_N_DIAGNOSTICS:
        return unsupported(["insufficient_sample_for_diagnostics"])
    if not bool(np.all(np.isfinite(r))):
        return unsupported(["diagnostic_numeric_failure:non_finite_r"])
    if _is_constant(r):
        return unsupported(["zero_variance_r"])
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            variance_r = float(np.var(r))
    except FloatingPointError:
        return unsupported(["diagnostic_numeric_failure:variance_r"])
    if not math.isfinite(variance_r) or variance_r <= 0.0:
        return unsupported(["diagnostic_numeric_failure:variance_r"])

    lags_max = min(math.ceil(10 * math.log10(n)), n - 1)
    l1 = min(10, lags_max)
    big_h = sorted({l1, lags_max})

    tests: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []

    # V6 section 0.9 / effective section 5.3: rank-portmanteau tests on
    # Gaussian scores and their squares. Temporal order is unchanged.
    try:
        normal_scores = _normal_scores(r)
        normal_scores_squared = np.square(normal_scores)
    except (FloatingPointError, OverflowError, ValueError):
        return unsupported(["diagnostic_numeric_failure:normal_scores"])
    for series_name, x in (
        ("normal_score", normal_scores),
        ("normal_score_squared", normal_scores_squared),
    ):
        if _is_constant(x):
            omissions.append(
                {
                    "id": f"ljung_box_{series_name}",
                    "reason": "not_applicable_constant_transform",
                }
            )
            continue
        try:
            ljung_box_results = _ljung_box(x, n, big_h)
        except (FloatingPointError, OverflowError, ValueError, ZeroDivisionError):
            diagnostics["tests"] = tests
            diagnostics["omissions"] = omissions
            return unsupported([f"diagnostic_numeric_failure:ljung_box_{series_name}"])
        for lag, (q, p) in ljung_box_results.items():
            tests.append(
                {
                    "id": f"ljung_box_{series_name}_h{lag}",
                    "kind": "ljung_box",
                    "series": series_name,
                    "lag": lag,
                    "statistic": q,
                    "df": lag,
                    "p_value": p,
                }
            )

    # Section 5.4: runs test on sign(r); zero-R trades are non-wins.
    w = r > 0.0
    n1 = int(np.sum(w))
    n0 = n - n1
    var_r = -1.0
    mu_r = 0.0
    if n1 > 0 and n0 > 0:
        mu_r = 1.0 + 2.0 * n1 * n0 / n
        var_r = 2.0 * n1 * n0 * (2.0 * n1 * n0 - n) / (n**2 * (n - 1))
    if n1 == 0 or n0 == 0 or var_r <= 0.0:
        omissions.append(
            {"id": "runs_test", "reason": "not_applicable_single_outcome_class"}
        )
    else:
        n_runs = 1 + int(np.sum(w[1:] != w[:-1]))
        z_r = (n_runs - mu_r) / math.sqrt(var_r)
        p_runs = float(2.0 * sp_stats.norm.sf(abs(z_r)))
        if not (math.isfinite(z_r) and math.isfinite(p_runs)):
            diagnostics["tests"] = tests
            diagnostics["omissions"] = omissions
            return unsupported(["diagnostic_numeric_failure:runs_test"])
        tests.append(
            {
                "id": "runs_test",
                "kind": "runs",
                "statistic": z_r,
                "runs": n_runs,
                "p_value": p_runs,
            }
        )

    # Partial diagnostics stay visible even if a regime screen fails below.
    diagnostics["tests"] = tests
    diagnostics["omissions"] = omissions

    # Section 5.5: regime screens (heuristic; eight p-values). Per v5 section
    # 0.5, a screen whose transformed group is exactly constant is omitted as
    # not_applicable_constant_transform instead of running an invalid
    # zero-variance Welch/Levene call (SciPy warnings, non-finite results).
    half = n // 2
    third = n // 3
    split_pairs = {
        "halves": (r[:half], r[half:]),
        "thirds": (r[:third], r[-third:]),
    }
    for split_name, (a, b) in split_pairs.items():
        for transform_name, ta, tb in (
            ("level", a, b),
            ("abs", np.abs(a), np.abs(b)),
        ):
            screen_ids = (
                f"regime_welch_{transform_name}_{split_name}",
                f"regime_levene_{transform_name}_{split_name}",
            )
            if _is_constant(ta) or _is_constant(tb):
                for test_id in screen_ids:
                    omissions.append(
                        {"id": test_id, "reason": "not_applicable_constant_transform"}
                    )
                continue
            try:
                welch_result = sp_stats.ttest_ind(
                    ta,
                    tb,
                    equal_var=False,
                    nan_policy="raise",
                    alternative="two-sided",
                )
            except (FloatingPointError, OverflowError, ValueError, ZeroDivisionError):
                diagnostics["tests"] = tests
                diagnostics["omissions"] = omissions
                return unsupported([f"diagnostic_numeric_failure:{screen_ids[0]}"])
            candidates: list[tuple[str, str, Any]] = [
                (screen_ids[0], "welch", welch_result)
            ]
            # Levene's statistic divides by the within-group sum of squared
            # absolute median deviations; it is degenerate (0/0) when BOTH
            # groups' deviations are exactly constant (e.g. two-point data
            # tied around the median). Omit instead of executing an invalid
            # test (v5 section 0.5).
            z_a = np.abs(ta - np.median(ta))
            z_b = np.abs(tb - np.median(tb))
            if _is_constant(z_a) and _is_constant(z_b):
                omissions.append(
                    {"id": screen_ids[1], "reason": "not_applicable_constant_transform"}
                )
            else:
                try:
                    levene_result = sp_stats.levene(
                        ta, tb, center="median", nan_policy="raise"
                    )
                except (
                    FloatingPointError,
                    OverflowError,
                    ValueError,
                    ZeroDivisionError,
                ):
                    diagnostics["tests"] = tests
                    diagnostics["omissions"] = omissions
                    return unsupported([f"diagnostic_numeric_failure:{screen_ids[1]}"])
                candidates.append((screen_ids[1], "levene", levene_result))
            for test_id, kind, res in candidates:
                statistic = float(res.statistic)
                p_value = float(res.pvalue)
                if not (math.isfinite(statistic) and math.isfinite(p_value)):
                    return unsupported([f"diagnostic_numeric_failure:{test_id}"])
                tests.append(
                    {
                        "id": test_id,
                        "kind": kind,
                        "statistic": statistic,
                        "p_value": p_value,
                    }
                )

    # Section 5.2: descriptive ACF for r (never a classification input).
    try:
        acf_values = _acf(r, lags_max)
    except (FloatingPointError, OverflowError, ValueError, ZeroDivisionError):
        return unsupported(["diagnostic_numeric_failure:acf_r"])
    diagnostics["acf"] = {
        "series": "r",
        "values": acf_values,
        "bound": 1.96 / math.sqrt(n),
    }
    diagnostics["lags"] = big_h

    # Section 5.6 as revised by v5 section 0.2: multiplicity, classification.
    m = len(tests)
    alpha_b = ALPHA_FAMILY / m
    diagnostics["m"] = m
    diagnostics["alpha_b"] = alpha_b
    state, state_reasons, rejecting = _classify(tests, alpha_b)
    if state == STATE_UNSUPPORTED:
        return unsupported(state_reasons, rejecting)

    # Sections 6-7: resampling and intervals.
    validity = VALIDITY_IID if state == STATE_IID else VALIDITY_DEPENDENT
    estimands: dict[str, Any] = {}
    for name, gen in generators.items():
        entry: dict[str, Any] = {
            "status": "estimable",
            "value": None,
            "reason": None,
            "validity": validity,
            "intervals": [],
            "interval_omissions": [],
            "block_length": None,
            "warnings": [],
        }
        estimands[name] = entry

        # Per-estimand conditions (sections 5.4 and 7).
        if name == "win_rate" and (n1 == 0 or n0 == 0):
            entry.update(status="not_estimable", reason="single_outcome_class")
            continue
        try:
            theta_hat = _estimate(name, r)
        except (FloatingPointError, OverflowError, ValueError, ZeroDivisionError):
            entry.update(
                status="not_estimable",
                reason="point_estimate_numeric_failure",
                value=None,
            )
            continue
        entry["value"] = theta_hat

        if state == STATE_DEPENDENT:
            try:
                influence = _influence_series(name, r)
                with np.errstate(over="raise", invalid="raise", divide="raise"):
                    l_hat = float(
                        optimal_block_length(influence).loc[0, "circular"]
                    )
                if not math.isfinite(l_hat):
                    raise FloatingPointError("non-finite block length")
            except Exception as exc:  # noqa: BLE001 - selector failure is per-estimand
                entry.update(
                    status="not_estimable",
                    reason="block_length_selection_failed",
                    value=None,
                )
                entry["block_length"] = {
                    "influence_series": f"u_{name}",
                    "selector_value_reported": None,
                    "final": None,
                    "k": None,
                    "error": str(exc),
                }
                continue
            block = min(n, max(1, int(math.ceil(l_hat))))
            # Vector dot products inside arch can vary by a final binary ULP
            # across repeated calls on the same locked platform. Preserve the
            # unrounded value for the method's ceil() decision, but canonicalize
            # the reported diagnostic so the public result is bit-reproducible.
            l_hat_reported = float(
                format(l_hat, f".{BLOCK_LENGTH_REPORT_SIGNIFICANT_DIGITS}g")
            )
            entry["block_length"] = {
                "influence_series": f"u_{name}",
                "selector_value_reported": l_hat_reported,
                "final": block,
                "k": math.ceil(n / block),
            }
        else:
            block = None

        if state == STATE_IID:
            idx = gen.integers(0, n, size=(B, n))
        else:
            assert block is not None
            k = entry["block_length"]["k"]
            starts = gen.integers(0, n, size=(B, k))
            offsets = np.arange(block)
            idx = ((starts[:, :, None] + offsets) % n).reshape(B, k * block)[:, :n]
        resamples = r[idx]
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                if name == "expectancy":
                    theta_star = resamples.mean(axis=1)
                elif name == "win_rate":
                    theta_star = (resamples > 0.0).mean(axis=1)
                else:
                    theta_star = resamples.std(axis=1, ddof=1)
        except FloatingPointError:
            entry.update(
                status="not_estimable",
                reason="resampling_numeric_failure",
                value=None,
            )
            continue
        if not bool(np.all(np.isfinite(theta_star))):
            entry.update(
                status="not_estimable",
                reason="resampling_numeric_failure",
                value=None,
            )
            continue

        q_lo, q_hi = (
            float(v)
            for v in np.quantile(theta_star, [QUANTILE_LO, QUANTILE_HI],
                                 method=QUANTILE_METHOD)
        )
        if not (math.isfinite(q_lo) and math.isfinite(q_hi)):
            entry.update(
                status="not_estimable",
                reason="resampling_numeric_failure",
                value=None,
            )
            continue
        if name == "std" and state == STATE_IID:
            # V5 section 0.3: the IID standard-deviation interval is
            # studentized (bootstrap-t); percentile undercovers variance-type
            # estimands under skew. CBB std remains percentile/exploratory.
            _add_studentized_std(entry, theta_star, resamples, r, theta_hat,
                                 validity)
        else:
            entry["intervals"].append(
                {"method": "percentile", "lower": q_lo, "upper": q_hi,
                 "validity": validity}
            )
        if name == "expectancy":
            basic_lower = 2.0 * theta_hat - q_hi
            basic_upper = 2.0 * theta_hat - q_lo
            if not (math.isfinite(basic_lower) and math.isfinite(basic_upper)):
                entry.update(
                    status="not_estimable",
                    reason="interval_numeric_failure",
                    value=None,
                    intervals=[],
                )
                continue
            entry["intervals"].append(
                {
                    "method": "basic",
                    "lower": basic_lower,
                    "upper": basic_upper,
                    "validity": validity,
                }
            )
            width = q_hi - q_lo
            mid_p = 0.5 * (q_lo + q_hi)
            mid_b = 2.0 * theta_hat - mid_p
            if abs(mid_p - mid_b) > DISCREPANCY_CENTER_FRACTION * width:
                entry["warnings"].append("interval_method_discrepancy")
            if state == STATE_IID:
                _add_bca(entry, theta_star, theta_hat, r, B, validity)

    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "eligibility": {
            "state": state,
            "reasons": state_reasons,
            "rejecting_tests": rejecting,
        },
        "diagnostics": diagnostics,
        "estimands": estimands,
        "rng": rng_record,
        "parameters": {
            "B": B,
            "confidence_level": CONFIDENCE_LEVEL,
            "quantile_method": QUANTILE_METHOD,
            "alpha_family": ALPHA_FAMILY,
            "min_n_diagnostics": MIN_N_DIAGNOSTICS,
            "block_length_report_significant_digits": (
                BLOCK_LENGTH_REPORT_SIGNIFICANT_DIGITS
            ),
        },
        "provenance": provenance,
        "labels": labels,
        "limitations": limitations,
    }


def _add_bca(
    entry: dict[str, Any],
    theta_star: np.ndarray,
    theta_hat: float,
    r: np.ndarray,
    B: int,
    validity: str,
) -> None:
    """BCa interval for IID expectancy (section 7), with exact failure codes."""
    count = int(np.sum(theta_star < theta_hat))
    if count == 0 or count == B:
        entry["interval_omissions"].append(
            {"method": "bca", "reason": "bca_z0_undefined"}
        )
        return
    z0 = float(sp_stats.norm.ppf(count / B))

    n = r.shape[0]
    total = float(np.sum(r))
    theta_j = (total - r) / (n - 1)  # leave-one-out means
    d = float(np.mean(theta_j)) - theta_j
    denom = 6.0 * float(np.sum(d**2)) ** 1.5
    if denom == 0.0 or not math.isfinite(denom):
        entry["interval_omissions"].append(
            {"method": "bca", "reason": "bca_acceleration_undefined"}
        )
        return
    a_hat = float(np.sum(d**3)) / denom

    adjusted: list[float] = []
    for alpha in (QUANTILE_LO, QUANTILE_HI):
        z_a = float(sp_stats.norm.ppf(alpha))
        adj_denom = 1.0 - a_hat * (z0 + z_a)
        if adj_denom == 0.0 or not math.isfinite(adj_denom):
            entry["interval_omissions"].append(
                {"method": "bca", "reason": "bca_adjustment_denominator_invalid"}
            )
            return
        p_adj = float(sp_stats.norm.cdf(z0 + (z0 + z_a) / adj_denom))
        if not 0.0 < p_adj < 1.0:
            entry["interval_omissions"].append(
                {"method": "bca", "reason": "bca_level_out_of_range"}
            )
            return
        adjusted.append(p_adj)

    lo, hi = (
        float(v)
        for v in np.quantile(theta_star, adjusted, method=QUANTILE_METHOD)
    )
    entry["intervals"].append(
        {"method": "bca", "lower": lo, "upper": hi, "validity": validity,
         "z0": z0, "acceleration": a_hat}
    )


def _plug_in_se_sd(x: np.ndarray) -> np.ndarray:
    """Plug-in delta-method SE of the sample SD along the last axis (v5 0.3).

    Var(s^2) ≈ (mu4 - sigma^4)/n by the CLT for sample moments; the delta
    method for the square root gives Var(s) ≈ (mu4 - sigma^4)/(4 sigma^2 n).
    Moments are plug-in (ddof=0). Overflow surfaces as non-finite output and
    is handled by the caller's validity checks, so warnings are suppressed.
    """
    n = x.shape[-1]
    with np.errstate(over="ignore", invalid="ignore"):
        mean = np.mean(x, axis=-1, keepdims=True)
        d2 = np.square(x - mean)
        v = np.mean(d2, axis=-1)
        m4 = np.mean(np.square(d2), axis=-1)
        return np.sqrt(np.maximum(m4 - np.square(v), 0.0) / (4.0 * v * n))


def _add_studentized_std(
    entry: dict[str, Any],
    theta_star: np.ndarray,
    resamples: np.ndarray,
    r: np.ndarray,
    theta_hat: float,
    validity: str,
) -> None:
    """Studentized (bootstrap-t) interval for the IID std estimand (v5 0.3).

    Replicates with non-finite or zero studentizing SE are excluded from the
    t* distribution and counted. The interval is omitted with reason
    ``studentization_failed`` when the observed SE is not positive finite or
    fewer than B/2 valid replicates remain (e.g. symmetric two-point data).
    """
    B = int(theta_star.shape[0])
    se_hat = float(_plug_in_se_sd(r))
    if not (math.isfinite(se_hat) and se_hat > 0.0):
        entry["interval_omissions"].append(
            {"method": "studentized", "reason": "studentization_failed"}
        )
        return
    se_star = _plug_in_se_sd(resamples)
    valid_se = np.isfinite(se_star) & (se_star > 0.0)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        t_all = (theta_star - theta_hat) / se_star
    valid = valid_se & np.isfinite(t_all)
    n_valid = int(np.sum(valid))
    if 2 * n_valid < B:
        entry["interval_omissions"].append(
            {"method": "studentized", "reason": "studentization_failed"}
        )
        return
    t_star = t_all[valid]
    t_lo, t_hi = (
        float(v)
        for v in np.quantile(t_star, [QUANTILE_LO, QUANTILE_HI],
                             method=QUANTILE_METHOD)
    )
    lower = theta_hat - t_hi * se_hat
    upper = theta_hat - t_lo * se_hat
    if not (math.isfinite(lower) and math.isfinite(upper) and lower <= upper):
        entry["interval_omissions"].append(
            {"method": "studentized", "reason": "studentization_failed"}
        )
        return
    entry["intervals"].append(
        {
            "method": "studentized",
            "lower": lower,
            "upper": upper,
            "validity": validity,
            "se": se_hat,
            "replicates_valid": n_valid,
            "replicates_excluded": B - n_valid,
        }
    )
    if n_valid < B:
        entry["warnings"].append("studentized_replicates_excluded")

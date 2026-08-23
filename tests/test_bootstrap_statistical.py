"""Independent statistical acceptance tests for Phase 10A design v6.

Slow operating-characteristics validation: false-positive rate, dependence
detection power, and end-to-end IID interval coverage. The consumed v4/v5 roots
are not reused: v6 uses a confirmation root derived from a predeclared SHA-256
label. Generators, sizes, and thresholds remain frozen.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from scipy import stats as sp_stats

from src.bootstrap import (
    STATE_DEPENDENT,
    STATE_IID,
    VALIDITY_IID,
    analyze_bootstrap,
)
from src.ingestion import load_trade_csv

CONFIRMATION_SEED_LABEL = b"FARS-1.2-phase10a-v6-independent-confirmation"
CONFIRMATION_SEED_SHA256 = hashlib.sha256(CONFIRMATION_SEED_LABEL).hexdigest()
ROOT_ENTROPY = int.from_bytes(
    hashlib.sha256(CONFIRMATION_SEED_LABEL).digest()[:8], "big"
)

FP_M, FP_N = 1000, 250
POWER_M, POWER_N = 500, 500
COV_M, COV_N, COV_B = 500, 250, 2000

_T0 = datetime(2023, 1, 2, tzinfo=timezone.utc)


def _dataset_from_values(tmp_path, values, tag):
    rows = ["timestamp,r_result"]
    for i, v in enumerate(values):
        ts = (_T0 + timedelta(hours=i)).isoformat()
        rows.append(f"{ts},{float(v)!r}")
    path = tmp_path / f"trades_{tag}.csv"
    path.write_text("\n".join(rows), encoding="utf-8")
    return load_trade_csv(path, outcomes_finalized=True)


def _suite_children(suite_name, *, root_entropy=ROOT_ENTROPY):
    """Fixed seed tree: root -> suites -> generators -> replicates -> leaves."""
    order = ("false_positive", "power", "coverage")
    root = np.random.SeedSequence(root_entropy)
    return root.spawn(3)[order.index(suite_name)]


def _replicate_leaves(suite_seed, gen_index, n_generators, m):
    gen_seed = suite_seed.spawn(n_generators)[gen_index]
    rep_seeds = gen_seed.spawn(m)
    return [rs.spawn(2) for rs in rep_seeds]  # [data_generation, analysis]


# ---------------------------------------------------------------------------
# Generators (section 9.2 / 9.3)
# ---------------------------------------------------------------------------


def _gen_normal(rng, n):
    return rng.standard_normal(n)


def _gen_t5(rng, n):
    return rng.standard_t(5, n) / math.sqrt(5.0 / 3.0)


def _gen_lognormal(rng, n):
    return rng.lognormal(0.0, 0.75, n) - math.exp(0.75**2 / 2.0)


def _gen_discrete(rng, n):
    return rng.choice([-1.0, 0.0, 2.0], size=n, p=[0.45, 0.10, 0.45])


def _gen_ar1(rng, n):
    phi = 0.3
    out = np.empty(n)
    out[0] = rng.normal(0.0, 1.0 / math.sqrt(1.0 - phi**2))
    for t in range(1, n):
        out[t] = phi * out[t - 1] + rng.standard_normal()
    return out


def _gen_sign_markov(rng, n):
    signs = np.empty(n)
    signs[0] = rng.choice([-1.0, 1.0])
    keep = rng.uniform(size=n - 1) < 0.70
    flips = np.where(keep, 1.0, -1.0)
    signs[1:] = signs[0] * np.cumprod(flips)
    return signs * rng.exponential(1.0, n)


def _gen_garch(rng, n):
    omega, alpha, beta = 0.05, 0.10, 0.85
    sigma2 = omega / (1.0 - alpha - beta)
    out = np.empty(n)
    eps_prev2 = sigma2
    z = rng.standard_normal(n)
    for t in range(n):
        sigma2 = omega + alpha * eps_prev2 + beta * sigma2
        out[t] = math.sqrt(sigma2) * z[t]
        eps_prev2 = out[t] ** 2
    return out


FP_GENERATORS = (_gen_normal, _gen_t5, _gen_lognormal, _gen_discrete)
POWER_GENERATORS = (_gen_ar1, _gen_sign_markov, _gen_garch)

_FP_BOUND = float(sp_stats.binom.ppf(0.999, FP_M, 0.05))


@pytest.mark.statistical
@pytest.mark.parametrize("gen_index", range(len(FP_GENERATORS)))
def test_false_positive_rate(tmp_path, gen_index):
    leaves = _replicate_leaves(
        _suite_children("false_positive"), gen_index, len(FP_GENERATORS), FP_M
    )
    gen = FP_GENERATORS[gen_index]
    k = 0
    for i, (data_seed, analysis_seed) in enumerate(leaves):
        values = gen(np.random.Generator(np.random.PCG64(data_seed)), FP_N)
        dataset = _dataset_from_values(tmp_path, values, f"fp{gen_index}_{i}")
        analysis_rng = np.random.Generator(np.random.PCG64(analysis_seed))
        result = analyze_bootstrap(
            dataset, master_seed=int(analysis_rng.integers(0, 2**31))
        )
        if result["eligibility"]["state"] != STATE_IID:
            k += 1
    assert k <= _FP_BOUND, f"false-positive count {k} exceeds bound {_FP_BOUND}"


@pytest.mark.statistical
@pytest.mark.parametrize("gen_index", range(len(POWER_GENERATORS)))
def test_dependence_detection_power(tmp_path, gen_index):
    leaves = _replicate_leaves(
        _suite_children("power"), gen_index, len(POWER_GENERATORS), POWER_M
    )
    gen = POWER_GENERATORS[gen_index]
    detected = 0
    regime_rejections = 0
    for i, (data_seed, analysis_seed) in enumerate(leaves):
        values = gen(np.random.Generator(np.random.PCG64(data_seed)), POWER_N)
        dataset = _dataset_from_values(tmp_path, values, f"pw{gen_index}_{i}")
        analysis_rng = np.random.Generator(np.random.PCG64(analysis_seed))
        result = analyze_bootstrap(
            dataset, master_seed=int(analysis_rng.integers(0, 2**31))
        )
        eligibility = result["eligibility"]
        if eligibility["state"] == STATE_DEPENDENT:
            detected += 1
        alpha_b = result["diagnostics"]["alpha_b"]
        rejecting_regime = [
            test["id"]
            for test in result["diagnostics"]["tests"]
            if test["kind"] in ("welch", "levene")
            and test["p_value"] < alpha_b
        ]
        if rejecting_regime:
            regime_rejections += 1
            if eligibility["state"] == STATE_DEPENDENT:
                assert "regime_rejection_descriptive_only" in eligibility["reasons"]
    assert detected >= 0.80 * POWER_M, (
        f"detection {detected}/{POWER_M} below 80% for {gen.__name__}; "
        f"regime screens rejected descriptively in {regime_rejections}/{POWER_M}"
    )


# ---------------------------------------------------------------------------
# Section 9.4: IID end-to-end coverage
# ---------------------------------------------------------------------------

_COV_TARGETS = {
    "normal": {
        "expectancy": 0.0,
        "win_rate": 0.5,
        "std": 1.0,
    },
    "lognormal": {
        "expectancy": 0.0,
        "win_rate": 1.0 - float(sp_stats.norm.cdf(0.375)),
        "std": math.sqrt((math.exp(0.75**2) - 1.0) * math.exp(0.75**2)),
    },
    "discrete": {
        "expectancy": 0.45,
        "win_rate": 0.45,
        "std": math.sqrt(2.0475),
    },
}
_COV_GENERATORS = {
    "normal": _gen_normal,
    "lognormal": _gen_lognormal,
    "discrete": _gen_discrete,
}
# Methods whose coverage is certified under IID per estimand.
_COV_METHODS = {
    "expectancy": ("percentile", "basic", "bca"),
    "win_rate": ("percentile",),
    "std": ("studentized",),
}


@pytest.mark.statistical
@pytest.mark.parametrize("gen_name", ["normal", "lognormal", "discrete"])
def test_iid_end_to_end_coverage(tmp_path, gen_name):
    gen_index = ["normal", "lognormal", "discrete"].index(gen_name)
    leaves = _replicate_leaves(
        _suite_children("coverage"), gen_index, len(_COV_GENERATORS), COV_M
    )
    gen = _COV_GENERATORS[gen_name]
    targets = _COV_TARGETS[gen_name]

    emitted = {(est, m): 0 for est, ms in _COV_METHODS.items() for m in ms}
    covered = {(est, m): 0 for est, ms in _COV_METHODS.items() for m in ms}

    for i, (data_seed, analysis_seed) in enumerate(leaves):
        values = gen(np.random.Generator(np.random.PCG64(data_seed)), COV_N)
        dataset = _dataset_from_values(tmp_path, values, f"cov{gen_index}_{i}")
        analysis_rng = np.random.Generator(np.random.PCG64(analysis_seed))
        result = analyze_bootstrap(
            dataset, master_seed=int(analysis_rng.integers(0, 2**31)), B=COV_B
        )
        for estimand, methods in _COV_METHODS.items():
            entry = result["estimands"][estimand]
            for iv in entry["intervals"]:
                if iv["validity"] != VALIDITY_IID or iv["method"] not in methods:
                    continue
                key = (estimand, iv["method"])
                emitted[key] += 1
                if iv["lower"] <= targets[estimand] <= iv["upper"]:
                    covered[key] += 1

    for (estimand, method), count in emitted.items():
        assert count >= 0.90 * COV_M, (
            f"{gen_name}/{estimand}/{method}: only {count}/{COV_M} IID intervals"
        )
        rate = covered[(estimand, method)] / count
        assert 0.90 <= rate <= 0.99, (
            f"{gen_name}/{estimand}/{method}: coverage {rate:.3f} outside "
            f"[0.90, 0.99] ({covered[(estimand, method)]}/{count})"
        )

"""Candidate v6 calibration: rank-portmanteau size and power.

Both roots used here have already been consumed (v4 calibration and failed v5
confirmation). Results may guide a v6 design but cannot confirm it.
"""

from __future__ import annotations

import math
import sys
from collections import Counter

import numpy as np
from scipy import stats as sp_stats

sys.path.insert(0, ".")
from research.experiments.phase10_calibration.experiment1_classification import (  # noqa: E402
    pvalues,
)
from src.bootstrap import _is_constant, _ljung_box  # noqa: E402
from tests.test_bootstrap_statistical import (  # noqa: E402
    FP_GENERATORS,
    FP_M,
    FP_N,
    POWER_GENERATORS,
    POWER_M,
    POWER_N,
    _replicate_leaves,
    _suite_children,
)

ALPHA_FAMILY = 0.05
CALIBRATION_ROOTS = (20260821, 14070111912601187797)


def candidate_pvalues(r: np.ndarray) -> dict[str, float]:
    """V5 family with raw/r-squared Ljung-Box replaced by rank scores."""
    out = {
        name: value
        for name, value in pvalues(r).items()
        if not name.startswith("lb_")
    }
    n = r.shape[0]
    lags_max = min(math.ceil(10 * math.log10(n)), n - 1)
    lags = sorted({min(10, lags_max), lags_max})
    for name, values in (
        ("rank_r", sp_stats.rankdata(r, method="average")),
        ("rank_abs_r", sp_stats.rankdata(np.abs(r), method="average")),
    ):
        if _is_constant(values):
            continue
        for lag, (_q, p) in _ljung_box(values, n, lags).items():
            out[f"lb_{name}_h{lag}"] = p
    return out


def classify(pv: dict[str, float]) -> str:
    alpha_b = ALPHA_FAMILY / len(pv)
    rejecting = {name for name, p in pv.items() if p < alpha_b}
    dependence = {
        name for name in rejecting if name.startswith(("lb_", "runs"))
    }
    regime = rejecting - dependence
    if dependence:
        return "dependent"
    if regime:
        return "unsupported"
    return "iid"


def evaluate_root(root_entropy: int) -> None:
    print(f"\n=== root {root_entropy} ===")
    for gen_index, generator in enumerate(FP_GENERATORS):
        leaves = _replicate_leaves(
            _suite_children("false_positive", root_entropy=root_entropy),
            gen_index,
            len(FP_GENERATORS),
            FP_M,
        )
        counts: Counter[str] = Counter()
        for data_seed, _analysis_seed in leaves:
            r = generator(np.random.Generator(np.random.PCG64(data_seed)), FP_N)
            counts[classify(candidate_pvalues(r))] += 1
        print(f"FP {generator.__name__:18s} {dict(counts)}")

    for gen_index, generator in enumerate(POWER_GENERATORS):
        leaves = _replicate_leaves(
            _suite_children("power", root_entropy=root_entropy),
            gen_index,
            len(POWER_GENERATORS),
            POWER_M,
        )
        counts = Counter()
        for data_seed, _analysis_seed in leaves:
            r = generator(np.random.Generator(np.random.PCG64(data_seed)), POWER_N)
            counts[classify(candidate_pvalues(r))] += 1
        print(f"POWER {generator.__name__:15s} {dict(counts)}")


def main() -> None:
    for root_entropy in CALIBRATION_ROOTS:
        evaluate_root(root_entropy)


if __name__ == "__main__":
    main()

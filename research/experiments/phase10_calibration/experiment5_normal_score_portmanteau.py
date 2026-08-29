"""Candidate v6 calibration: normal-score rank portmanteau size and power.

Maps empirical ranks to finite Gaussian scores, then applies Ljung-Box to the
scores and their squares. Both roots are already consumed and are calibration
only; a selected revision still requires a new confirmation root.
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
from research.experiments.phase10_calibration.experiment4_rank_portmanteau import (  # noqa: E402
    ALPHA_FAMILY,
    CALIBRATION_ROOTS,
    classify,
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


def _normal_scores(values: np.ndarray) -> np.ndarray:
    ranks = sp_stats.rankdata(values, method="average")
    probabilities = (ranks - 0.5) / values.shape[0]
    return sp_stats.norm.ppf(probabilities)


def candidate_pvalues(r: np.ndarray) -> dict[str, float]:
    """V5 family with raw diagnostics replaced by Gaussian rank scores."""
    out = {
        name: value
        for name, value in pvalues(r).items()
        if not name.startswith("lb_")
    }
    n = r.shape[0]
    lags_max = min(math.ceil(10 * math.log10(n)), n - 1)
    lags = sorted({min(10, lags_max), lags_max})
    z = _normal_scores(r)
    for name, values in (("normal_score", z), ("normal_score_squared", z * z)):
        if _is_constant(values):
            continue
        for lag, (_q, p) in _ljung_box(values, n, lags).items():
            out[f"lb_{name}_h{lag}"] = p
    return out


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
    assert ALPHA_FAMILY == 0.05
    for root_entropy in CALIBRATION_ROOTS:
        evaluate_root(root_entropy)


if __name__ == "__main__":
    main()

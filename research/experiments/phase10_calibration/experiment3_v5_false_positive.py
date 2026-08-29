"""Decompose the failed v5 independent LogNormal false-positive result.

Uses the consumed v5 confirmation root only for diagnosis. Its output must not
be reused to accept a revised algorithm; any revision needs a new predeclared
confirmation root.
"""

from __future__ import annotations

import sys
from collections import Counter

import numpy as np

sys.path.insert(0, ".")
from research.experiments.phase10_calibration.experiment1_classification import pvalues
from tests.test_bootstrap_statistical import (
    FP_GENERATORS,
    FP_M,
    FP_N,
    _replicate_leaves,
    _suite_children,
)

ALPHA_FAMILY = 0.05
LOGNORMAL_INDEX = 2
V5_CONFIRMATION_ROOT_ENTROPY = 14070111912601187797


def main() -> None:
    leaves = _replicate_leaves(
        _suite_children(
            "false_positive", root_entropy=V5_CONFIRMATION_ROOT_ENTROPY
        ),
        LOGNORMAL_INDEX,
        len(FP_GENERATORS),
        FP_M,
    )
    generator = FP_GENERATORS[LOGNORMAL_INDEX]
    classifications: Counter[str] = Counter()
    rejecting_tests: Counter[str] = Counter()
    rejection_patterns: Counter[tuple[str, ...]] = Counter()

    for data_seed, _analysis_seed in leaves:
        r = generator(np.random.Generator(np.random.PCG64(data_seed)), FP_N)
        pv = pvalues(r)
        alpha_b = ALPHA_FAMILY / len(pv)
        rejecting = tuple(sorted(name for name, p in pv.items() if p < alpha_b))
        rejection_patterns[rejecting] += 1
        rejecting_tests.update(rejecting)

        dependence = tuple(
            name for name in rejecting if name.startswith(("lb_", "runs"))
        )
        regime = tuple(
            name for name in rejecting if name.startswith(("welch_", "levene_"))
        )
        if dependence:
            classifications["dependent"] += 1
        elif regime:
            classifications["unsupported"] += 1
        else:
            classifications["iid"] += 1

    print(f"root_entropy={V5_CONFIRMATION_ROOT_ENTROPY}")
    print(f"classifications={dict(classifications)}")
    print(f"rejecting_tests={dict(rejecting_tests.most_common())}")
    print("most_common_patterns=")
    for pattern, count in rejection_patterns.most_common(12):
        print(f"  {count:4d} {pattern}")


if __name__ == "__main__":
    main()

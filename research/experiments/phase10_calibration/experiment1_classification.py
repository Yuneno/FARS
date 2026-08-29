"""Phase 10A diagnosis, experiment 1: classification decomposition.

Uses the EXACT v4 seed tree and generators from tests/test_bootstrap_statistical.py
(power suite, M=500, n=500) but computes diagnostics only (no resampling), then
compares v4 classification order (regime veto first) vs candidate v5 order
(dependence precedence), with identical test family and alpha_b.

Diagnostic aid for the v5 design revision. Not part of the test suite.
"""

from __future__ import annotations

import math
import sys
from collections import Counter

import numpy as np
from scipy import stats as sp_stats

sys.path.insert(0, ".")
from src.bootstrap import _acf, _is_constant, _ljung_box  # noqa: E402
from tests.test_bootstrap_statistical import (  # noqa: E402
    POWER_GENERATORS,
    POWER_M,
    POWER_N,
    _replicate_leaves,
    _suite_children,
)

ALPHA_FAMILY = 0.05
CALIBRATION_ROOT_ENTROPY = 20260821


def pvalues(r: np.ndarray) -> dict[str, float]:
    """All Phase 10A v4/v5 raw-score p-values for one dataset."""
    n = r.shape[0]
    out: dict[str, float] = {}
    r2 = np.square(r)
    lags_max = min(math.ceil(10 * math.log10(n)), n - 1)
    l1 = min(10, lags_max)
    big_h = sorted({l1, lags_max})
    for series_name, x in (("r", r), ("r2", r2)):
        if _is_constant(x):
            continue
        for lag, (_q, p) in _ljung_box(x, n, big_h).items():
            out[f"lb_{series_name}_h{lag}"] = p
    w = r > 0.0
    n1 = int(np.sum(w))
    n0 = n - n1
    if n1 > 0 and n0 > 0:
        mu_r = 1.0 + 2.0 * n1 * n0 / n
        var_r = 2.0 * n1 * n0 * (2.0 * n1 * n0 - n) / (n**2 * (n - 1))
        if var_r > 0.0:
            n_runs = 1 + int(np.sum(w[1:] != w[:-1]))
            z = (n_runs - mu_r) / math.sqrt(var_r)
            out["runs"] = float(2.0 * sp_stats.norm.sf(abs(z)))
    half, third = n // 2, n // 3
    pairs = {"halves": (r[:half], r[half:]), "thirds": (r[:third], r[-third:])}
    for sname, (a, b) in pairs.items():
        for tname, ta, tb in (("level", a, b), ("abs", np.abs(a), np.abs(b))):
            out[f"welch_{tname}_{sname}"] = float(
                sp_stats.ttest_ind(ta, tb, equal_var=False).pvalue
            )
            out[f"levene_{tname}_{sname}"] = float(
                sp_stats.levene(ta, tb, center="median").pvalue
            )
    return out


def main() -> None:
    for gen_index, gen in enumerate(POWER_GENERATORS):
        leaves = _replicate_leaves(
            _suite_children(
                "power", root_entropy=CALIBRATION_ROOT_ENTROPY
            ),
            gen_index,
            len(POWER_GENERATORS),
            POWER_M,
        )
        v4 = Counter()
        v5 = Counter()
        rej_counts = Counter()
        for data_seed, _analysis_seed in leaves:
            r = gen(np.random.Generator(np.random.PCG64(data_seed)), POWER_N)
            pv = pvalues(r)
            m = len(pv)
            alpha_b = ALPHA_FAMILY / m
            rejecting = {k for k, p in pv.items() if p < alpha_b}
            for k in rejecting:
                rej_counts[k] += 1
            regime_rej = {k for k in rejecting if k.startswith(("welch_", "levene_"))}
            dep_rej = rejecting - regime_rej
            # v4: regime veto first
            if regime_rej:
                v4["unsupported"] += 1
            elif dep_rej:
                v4["dependent"] += 1
            else:
                v4["iid"] += 1
            # v5 candidate: dependence precedence, same family/alpha_b
            if dep_rej:
                v5["dependent"] += 1
            elif regime_rej:
                v5["unsupported"] += 1
            else:
                v5["iid"] += 1
        print(f"\n=== {gen.__name__} (M={POWER_M}, n={POWER_N}) ===")
        print(f"v4 order: {dict(v4)}  detection={v4['dependent']/POWER_M:.3f}")
        print(f"v5 order: {dict(v5)}  detection={v5['dependent']/POWER_M:.3f}")
        top = ", ".join(f"{k}:{c}" for k, c in rej_counts.most_common(8))
        print(f"top rejecting tests: {top}")


if __name__ == "__main__":
    main()

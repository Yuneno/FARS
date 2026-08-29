"""Phase 10A diagnosis, experiment 2: std interval methods under skew.

Exact v4 calibration fixtures (root 20260821, M=500, n=250, B=2000).
For each IID-classified dataset, one shared set of B bootstrap replicates is
used to compare three 95% interval constructions for the sample SD (ddof=1):

- percentile (v4 contract)
- BCa (jackknife acceleration via exact leave-one-out sufficient statistics)
- studentized (bootstrap-t, delta-method SE with 4th-moment estimate)

Emission uses the v4 classification order; for IID generators the emitted set
is identical under the v5 dependence-precedence order (any dependence or
regime rejection excludes the dataset either way).

Diagnostic aid for the v5 design revision. Not part of the test suite.
"""

from __future__ import annotations

import math
import sys

import numpy as np
from scipy import stats as sp_stats

sys.path.insert(0, ".")
from tests.test_bootstrap_statistical import (  # noqa: E402
    COV_B,
    COV_M,
    COV_N,
    _COV_GENERATORS,
    _COV_TARGETS,
    _replicate_leaves,
    _suite_children,
)
from research.experiments.phase10_calibration.experiment1_classification import (  # noqa: E402
    pvalues,
)

ALPHA_FAMILY = 0.05
Q_LO, Q_HI = 0.025, 0.975
CALIBRATION_ROOT_ENTROPY = 20260821


def classify_iid(r: np.ndarray) -> bool:
    pv = pvalues(r)
    alpha_b = ALPHA_FAMILY / len(pv)
    return not any(p < alpha_b for p in pv.values())


def loo_std(r: np.ndarray) -> np.ndarray:
    """Exact leave-one-out sample SD (ddof=1) via sufficient statistics."""
    n = r.shape[0]
    s = float(np.sum(r))
    q = float(np.sum(r * r))
    sj = s - r
    qj = q - r * r
    var_j = (qj - sj * sj / (n - 1.0)) / (n - 2.0)
    return np.sqrt(np.maximum(var_j, 0.0))


def intervals_for_dataset(r: np.ndarray, rng: np.random.Generator):
    n = r.shape[0]
    s_hat = float(np.std(r, ddof=1))
    idx = rng.integers(0, n, size=(COV_B, n))
    rs = r[idx]
    s_star = rs.std(axis=1, ddof=1)

    out: dict[str, tuple[float, float] | None] = {}

    # percentile
    q_lo, q_hi = np.quantile(s_star, [Q_LO, Q_HI], method="linear")
    out["percentile"] = (float(q_lo), float(q_hi))

    # BCa
    count = int(np.sum(s_star < s_hat))
    if 0 < count < COV_B:
        z0 = float(sp_stats.norm.ppf(count / COV_B))
        theta_j = loo_std(r)
        d = float(np.mean(theta_j)) - theta_j
        denom = 6.0 * float(np.sum(d**2)) ** 1.5
        if denom > 0.0 and math.isfinite(denom):
            a_hat = float(np.sum(d**3)) / denom
            adj = []
            ok = True
            for alpha in (Q_LO, Q_HI):
                z_a = float(sp_stats.norm.ppf(alpha))
                adj_denom = 1.0 - a_hat * (z0 + z_a)
                if adj_denom == 0.0 or not math.isfinite(adj_denom):
                    ok = False
                    break
                p_adj = float(sp_stats.norm.cdf(z0 + (z0 + z_a) / adj_denom))
                if not 0.0 < p_adj < 1.0:
                    ok = False
                    break
                adj.append(p_adj)
            if ok:
                lo, hi = np.quantile(s_star, adj, method="linear")
                out["bca"] = (float(lo), float(hi))
            else:
                out["bca"] = None
        else:
            out["bca"] = None
    else:
        out["bca"] = None

    # studentized (delta-method SE, plug-in 4th moment)
    def se_sd(x: np.ndarray, axis=None):
        m = np.mean(x, axis=axis, keepdims=True)
        d2 = (x - m) ** 2
        v = np.mean(d2, axis=axis)
        m4 = np.mean(d2**2, axis=axis)
        return np.sqrt(np.maximum(m4 - v * v, 0.0) / (4.0 * v * x.shape[-1]))

    se_hat = float(se_sd(r))
    if se_hat > 0.0:
        se_star = se_sd(rs, axis=1)
        valid = se_star > 0.0
        t_star = (s_star[valid] - s_hat) / se_star[valid]
        t_lo, t_hi = np.quantile(t_star, [Q_LO, Q_HI], method="linear")
        out["studentized"] = (s_hat - float(t_hi) * se_hat, s_hat - float(t_lo) * se_hat)
    else:
        out["studentized"] = None

    return out


def main() -> None:
    for gen_index, gen_name in enumerate(["normal", "lognormal", "discrete"]):
        gen = _COV_GENERATORS[gen_name]
        target = _COV_TARGETS[gen_name]["std"]
        leaves = _replicate_leaves(
            _suite_children(
                "coverage", root_entropy=CALIBRATION_ROOT_ENTROPY
            ),
            gen_index,
            len(_COV_GENERATORS),
            COV_M,
        )
        emitted = 0
        cov = {"percentile": [0, 0], "bca": [0, 0], "studentized": [0, 0]}
        for data_seed, analysis_seed in leaves:
            r = gen(np.random.Generator(np.random.PCG64(data_seed)), COV_N)
            if not classify_iid(r):
                continue
            emitted += 1
            analysis_rng = np.random.Generator(np.random.PCG64(analysis_seed))
            master_seed = int(analysis_rng.integers(0, 2**31))
            std_seed = np.random.SeedSequence(master_seed).spawn(3)[2]
            std_rng = np.random.Generator(np.random.PCG64(std_seed))
            ivs = intervals_for_dataset(r, std_rng)
            for method, iv in ivs.items():
                if iv is None:
                    continue
                cov[method][0] += 1
                if iv[0] <= target <= iv[1]:
                    cov[method][1] += 1
        print(f"\n=== {gen_name}: target std={target:.6f}, emitted {emitted}/{COV_M} ===")
        for method, (em, c) in cov.items():
            rate = c / em if em else float("nan")
            flag = "OK" if 0.90 <= rate <= 0.99 else "FAIL"
            print(f"  {method:12s} coverage {c}/{em} = {rate:.4f}  [{flag}]")


if __name__ == "__main__":
    main()

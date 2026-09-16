"""Carril 2 — Screening de recalibracion SMC-FVG por mercado (preregistrado).

Escalera de min_risk_pts por mercado (MNQ/MYM/MGC), walk-forward 36/6/6 OOS,
costes por_tramo, CI bootstrap CBB, gates de screening + marca Bonferroni.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, "E:/FARS-LAB/FARS")
from lab_artifacts.e7_protocol.run_e7_multimercado import (  # noqa: E402
    RAW_DIR,
    gen_trades,
    load_market_csv,
)
from lab_artifacts.run_d_protocol import (  # noqa: E402
    CALIBRATION_BARS_COUNT,
    MNQ,
    ZIP_PATH,
    SmcFvgStrategy,
    WalkForwardPlan,
    load_canonical_m5,
    run_backtest,
    smc_fvg_config,
)
from src.backtest.markets import MGC, MYM  # noqa: E402

BASE = Path(__file__).resolve().parent
PREREG = BASE / "preregistro_recalibracion.json"
LADDER = [2.0, 3.0, 5.0, 8.0, 10.0, 15.0, 20.0, 30.0]
POR_TRAMO = dict(commission_per_side=0.62, slippage_points=0.25, time_exit_slippage_points=0.25)
SEED, B, BLOCK = 20260729, 2000, 10
ALPHA_BONF = 0.05 / (3 * len(LADDER))


def gen(bars, market, min_risk):
    plan = WalkForwardPlan.create_calendar_rolling(bars, train_months=36, test_months=6, step_months=6)
    cfg = smc_fvg_config(market=market, discrete_partial_contracts=True, initial_balance=50000.0, **POR_TRAMO)
    trades = []
    for fold in plan.folds:
        cal = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT):fold.test_start_idx]
        test_bars = bars[fold.test_start_idx:fold.test_end_idx]
        res = run_backtest(test_bars, SmcFvgStrategy(min_risk_pts=min_risk), cfg, calibration_bars=cal)
        for t in res.trades:
            trades.append(dataclasses.replace(t, trade_id=f"fold_{fold.fold_id}_{t.trade_id}"))
    return trades, plan


def cbb_ci(r: np.ndarray, b: int = B, block: int = BLOCK, alpha: float = 0.05, seed: int = SEED) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    n = len(r)
    means = np.empty(b)
    for k in range(b):
        # circular block bootstrap
        starts = rng.integers(0, n, size=max(1, n // block + 1))
        idx = np.concatenate([np.arange(s, s + block) % n for s in starts])[:n]
        means[k] = r[idx].mean()
    lo, hi = np.percentile(means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def main() -> None:
    started = datetime.now(timezone.utc).isoformat()
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    assert prereg["metadata"]["preregistered_at_utc"] < started, "preregistro debe preceder a la corrida"

    mnq_bars, _ = load_canonical_m5(ZIP_PATH)
    markets = {"MNQ": (mnq_bars, MNQ), "MYM": (load_market_csv("MYM", RAW_DIR / "MYM_M5.csv"), MYM),
               "MGC": (load_market_csv("MGC", RAW_DIR / "MGC_M5.csv"), MGC)}

    rows = []
    print(f"{'mercado':>6} {'minR':>5} {'n':>6} {'E[R]':>8} {'PF':>7} {'WR%':>6} {'CI lo':>8} {'CI hi':>8} {'folds+':>7} {'bonf':>5}")
    for sym, (bars, market) in markets.items():
        for mr in LADDER:
            trades, plan = gen(bars, market, mr)
            n = len(trades)
            r = np.array([float(t.r_result) for t in trades]) if n else np.array([])
            # folds positivos (por fold, usando los prefijos fold_N_)
            if n:
                fold_means = {}
                for t, ri in zip(trades, r):
                    fid = t.trade_id.split("_")[1]
                    fold_means.setdefault(fid, []).append(ri)
                fr = np.array([np.mean(v) for v in fold_means.values()])
                folds_pos = (fr > 0).mean()
            else:
                folds_pos = 0.0
            if n >= 50:
                lo, hi = cbb_ci(r, alpha=0.05)
                lo_b, hi_b = cbb_ci(r, alpha=ALPHA_BONF)
            else:
                lo, hi, lo_b, hi_b = float("nan"), float("nan"), float("nan"), float("nan")
            er = float(r.mean()) if n else float("nan")
            wins = r > 0; losses = r < 0
            pf = (r[wins].sum() / -r[losses].sum()) if losses.any() and wins.any() else float("nan")
            wr = float(wins.mean() * 100) if n else float("nan")
            rows.append({"market": sym, "min_risk": mr, "n": n, "e_r": round(er, 4) if n else None,
                         "pf": round(pf, 4) if n else None, "wr": round(wr, 2) if n else None,
                         "ci": [round(lo, 4), round(hi, 4)], "folds_pos": round(float(folds_pos), 3),
                         "bonferroni_edge": bool((not np.isnan(lo_b)) and lo_b > 0)})
            print(f"{sym:>6} {mr:>5} {n:>6} {er:>+8.4f} {pf:>7.3f} {wr:>5.1f}% {lo:>+8.4f} {hi:>+8.4f} {folds_pos*100:>6.0f}% {'SI' if (not np.isnan(lo)) and lo > 0 else 'no'}", flush=True)

    out = BASE / "screening.json"
    out.write_text(json.dumps({"metadata": {"title": "Carril 2 screening", "ran_at_utc": started,
                                            "preregistered_at_utc": prereg["metadata"]["preregistered_at_utc"],
                                            "alpha_bonferroni": ALPHA_BONF},
                              "rows": rows}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n[escrito] {out}")


if __name__ == "__main__":
    main()

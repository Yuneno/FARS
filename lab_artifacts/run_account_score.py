"""FARS E1 — Puntuador formal por OBJETIVO DE CUENTA (Hermes).

Criterio (E1): toda config candidata se puntua con el motor de cuenta Apex 25K —
P(pase), quema y bloqueadas en 30 dias con trailing — no solo con E[R]/PF/DD.
El ranking de cuenta es el yardstick de promocion; los gates estadisticos (IC,
PBO, regime) siguen siendo la higiene anti-sobreajuste.

Uso:
  python lab_artifacts/run_account_score.py [--min-risks 5,8,10,15,20,30] [--risk 0.004671] [--sims 1500]

Modelo: trades cerrados (rapido, cota optimista para RANKEAR). La campeona se
re-verifica despues con el motor FULL (MAE intrabar) via 06_verificacion_5p0_full.py.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, "E:/FARS-LAB/FARS")
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

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "lab_artifacts" / "account_score"
ACCOUNT, TARGET, DD, LOCK = 25000.0, 1500.0, 1500.0, 100.0
SAFETY, MAX_MICROS = 0.75, 20
SEED = 20260729


def gen_trades(bars, plan, cfg, min_risk: float) -> list:
    trades = []
    for fold in plan.folds:
        cal = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT):fold.test_start_idx]
        test_bars = bars[fold.test_start_idx:fold.test_end_idx]
        res = run_backtest(test_bars, SmcFvgStrategy(min_risk_pts=min_risk), cfg, calibration_bars=cal)
        for t in res.trades:
            trades.append(dataclasses.replace(t, trade_id=f"fold_{fold.fold_id}_{t.trade_id}"))
    return trades


def account_score(trades: list, tpd: float, risk_pct: float, sims: int, seed: int) -> dict:
    n = len(trades)
    qty0 = np.array([float(t.quantity) for t in trades])
    bud = np.array([float(t.budgeted_risk_dollars) for t in trades])
    net_pc = np.array([float(t.net_pnl) / max(float(t.quantity), 1.0) for t in trades])
    unit = np.where((bud > 0) & (qty0 > 0), bud / np.maximum(qty0, 1.0), np.inf)
    valid = np.isfinite(unit) & (unit > 0)
    r = net_pc / np.where(unit > 0, unit, np.inf)
    rv = r[valid]
    nominal = risk_pct * ACCOUNT
    rng = np.random.default_rng(seed)
    bal = np.full(sims, ACCOUNT); peak = np.full(sims, ACCOUNT)
    status = np.zeros(sims, dtype=np.int8)
    T = max(1, round(tpd * 30))
    idx = rng.integers(0, n, size=(sims, T))
    for t in range(T):
        live = status == 0
        if not live.any():
            break
        i = idx[:, t]
        floor = np.minimum(peak - DD, ACCOUNT + LOCK)
        buffer = bal - floor
        qmax = np.floor((SAFETY * buffer) / unit[i])
        status[live & ((qmax < 1) | ~valid[i]) & (buffer > 0)] = 3
        status[live & (buffer <= 0)] = 2
        live = status == 0
        if not live.any():
            break
        q = np.maximum(1.0, np.minimum(np.round(nominal / unit[i]), np.minimum(qmax, MAX_MICROS)))
        bal = np.where(live, bal + q * net_pc[i], bal)
        peak = np.maximum(peak, bal)
        status[(status == 0) & (bal <= np.minimum(peak - DD, ACCOUNT + LOCK))] = 2
        status[(status == 0) & (bal >= ACCOUNT + TARGET)] = 1
    return {
        "n_trades": n,
        "trades_per_day": round(tpd, 3),
        "e_r": round(float(np.nanmean(rv)), 4),
        "sigma_r": round(float(np.nanstd(rv, ddof=1)), 4),
        "win_rate_pct": round(float((rv > 0).mean() * 100), 2),
        "pass_pct": round(float((status == 1).mean() * 100), 2),
        "blown_pct": round(float((status == 2).mean() * 100), 2),
        "blocked_pct": round(float((status == 3).mean() * 100), 2),
        "timeout_pct": round(float((status == 0).mean() * 100), 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-risks", default="5,8,10,15,20,30")
    ap.add_argument("--risk", type=float, default=0.004671)
    ap.add_argument("--sims", type=int, default=1500)
    args = ap.parse_args()
    min_risks = [float(x) for x in args.min_risks.split(",")]

    bars, _ = load_canonical_m5(ZIP_PATH)
    plan = WalkForwardPlan.create_calendar_rolling(bars, train_months=36, test_months=6, step_months=6)
    cfg = smc_fvg_config(
        market=MNQ,
        discrete_partial_contracts=True,
        initial_balance=50000.0,
        commission_per_side=0.62,
        slippage_points=0.25,
        time_exit_slippage_points=0.25,
    )

    rows = []
    print(f"{'min_risk':>9} {'n':>6} {'t/dia':>6} {'E[R]':>8} {'sigma':>6} {'WR%':>6} {'P(pase)':>8} {'quema':>7} {'bloq':>7} {'timeout':>8}", flush=True)
    for mr in min_risks:
        trades = gen_trades(bars, plan, cfg, mr)
        span = max((max(t.exit_time for t in trades) - min(t.entry_time for t in trades)).days, 1)
        tpd = len(trades) / span
        o = account_score(trades, tpd, args.risk, args.sims, SEED)
        o["min_risk_pts"] = mr
        rows.append(o)
        print(f"{mr:>9} {o['n_trades']:>6} {tpd:>6.2f} {o['e_r']:>+8.4f} {o['sigma_r']:>6.3f} {o['win_rate_pct']:>5.1f}% "
              f"{o['pass_pct']:>7.2f}% {o['blown_pct']:>6.2f}% {o['blocked_pct']:>6.2f}% {o['timeout_pct']:>7.2f}%", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "metadata": {
            "title": "Account-objective ranking (E1)",
            "ran_at_utc": datetime.now(timezone.utc).isoformat(),
            "model": "closed-trade trailing (cota optimista; la campeona se re-verifica con MAE intrabar)",
            "account": "Apex 25K, target $1,500, trailing DD $1,500, lock +$100, 30 dias calendario",
            "risk_pct": args.risk,
            "sims": args.sims,
            "seed": SEED,
            "engine_version": "post-E5 (fill de pendientes corregido)",
        },
        "ranking": sorted(rows, key=lambda x: -x["pass_pct"]),
    }
    out = OUT_DIR / "score.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n[escrito] {out}", flush=True)


if __name__ == "__main__":
    main()

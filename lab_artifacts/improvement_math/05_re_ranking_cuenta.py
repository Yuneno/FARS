"""FARS — Re-ranking de configs SMC-FVG por el OBJETIVO DE CUENTA (Hermes).

Pregunta: C3 eligio min_risk_pts=10.0 con criterios de backtest (E[R]/PF/DD).
Pero el exito real es P(pasar la 25K en 30 dias con trailing). Nadie ha puntuado
las candidatas con ESE objetivo. Aqui va la primera pasada.

Modelo de cuenta: trades cerrados (sin MAE intrabar) a riesgo fijo 0.4671%.
Es una cota optimista (la quema real es ~1.83x); sirve para RANKEAR.
El ganador se re-verifica despues con el motor completo (MAE M1 causal).

Configs: min_risk_pts in {5, 8, 10, 15, 20, 30}, target_rr fijo 1.5.
"""
from __future__ import annotations

import dataclasses
import sys
import time

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

ACCOUNT, TARGET, DD, LOCK = 25000.0, 1500.0, 1500.0, 100.0
SAFETY, MAX_MICROS = 0.75, 20
RISK, NS, SEED = 0.004671, 1500, 20260729

print("[carga] dataset...", flush=True)
bars, _ = load_canonical_m5(ZIP_PATH)
plan = WalkForwardPlan.create_calendar_rolling(bars, train_months=36, test_months=6, step_months=6)
print(f"[carga] {len(bars)} barras, {len(plan.folds)} folds", flush=True)


def gen(min_risk: float) -> list:
    cfg = smc_fvg_config(
        market=MNQ,
        discrete_partial_contracts=True,
        initial_balance=50000.0,
        commission_per_side=0.62,
        slippage_points=0.25,
        time_exit_slippage_points=0.25,
    )
    trades = []
    for fold in plan.folds:
        cal = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT):fold.test_start_idx]
        test_bars = bars[fold.test_start_idx:fold.test_end_idx]
        res = run_backtest(test_bars, SmcFvgStrategy(min_risk_pts=min_risk), cfg, calibration_bars=cal)
        for t in res.trades:
            trades.append(dataclasses.replace(t, trade_id=f"fold_{fold.fold_id}_{t.trade_id}"))
    return trades


def acct(trades: list, tpd: float, t0: float) -> dict:
    n = len(trades)
    qty0 = np.array([float(t.quantity) for t in trades])
    bud = np.array([float(t.budgeted_risk_dollars) for t in trades])
    net_pc = np.array([float(t.net_pnl) / max(float(t.quantity), 1.0) for t in trades])
    unit = np.where((bud > 0) & (qty0 > 0), bud / np.maximum(qty0, 1.0), np.inf)
    valid = np.isfinite(unit) & (unit > 0)
    r = net_pc / np.where(unit > 0, unit, np.inf)
    rv = r[valid]
    nominal = RISK * ACCOUNT
    rng = np.random.default_rng(SEED)
    bal = np.full(NS, ACCOUNT); peak = np.full(NS, ACCOUNT)
    status = np.zeros(NS, dtype=np.int8); used = np.zeros(NS, dtype=np.int32)
    T = max(1, round(tpd * 30))  # presupuesto REAL de trades en 30 dias para esta config
    idx = rng.integers(0, n, size=(NS, T))
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
        used = np.where((status != 0) & (used == 0), t + 1, used)
    el = time.perf_counter() - t0
    return {
        "n": n,
        "er": float(np.nanmean(rv)),
        "sigma": float(np.nanstd(rv, ddof=1)),
        "wr": float((rv > 0).mean() * 100),
        "pass": float((status == 1).mean() * 100),
        "blown": float((status == 2).mean() * 100),
        "blocked": float((status == 3).mean() * 100),
        "timeout": float((status == 0).mean() * 100),
        "days": float(np.median(used[status == 1]) / tpd) if (status == 1).any() else float("nan"),
        "secs": el,
    }


print(f"\n{'min_risk':>9} {'n':>6} {'t/dia':>6} {'E[R]':>8} {'sigma':>6} {'WR%':>6} {'P(pase)':>8} {'quema':>7} {'bloq':>7} {'timeout':>8} {'dias':>5}")
rows = []
for mr in (5.0, 8.0, 10.0, 15.0, 20.0, 30.0):
    t0 = time.perf_counter()
    tr = gen(mr)
    span = max((max(t.exit_time for t in tr) - min(t.entry_time for t in tr)).days, 1)
    tpd = len(tr) / span
    o = acct(tr, tpd, t0)
    rows.append((mr, o, tpd))
    print(f"{mr:>9} {o['n']:>6} {tpd:>6.2f} {o['er']:>+8.4f} {o['sigma']:>6.2f} {o['wr']:>5.1f}% {o['pass']:>7.2f}% {o['blown']:>6.2f}% {o['blocked']:>6.2f}% {o['timeout']:>7.2f}% {o['days']:>5.1f}  ({o['secs']:.0f}s)", flush=True)

print("\n=== RANKING por P(pase) (modelo de trades cerrados; la quema real es ~1.83x esta) ===")
for mr, o, tpd in sorted(rows, key=lambda x: -x[1]["pass"]):
    print(f"min_risk={mr:>4}: pase {o['pass']:>5.2f}% | fracaso {o['blown']+o['blocked']:>5.2f}% | n={o['n']} t/dia={tpd:.2f}")

"""FARS E5 — Re-corrida post-fix: regenera los sets de trades (10.0 y 5.0) con el
fill corregido, y mide el impacto en las cadenas afectadas (E[R], cola, motor de
cuenta closed + intraday). El fix E5: el pendiente solo llena si la barra negocia
el nivel (gap-through -> no fill).
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
    AccountEngineConfig,
    apex_profile,
    extract_m1_bars_and_compute_maes,
    load_canonical_m5,
    run_account_monte_carlo,
    run_backtest,
    smc_fvg_config,
)

ACCOUNT, TARGET, DD, LOCK = 25000.0, 1500.0, 1500.0, 100.0
SAFETY, MAX_MICROS = 0.75, 20
RISK, NS, SEED = 0.004671, 1500, 20260729
T30 = 56

print("[carga] dataset...", flush=True)
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


def gen(min_risk: float) -> list:
    trades = []
    for fold in plan.folds:
        cal = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT):fold.test_start_idx]
        test_bars = bars[fold.test_start_idx:fold.test_end_idx]
        res = run_backtest(test_bars, SmcFvgStrategy(min_risk_pts=min_risk), cfg, calibration_bars=cal)
        for t in res.trades:
            trades.append(dataclasses.replace(t, trade_id=f"fold_{fold.fold_id}_{t.trade_id}"))
    return trades


def stats(trades: list, tpd: float):
    n = len(trades)
    qty0 = np.array([float(t.quantity) for t in trades])
    bud = np.array([float(t.budgeted_risk_dollars) for t in trades])
    net_pc = np.array([float(t.net_pnl) / max(float(t.quantity), 1.0) for t in trades])
    unit = np.where((bud > 0) & (qty0 > 0), bud / np.maximum(qty0, 1.0), np.inf)
    valid = np.isfinite(unit) & (unit > 0)
    r = net_pc / np.where(unit > 0, unit, np.inf)
    rv = r[valid]
    tail = int((rv < -2.0).sum())
    # closed-model account MC
    nominal = RISK * ACCOUNT
    rng = np.random.default_rng(SEED)
    bal = np.full(NS, ACCOUNT); peak = np.full(NS, ACCOUNT)
    status = np.zeros(NS, dtype=np.int8)
    T = max(1, round(tpd * 30))
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
    print(f"    n={n}  E[R]={np.nanmean(rv):+.4f}  sigma={np.nanstd(rv, ddof=1):.3f}  cola(r<-2R)={tail}  "
          f"[closed] pase={(status == 1).mean()*100:.2f}%  quema={(status == 2).mean()*100:.2f}%  "
          f"bloq={(status == 3).mean()*100:.2f}%", flush=True)
    return trades, tail


print("\n[1] min_risk=10.0 ...")
tr10, tail10 = stats(gen(10.0), 1.95)
print("[2] min_risk=5.0 ...")
tr5, tail5 = stats(gen(5.0), 3.85)

print("\n[3] motor FULL intraday (MAE M1 causal) @ 0.4671% ...", flush=True)
for label, tr in (("10.0", tr10), ("5.0", tr5)):
    t0 = time.perf_counter()
    maes = extract_m1_bars_and_compute_maes(tr, ZIP_PATH)
    prof = apex_profile("25k", cadence="intraday_event")
    c = AccountEngineConfig(risk_pct=RISK, trailing_mode="intraday_event", horizon_calendar_days=30)
    mc = run_account_monte_carlo(prof, tr, c, n_simulations=NS, seed=SEED, precomputed_maes=maes)
    print(f"    {label}: pase {mc.pass_rate*100:6.2f}%  quema {mc.blown_rate*100:6.2f}%  "
          f"bloq {mc.blocked_rate*100:6.2f}%  timeout {mc.timeout_rate*100:6.2f}%  "
          f"dias {mc.median_days_to_pass:5.1f}  DDp95 {mc.p95_max_drawdown_pct:.1f}%  ({time.perf_counter()-t0:.0f}s)", flush=True)

print("\n[referencia PRE-fix] 10.0: n=2842 E[R]=+0.0879 cola=7 closed: pase 26.9% quema 2.9% | intraday: pase 25.60% quema 6.60%")
print("[referencia PRE-fix]  5.0: n=5626 E[R]=+0.0688           | intraday: pase 46.65% quema 16.90%")

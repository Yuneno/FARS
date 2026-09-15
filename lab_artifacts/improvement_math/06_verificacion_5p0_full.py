"""FARS — Verificacion completa de min_risk=5.0 en el motor de cuentas FULL (Hermes).

Stage 2 del re-ranking: genera los trades OOS de 5.0, extrae MAEs M1 causales
y corre el motor de cuenta Apex 25K con trailing intrabar (el de D) en 3 sizings.
Compara contra los numeros auditados de la 10.0 (pase intradia 25.6%).
"""
from __future__ import annotations

import dataclasses
import sys

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

SEED = 20260729
NS = 2000
MIN_RISK = 5.0

print(f"[1/3] generando trades OOS para min_risk={MIN_RISK}...", flush=True)
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
trades = []
for fold in plan.folds:
    cal = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT):fold.test_start_idx]
    test_bars = bars[fold.test_start_idx:fold.test_end_idx]
    res = run_backtest(test_bars, SmcFvgStrategy(min_risk_pts=MIN_RISK), cfg, calibration_bars=cal)
    for t in res.trades:
        trades.append(dataclasses.replace(t, trade_id=f"fold_{fold.fold_id}_{t.trade_id}"))
print(f"    {len(trades)} trades OOS", flush=True)

print("[2/3] extrayendo MAEs M1 causales (lento)...", flush=True)
maes = extract_m1_bars_and_compute_maes(trades, ZIP_PATH)
print(f"    MAEs: {len(maes)}", flush=True)

print("[3/3] motor de cuenta 25K, trailing intrabar...", flush=True)
prof = apex_profile("25k", cadence="intraday_event")
print(f"{'riesgo':>8} {'pase':>7} {'quema':>7} {'bloq':>7} {'timeout':>8} {'dias':>6} {'t/dia':>6} {'DD p95':>7}")
for risk_pct in (0.0030, 0.004671, 0.0060):
    c = AccountEngineConfig(risk_pct=risk_pct, trailing_mode="intraday_event", horizon_calendar_days=30)
    mc = run_account_monte_carlo(prof, trades, c, n_simulations=NS, seed=SEED, precomputed_maes=maes)
    print(f"{risk_pct*100:>7.2f}% {mc.pass_rate*100:>6.2f}% {mc.blown_rate*100:>6.2f}% "
          f"{mc.blocked_rate*100:>6.2f}% {mc.timeout_rate*100:>7.2f}% "
          f"{mc.median_days_to_pass:>5.1f} {mc.trades_per_day:>6.2f} {mc.p95_max_drawdown_pct*100:>6.2f}%", flush=True)

print("\nReferencia auditada 10.0 @ 0.4671%: pase 25.60% quema 6.60% bloq 7.05% dias 20.8 t/dia 1.86")

"""Carril 2b — Puntuacion de cuenta para las candidatas MGC 2.0/3.0 (screening -> motor).

Pools: MGC sola y MNQ+MGC, con la config recalibrada del screening, en el motor
Apex 25K intraday (MAEs: MNQ = M1 causal, MGC = fallback M5 conservador).
"""
from __future__ import annotations

import dataclasses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "E:/FARS-LAB/FARS")
from lab_artifacts.e7_protocol.run_e7_multimercado import (  # noqa: E402
    RAW_DIR,
    build_maes,
    gen_trades,
    load_market_csv,
)
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
from src.account_policy import SizingPolicyConfig  # noqa: E402
from src.backtest.markets import MGC  # noqa: E402

BASE = Path(__file__).resolve().parent
SEED, NS = 20260729, 2000
POR_TRAMO = dict(commission_per_side=0.62, slippage_points=0.25, time_exit_slippage_points=0.25)


def gen_market(bars, market, min_risk):
    plan = WalkForwardPlan.create_calendar_rolling(bars, train_months=36, test_months=6, step_months=6)
    cfg = smc_fvg_config(market=market, discrete_partial_contracts=True, initial_balance=50000.0, **POR_TRAMO)
    trades = []
    for fold in plan.folds:
        cal = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT):fold.test_start_idx]
        test_bars = bars[fold.test_start_idx:fold.test_end_idx]
        res = run_backtest(test_bars, SmcFvgStrategy(min_risk_pts=min_risk), cfg, calibration_bars=cal)
        for t in res.trades:
            trades.append(dataclasses.replace(t, trade_id=f"fold_{fold.fold_id}_{t.trade_id}"))
    return trades


def main() -> None:
    started = datetime.now(timezone.utc).isoformat()
    mnq_bars, _ = load_canonical_m5(ZIP_PATH)
    mgc_bars = load_market_csv("MGC", RAW_DIR / "MGC_M5.csv")
    prof = apex_profile("25k", cadence="intraday_event")
    out_rows = []

    for mgc_mr in (2.0, 3.0):
        mgc_trades = gen_market(mgc_bars, MGC, mgc_mr)
        mgc_trades_p = [dataclasses.replace(t, trade_id=f"MGC_{t.trade_id}") for t in mgc_trades]
        mgc_mae = build_maes(mgc_trades, mgc_bars, MGC.dollar_per_point, "MGC")
        # pool MNQ(5.0, pilar) + MGC recalibrada
        mnq_trades = gen_market(mnq_bars, MNQ, 5.0)
        mnq_mae = extract_m1_bars_and_compute_maes(mnq_trades, ZIP_PATH)
        pool = list(mnq_trades) + mgc_trades_p
        mae = dict(mnq_mae); mae.update(mgc_mae)

        for label, (tr, ma) in (("MGC_solo", (mgc_trades_p, mgc_mae)), ("MNQ5+MGC", (pool, mae))):
            for pcfg in (SizingPolicyConfig(kind="fixed"),
                         SizingPolicyConfig(kind="buffer_prop", param=0.10)):
                cfg = AccountEngineConfig(risk_pct=0.004671, trailing_mode="intraday_event",
                                          horizon_calendar_days=30, policy=pcfg)
                mc = run_account_monte_carlo(prof, tr, cfg, n_simulations=NS, seed=SEED, precomputed_maes=ma)
                row = {"mgc_min_risk": mgc_mr, "pool": label, "policy": pcfg.kind,
                       "pass": round(mc.pass_rate, 4), "blown": round(mc.blown_rate, 4),
                       "blocked": round(mc.blocked_rate, 4), "timeout": round(mc.timeout_rate, 4),
                       "days": mc.median_days_to_pass, "tpd": mc.trades_per_day}
                out_rows.append(row)
                print(f"  MGC{mgc_mr:.0f} [{label:<10}] {pcfg.kind:<11} pase {mc.pass_rate*100:6.2f}%  "
                      f"quema {mc.blown_rate*100:6.2f}%  bloq {mc.blocked_rate*100:6.2f}%  "
                      f"dias {mc.median_days_to_pass}  t/dia {mc.trades_per_day:.2f}", flush=True)

    out = BASE / "cuenta_candidatas.json"
    out.write_text(json.dumps({"metadata": {"title": "Carril 2b cuenta", "ran_at_utc": started},
                              "rows": out_rows}, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(f"\n[escrito] {out}")


if __name__ == "__main__":
    main()

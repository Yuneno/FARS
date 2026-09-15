"""FARS E4 — Evaluacion formal de politicas de apuesta (post-E5).

Ejecuta la rejilla preregistrada (preregistro_politicas.json) sobre los sets
post-fix de SMC-FVG (5.0 y 10.0) en el motor FULL (trailing intradia + MAE M1).
"""
from __future__ import annotations

import dataclasses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

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
from src.account_policy import SizingPolicyConfig  # noqa: E402

BASE = Path(__file__).resolve().parent
PREREG = BASE / "preregistro_politicas.json"
NS, SEED = 2000, 20260729


def gen(min_risk: float) -> list:
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
        res = run_backtest(test_bars, SmcFvgStrategy(min_risk_pts=min_risk), cfg, calibration_bars=cal)
        for t in res.trades:
            trades.append(dataclasses.replace(t, trade_id=f"fold_{fold.fold_id}_{t.trade_id}"))
    return trades


def main() -> None:
    started = datetime.now(timezone.utc).isoformat()
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    assert prereg["metadata"]["preregistered_at_utc"] < started, "preregistro debe preceder a la corrida"

    engine_cfg = prereg["grid"]["engine"]
    prof = apex_profile("25k", cadence="intraday_event")
    results = {"metadata": {
        "title": "E4 — politicas de apuesta (post-E5)",
        "ran_at_utc": started,
        "preregistered_at_utc": prereg["metadata"]["preregistered_at_utc"],
        "engine": engine_cfg,
    }, "results": []}

    for set_label, min_risk in (("smc_fvg_risk_5", 5.0), ("smc_fvg_risk_10", 10.0)):
        print(f"[set] {set_label}: generando trades y MAEs...", flush=True)
        trades = gen(min_risk)
        maes = extract_m1_bars_and_compute_maes(trades, ZIP_PATH)
        print(f"    {len(trades)} trades, {len(maes)} MAEs", flush=True)
        for pol in prereg["grid"]["policies"]:
            pcfg = SizingPolicyConfig(
                kind=pol["kind"], param=pol["param"],
                streak_threshold=pol["streak_threshold"], streak_factor=pol["streak_factor"],
            )
            cfg = AccountEngineConfig(
                risk_pct=engine_cfg["risk_pct"],
                trailing_mode=engine_cfg["trailing_mode"],
                horizon_calendar_days=engine_cfg["horizon_calendar_days"],
                policy=pcfg,
            )
            mc = run_account_monte_carlo(prof, trades, cfg, n_simulations=NS, seed=SEED, precomputed_maes=maes)
            row = {
                "set": set_label, "policy": pol["id"],
                "pass_rate": round(mc.pass_rate, 4),
                "blown_rate": round(mc.blown_rate, 4),
                "blocked_rate": round(mc.blocked_rate, 4),
                "timeout_rate": round(mc.timeout_rate, 4),
                "median_days_to_pass": mc.median_days_to_pass,
                "trades_per_day": mc.trades_per_day,
            }
            results["results"].append(row)
            print(f"    {pol['id']:<18} pase {mc.pass_rate*100:6.2f}%  quema {mc.blown_rate*100:6.2f}%  "
                  f"bloq {mc.blocked_rate*100:6.2f}%  timeout {mc.timeout_rate*100:6.2f}%  "
                  f"dias {mc.median_days_to_pass}", flush=True)

    out = BASE / "resultados.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n[escrito] {out}")


if __name__ == "__main__":
    main()

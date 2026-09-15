"""FARS E2 — Frontera pase/fracaso por set de config (preregistrada).

Barre sets x sizings x politicas en el motor FULL y escribe la frontera Pareto +
la seleccion segun el criterio preregistrado (max pase con fracaso <= 15%).
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
PREREG = BASE / "preregistro_frontera.json"
NS, SEED = 2000, 20260729
SETS = {"smc_fvg_risk_5": 5.0, "smc_fvg_baseline": 8.0, "smc_fvg_risk_10": 10.0}


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
    grid = prereg["grid"]
    prof = apex_profile("25k", cadence="intraday_event")
    rows = []

    for set_label, min_risk in SETS.items():
        print(f"[set] {set_label}: generando trades y MAEs...", flush=True)
        trades = gen(min_risk)
        maes = extract_m1_bars_and_compute_maes(trades, ZIP_PATH)
        for risk_pct in grid["risk_pcts"]:
            for pol in grid["policies"]:
                pcfg = SizingPolicyConfig(
                    kind=pol["kind"], param=pol["param"],
                    streak_threshold=pol["streak_threshold"], streak_factor=pol["streak_factor"],
                )
                cfg = AccountEngineConfig(
                    risk_pct=risk_pct, trailing_mode="intraday_event",
                    horizon_calendar_days=30, policy=pcfg,
                )
                mc = run_account_monte_carlo(prof, trades, cfg, n_simulations=NS, seed=SEED, precomputed_maes=maes)
                rows.append({
                    "set": set_label, "risk_pct": risk_pct, "policy": pol["id"],
                    "pass_rate": round(mc.pass_rate, 4),
                    "blown_rate": round(mc.blown_rate, 4),
                    "blocked_rate": round(mc.blocked_rate, 4),
                    "timeout_rate": round(mc.timeout_rate, 4),
                    "failure_rate": round(mc.blown_rate + mc.blocked_rate, 4),
                    "median_days_to_pass": mc.median_days_to_pass,
                })
                print(f"    {risk_pct*100:>5.2f}% {pol['id']:<18} pase {mc.pass_rate*100:6.2f}%  "
                      f"fracaso {(mc.blown_rate+mc.blocked_rate)*100:6.2f}%  dias {mc.median_days_to_pass}", flush=True)

    # Frontera Pareto por set + seleccion preregistrada
    cap = prereg["selection_criteria"]["primary"]
    summary = {}
    for set_label in SETS:
        srows = [r for r in rows if r["set"] == set_label]
        srows.sort(key=lambda r: -r["pass_rate"])
        frontier = []
        for r in srows:
            if not any(f["failure_rate"] <= r["failure_rate"] for f in frontier):
                frontier.append(r)
        picks = [r for r in srows if r["failure_rate"] <= 0.15]
        best = max(picks, key=lambda r: r["pass_rate"]) if picks else None
        summary[set_label] = {
            "frontier": frontier,
            "selected_under_cap": best,
            "n_points": len(srows),
        }
        print(f"\n[{set_label}] frontera ({len(frontier)} puntos):")
        for r in frontier:
            print(f"    {r['risk_pct']*100:>5.2f}% {r['policy']:<18} pase {r['pass_rate']*100:6.2f}%  fracaso {r['failure_rate']*100:6.2f}%  dias {r['median_days_to_pass']}")
        if best:
            print(f"    SELECCION (fracaso<=15%, max pase): {best['risk_pct']*100:.2f}% {best['policy']} -> pase {best['pass_rate']*100:.2f}% fracaso {best['failure_rate']*100:.2f}%")
        else:
            print("    SIN PUNTO bajo el tope de 15% — se reporta la frontera.")

    out = BASE / "frontera.json"
    out.write_text(json.dumps(
        {"metadata": {"title": "E2 frontera", "ran_at_utc": started,
                      "preregistered_at_utc": prereg["metadata"]["preregistered_at_utc"]},
         "rows": rows, "summary": summary}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n[escrito] {out}")


if __name__ == "__main__":
    main()

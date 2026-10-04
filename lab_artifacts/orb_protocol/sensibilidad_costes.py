#!/usr/bin/env python3
"""
sensibilidad_costes.py — análisis de sensibilidad de costes sobre el motor ORB.

Evalúa 4 configuraciones de costes:
  1. Base: comisión 0,62 USD/side + 1,5 ticks/side (2,74 USD/RT)
  2. ×1,5: comisión 0,93 USD/side + 2,25 ticks/side (4,11 USD/RT)
  3. ×2,0: comisión 1,24 USD/side + 3,00 ticks/side (5,48 USD/RT)
  4. Desglose:
     - solo comisión ×2: comisión 1,24 USD/side + 1,5 ticks/side (3,98 USD/RT)
     - solo slippage ×2: comisión 0,62 USD/side + 3,0 ticks/side (4,24 USD/RT)

Reutiliza `run_orb.py` (lee `Config` y `run_backtest`) sin duplicar lógica.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, r"E:\FARS-LAB\FARS")
sys.path.insert(0, str(HERE))
sys.path.insert(0, r"E:\FARS-LAB\ext_review2\nq-intraday-breakout\src")

from run_orb import (  # noqa: E402
    DATA, MNQ_CFG, SimFlags, compute_metrics as compute_engine_metrics,
    load_nq_1min, replace, resample_5min, run_backtest,
)
from src.metrics import compute_metrics  # noqa: E402

COST_SCENARIOS = {
    "base": {
        "commission": 0.62,
        "slippage_ticks": 1.5,
        "desc": "Base realista (0,62 USD + 1,5 ticks)",
    },
    "x1.5": {
        "commission": 0.62 * 1.5,
        "slippage_ticks": 1.5 * 1.5,
        "desc": "×1,5 comisiones y slippage",
    },
    "x2.0": {
        "commission": 0.62 * 2.0,
        "slippage_ticks": 1.5 * 2.0,
        "desc": "×2,0 comisiones y slippage",
    },
    "solo_comision_x2": {
        "commission": 0.62 * 2.0,
        "slippage_ticks": 1.5,
        "desc": "Desglose: solo comisión ×2",
    },
    "solo_slippage_x2": {
        "commission": 0.62,
        "slippage_ticks": 1.5 * 2.0,
        "desc": "Desglose: solo slippage ×2",
    },
}


def run_cost_sensitivity(df_5min=None) -> dict[str, dict]:
    if df_5min is None:
        print("Cargando datos históricos 5-min...")
        df1 = load_nq_1min(DATA)
        df_5min = resample_5min(df1)

    results = {}
    for name, params in COST_SCENARIOS.items():
        cfg = replace(
            MNQ_CFG,
            commission_per_side=params["commission"],
            slippage_ticks_per_side=params["slippage_ticks"],
        )
        res = run_backtest(df_5min, cfg, SimFlags())

        # Calcular serie R según el modelo FARS
        r_list = []
        for t in res.trades.itertuples(index=False):
            sign = 1.0 if t.direction == "long" else -1.0
            gross_pts = sign * (t.exit_price - t.entry_price)
            cost_pts = cfg.round_turn_cost / cfg.point_value
            r = (gross_pts - cost_pts) / float(t.stop_points)
            r_list.append(float(r))

        fars_m = compute_metrics(r_list)
        eng_m = compute_engine_metrics(res.equity) if not res.equity.empty else {}

        sign_flipped = fars_m.expectancy_r < 0.0

        results[name] = {
            "desc": params["desc"],
            "round_turn_cost_usd": round(cfg.round_turn_cost, 2),
            "trades": len(res.trades),
            "expectancy_r": fars_m.expectancy_r,
            "win_rate": fars_m.win_rate,
            "std_r": fars_m.std_r,
            "max_dd_r": fars_m.max_drawdown_r,
            "max_dd_pct": float(eng_m.get("Max Drawdown %", 0.0)) * 100,
            "sign_flipped": sign_flipped,
        }

    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="Sensibilidad de costes en motor ORB")
    ap.parse_args()

    print("Ejecutando análisis de sensibilidad de costes...")
    res = run_cost_sensitivity()

    print("\n" + "═" * 80)
    print("ANÁLISIS DE SENSIBILIDAD DE COSTES (MNQ, SimFlags Corregido)")
    print("═" * 80)
    header = f"{'Escenario':<20} {'Coste RT':>9} {'Trades':>7} {'E[R]':>9} {'WR (%)':>8} {'Max DD(R)':>10} {'DD (%)':>8}"
    print(header)
    print("─" * 80)

    for name, r in res.items():
        sign_flag = " [CAMBIO DE SIGNO]" if r["sign_flipped"] else ""
        row = (
            f"{name:<20} "
            f"${r['round_turn_cost_usd']:>7.2f} "
            f"{r['trades']:>7d} "
            f"{r['expectancy_r']:>9.4f} "
            f"{r['win_rate']*100:>7.2f}% "
            f"{r['max_dd_r']:>10.2f} "
            f"{r['max_dd_pct']:>7.2f}%"
            f"{sign_flag}"
        )
        print(row)

    print("═" * 80)

    # Hallazgos y conclusiones de degradación
    base_er = res["base"]["expectancy_r"]
    x15_er = res["x1.5"]["expectancy_r"]
    x20_er = res["x2.0"]["expectancy_r"]
    com_er = res["solo_comision_x2"]["expectancy_r"]
    slip_er = res["solo_slippage_x2"]["expectancy_r"]

    print("\nConclusiones de sensibilidad:")
    print(f"  • Degradación E[R] a ×1,5 : {x15_er - base_er:+.4f} R ({(x15_er/base_er - 1)*100:+.1f}%)")
    print(f"  • Degradación E[R] a ×2,0 : {x20_er - base_er:+.4f} R ({(x20_er/base_er - 1)*100:+.1f}%)")
    print(f"  • Impacto relativo        : Slippage ×2 ({slip_er - base_er:+.4f} R) vs Comisión ×2 ({com_er - base_er:+.4f} R)")

    for name, r in res.items():
        if r["sign_flipped"]:
            print(f"  ⚠ ADVERTENCIA: En el escenario '{name}', E[R] cambia de signo a negativo ({r['expectancy_r']:.4f} R).")

    return 0


if __name__ == "__main__":
    sys.exit(main())

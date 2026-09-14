#!/usr/bin/env python3
"""Script de generacion del artefacto de Slippage de Salida Temporal (Bloque A.3.1).

Ejecuta el baseline canonico de CRT-TBS Champion y ORB sobre el dataset M5 canonico
(>= 2019-05-06, 518,237 barras) con $4.00 RT de comision, comparando
time_exit_slippage_points = 0.0 vs 0.25 (1 tick).

Genera: lab_artifacts/a3/time_exit_slippage.json
"""
from __future__ import annotations

import csv
import io
import json
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.backtest.crt_tbs import CrtTbsConfig, CrtTbsStrategy, crt_tbs_config  # noqa: E402
from src.backtest.executor import run_backtest  # noqa: E402
from src.backtest.history import Bar  # noqa: E402
from src.backtest.markets import MNQ  # noqa: E402
from src.backtest.orb import OrbConfig, OrbStrategy, orb_config  # noqa: E402

ZIP_PATH = Path("E:/FARS-LAB/databento.zip")
TICK_PTS = 0.25
DOLLAR_PER_POINT = MNQ.dollar_per_point
RISK_BUDGET = 500.0  # $50,000 * 0.01


def load_canonical_m5(zip_path: Path, member: str = "databento/MNQ_M5.csv") -> list[Bar]:
    bars: list[Bar] = []
    with zipfile.ZipFile(zip_path) as zf, zf.open(member) as raw:
        for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8")):
            ts = row["timestamp"]
            if ts >= "2019-05-06":
                bars.append(
                    Bar(
                        timestamp=datetime.fromisoformat(ts),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row.get("volume", 0)),
                    )
                )
    return bars


def build_strategy_and_config(strat_id: str, slip_pts: float):
    if strat_id == "crt_tbs_champion":
        strat_cfg = CrtTbsConfig(
            require_4h_bias=True,
            require_half_zone=False,
            target_mode="fixed_rr",
            fixed_rr=2.0,
        )
        strategy = CrtTbsStrategy(strat_cfg)
        cfg = crt_tbs_config(
            market=MNQ,
            commission_per_side=2.0,
            slippage_points=0.0,
            time_exit_mode="market",
            time_exit_slippage_points=slip_pts,
        )
        return "CRT-TBS (Champion)", strategy, cfg
    elif strat_id == "orb":
        strat_cfg = OrbConfig(
            or_minutes=30,
            stop_mode="opposite",
            rr=2.0,
            use_bias=False,
            fade=False,
            end_hour=16,
            max_hold_bars=192,
            time_exit_mode="market",
        )
        strategy = OrbStrategy(strat_cfg)
        cfg = orb_config(
            market=MNQ,
            commission_per_side=2.0,
            slippage_points=0.0,
            max_bars_held=192,
            time_exit_mode="market",
            time_exit_slippage_points=slip_pts,
        )
        return "ORB (Experimental)", strategy, cfg
    else:
        raise ValueError(f"Unknown strategy: {strat_id}")


def extract_leg_stats(trades):
    legs = {}
    for reason in ("stop_loss", "take_profit", "time_exit"):
        leg_trades = [t for t in trades if t.exit_reason == reason]
        legs[reason] = {
            "n_trades": len(leg_trades),
            "net_pnl": round(sum(t.net_pnl for t in leg_trades), 2),
            "total_slippage_cost": round(sum(t.slippage_cost for t in leg_trades), 2),
            "total_quantity": sum(int(t.quantity) for t in leg_trades),
        }
    return legs


def analyze_run(label: str, res, slip_pts: float) -> dict:
    time_exits = [t for t in res.trades if t.exit_reason == "time_exit"]
    qty_hist = Counter(int(t.quantity) for t in time_exits)
    total_qty_time_exits = sum(int(t.quantity) for t in time_exits)
    total_slip_dollars = sum(t.slippage_cost for t in time_exits)

    stop_pts = [
        (t.stop_risk_dollars / (DOLLAR_PER_POINT * t.quantity)) if t.quantity else 0.0
        for t in res.trades
    ]
    median_stop = sorted(stop_pts)[len(stop_pts) // 2] if stop_pts else 0.0
    expectancy_r = (
        sum(t.r_result for t in res.trades) / len(res.trades) if res.trades else 0.0
    )

    legs = extract_leg_stats(res.trades)

    return {
        "estrategia": label,
        "slip_pts_por_contrato": slip_pts,
        "n_trades": res.n_trades,
        "n_expiraciones": len(time_exits),
        "qty_distribution_time_exits": {str(k): v for k, v in sorted(qty_hist.items())},
        "qty_total_time_exits": total_qty_time_exits,
        "slippage_total_dollars": round(total_slip_dollars, 2),
        "slippage_total_R": round(total_slip_dollars / RISK_BUDGET, 4),
        "net_pnl": round(res.net_pnl, 2),
        "profit_factor": (
            round(res.profit_factor, 4) if res.profit_factor != float("inf") else "inf"
        ),
        "expectancy_r": round(expectancy_r, 6),
        "max_drawdown_pct": round(res.max_drawdown_pct, 4),
        "median_stop_distance_pts": round(median_stop, 2),
        "friction_R_del_slippage_por_trade": (
            round(total_slip_dollars / len(time_exits) / RISK_BUDGET, 6)
            if time_exits
            else 0.0
        ),
        "delta_por_pata": legs,
    }


def main():
    print(f"Cargando M5 canonico desde {ZIP_PATH}...", flush=True)
    bars = load_canonical_m5(ZIP_PATH)
    print(f"Cargadas {len(bars)} barras M5", flush=True)

    report = {
        "metadata": {
            "title": "A3.1 - Impacto del Slippage en la Salida Temporal a Mercado",
            "dataset": "databento/MNQ_M5.csv (>= 2019-05-06)",
            "total_bars": len(bars),
            "cost_basis": "$4.00 RT commission, MNQ $2.00/pt",
            "tick_size_pts": TICK_PTS,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "comparisons": [],
    }

    for strat_id in ("crt_tbs_champion", "orb"):
        print(f"\nEvaluando {strat_id}...", flush=True)
        strat_results = []
        for slip in (0.0, TICK_PTS):
            label, strategy, cfg = build_strategy_and_config(strat_id, slip)
            res = run_backtest(bars, strategy, cfg)
            row = analyze_run(label, res, slip)
            strat_results.append(row)
            print(
                f"  slip={slip} pts | n={row['n_trades']} | exp={row['n_expiraciones']} | "
                f"net=${row['net_pnl']:,.2f} | PF={row['profit_factor']} | exp_r={row['expectancy_r']:+.4f} | "
                f"slip_cost=${row['slippage_total_dollars']:,.2f}"
            )

        row_0, row_1 = strat_results[0], strat_results[1]
        comparison = {
            "estrategia": row_0["estrategia"],
            "baseline_0_tick": row_0,
            "slippage_1_tick": row_1,
            "delta_net_pnl": round(row_1["net_pnl"] - row_0["net_pnl"], 2),
            "delta_profit_factor": (
                round(row_1["profit_factor"] - row_0["profit_factor"], 4)
                if isinstance(row_0["profit_factor"], (int, float))
                and isinstance(row_1["profit_factor"], (int, float))
                else 0.0
            ),
            "delta_expectancy_r": round(row_1["expectancy_r"] - row_0["expectancy_r"], 6),
            "delta_max_drawdown_pct": round(
                row_1["max_drawdown_pct"] - row_0["max_drawdown_pct"], 4
            ),
        }
        report["comparisons"].append(comparison)

    out_file = REPO_ROOT / "lab_artifacts" / "a3" / "time_exit_slippage.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nArtefacto guardado exitosamente en: {out_file}")

    # Mirror to root lab_artifacts/a3 if needed
    root_artifacts_a3 = Path("E:/FARS-LAB/lab_artifacts/a3")
    root_artifacts_a3.mkdir(parents=True, exist_ok=True)
    with open(root_artifacts_a3 / "time_exit_slippage.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"Artefacto copiado a raiz: {root_artifacts_a3 / 'time_exit_slippage.json'}")


if __name__ == "__main__":
    main()

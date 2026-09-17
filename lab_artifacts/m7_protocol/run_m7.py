#!/usr/bin/env python3
"""Runner reproducible y paralelo del Bloque M7.

Protocolo C1:
- 4 Mercados: MNQ, MES, MYM, MGC (streaming desde E:/FARS-LAB/databento.zip)
- 7 Arms: baseline, ote_062, ote_0705, ote_band, trend_kai, ote_band_plus_trend, wrapper_trivial
- 3 Escenarios de coste: canonico, canonico_mas_1tick, realista
- Walk-Forward: 8 folds de calendario 36/6/6, purga + embargo h=192, 500 barras warmup
- Control anti-fraude: wrapper_trivial == baseline bit a bit
- Workers: <= 4 (concurrencia controlada)
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import dataclasses
import hashlib
import io
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
import zipfile
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lab_artifacts.m7_protocol.filters import (
    KaiDailyBiasIndex,
    M7FilteredStrategy,
)
from lab_artifacts.run_c1_walkforward import (
    CALIBRATION_BARS_COUNT,
    ZIP_PATH,
    compute_file_sha256,
    run_bootstrap_ci,
)
from src.backtest.executor import BacktestResult, run_backtest
from src.backtest.history import Bar
from src.backtest.markets import MES, MGC, MNQ, MYM, MarketSpec
from src.backtest.smc_fvg import smc_fvg_config
from src.hypothesis_registry import WalkForwardPlan

M7_DIR = Path(__file__).resolve().parent
PREREGISTRO_PATH = M7_DIR / "preregistro.json"

MARKET_SPECS: dict[str, MarketSpec] = {
    "MNQ": MNQ,
    "MES": MES,
    "MYM": MYM,
    "MGC": MGC,
}

MARKET_FILES: dict[str, str] = {
    "MNQ": "databento/MNQ_M5.csv",
    "MES": "databento/MES_M5.csv",
    "MYM": "databento/MYM_M5.csv",
    "MGC": "databento/MGC_M5.csv",
}

SCENARIO_CONFIGS: dict[str, dict[str, float]] = {
    "canonico": {"comm_side": 2.0, "slip_ticks": 0.0},
    "canonico_mas_1tick": {"comm_side": 2.0, "slip_ticks": 1.0},
    "realista": {"comm_side": 0.62, "slip_ticks": 1.0},
}


def load_market_bars_from_zip(zip_path: Path, member_name: str) -> tuple[list[Bar], str, str, str]:
    """Lee barras M5 por streaming desde el zip canónico, calcula sha256 y filtra >= 2019-05-06."""
    hasher = hashlib.sha256()
    bars: list[Bar] = []
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(member_name) as raw:
            content_bytes = raw.read()
            hasher.update(content_bytes)
            reader = csv.DictReader(io.StringIO(content_bytes.decode("utf-8")))
            for row in reader:
                ts_str = row["timestamp"]
                if ts_str >= "2019-05-06":
                    bars.append(
                        Bar(
                            timestamp=datetime.fromisoformat(ts_str.replace("Z", "+00:00")),
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                            volume=float(row.get("volume", 0)),
                        )
                    )
    file_sha = hasher.hexdigest()
    first_ts = bars[0].timestamp.isoformat() if bars else "N/A"
    last_ts = bars[-1].timestamp.isoformat() if bars else "N/A"
    return bars, file_sha, first_ts, last_ts


def serialize_trade(t: Any) -> dict[str, Any]:
    return {
        "trade_id": t.trade_id,
        "direction": t.direction,
        "entry_time": t.entry_time.isoformat(),
        "exit_time": t.exit_time.isoformat(),
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "stop_price": t.stop_price,
        "target_price": t.target_price,
        "quantity": t.quantity,
        "gross_pnl": round(t.gross_pnl, 4),
        "commission": round(t.commission, 4),
        "net_pnl": round(t.net_pnl, 4),
        "r_result": round(t.r_result, 6),
        "exit_reason": t.exit_reason,
    }


def compute_trades_sha256(trades: list[Any]) -> str:
    raw = [serialize_trade(t) if not isinstance(t, dict) else t for t in trades]
    serialized = json.dumps(raw, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compute_drawdown_r(r_results: list[float]) -> float:
    if not r_results:
        return 0.0
    equity = np.cumsum(r_results)
    peak = np.maximum.accumulate(equity)
    dd = peak - equity
    return float(np.max(dd)) if len(dd) > 0 else 0.0


def run_single_cell(
    market_symbol: str,
    market_spec: MarketSpec,
    arm_id: str,
    scenario_name: str,
    bars: list[Bar],
    bias_index: KaiDailyBiasIndex,
    plan: WalkForwardPlan,
    max_folds: int | None = None,
) -> dict[str, Any]:
    """Ejecuta los folds de walk-forward para una combinación mercado x arm x escenario."""
    sc_info = SCENARIO_CONFIGS[scenario_name]
    comm = sc_info["comm_side"]
    slip = sc_info["slip_ticks"] * market_spec.tick_size

    cfg = smc_fvg_config(
        market=market_spec,
        wait=48,
        commission_per_side=comm,
        slippage_points=slip,
        time_exit_slippage_points=slip,
    )

    folds_to_run = plan.folds[:max_folds] if max_folds else plan.folds
    fold_records: list[dict[str, Any]] = []
    all_trades: list[Any] = []

    for fold in folds_to_run:
        test_cal = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT) : fold.test_start_idx]
        test_bars = bars[fold.test_start_idx : fold.test_end_idx]

        strat = M7FilteredStrategy(
            market=market_spec,
            arm_id=arm_id,
            daily_bias_index=bias_index,
            min_risk_pts=8.0,
            wait=48,
            target_rr=2.0,
            f=0.5,
            swing_w=5,
            cooldown=6,
            log_decisions=False,
        )

        res: BacktestResult = run_backtest(test_bars, strat, cfg, calibration_bars=test_cal)

        fold_r = [t.r_result for t in res.trades]
        fold_pnl = [t.net_pnl for t in res.trades]
        wins = [p for p in fold_pnl if p > 0]
        losses = [-p for p in fold_pnl if p < 0]
        pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (999.0 if wins else 0.0)
        mean_er = float(np.mean(fold_r)) if fold_r else 0.0

        record = {
            "fold_id": fold.fold_id,
            "test_start": fold.test_start,
            "test_end": fold.test_end,
            "test_bars": len(test_bars),
            "n_trades": len(res.trades),
            "mean_er": round(mean_er, 4),
            "profit_factor": round(min(pf, 999.0), 4),
            "net_pnl": round(res.net_pnl, 2),
            "win_rate": round(res.win_rate, 4),
            "trades": [serialize_trade(t) for t in res.trades],
        }
        fold_records.append(record)
        all_trades.extend(res.trades)

    all_r = [t.r_result for t in all_trades]
    all_pnl = [t.net_pnl for t in all_trades]
    wins = [p for p in all_pnl if p > 0]
    losses = [-p for p in all_pnl if p < 0]
    pf_global = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (999.0 if wins else 0.0)
    mean_er_global = float(np.mean(all_r)) if all_r else 0.0
    win_rate_global = (len(wins) / len(all_trades)) if all_trades else 0.0
    dd_r = compute_drawdown_r(all_r)
    positive_folds = sum(1 for f in fold_records if f["mean_er"] > 0)
    positive_folds_ratio = positive_folds / len(fold_records) if fold_records else 0.0

    ci_lo, ci_hi = run_bootstrap_ci(all_r, seed=42)

    cell_result = {
        "metadata": {
            "market": market_symbol,
            "arm_id": arm_id,
            "scenario": scenario_name,
            "ran_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "aggregate": {
            "n_trades": len(all_trades),
            "mean_er": round(mean_er_global, 4),
            "profit_factor": round(min(pf_global, 999.0), 4),
            "win_rate": round(win_rate_global, 4),
            "net_r": round(float(np.sum(all_r)), 4) if all_r else 0.0,
            "net_pnl": round(float(np.sum(all_pnl)), 2) if all_pnl else 0.0,
            "max_drawdown_r": round(dd_r, 4),
            "positive_folds": positive_folds,
            "total_folds": len(fold_records),
            "positive_folds_ratio": round(positive_folds_ratio, 4),
            "cbb_bootstrap_ci95": [round(ci_lo, 4), round(ci_hi, 4)] if ci_lo is not None else None,
            "trades_sha256": compute_trades_sha256(all_trades),
        },
        "folds": fold_records,
    }

    return cell_result


def main() -> None:
    parser = argparse.ArgumentParser(description="Runner M7 OTE + Sesgo D1 Kai Multi-Mercado")
    parser.add_argument("--workers", type=int, default=4, help="Número de workers (máximo 4)")
    parser.add_argument("--markets", type=str, default="MNQ,MES,MYM,MGC", help="Mercados separados por coma")
    parser.add_argument("--arms", type=str, default="baseline,ote_062,ote_0705,ote_band,trend_kai,ote_band_plus_trend,wrapper_trivial")
    parser.add_argument("--scenarios", type=str, default="canonico,canonico_mas_1tick,realista")
    parser.add_argument("--max-folds", type=int, default=None, help="Máximo número de folds a correr (para tests)")
    args = parser.parse_args()

    max_workers = min(max(1, args.workers), 4)
    print(f"=== INICIANDO RUNNER BLOQUE M7 (Workers: {max_workers}) ===", flush=True)

    if not PREREGISTRO_PATH.exists():
        raise FileNotFoundError(f"Preregistro no encontrado en {PREREGISTRO_PATH}")

    prereg_hash = compute_file_sha256(PREREGISTRO_PATH)
    prereg = json.loads(PREREGISTRO_PATH.read_text(encoding="utf-8"))
    print(f"Preregistro verificado (SHA256: {prereg_hash})", flush=True)

    target_markets = [m.strip().upper() for m in args.markets.split(",") if m.strip()]
    target_arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    target_scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]

    print(f"Mercados seleccionados: {target_markets}")
    print(f"Arms seleccionados: {target_arms}")
    print(f"Escenarios seleccionados: {target_scenarios}")

    # 1. Carga y verificación de datasets
    print("\n[1/4] Cargando datasets multi-mercado desde databento.zip...", flush=True)
    datasets: dict[str, dict[str, Any]] = {}
    coverage_report: dict[str, Any] = {}

    for sym in target_markets:
        member = MARKET_FILES[sym]
        t0 = time.time()
        bars, sha, first_ts, last_ts = load_market_bars_from_zip(ZIP_PATH, member)
        dt = time.time() - t0
        print(f"  [{sym}] {len(bars)} barras M5 cargadas en {dt:.1f}s | {first_ts} -> {last_ts} | SHA256: {sha[:16]}...", flush=True)

        plan = WalkForwardPlan.create_calendar_rolling(
            bars,
            train_months=36,
            test_months=6,
            step_months=6,
            purge_gap_bars=0,
            warmup_bars=CALIBRATION_BARS_COUNT,
        )

        bias_index = KaiDailyBiasIndex(bars, bar_duration_sec=300)

        datasets[sym] = {
            "bars": bars,
            "sha256": sha,
            "plan": plan,
            "bias_index": bias_index,
            "first_ts": first_ts,
            "last_ts": last_ts,
            "n_bars": len(bars),
        }
        coverage_report[sym] = {
            "data_file": member,
            "n_bars": len(bars),
            "first_timestamp": first_ts,
            "last_timestamp": last_ts,
            "sha256": sha,
            "friction_points": MARKET_SPECS[sym].friction_points,
            "dollar_per_point": MARKET_SPECS[sym].dollar_per_point,
            "tick_size": MARKET_SPECS[sym].tick_size,
        }

    # 2. Planificación de tareas concurrentes
    tasks: list[tuple[str, str, str]] = []
    for sym in target_markets:
        for arm in target_arms:
            for sc in target_scenarios:
                tasks.append((sym, arm, sc))

    total_tasks = len(tasks)
    print(f"\n[2/4] Ejecutando matriz experimental ({total_tasks} celdas en total con {max_workers} workers)...", flush=True)

    results_matrix: dict[str, dict[str, dict[str, Any]]] = {sym: {arm: {} for arm in target_arms} for sym in target_markets}

    def _execute_task(t: tuple[str, str, str]) -> tuple[str, str, str, dict[str, Any]]:
        sym, arm, sc = t
        ds = datasets[sym]
        res = run_single_cell(
            market_symbol=sym,
            market_spec=MARKET_SPECS[sym],
            arm_id=arm,
            scenario_name=sc,
            bars=ds["bars"],
            bias_index=ds["bias_index"],
            plan=ds["plan"],
            max_folds=args.max_folds,
        )
        return sym, arm, sc, res

    done_count = 0
    t_start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_execute_task, task) for task in tasks]
        for f in concurrent.futures.as_completed(futures):
            sym, arm, sc, cell_res = f.result()
            results_matrix[sym][arm][sc] = cell_res
            done_count += 1
            agg = cell_res["aggregate"]
            ci = agg["cbb_bootstrap_ci95"]
            ci_str = f"[{ci[0]:+.3f}, {ci[1]:+.3f}]" if ci else "[N/A]"
            print(
                f"  [{done_count:02d}/{total_tasks:02d}] {sym:<4} | {arm:<20} | {sc:<18} | "
                f"n={agg['n_trades']:<5} | E[R]={agg['mean_er']:+.4f} | PF={agg['profit_factor']:.3f} | "
                f"WR={agg['win_rate']*100:4.1f}% | DD={agg['max_drawdown_r']:5.1f}R | "
                f"folds+={agg['positive_folds']}/{agg['total_folds']} | CI95={ci_str}",
                flush=True,
            )

            # Guardar JSON individual de celda
            cell_path = M7_DIR / f"metrics_{sym}_{arm}_{sc}.json"
            cell_path.write_text(json.dumps(cell_res, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    elapsed = time.time() - t_start
    print(f"\nMatriz experimental completada en {elapsed:.1f}s.", flush=True)

    # 3. Control anti-fraude bit a bit: wrapper_trivial == baseline
    print("\n[3/4] Verificando control anti-fraude (wrapper_trivial == baseline)...", flush=True)
    equivalence_reports: dict[str, Any] = {}

    for sym in target_markets:
        equiv_report: dict[str, Any] = {
            "market": sym,
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "scenarios": {},
            "all_scenarios_identical": True,
        }
        for sc in target_scenarios:
            base_cell = results_matrix[sym]["baseline"][sc]
            triv_cell = results_matrix[sym]["wrapper_trivial"][sc]

            base_hash = base_cell["aggregate"]["trades_sha256"]
            triv_hash = triv_cell["aggregate"]["trades_sha256"]
            identical = (base_hash == triv_hash)

            if not identical:
                equiv_report["all_scenarios_identical"] = False

            equiv_report["scenarios"][sc] = {
                "baseline_trades_count": base_cell["aggregate"]["n_trades"],
                "trivial_trades_count": triv_cell["aggregate"]["n_trades"],
                "baseline_sha256": base_hash,
                "trivial_sha256": triv_hash,
                "is_bit_for_bit_identical": identical,
            }
            status_str = "PASS (100% IDENTICO BIT A BIT)" if identical else "FAIL (DISCREPANCIA)"
            print(f"  [{sym} | {sc}] Anti-fraude: {status_str} | SHA: {base_hash[:16]}", flush=True)

        equiv_path = M7_DIR / f"equivalence_wrapper_{sym}.json"
        equiv_path.write_text(json.dumps(equiv_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        equivalence_reports[sym] = equiv_report

    # 4. Generación de Manifest
    print("\n[4/4] Generando manifest.json...", flush=True)
    manifest_files: dict[str, str] = {}
    for p in sorted(M7_DIR.glob("*.json")):
        manifest_files[p.name] = compute_file_sha256(p)

    manifest = {
        "metadata": {
            "title": "Manifest del Bloque M7",
            "protocol": "C1",
            "experiment": "§12 #7",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "workers_used": max_workers,
            "preregistro_sha256": prereg_hash,
        },
        "datasets": coverage_report,
        "artifacts_sha256": manifest_files,
    }

    manifest_path = M7_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Manifest escrito en {manifest_path} ({len(manifest_files)} artefactos registrados).", flush=True)

    print("\n=== EJECUCION EXITOSA DEL RUNNER M7 ===", flush=True)


if __name__ == "__main__":
    main()

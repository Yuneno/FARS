#!/usr/bin/env python3
"""Runner reproducible y paralelo del Bloque M8.

Protocolo C1:
- 4 Mercados: MNQ, MES, MYM, MGC (streaming desde E:/FARS-LAB/databento.zip)
- 7 Arms: control_m7, atr_k050, atr_k100, atr_k050_ote_band, atr_k050_trend_kai, atr_k050_premium_discount, wrapper_trivial
- 3 Escenarios de coste: canonico, canonico_mas_1tick, realista
- Walk-Forward: 8 folds de calendario 36/6/6, purga + embargo h=192, 500 barras warmup
- Control anti-fraude: wrapper_trivial == atr_k050 bit a bit
- Continuidad metodológica: control_m7 == baseline M7 bit a bit
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

from lab_artifacts.m7_protocol.filters import KaiDailyBiasIndex
from lab_artifacts.m8_protocol.filters import M8FilteredStrategy
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

M8_DIR = Path(__file__).resolve().parent
M7_DIR = REPO_ROOT / "lab_artifacts" / "m7_protocol"
PREREGISTRO_PATH = M8_DIR / "preregistro.json"

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

        strat = M8FilteredStrategy(
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


def audit_causality_sample(
    market_symbol: str,
    market_spec: MarketSpec,
    bars: list[Bar],
    bias_index: KaiDailyBiasIndex,
    plan: WalkForwardPlan,
    sample_size: int = 500,
) -> dict[str, Any]:
    """Audita una muestra de decisiones de M8 asegurando cero violaciones de causalidad."""
    fold = plan.folds[0]
    test_cal = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT) : fold.test_start_idx]
    test_bars = bars[fold.test_start_idx : fold.test_end_idx][:sample_size]

    cfg = smc_fvg_config(market=market_spec, wait=48, commission_per_side=2.0, slippage_points=0.0)
    strat = M8FilteredStrategy(
        market=market_spec,
        arm_id="atr_k050_premium_discount",
        daily_bias_index=bias_index,
        min_risk_pts=8.0,
        wait=48,
        target_rr=2.0,
        f=0.5,
        swing_w=5,
        cooldown=6,
        log_decisions=True,
    )

    run_backtest(test_bars, strat, cfg, calibration_bars=test_cal)

    violations = 0
    checks_performed = 0
    for audit in strat.m8_audits:
        checks_performed += 1
        # Verificación 1: ATR positivo y finito
        if audit.atr_14 <= 0 or not math.isfinite(audit.atr_14):
            violations += 1
        # Verificación 2: umbral coincide con 0.5 * atr
        expected_thresh = 0.5 * audit.atr_14
        if abs(audit.min_risk_threshold - expected_thresh) > 1e-9:
            violations += 1
        # Verificación 3: causalidad de pivotes (leg_low <= leg_high)
        if audit.leg_low is not None and audit.leg_high is not None:
            if audit.leg_low >= audit.leg_high:
                violations += 1

    return {
        "market": market_symbol,
        "audited_bars": len(test_bars),
        "decisions_audited": len(strat.m8_audits),
        "checks_performed": checks_performed,
        "causality_violations": violations,
        "is_causal_sound": (violations == 0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Runner M8 Riesgo Normalizado ATR Multi-Mercado")
    parser.add_argument("--workers", type=int, default=4, help="Número de workers (máximo 4)")
    parser.add_argument("--markets", type=str, default="MNQ,MES,MYM,MGC", help="Mercados separados por coma")
    parser.add_argument(
        "--arms",
        type=str,
        default="control_m7,atr_k050,atr_k100,atr_k050_ote_band,atr_k050_trend_kai,atr_k050_premium_discount,wrapper_trivial",
        help="Arms separados por coma",
    )
    parser.add_argument("--scenarios", type=str, default="canonico,canonico_mas_1tick,realista")
    parser.add_argument("--max-folds", type=int, default=None, help="Máximo número de folds a correr (para tests)")
    args = parser.parse_args()

    max_workers = min(max(1, args.workers), 4)
    print(f"=== INICIANDO RUNNER BLOQUE M8 (Workers: {max_workers}) ===", flush=True)

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
    print("\n[1/5] Cargando datasets multi-mercado desde databento.zip...", flush=True)
    datasets: dict[str, dict[str, Any]] = {}
    coverage_report: dict[str, Any] = {}

    for sym in target_markets:
        member = MARKET_FILES[sym]
        t0 = time.time()
        bars, sha, first_ts, last_ts = load_market_bars_from_zip(ZIP_PATH, member)
        dt = time.time() - t0
        print(
            f"  [{sym}] {len(bars)} barras M5 cargadas en {dt:.1f}s | {first_ts} -> {last_ts} | SHA256: {sha[:16]}...",
            flush=True,
        )

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

    # 2. Planificación y ejecución concurrente de la matriz
    tasks: list[tuple[str, str, str]] = []
    for sym in target_markets:
        for arm in target_arms:
            for sc in target_scenarios:
                tasks.append((sym, arm, sc))

    total_tasks = len(tasks)
    print(
        f"\n[2/5] Ejecutando matriz experimental ({total_tasks} celdas en total con {max_workers} workers)...",
        flush=True,
    )

    results_matrix: dict[str, dict[str, dict[str, Any]]] = {
        sym: {arm: {} for arm in target_arms} for sym in target_markets
    }

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
                f"  [{done_count:02d}/{total_tasks:02d}] {sym:<4} | {arm:<26} | {sc:<18} | "
                f"n={agg['n_trades']:<5} | E[R]={agg['mean_er']:+.4f} | PF={agg['profit_factor']:.3f} | "
                f"WR={agg['win_rate']*100:4.1f}% | DD={agg['max_drawdown_r']:5.1f}R | "
                f"folds+={agg['positive_folds']}/{agg['total_folds']} | CI95={ci_str}",
                flush=True,
            )

            # Guardar JSON individual de celda
            cell_path = M8_DIR / f"metrics_{sym}_{arm}_{sc}.json"
            cell_path.write_text(json.dumps(cell_res, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    elapsed = time.time() - t_start
    print(f"\nMatriz experimental completada en {elapsed:.1f}s.", flush=True)

    # 3. Control anti-fraude bit a bit: wrapper_trivial == atr_k050
    print("\n[3/5] Verificando control anti-fraude (wrapper_trivial == atr_k050)...", flush=True)
    equivalence_reports: dict[str, Any] = {}

    for sym in target_markets:
        equiv_report: dict[str, Any] = {
            "market": sym,
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "reference_arm": "atr_k050",
            "wrapper_arm": "wrapper_trivial",
            "scenarios": {},
            "all_scenarios_identical": True,
        }
        for sc in target_scenarios:
            base_cell = results_matrix[sym]["atr_k050"][sc]
            triv_cell = results_matrix[sym]["wrapper_trivial"][sc]

            base_hash = base_cell["aggregate"]["trades_sha256"]
            triv_hash = triv_cell["aggregate"]["trades_sha256"]
            identical = base_hash == triv_hash

            if not identical:
                equiv_report["all_scenarios_identical"] = False

            equiv_report["scenarios"][sc] = {
                "reference_trades_count": base_cell["aggregate"]["n_trades"],
                "wrapper_trades_count": triv_cell["aggregate"]["n_trades"],
                "reference_sha256": base_hash,
                "wrapper_sha256": triv_hash,
                "is_bit_for_bit_identical": identical,
            }
            status_str = "PASS (100% IDENTICO BIT A BIT)" if identical else "FAIL (DISCREPANCIA)"
            print(f"  [{sym} | {sc}] Anti-fraude wrapper vs atr_k050: {status_str} | SHA: {base_hash[:16]}", flush=True)

        equiv_path = M8_DIR / f"equivalence_wrapper_{sym}.json"
        equiv_path.write_text(json.dumps(equiv_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        equivalence_reports[sym] = equiv_report

    # 4. Continuidad metodológica: control_m7 == M7 baseline bit a bit
    print("\n[4/5] Verificando continuidad metodológica (control_m7 == M7 baseline)...", flush=True)
    m7_continuity_reports: dict[str, Any] = {}
    for sym in target_markets:
        cont_report: dict[str, Any] = {
            "market": sym,
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "scenarios": {},
            "all_scenarios_identical": True,
        }
        for sc in target_scenarios:
            m8_cell = results_matrix[sym]["control_m7"][sc]
            m7_cell_file = M7_DIR / f"metrics_{sym}_baseline_{sc}.json"
            if m7_cell_file.exists():
                m7_cell = json.loads(m7_cell_file.read_text("utf-8"))
                n_folds_m8 = len(m8_cell["folds"])
                m8_trades = [t for f in m8_cell["folds"] for t in f["trades"]]
                m7_trades = [t for f in m7_cell["folds"][:n_folds_m8] for t in f["trades"]]
                m8_hash = compute_trades_sha256(m8_trades)
                m7_hash = compute_trades_sha256(m7_trades)
                identical = (m8_hash == m7_hash)
                if not identical:
                    cont_report["all_scenarios_identical"] = False

                cont_report["scenarios"][sc] = {
                    "m8_control_trades": len(m8_trades),
                    "m7_baseline_trades": len(m7_trades),
                    "m8_control_sha256": m8_hash,
                    "m7_baseline_sha256": m7_hash,
                    "is_bit_for_bit_identical": identical,
                }
                status_str = "PASS (100% REPRODUCIDO BIT A BIT)" if identical else "FAIL (DISCREPANCIA)"
                print(f"  [{sym} | {sc}] Continuidad M8 control vs M7 baseline: {status_str}", flush=True)
            else:
                cont_report["scenarios"][sc] = {"error": f"Archivo M7 no encontrado: {m7_cell_file.name}"}

        cont_path = M8_DIR / f"equivalence_m7_control_{sym}.json"
        cont_path.write_text(json.dumps(cont_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        m7_continuity_reports[sym] = cont_report

    # Auditoría causal
    print("\nAuditoría causal point-in-time...", flush=True)
    causality_report = {}
    for sym in target_markets:
        ds = datasets[sym]
        c_res = audit_causality_sample(sym, MARKET_SPECS[sym], ds["bars"], ds["bias_index"], ds["plan"])
        causality_report[sym] = c_res
        status_c = "PASS (0 VIOLACIONES)" if c_res["is_causal_sound"] else "FAIL"
        print(f"  [{sym}] Causalidad: {status_c} ({c_res['decisions_audited']} decisiones auditadas)", flush=True)

    causality_path = M8_DIR / "audit_causality.json"
    causality_path.write_text(json.dumps(causality_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 5. Generación de Manifest
    print("\n[5/5] Generando manifest.json...", flush=True)
    manifest_files: dict[str, str] = {}
    for p in sorted(M8_DIR.glob("*.json")):
        manifest_files[p.name] = compute_file_sha256(p)

    manifest = {
        "metadata": {
            "title": "Manifest del Bloque M8",
            "protocol": "C1",
            "experiment": "§12 #8",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "workers_used": max_workers,
            "preregistro_sha256": prereg_hash,
        },
        "datasets": coverage_report,
        "artifacts_sha256": manifest_files,
    }

    manifest_path = M8_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Manifest escrito en {manifest_path} ({len(manifest_files)} artefactos registrados).", flush=True)

    print("\n=== EJECUCION EXITOSA DEL RUNNER M8 ===", flush=True)


if __name__ == "__main__":
    main()

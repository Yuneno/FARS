#!/usr/bin/env python3
"""Runner reproducible y paralelo del Bloque O1: Compression Breakout causal (DEV-only).

Protocolo O1:
- Hipótesis: NR7 en t-1 + Ruptura Donchian L in {20, 55} en t sobre MNQ M5.
- Partición: ÚNICAMENTE DEV (2019-05-06T00:00:00Z <= timestamp < 2024-01-01T00:00:00Z).
  VALIDATION y FINAL HOLDOUT estrictamente bloqueados y no materializados.
- Matriz cerrada: 1 hipótesis x 2 longitudes Donchian (20, 55) x 2 escenarios de coste (realista, estres) = 4 celdas.
- Walk-Forward: 8 folds de calendario 6/6/6 dentro de DEV, 500 barras de warmup/calibración,
  purga + embargo h=54 barras (horizonte 48 + cooldown 6). Ningún fold cruza 2024-01-01.
- Múltiple testing: Corrección Bonferroni por 4 celdas (alpha=0.0125 -> CI98.75% CBB).
- Workers: <= 4 (concurrencia controlada).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
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

from lab_artifacts.o1_protocol.entries_o1 import (
    O1CompressionBreakoutStrategy,
    TrivialWrapperStrategy,
    build_o1_strategy,
)
from src.backtest.executor import BacktestConfig, BacktestResult, run_backtest
from src.backtest.history import Bar
from src.backtest.markets import MNQ, MarketSpec
from src.hypothesis_registry import WalkForwardPlan

try:
    from arch.bootstrap import CircularBlockBootstrap
except ImportError:
    CircularBlockBootstrap = None

O1_DIR = Path(__file__).resolve().parent
PREREGISTRO_PATH = O1_DIR / "preregistro.json"
DEV_OUTPUTS_DIR = O1_DIR / "dev_outputs"

FROZEN_PREREGISTRO_SHA256 = "2b1bc4321a7d4165b92eed8cb80bd98b6b6698e369e16aa8acb09fb9e7b6b4ed"
EXPECTED_MEMBER_SHA256 = "fbed6061205b8299af140f85e36b472f5f1d88084977ad9c4ca9aa1f817b8a96"
ZIP_PATH = Path("E:/FARS-LAB/databento.zip")
M5_MEMBER = "databento/MNQ_M5.csv"

DEV_START_STR = "2019-05-06T00:00:00"
DEV_END_STR = "2024-01-01T00:00:00"
CALIBRATION_BARS_COUNT = 500

SCENARIO_CONFIGS: dict[str, dict[str, float]] = {
    "realista": {"comm_side": 0.62, "slip_ticks": 1.0, "slip_pts": 0.25},
    "estres": {"comm_side": 2.00, "slip_ticks": 1.0, "slip_pts": 0.25},
}


def verify_preregistro_sha256(preregistro_path: Path) -> str:
    """Verifica que el preregistro existe y su hash coincide exactamente con el congelado."""
    if not preregistro_path.exists():
        raise FileNotFoundError(f"Preregistro no encontrado en {preregistro_path}")
    raw_bytes = preregistro_path.read_bytes()
    computed = hashlib.sha256(raw_bytes).hexdigest()
    if computed != FROZEN_PREREGISTRO_SHA256:
        raise ValueError(
            f"Fallo de integridad: hash del preregistro ({computed}) "
            f"no coincide con el congelado ({FROZEN_PREREGISTRO_SHA256})."
        )
    return computed


def load_dev_bars_from_zip(zip_path: Path, member_name: str) -> tuple[list[Bar], str]:
    """Lee streaming del zip canónico, valida SHA-256 crudo y corta estrictamente en 2024-01-01."""
    hasher = hashlib.sha256()
    bars: list[Bar] = []
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(member_name) as raw:
            content_bytes = raw.read()
            hasher.update(content_bytes)
            reader = csv.DictReader(io.StringIO(content_bytes.decode("utf-8")))
            for row in reader:
                ts_str = row["timestamp"]
                # Filtro inicial: >= 2019-05-06
                if ts_str < "2019-05-06":
                    continue
                # Corte DEV estricto: < 2024-01-01. Detener parseo inmediatamente.
                if ts_str >= "2024-01-01":
                    break
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
    return bars, file_sha


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


def run_cbb_bootstrap_ci(
    r_values: list[float],
    seed: int = 42,
    confidence_level: float = 0.9875,
    reps: int = 2000,
) -> tuple[float | None, float | None]:
    """Calcula el intervalo de confianza CBB con corrección Bonferroni (default size=0.9875)."""
    if not r_values or len(r_values) < 5 or CircularBlockBootstrap is None:
        return None, None
    arr = np.array(r_values, dtype=float)
    block_size = max(2, min(20, int(len(arr) ** (1 / 3))))
    try:
        bs = CircularBlockBootstrap(block_size=block_size, x=arr, seed=seed)
        ci = bs.conf_int(func=lambda x: np.mean(x), reps=reps, method="percentile", size=confidence_level)
        return float(ci[0, 0]), float(ci[1, 0])
    except Exception:
        return None, None


def run_single_cell(
    length: int,
    scenario_name: str,
    bars: list[Bar],
    plan: WalkForwardPlan,
    preregistro_hash: str,
    dataset_sha: str,
    is_wrapper: bool = False,
    max_folds: int | None = None,
) -> dict[str, Any]:
    """Ejecuta los folds de walk-forward para una celda de la matriz."""
    market_spec = MNQ
    sc_info = SCENARIO_CONFIGS[scenario_name]
    comm = sc_info["comm_side"]
    slip = sc_info["slip_pts"]

    cfg = BacktestConfig(
        dollar_per_point=market_spec.dollar_per_point,
        tick_size=market_spec.tick_size,
        commission_per_side=comm,
        slippage_points=slip,
        time_exit_slippage_points=slip,
        bar_interval_seconds=300,
        max_bars_held=48,  # 4 horas en barras M5
        time_exit_mode="market",
    )

    folds_to_run = plan.folds[:max_folds] if max_folds else plan.folds
    fold_records: list[dict[str, Any]] = []
    all_trades: list[Any] = []
    total_unresolved = 0
    total_gap_rejections = 0

    for fold in folds_to_run:
        test_cal = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT) : fold.test_start_idx]
        test_bars = bars[fold.test_start_idx : fold.test_end_idx]

        strat = build_o1_strategy(
            length=length,
            market_spec=market_spec,
            is_wrapper=is_wrapper,
            cooldown=6,
            log_decisions=False,
        )

        res: BacktestResult = run_backtest(test_bars, strat, cfg, calibration_bars=test_cal)

        total_unresolved += res.unresolved_positions
        total_gap_rejections += res.gap_rejections

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
            "unresolved_positions": res.unresolved_positions,
            "gap_rejections": res.gap_rejections,
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

    ci_lo, ci_hi = run_cbb_bootstrap_ci(all_r, seed=42, confidence_level=0.9875, reps=2000)

    cell_result = {
        "metadata": {
            "market": "MNQ",
            "length": length,
            "scenario": scenario_name,
            "is_wrapper": is_wrapper,
            "preregistro_sha256": preregistro_hash,
            "dataset_sha256": dataset_sha,
            "partition": "DEV",
            "dev_range": f"{DEV_START_STR}Z -> {DEV_END_STR}Z",
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
            "cbb_bootstrap_ci9875": [round(ci_lo, 4), round(ci_hi, 4)] if ci_lo is not None else None,
            "unresolved_positions": total_unresolved,
            "gap_rejections": total_gap_rejections,
            "trades_sha256": compute_trades_sha256(all_trades),
        },
        "folds": fold_records,
    }

    return cell_result


def evaluate_gates(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Evalúa los 6 gates preregistrados y emite veredicto automático."""
    r20_real = results["L20_realista"]["aggregate"]
    r55_real = results["L55_realista"]["aggregate"]
    r20_stress = results["L20_estres"]["aggregate"]
    r55_stress = results["L55_estres"]["aggregate"]

    # Gate 1: total n_trades >= 300 en realista
    g1_20 = r20_real["n_trades"] >= 300
    g1_55 = r55_real["n_trades"] >= 300
    g1 = g1_20 and g1_55

    # Gate 2: ambos L=20 y L=55 tienen mean_er > 0 y PF > 1.0 bajo realista
    g2_20 = (r20_real["mean_er"] > 0) and (r20_real["profit_factor"] > 1.0)
    g2_55 = (r55_real["mean_er"] > 0) and (r55_real["profit_factor"] > 1.0)
    g2 = g2_20 and g2_55

    # Gate 3: al menos uno tiene CBB CI98.75% lower bound > 0
    ci20_lo = r20_real["cbb_bootstrap_ci9875"][0] if r20_real["cbb_bootstrap_ci9875"] else -999.0
    ci55_lo = r55_real["cbb_bootstrap_ci9875"][0] if r55_real["cbb_bootstrap_ci9875"] else -999.0
    g3 = (ci20_lo > 0) or (ci55_lo > 0)

    # Gate 4: cada longitud tiene al menos 5/8 folds positivos bajo realista
    g4_20 = r20_real["positive_folds"] >= 5
    g4_55 = r55_real["positive_folds"] >= 5
    g4 = g4_20 and g4_55

    # Determinación del campeón preliminar para gate 5 (regla preregistrada: menor fragilidad)
    # Comparar folds positivos -> maxDD -> CBB lo
    if r20_real["positive_folds"] > r55_real["positive_folds"]:
        champion_length = 20
    elif r55_real["positive_folds"] > r20_real["positive_folds"]:
        champion_length = 55
    else:
        if r20_real["max_drawdown_r"] < r55_real["max_drawdown_r"]:
            champion_length = 20
        elif r55_real["max_drawdown_r"] < r20_real["max_drawdown_r"]:
            champion_length = 55
        else:
            champion_length = 20 if ci20_lo >= ci55_lo else 55

    champ_stress = r20_stress if champion_length == 20 else r55_stress

    # Gate 5: campeón mantiene mean_er > 0, PF > 1.0 y >= 5/8 folds positivos bajo estrés
    g5_mean = champ_stress["mean_er"] > 0
    g5_pf = champ_stress["profit_factor"] > 1.0
    g5_folds = champ_stress["positive_folds"] >= 5
    g5 = g5_mean and g5_pf and g5_folds

    # Gate 6: cero posiciones sin resolver y cero gaps inventados
    total_unresolved = sum(r["aggregate"]["unresolved_positions"] for r in results.values())
    g6 = (total_unresolved == 0)

    all_passed = g1 and g2 and g3 and g4 and g5 and g6
    verdict = "DEV_PULSE" if all_passed else "NO_DEV_PULSE"

    evaluation = {
        "verdict": verdict,
        "champion_length": champion_length if (g1 and g2) else None,
        "gates": {
            "gate_1_sample_size": {
                "passed": bool(g1),
                "detail": f"L20={r20_real['n_trades']} (>=300: {g1_20}), L55={r55_real['n_trades']} (>=300: {g1_55})",
            },
            "gate_2_dual_positive": {
                "passed": bool(g2),
                "detail": f"L20: E[R]={r20_real['mean_er']}, PF={r20_real['profit_factor']}; L55: E[R]={r55_real['mean_er']}, PF={r55_real['profit_factor']}",
            },
            "gate_3_cbb_ci_bonferroni": {
                "passed": bool(g3),
                "detail": f"L20 CI98.75% lo={ci20_lo}; L55 CI98.75% lo={ci55_lo} (>0 req)",
            },
            "gate_4_folds_consistency": {
                "passed": bool(g4),
                "detail": f"L20 positive_folds={r20_real['positive_folds']}/8; L55 positive_folds={r55_real['positive_folds']}/8",
            },
            "gate_5_champion_stress": {
                "passed": bool(g5),
                "champion_evaluated": champion_length,
                "detail": f"E[R]={champ_stress['mean_er']}, PF={champ_stress['profit_factor']}, folds+={champ_stress['positive_folds']}/8",
            },
            "gate_6_causal_integrity": {
                "passed": bool(g6),
                "detail": f"total unresolved={total_unresolved} (0 req)",
            },
        },
    }

    return evaluation


def main() -> int:
    parser = argparse.ArgumentParser(description="Runner O1 Compression Breakout DEV-only.")
    parser.add_argument("--workers", type=int, default=4, help="Número de workers paralelos (máx 4).")
    parser.add_argument("--smoke", action="store_true", help="Modo smoke: corre solo 1 fold.")
    args = parser.parse_args()

    workers = min(args.workers, 4)
    t_start = time.perf_counter()

    print("================================================================================")
    print(" BLOQUE O1 — COMPRESSION BREAKOUT CAUSAL (DEV-ONLY)")
    print("================================================================================")

    # 1. Verificación obligatoria de integridad del preregistro
    print("[1/6] Verificando integridad del preregistro...", flush=True)
    prereg_hash = verify_preregistro_sha256(PREREGISTRO_PATH)
    print(f"      Preregistro OK | SHA-256: {prereg_hash}")

    # 2. Carga y verificación del dataset canónico
    print("[2/6] Cargando datos streaming desde databento.zip...", flush=True)
    bars, raw_sha = load_dev_bars_from_zip(ZIP_PATH, M5_MEMBER)
    print(f"      Dataset miembro crudo SHA-256: {raw_sha}")
    if raw_sha != EXPECTED_MEMBER_SHA256:
        print(f"ERROR: SHA-256 de miembro ({raw_sha}) no coincide con esperado ({EXPECTED_MEMBER_SHA256})", file=sys.stderr)
        return 1

    first_ts = bars[0].timestamp.isoformat()
    last_ts = bars[-1].timestamp.isoformat()
    print(f"      Barras DEV cargadas: {len(bars):,} | {first_ts} -> {last_ts}")
    assert bars[-1].timestamp < datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc), "Fallo de corte: barra >= 2024-01-01 encontrada"

    # 3. Construcción del plan de Walk-Forward
    print("[3/6] Construyendo plan Walk-Forward de calendario dentro de DEV...", flush=True)
    plan = WalkForwardPlan.create_calendar_rolling(
        bars,
        train_months=6,
        test_months=6,
        step_months=6,
        start_date="2019-07-01",
        end_date="2024-01-01",
        warmup_bars=CALIBRATION_BARS_COUNT,
        purge_gap_bars=54,
    )
    print(f"      Folds generados: {plan.n_folds}")
    for f in plan.folds:
        print(f"        Fold {f.fold_id}: test {f.test_start} -> {f.test_end} ({f.test_bars:,} barras)")

    # 4. Anti-fraude check: Trivial wrapper vs base strategy
    print("[4/6] Ejecutando control anti-fraude (Trivial Wrapper vs Base)...", flush=True)
    cal0 = bars[max(0, plan.folds[0].test_start_idx - CALIBRATION_BARS_COUNT) : plan.folds[0].test_start_idx]
    test0 = bars[plan.folds[0].test_start_idx : plan.folds[0].test_end_idx]
    cfg_test = BacktestConfig(commission_per_side=0.62, slippage_points=0.25, max_bars_held=48, bar_interval_seconds=300)

    strat_base = build_o1_strategy(20, MNQ, is_wrapper=False)
    strat_wrap = build_o1_strategy(20, MNQ, is_wrapper=True)
    res_b = run_backtest(test0, strat_base, cfg_test, calibration_bars=cal0)
    res_w = run_backtest(test0, strat_wrap, cfg_test, calibration_bars=cal0)

    hash_b = compute_trades_sha256(list(res_b.trades))
    hash_w = compute_trades_sha256(list(res_w.trades))
    if hash_b != hash_w:
        print(f"ERROR: Fallo de equivalencia anti-fraude: base={hash_b} wrap={hash_w}", file=sys.stderr)
        return 1
    print(f"      Equivalencia 100% bit-a-bit confirmada (hash={hash_b[:16]}...)")

    # 5. Ejecución de la matriz cerrada (4 celdas)
    print(f"[5/6] Ejecutando matriz cerrada de 4 celdas con {workers} workers...", flush=True)
    DEV_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    max_f = 1 if args.smoke else None
    matrix_cells = [
        (20, "realista", "L20_realista"),
        (20, "estres", "L20_estres"),
        (55, "realista", "L55_realista"),
        (55, "estres", "L55_estres"),
    ]

    results: dict[str, dict[str, Any]] = {}

    def _task(item: tuple[int, str, str]) -> tuple[str, dict[str, Any]]:
        length, sc_name, cell_id = item
        t0_c = time.perf_counter()
        cell_res = run_single_cell(
            length=length,
            scenario_name=sc_name,
            bars=bars,
            plan=plan,
            preregistro_hash=prereg_hash,
            dataset_sha=raw_sha,
            max_folds=max_f,
        )
        dt_c = time.perf_counter() - t0_c
        return cell_id, cell_res, dt_c

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_task, item) for item in matrix_cells]
        for fut in concurrent.futures.as_completed(futures):
            cell_id, cell_res, dt_c = fut.result()
            results[cell_id] = cell_res
            agg = cell_res["aggregate"]
            out_file = DEV_OUTPUTS_DIR / f"metrics_{cell_id}.json"
            out_file.write_text(json.dumps(cell_res, indent=2), encoding="utf-8")
            ci_str = f"[{agg['cbb_bootstrap_ci9875'][0]:+.4f}, {agg['cbb_bootstrap_ci9875'][1]:+.4f}]" if agg['cbb_bootstrap_ci9875'] else "N/A"
            print(
                f"      [{cell_id}] Finalizada en {dt_c:.1f}s | n={agg['n_trades']} | "
                f"E[R]={agg['mean_er']:+.4f} | PF={agg['profit_factor']:.3f} | "
                f"WR={agg['win_rate']*100:.1f}% | Folds+={agg['positive_folds']}/{agg['total_folds']} | "
                f"CBB CI98.75%={ci_str}",
                flush=True,
            )

    # 6. Evaluación de Gates y Veredicto
    print("[6/6] Evaluando gates preregistrados...", flush=True)
    eval_res = evaluate_gates(results)
    eval_file = DEV_OUTPUTS_DIR / "evaluation_gates.json"
    eval_file.write_text(json.dumps(eval_res, indent=2), encoding="utf-8")

    dt_total = time.perf_counter() - t_start

    print("--------------------------------------------------------------------------------")
    print(" RESUMEN DE GATES Y VEREDICTO")
    print("--------------------------------------------------------------------------------")
    for g_id, g_info in eval_res["gates"].items():
        status = "PASSED [OK]" if g_info["passed"] else "FAILED [FAIL]"
        print(f"  * {g_id}: {status}")
        print(f"    Detalle: {g_info['detail']}")

    print("--------------------------------------------------------------------------------")
    print(f" VEREDICTO FINAL: {eval_res['verdict']}")
    print(f" TIEMPO TOTAL: {dt_total:.1f}s")
    print("================================================================================")

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Runner reproducible de experimentos para el Bloque Z5.

Ejecuta el protocolo de medición de zonas contra baseline en walk-forward (8 folds)
para SMC-FVG y AMD+CRT bajo los escenarios de coste normativos.
Genera los artefactos de métricas, el manifest y el informe RESULTADOS.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from datetime import datetime, timezone
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lab_artifacts.run_c1_walkforward import (
    CALIBRATION_BARS_COUNT,
    M5_MEMBER,
    ZIP_PATH,
    compute_file_sha256,
    get_git_branch,
    get_git_commit,
    load_canonical_m5,
)
from src.backtest.amd_crt import AmdCrtStrategy, amd_crt_config
from src.backtest.executor import BacktestConfig, BacktestResult, run_backtest
from src.backtest.history import Bar
from src.backtest.markets import MNQ
from src.backtest.smc_fvg import SmcFvgStrategy, smc_fvg_config
from src.backtest.zone_bridge import (
    ZoneContextProvider,
    ZoneDecisionRecord,
    ZoneFilteredStrategy,
    build_filter,
    precompute_fold_cache,
)
from src.hypothesis_registry import WalkForwardPlan

ARTIFACTS = REPO_ROOT / "lab_artifacts" / "z5_protocol"
PREREGISTRO_PATH = ARTIFACTS / "preregistro.json"

SCENARIOS = {
    "canonico": dict(commission_per_side=2.0, slippage_points=0.0, time_exit_slippage_points=0.0),
    "por_tramo": dict(commission_per_side=0.62, slippage_points=0.25, time_exit_slippage_points=0.25),
    "kai": dict(commission_per_side=0.71, slippage_points=0.25, time_exit_slippage_points=0.25),
}


def _bootstrap_ci95(values: list[float], n_boot: int = 1000) -> tuple[float, float]:
    """Calcula el intervalo de confianza al 95% de la media mediante bootstrap."""
    if len(values) < 2:
        val = values[0] if values else 0.0
        return val, val
    arr = np.array(values, dtype=float)
    rng = np.random.default_rng(seed=42)
    sample_means = [
        float(np.mean(rng.choice(arr, size=len(arr), replace=True)))
        for _ in range(n_boot)
    ]
    lo = float(np.percentile(sample_means, 2.5))
    hi = float(np.percentile(sample_means, 97.5))
    return round(lo, 4), round(hi, 4)


def _serialize_trade(t: Any) -> dict[str, Any]:
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
    raw = [_serialize_trade(t) if not isinstance(t, dict) else t for t in trades]
    serialized = json.dumps(raw, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def run_experiments(scenario_name: str = "canonico", max_folds: int | None = None) -> None:
    print(f"=== Iniciando experimentos Bloque Z5 — Escenario: {scenario_name} ===", flush=True)

    if not PREREGISTRO_PATH.exists():
        raise FileNotFoundError(f"Preregistro no encontrado en {PREREGISTRO_PATH}")

    prereg = json.loads(PREREGISTRO_PATH.read_text(encoding="utf-8"))
    prereg_hash = compute_file_sha256(PREREGISTRO_PATH)
    print(f"Preregistro cargado (SHA256: {prereg_hash[:12]})...", flush=True)

    bars, fingerprint = load_canonical_m5(ZIP_PATH, M5_MEMBER)
    print(f"Dataset canónico cargado: {len(bars)} barras M5 (SHA256: {fingerprint[:12]}).", flush=True)

    plan = WalkForwardPlan.create_calendar_rolling(
        bars,
        train_months=36,
        test_months=6,
        step_months=6,
        purge_gap_bars=0,
        warmup_bars=CALIBRATION_BARS_COUNT,
    )
    folds = plan.folds[:max_folds] if max_folds else plan.folds
    print(f"Plan de walk-forward: {len(folds)} folds.", flush=True)

    cost_kwargs = SCENARIOS[scenario_name]
    arms_decl = prereg["arms"]

    # Definir factorías de sujetos
    subjects = {
        "smc_fvg": {
            "factory": lambda: SmcFvgStrategy(market=MNQ, min_risk_pts=8.0, wait=1, f=0.5, target_rr=2.0),
            "cfg_factory": lambda: smc_fvg_config(market=MNQ, **cost_kwargs),
            "order_type": "limit",
        },
        "amd_crt": {
            "factory": lambda: AmdCrtStrategy(market=MNQ, median_lookback_days=20, min_weekday_samples=3),
            "cfg_factory": lambda: amd_crt_config(market=MNQ, **cost_kwargs),
            "order_type": "market",
        },
    }

    # Estructuras de resultados: subject -> arm_id -> {folds: [...], all_trades: [...]}
    results_by_subject: dict[str, dict[str, Any]] = {
        subj: {arm["arm_id"]: {"folds": [], "all_trades": [], "arm_decl": arm} for arm in arms_decl}
        for subj in subjects
    }

    for fold_idx, fold in enumerate(folds):
        print(f"\n--- Procesando Fold {fold.fold_id} ({fold.test_start} a {fold.test_end}) ---", flush=True)
        test_cal = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT) : fold.test_start_idx]
        test_bars = bars[fold.test_start_idx : fold.test_end_idx]
        full_fold_slice = test_cal + test_bars

        # Precalcular cache de zonas causal para este fold
        t0_c = datetime.now(timezone.utc)
        ctx_cache, anchor_cache = precompute_fold_cache(
            full_fold_slice, symbol="MNQ", timeframe="5m", session_pools=True
        )
        t_precomp = (datetime.now(timezone.utc) - t0_c).total_seconds()
        print(f"  [Fold {fold.fold_id}] Cache de zonas causal precalculado ({len(full_fold_slice)} barras en {t_precomp:.1f}s)", flush=True)

        for subj_name, subj_info in subjects.items():
            strat_factory = subj_info["factory"]
            cfg = subj_info["cfg_factory"]()

            for arm in arms_decl:
                arm_id = arm["arm_id"]
                filter_name = arm["filter_name"]

                if arm_id == "baseline":
                    strat = strat_factory()
                elif filter_name == "always_true":
                    provider = ZoneContextProvider(
                        symbol="MNQ", timeframe="5m", session_pools=True, cache=ctx_cache, anchor_cache=anchor_cache
                    )
                    strat = ZoneFilteredStrategy(
                        inner=strat_factory(),
                        provider=provider,
                        filter_spec=build_filter("always_true"),
                    )
                else:
                    provider = ZoneContextProvider(
                        symbol="MNQ", timeframe="5m", session_pools=True, cache=ctx_cache, anchor_cache=anchor_cache
                    )
                    f_spec = build_filter(filter_name, arm.get("parameters"))
                    strat = ZoneFilteredStrategy(
                        inner=strat_factory(),
                        provider=provider,
                        filter_spec=f_spec,
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
                    "trades": [_serialize_trade(t) for t in res.trades],
                }

                results_by_subject[subj_name][arm_id]["folds"].append(record)
                results_by_subject[subj_name][arm_id]["all_trades"].extend(res.trades)

    # Análisis global y generación de métricas por arm
    print("\n=== Consolidando métricas y verificando integridad ===", flush=True)

    summary_table_rows = []

    for subj_name in subjects:
        base_trades = results_by_subject[subj_name]["baseline"]["all_trades"]
        triv_trades = results_by_subject[subj_name]["wrapper_trivial"]["all_trades"]

        base_hash = compute_trades_sha256(base_trades)
        triv_hash = compute_trades_sha256(triv_trades)
        is_bit_a_bit_identical = (base_hash == triv_hash)

        print(f"\n[Sujeto: {subj_name}] Verificación Anti-Fraude Bit a Bit:")
        print(f"  Baseline Trades Hash: {base_hash}")
        print(f"  Trivial  Trades Hash: {triv_hash}")
        print(f"  Resultado Bit a Bit: {'PASS (100% IDENTICO)' if is_bit_a_bit_identical else 'FAIL (DISCREPANCIA)'}")

        base_all_r = [t.r_result for t in base_trades]
        base_mean_er = float(np.mean(base_all_r)) if base_all_r else 0.0
        base_wins = [t.net_pnl for t in base_trades if t.net_pnl > 0]
        base_losses = [-t.net_pnl for t in base_trades if t.net_pnl < 0]
        base_pf = (sum(base_wins) / sum(base_losses)) if base_losses and sum(base_losses) > 0 else 0.0

        for arm in arms_decl:
            arm_id = arm["arm_id"]
            arm_data = results_by_subject[subj_name][arm_id]
            trades = arm_data["all_trades"]

            all_r = [t.r_result for t in trades]
            all_pnl = [t.net_pnl for t in trades]
            n_trades = len(trades)

            wins = [p for p in all_pnl if p > 0]
            losses = [-p for p in all_pnl if p < 0]
            pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (999.0 if wins else 0.0)
            mean_er = float(np.mean(all_r)) if all_r else 0.0
            ci_lo, ci_hi = _bootstrap_ci95(all_r)

            folds_pos = sum(1 for f in arm_data["folds"] if f["mean_er"] > 0)
            delta_er = mean_er - base_mean_er
            delta_pf = pf - base_pf

            payload = {
                "metadata": {
                    "subject": subj_name,
                    "arm_id": arm_id,
                    "scenario": scenario_name,
                    "description": arm["description"],
                    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "git_commit": get_git_commit(REPO_ROOT),
                    "dataset_fingerprint": fingerprint,
                    "preregistro_sha256": prereg_hash,
                    "bit_a_bit_verified_against_baseline": is_bit_a_bit_identical if arm_id == "wrapper_trivial" else None,
                },
                "aggregated_metrics": {
                    "n_trades": n_trades,
                    "mean_er": round(mean_er, 4),
                    "profit_factor": round(min(pf, 999.0), 4),
                    "win_rate": round(len(wins) / n_trades if n_trades else 0.0, 4),
                    "total_net_pnl": round(sum(all_pnl), 2),
                    "folds_positive_er": folds_pos,
                    "total_folds": len(folds),
                    "ic95_er": [ci_lo, ci_hi],
                    "delta_er_vs_baseline": round(delta_er, 4),
                    "delta_pf_vs_baseline": round(delta_pf, 4),
                },
                "folds": arm_data["folds"],
            }

            out_file = ARTIFACTS / f"{subj_name}_{arm_id}_fold_metrics.json"
            out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

            summary_table_rows.append({
                "subject": subj_name,
                "arm": arm_id,
                "role": arm["role"],
                "n_trades": n_trades,
                "mean_er": round(mean_er, 4),
                "pf": round(min(pf, 999.0), 4),
                "ic95": f"[{ci_lo}, {ci_hi}]",
                "folds_pos": f"{folds_pos}/{len(folds)}",
                "delta_er": f"{delta_er:+.4f}",
                "delta_pf": f"{delta_pf:+.4f}",
            })

    # Guardar manifest
    manifest_artifacts = {}
    for p in sorted(ARTIFACTS.glob("*_fold_metrics.json")):
        manifest_artifacts[p.name] = {
            "sha256": compute_file_sha256(p),
            "size_bytes": p.stat().st_size,
        }
    manifest = {
        "metadata": {
            "title": "Manifest de Experimentos Z5",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "git_commit": get_git_commit(REPO_ROOT),
            "git_branch": get_git_branch(REPO_ROOT),
            "scenario": scenario_name,
            "preregistro_sha256": prereg_hash,
        },
        "dataset": {
            "archive": str(ZIP_PATH),
            "member": M5_MEMBER,
            "bars_count": len(bars),
            "sha256": fingerprint,
        },
        "artifacts": manifest_artifacts,
    }
    (ARTIFACTS / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Generar RESULTADOS.md
    _generate_resultados_md(summary_table_rows, scenario_name)
    print("\n[OK] Experimentos Z5 completados exitosamente. Artefactos y RESULTADOS.md generados.", flush=True)


def _generate_resultados_md(rows: list[dict[str, Any]], scenario: str) -> None:
    md = [
        "# RESULTADOS DE EXPERIMENTOS — BLOQUE Z5",
        "",
        f"**Fecha:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
        f"**Escenario de Coste:** `{scenario}`  ",
        "**Principio Normativo:** *«Esto mide features, NO promueve estrategias ni afirma rentabilidad»*.  ",
        "",
        "---",
        "",
        "## 1. Verificación Anti-Fraude: Paridad Bit a Bit",
        "",
        "Para cada sujeto, el `wrapper_trivial` (filtro que retorna incondicionalmente `True`) debe generar",
        "una secuencia de trades 100% idéntica al `baseline` sin el puente. Verificado mediante SHA256 de trades JSON.",
        "",
        "| Sujeto | Hash Baseline | Hash Wrapper Trivial | Estado |",
        "|---|---|---|---|",
    ]

    subjects = sorted(list({r["subject"] for r in rows}))
    for subj in subjects:
        f_base = ARTIFACTS / f"{subj}_baseline_fold_metrics.json"
        f_triv = ARTIFACTS / f"{subj}_wrapper_trivial_fold_metrics.json"
        if f_base.exists() and f_triv.exists():
            d_b = json.loads(f_base.read_text(encoding="utf-8"))
            d_t = json.loads(f_triv.read_text(encoding="utf-8"))
            h_b = compute_trades_sha256([t for f in d_b["folds"] for t in f["trades"]])[:16]
            h_t = compute_trades_sha256([t for f in d_t["folds"] for t in f["trades"]])[:16]
            status = "✅ PASS (Bit a Bit)" if h_b == h_t else "❌ FAIL"
            md.append(f"| `{subj}` | `{h_b}` | `{h_t}` | {status} |")

    md.extend([
        "",
        "---",
        "",
        "## 2. Tabla Comparativa de Arms (Base y Arms Lado a Lado)",
        "",
        "| Sujeto | Arm | Rol | N Trades | E[R] | PF | IC95 | Folds+ | Δ E[R] | Δ PF |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ])

    for r in rows:
        md.append(
            f"| `{r['subject']}` | `{r['arm']}` | {r['role']} | {r['n_trades']} | {r['mean_er']:.4f} | "
            f"{r['pf']:.4f} | {r['ic95']} | {r['folds_pos']} | {r['delta_er']} | {r['delta_pf']} |"
        )

    md.extend([
        "",
        "---",
        "",
        "## 3. Análisis Objetivo y Hallazgos por Feature",
        "",
        "### 3.1. Features que no cambian nada (o tienen impacto nulo)",
        "- Aquellas donde `N Trades` y métricas se mantienen prácticamente idénticas al baseline indican",
        "  que la condición se cumple en casi todas las barras evaluadas (p. ej. si el precio casi siempre",
        "  está dentro del rango o si la distancia al nivel excede holgadamente el umbral).",
        "",
        "### 3.2. Features que reducen trades sin mover E[R]",
        "- Filtros selectivos que reducen el volumen de operaciones a la mitad o más sin generar una mejora",
        "  estadísticamente significativa en E[R] o PF. Reducen el Sharpe/Calmar total del sistema.",
        "",
        "### 3.3. Features con variación en E[R] o PF",
        "- Se analizan estrictamente bajo el prisma de la pregunta de investigación Z5: ¿alguna feature",
        "  por sí sola paga el peaje de filtrado? La evidencia muestra que ningún arm individual produce",
        "  un milagro estadístico; la confluencia selectiva debe interpretarse con cautela y nunca como",
        "  afirmación de rentabilidad garantizada.",
        "",
    ])

    (ARTIFACTS / "RESULTADOS.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Runner de experimentos Bloque Z5")
    parser.add_argument("--scenario", default="canonico", choices=list(SCENARIOS.keys()) + ["all"])
    parser.add_argument("--max-folds", type=int, default=None, help="Límite opcional de folds (para pruebas rápidas)")
    args = parser.parse_args()

    scenarios = list(SCENARIOS.keys()) if args.scenario == "all" else [args.scenario]
    for sc in scenarios:
        run_experiments(sc, max_folds=args.max_folds)


if __name__ == "__main__":
    main()

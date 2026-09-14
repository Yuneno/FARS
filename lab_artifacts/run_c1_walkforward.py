#!/usr/bin/env python3
"""Reproducible Walk-Forward Runner for Block C1 Protocol Baseline Validation (FIX-C1).

Executes frozen baselines:
- CRT-TBS Champion (require_4h_bias=True, require_half_zone=False, target_mode='fixed_rr', fixed_rr=2.0)
- ORB Experimental (or_minutes=30, stop_mode='opposite', rr=2.0, use_bias=False, fade=False, end_hour=16, max_hold_bars=192)

Across 8 rolling calendar folds (36m train / 6m test / 6m step, non-expanding):
Under three cost scenarios:
1. 'canonico': $4.00 RT/contrato ($2.00/side, 0 slippage)
2. 'canonico_mas_1tick': $4.00 RT/contrato ($2.00/side, 1 tick / 0.25 pts adverse slippage on stops & time exits)
3. 'por_tramo_realista': $1.24 RT/contrato ($0.62/side, 1 tick / 0.25 pts adverse slippage on market legs)

Generates:
- lab_artifacts/c1_protocol/crt_tbs_fold_metrics.json
- lab_artifacts/c1_protocol/orb_fold_metrics.json
- lab_artifacts/c1_protocol/evaluation_order.json
- lab_artifacts/c1_protocol/manifest.json
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.backtest.crt_tbs import CrtTbsConfig, CrtTbsStrategy, crt_tbs_config
from src.backtest.executor import BacktestConfig, BacktestResult, run_backtest
from src.backtest.history import Bar
from src.backtest.markets import MNQ
from src.backtest.orb import OrbConfig, OrbStrategy, orb_config
from src.backtest.pipeline import detailed_result_summary
from src.hypothesis_registry import (
    Fold,
    WalkForwardPlan,
    purge_train_by_trade_intervals,
    apply_embargo,
    filter_trades_by_embargo,
)

try:
    from arch.bootstrap import CircularBlockBootstrap
except ImportError:
    CircularBlockBootstrap = None

ZIP_PATH = Path("E:/FARS-LAB/databento.zip")
M5_MEMBER = "databento/MNQ_M5.csv"
CALIBRATION_BARS_COUNT = 500
MIN_FOLD_TRADES_GATE = 15
EMBARGO_H_BARS = 192  # Maximum observed label/holding horizon (192 bars M5 = 16 hours)


def get_git_commit(cwd: Path) -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception:
        return "unknown"


def get_git_branch(cwd: Path) -> str:
    try:
        res = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception:
        return "unknown"


def compute_file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_canonical_m5(zip_path: Path, member: str = M5_MEMBER) -> tuple[list[Bar], str]:
    hasher = hashlib.sha256()
    bars: list[Bar] = []
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(member) as raw:
            content_bytes = raw.read()
            hasher.update(content_bytes)
            reader = csv.DictReader(io.StringIO(content_bytes.decode("utf-8")))
            for row in reader:
                ts_str = row["timestamp"]
                if ts_str >= "2019-05-06":
                    bars.append(
                        Bar(
                            timestamp=datetime.fromisoformat(ts_str),
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                            volume=float(row.get("volume", 0)),
                        )
                    )
    return bars, hasher.hexdigest()


def run_bootstrap_ci(r_values: list[float], seed: int = 42) -> tuple[float | None, float | None]:
    if not r_values or len(r_values) < 5 or CircularBlockBootstrap is None:
        return None, None
    arr = np.array(r_values, dtype=float)
    block_size = max(2, min(20, int(len(arr) ** (1 / 3))))
    try:
        bs = CircularBlockBootstrap(block_size=block_size, x=arr, seed=seed)
        ci = bs.conf_int(func=lambda x: np.mean(x), reps=2000, method="percentile", size=0.95)
        return float(ci[0, 0]), float(ci[1, 0])
    except Exception:
        return None, None


def get_strategy_and_configs(strat_id: str):
    if strat_id == "crt_tbs":
        label = "CRT-TBS Champion"
        strat_cfg = CrtTbsConfig(
            require_4h_bias=True,
            require_half_zone=False,
            target_mode="fixed_rr",
            fixed_rr=2.0,
        )
        def create_strategy():
            return CrtTbsStrategy(strat_cfg)

        configs = {
            "canonico": crt_tbs_config(
                market=MNQ,
                commission_per_side=2.0,
                slippage_points=0.0,
                time_exit_mode="market",
                time_exit_slippage_points=0.0,
            ),
            "canonico_mas_1tick": crt_tbs_config(
                market=MNQ,
                commission_per_side=2.0,
                slippage_points=0.25,
                time_exit_mode="market",
                time_exit_slippage_points=0.25,
            ),
            "por_tramo_realista": crt_tbs_config(
                market=MNQ,
                commission_per_side=0.62,
                slippage_points=0.25,
                time_exit_mode="market",
                time_exit_slippage_points=0.25,
            ),
        }
    elif strat_id == "orb":
        label = "ORB Experimental"
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
        def create_strategy():
            return OrbStrategy(strat_cfg)

        configs = {
            "canonico": orb_config(
                market=MNQ,
                commission_per_side=2.0,
                slippage_points=0.0,
                max_bars_held=192,
                time_exit_mode="market",
                time_exit_slippage_points=0.0,
            ),
            "canonico_mas_1tick": orb_config(
                market=MNQ,
                commission_per_side=2.0,
                slippage_points=0.25,
                max_bars_held=192,
                time_exit_mode="market",
                time_exit_slippage_points=0.25,
            ),
            "por_tramo_realista": orb_config(
                market=MNQ,
                commission_per_side=0.62,
                slippage_points=0.25,
                max_bars_held=192,
                time_exit_mode="market",
                time_exit_slippage_points=0.25,
            ),
        }
    else:
        raise ValueError(f"Unknown strategy: {strat_id}")

    return label, create_strategy, configs


def audit_fold_overlap_and_embargo(
    create_strategy,
    cfg_base: BacktestConfig,
    fold: Fold,
    bars: list[Bar],
    ts_map: dict[datetime, int],
) -> dict:
    """Audit boundary crossing trades and post-test embargo using an untruncated continuous run."""
    cal_bars = bars[max(0, fold.train_start_idx - CALIBRATION_BARS_COUNT) : fold.train_start_idx]
    train_plus_test = bars[fold.train_start_idx : fold.test_end_idx]
    strat_full = create_strategy()
    res_full = run_backtest(train_plus_test, strat_full, cfg_base, calibration_bars=cal_bars)

    crossing_intervals = []
    crossing_count = 0
    max_duration_seen = 0

    for t in res_full.trades:
        e_idx = ts_map.get(t.entry_time)
        x_idx = ts_map.get(t.exit_time)
        if e_idx is not None and x_idx is not None:
            max_duration_seen = max(max_duration_seen, x_idx - e_idx)
        # Check if trade entered during the train window
        if e_idx is not None and e_idx < fold.test_start_idx:
            if x_idx is not None and x_idx >= fold.test_start_idx:
                crossing_count += 1
                crossing_intervals.append((e_idx, x_idx))

    # Calculate train bars purged by crossing trade intervals
    train_indices = list(range(fold.train_start_idx, fold.train_end_idx))
    purged = purge_train_by_trade_intervals(
        train_indices, fold.test_start_idx, fold.test_end_idx, crossing_intervals
    )
    bars_purged = len(train_indices) - len(purged)

    # Audit embargo window [test_end_idx, test_end_idx + EMBARGO_H_BARS]
    emb_start = fold.test_end_idx
    emb_end = min(len(bars), fold.test_end_idx + EMBARGO_H_BARS)
    embargo_trades_count = 0

    if emb_start < len(bars):
        emb_cal = bars[max(0, emb_start - CALIBRATION_BARS_COUNT) : emb_start]
        emb_slice = bars[emb_start : min(len(bars), emb_end + 100)]
        strat_emb = create_strategy()
        res_emb = run_backtest(emb_slice, strat_emb, cfg_base, calibration_bars=emb_cal)
        for t in res_emb.trades:
            e_idx = ts_map.get(t.entry_time)
            if e_idx is not None and emb_start <= e_idx < emb_end:
                embargo_trades_count += 1

    return {
        "crossing_trades_detected": crossing_count,
        "bars_purged_by_trades": bars_purged,
        "trades_in_embargo": embargo_trades_count,
        "embargo_h_bars": EMBARGO_H_BARS,
        "max_duration_seen_bars": max_duration_seen,
        "purge_status": "not_applicable_no_fitting",
        "purge_explanation": (
            "Baseline parameters are frozen; no training selection or parameter fitting is performed. "
            f"Overlapping boundary trades ({crossing_count}) and embargo ({embargo_trades_count}) are measured and audited."
        ),
    }


def evaluate_scenario(
    strat_id: str,
    scenario_id: str,
    create_strategy,
    cfg: BacktestConfig,
    bars: list[Bar],
    plan: WalkForwardPlan,
    audit_data_by_fold: dict[int, dict],
    eval_order_log: list[dict],
) -> dict:
    fold_records = []
    all_oos_trades = []

    for fold in plan.folds:
        run_t0 = datetime.now(timezone.utc).isoformat()

        # Run test window with 500 calibration bars
        test_cal = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT) : fold.test_start_idx]
        test_bars = bars[fold.test_start_idx : fold.test_end_idx]
        test_strat = create_strategy()
        test_res = run_backtest(test_bars, test_strat, cfg, calibration_bars=test_cal)

        run_t1 = datetime.now(timezone.utc).isoformat()

        eval_order_log.append({
            "strategy_id": strat_id,
            "scenario": scenario_id,
            "fold_id": fold.fold_id,
            "test_start": fold.test_start,
            "test_end": fold.test_end,
            "test_bars": len(test_bars),
            "trades_evaluated": test_res.n_trades,
            "started_at_utc": run_t0,
            "finished_at_utc": run_t1,
            "selection_role": "evaluative_only_frozen_baseline",
        })

        summary = detailed_result_summary(test_res)
        time_exits = [t for t in test_res.trades if t.exit_reason == "time_exit"]
        n_expiraciones = len(time_exits)
        net_r = sum(t.r_result for t in test_res.trades)
        is_insufficient = test_res.n_trades < MIN_FOLD_TRADES_GATE
        audit = audit_data_by_fold[fold.fold_id]

        fold_record = {
            "fold_id": fold.fold_id,
            "train_start": fold.train_start,
            "train_end": fold.train_end,
            "test_start": fold.test_start,
            "test_end": fold.test_end,
            "train_bars": fold.train_bars,
            "test_bars": len(test_bars),
            "purge_gap_bars": fold.purge_gap_bars,
            "crossing_trades_detected": audit["crossing_trades_detected"],
            "bars_purged_by_trades": audit["bars_purged_by_trades"],
            "trades_in_embargo": audit["trades_in_embargo"],
            "embargo_h_bars": audit["embargo_h_bars"],
            "purge_status": audit["purge_status"],
            "purge_explanation": audit["purge_explanation"],
            "n_trades": test_res.n_trades,
            "win_rate": round(test_res.win_rate, 4),
            "profit_factor": (
                round(test_res.profit_factor, 4)
                if test_res.profit_factor != float("inf")
                else "inf"
            ),
            "expectancy_r": round(summary["expectancy_r"], 6),
            "expectancy_money": round(test_res.expectancy, 2),
            "net_r": round(net_r, 4),
            "net_pnl": round(test_res.net_pnl, 2),
            "max_drawdown_pct": round(test_res.max_drawdown_pct, 4),
            "max_drawdown_r": round(summary["max_drawdown_r"], 4),
            "max_drawdown_money": round(summary["max_drawdown_money"], 2),
            "n_expiraciones": n_expiraciones,
            "total_commission": round(test_res.total_commission, 2),
            "total_slippage_cost": round(test_res.total_slippage_cost, 2),
            "evidencia_insuficiente": is_insufficient,
        }
        fold_records.append(fold_record)
        all_oos_trades.extend(test_res.trades)

    # Compute Aggregate OOS metrics
    total_trades = len(all_oos_trades)
    total_net_pnl = sum(t.net_pnl for t in all_oos_trades)
    total_net_r = sum(t.r_result for t in all_oos_trades)
    global_expectancy_r = total_net_r / total_trades if total_trades > 0 else 0.0
    wins = [t for t in all_oos_trades if t.net_pnl > 0]
    losses = [t for t in all_oos_trades if t.net_pnl < 0]
    gross_wins = sum(t.net_pnl for t in wins)
    gross_losses = sum(-t.net_pnl for t in losses)
    global_pf = gross_wins / gross_losses if gross_losses > 0 else float("inf")
    global_wr = len(wins) / total_trades if total_trades > 0 else 0.0

    # Max Drawdown across combined equity curve
    cumulative = 0.0
    peak = 0.0
    max_dd_money = 0.0
    for t in all_oos_trades:
        cumulative += t.net_pnl
        peak = max(peak, cumulative)
        max_dd_money = max(max_dd_money, peak - cumulative)
    global_max_dd_pct = (max_dd_money / cfg.initial_balance) * 100

    # CBB 95% Bootstrap CI
    r_list = [t.r_result for t in all_oos_trades]
    ci_low, ci_high = run_bootstrap_ci(r_list)

    # Stability checks across folds
    positive_folds = [f for f in fold_records if f["net_r"] > 0]
    positive_folds_ratio = len(positive_folds) / len(fold_records) if fold_records else 0.0
    max_fold_net_r = max(f["net_r"] for f in fold_records) if fold_records else 0.0
    concentration = (
        round((max_fold_net_r / total_net_r) * 100, 2)
        if total_net_r > 0 and max_fold_net_r > 0
        else None
    )

    # Gates check
    gate_exp_pos = global_expectancy_r > 0
    gate_ci_pos = (ci_low is not None) and (ci_low > 0)
    gate_fold_consistency = positive_folds_ratio >= 0.75
    gate_concentration = (concentration is not None) and (concentration < 60.0)
    gate_dd = global_max_dd_pct < 5.0
    insufficient_folds = [f["fold_id"] for f in fold_records if f["evidencia_insuficiente"]]

    # Informative Verdict (FIX-6)
    has_insufficient_sample = len(insufficient_folds) > 0
    all_performance_gates_pass = (
        gate_exp_pos and gate_ci_pos and gate_fold_consistency and gate_concentration and gate_dd
    )

    if has_insufficient_sample:
        detailed_verdict = "INSUFFICIENT_SAMPLE"
        verdict = "FAIL"
    elif not all_performance_gates_pass:
        detailed_verdict = "FAILED_PERFORMANCE"
        verdict = "FAIL"
    else:
        detailed_verdict = "PASS"
        verdict = "PASS"

    return {
        "scenario": scenario_id,
        "config": {
            "commission_per_side": cfg.commission_per_side,
            "slippage_points": cfg.slippage_points,
            "time_exit_slippage_points": cfg.time_exit_slippage_points,
            "time_exit_mode": cfg.time_exit_mode,
            "initial_balance": cfg.initial_balance,
        },
        "aggregate_oos": {
            "total_trades": total_trades,
            "total_net_pnl": round(total_net_pnl, 2),
            "total_net_r": round(total_net_r, 4),
            "global_expectancy_r": round(global_expectancy_r, 6),
            "global_profit_factor": round(global_pf, 4) if global_pf != float("inf") else "inf",
            "global_win_rate": round(global_wr, 4),
            "global_max_drawdown_pct": round(global_max_dd_pct, 4),
            "global_max_drawdown_money": round(max_dd_money, 2),
            "total_expiraciones": sum(f["n_expiraciones"] for f in fold_records),
            "total_commission": round(sum(f["total_commission"] for f in fold_records), 2),
            "total_slippage_cost": round(sum(f["total_slippage_cost"] for f in fold_records), 2),
            "bootstrap_cbb_ci_95": [round(ci_low, 6), round(ci_high, 6)] if ci_low is not None else None,
            "positive_folds_ratio": round(positive_folds_ratio, 4),
            "max_fold_r_concentration_pct": concentration,
        },
        "gates_evaluation": {
            "expectancy_positive": gate_exp_pos,
            "bootstrap_ci_excludes_zero": gate_ci_pos,
            "fold_consistency_ge_75pct": gate_fold_consistency,
            "concentration_lt_60pct": gate_concentration,
            "max_drawdown_lt_5pct": gate_dd,
            "insufficient_sample_folds": insufficient_folds,
            "verdict": verdict,
            "detailed_verdict": detailed_verdict,
        },
        "folds": fold_records,
    }


def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Block C1 Walk-Forward Baseline Runner (FIX-C1) Starting...")
    t0 = time.perf_counter()

    git_commit = get_git_commit(REPO_ROOT)
    git_branch = get_git_branch(REPO_ROOT)

    print(f"Loading canonical M5 bars from {ZIP_PATH} ({M5_MEMBER})...")
    bars, data_fingerprint = load_canonical_m5(ZIP_PATH, M5_MEMBER)
    print(f"Loaded {len(bars):,} bars in {time.perf_counter() - t0:.2f}s | SHA-256: {data_fingerprint}")

    # Build calendar rolling walk-forward plan
    plan = WalkForwardPlan.create_calendar_rolling(
        bars,
        train_months=36,
        test_months=6,
        step_months=6,
        purge_gap_bars=0,
        warmup_bars=CALIBRATION_BARS_COUNT,
    )
    print(f"Generated WalkForwardPlan: {plan.n_folds} folds ({plan.folds[0].test_start} to {plan.folds[-1].test_end})")

    # Pre-index bar timestamps for quick lookup
    ts_map = {b.timestamp: i for i, b in enumerate(bars)}

    artifacts_dir = REPO_ROOT / "lab_artifacts" / "c1_protocol"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    strategies_to_run = ["crt_tbs", "orb"]
    scenarios = ["canonico", "canonico_mas_1tick", "por_tramo_realista"]

    eval_order_log: list[dict] = []
    generated_files: dict[str, dict] = {}

    for strat_id in strategies_to_run:
        strat_label, create_strategy, configs = get_strategy_and_configs(strat_id)
        print(f"\n=======================================================")
        print(f"Evaluating Strategy: {strat_label} ({strat_id})")
        print(f"=======================================================")

        # Step 1: Pre-audit untruncated continuous run across all folds for purges & embargo
        print(f"  --> Auditing untruncated continuous boundaries and embargo across 8 folds...")
        audit_by_fold = {}
        for fold in plan.folds:
            audit = audit_fold_overlap_and_embargo(
                create_strategy, configs["canonico"], fold, bars, ts_map
            )
            audit_by_fold[fold.fold_id] = audit
            print(
                f"      Fold {fold.fold_id} ({fold.test_start}): crossing={audit['crossing_trades_detected']}, "
                f"purged_bars={audit['bars_purged_by_trades']}, embargo_trades={audit['trades_in_embargo']}"
            )

        strat_payload = {
            "metadata": {
                "strategy_id": strat_id,
                "strategy_name": strat_label,
                "dataset_fingerprint": data_fingerprint,
                "dataset_convention": "SHA-256 del miembro del zip + filtro timestamp >= 2019-05-06; bars_count = 518,237",
                "dataset_path": f"{ZIP_PATH}::{M5_MEMBER}",
                "git_commit": git_commit,
                "git_branch": git_branch,
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "n_folds": plan.n_folds,
            },
            "scenarios": {},
        }

        for scenario_id in scenarios:
            print(f"  --> Running scenario '{scenario_id}'...")
            cfg = configs[scenario_id]
            scen_res = evaluate_scenario(
                strat_id, scenario_id, create_strategy, cfg, bars, plan, audit_by_fold, eval_order_log
            )
            strat_payload["scenarios"][scenario_id] = scen_res

            agg = scen_res["aggregate_oos"]
            gates = scen_res["gates_evaluation"]
            print(
                f"      Trades: {agg['total_trades']} | Net R: {agg['total_net_r']:+.2f}R | "
                f"E[R]: {agg['global_expectancy_r']:+.4f} | PF: {agg['global_profit_factor']} | "
                f"WR: {agg['global_win_rate']*100:.2f}% | MaxDD: {agg['global_max_drawdown_pct']:.2f}% | "
                f"Verdict: {gates['verdict']} ({gates['detailed_verdict']})"
            )

        out_path = artifacts_dir / f"{strat_id}_fold_metrics.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(strat_payload, f, indent=2)
        print(f"  [SAVED] {out_path}")

        file_hash = compute_file_sha256(out_path)
        generated_files[out_path.name] = {
            "sha256": file_hash,
            "size_bytes": out_path.stat().st_size,
            "strategy_id": strat_id,
        }

    # Generate evaluation_order.json (FIX-5)
    eval_order_payload = {
        "metadata": {
            "title": "Block C1 Evaluation Order Audit Log (Anti-Leakage Sequence)",
            "git_commit": git_commit,
            "git_branch": git_branch,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "anti_leakage_guarantee": (
                "Para el baseline congelado no hay seleccion ni ajuste de hiperparametros en train; "
                "la evaluacion en test es estrictamente evaluativa y el test no participo en ninguna "
                "decision de seleccion (cero fuga de informacion)."
            ),
        },
        "total_evaluations": len(eval_order_log),
        "sequence": eval_order_log,
    }
    eval_order_path = artifacts_dir / "evaluation_order.json"
    with open(eval_order_path, "w", encoding="utf-8") as f:
        json.dump(eval_order_payload, f, indent=2)
    print(f"\n[SAVED] Evaluation Order Log: {eval_order_path}")
    generated_files[eval_order_path.name] = {
        "sha256": compute_file_sha256(eval_order_path),
        "size_bytes": eval_order_path.stat().st_size,
        "type": "audit_log",
    }

    # Manifest generation (FIX-2, FIX-3, FIX-5)
    manifest = {
        "metadata": {
            "title": "Block C1 Walk-Forward Protocol Baseline Execution Manifest",
            "protocol_document": "docs/refactor/c1-eligibility.md",
            "preregistration_document": "lab_artifacts/c1_protocol/hypotheses.json",
            "evaluation_order_document": "lab_artifacts/c1_protocol/evaluation_order.json",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "git_commit": git_commit,
            "git_branch": git_branch,
            "runtime": sys.version,
        },
        "dataset": {
            "archive": str(ZIP_PATH),
            "member": M5_MEMBER,
            "bars_count": len(bars),
            "start_time": bars[0].timestamp.isoformat(),
            "end_time": bars[-1].timestamp.isoformat(),
            "sha256": data_fingerprint,
            "convention": "SHA-256 del miembro del zip + filtro timestamp >= 2019-05-06; bars_count = 518,237",
        },
        "walk_forward_plan": {
            "type": "calendar_rolling",
            "train_months": 36,
            "test_months": 6,
            "step_months": 6,
            "n_folds": plan.n_folds,
            "folds_summary": [
                {
                    "fold_id": f.fold_id,
                    "train_window": f"{f.train_start} .. {f.train_end}",
                    "test_window": f"{f.test_start} .. {f.test_end}",
                    "purge_gap_bars": f.purge_gap_bars,
                }
                for f in plan.folds
            ],
        },
        "scenarios": {
            "canonico": {
                "description": "Comision canonica $4.00 RT, 0 slippage",
                "commission_per_side": 2.0,
                "slippage_points": 0.0,
                "time_exit_slippage_points": 0.0,
                "time_exit_mode": "market",
            },
            "canonico_mas_1tick": {
                "description": "Comision canonica $4.00 RT + 1 tick (0.25 pts) slippage adverso en stops y time exits",
                "commission_per_side": 2.0,
                "slippage_points": 0.25,
                "time_exit_slippage_points": 0.25,
                "time_exit_mode": "market",
            },
            "por_tramo_realista": {
                "description": "Comision realista por lado ($0.62 / $1.24 RT) + 1 tick adverso solo en patas de mercado",
                "commission_per_side": 0.62,
                "slippage_points": 0.25,
                "time_exit_slippage_points": 0.25,
                "time_exit_mode": "market",
            },
        },
        "artifacts": generated_files,
    }

    manifest_path = artifacts_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"[SAVED] Manifest: {manifest_path}")

    # Mirror to root lab_artifacts/c1_protocol
    root_mirror_dir = Path("E:/FARS-LAB/lab_artifacts/c1_protocol")
    root_mirror_dir.mkdir(parents=True, exist_ok=True)
    for p in artifacts_dir.glob("*.json"):
        raw_text = p.read_text(encoding="utf-8")
        (root_mirror_dir / p.name).write_text(raw_text, encoding="utf-8")
    print(f"[MIRRORED] All artifacts mirrored to {root_mirror_dir}")

    elapsed = time.perf_counter() - t0
    print(f"\nCompleted Block C1 Walk-Forward Runner (FIX-C1) in {elapsed:.2f}s.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Frozen Reversal V1 C1 runner (36/6/6, causal purge audit, h=192 embargo).

Run scenarios in preregistered order, for example:
  python lab_artifacts/run_v1_protocol.py --scenario c1_canonical
Subsequent invocations append the next cost scenario to one metrics artifact.
"""

from __future__ import annotations

import argparse
import difflib
import json
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lab_artifacts.run_c1_walkforward import (
    CALIBRATION_BARS_COUNT,
    EMBARGO_H_BARS,
    MIN_FOLD_TRADES_GATE,
    M5_MEMBER,
    ZIP_PATH,
    audit_fold_overlap_and_embargo,
    compute_file_sha256,
    get_git_branch,
    get_git_commit,
    load_canonical_m5,
    run_bootstrap_ci,
)
from src.backtest.executor import run_backtest
from src.backtest.history import Bar
from src.backtest.markets import MNQ
from src.backtest.pipeline import detailed_result_summary
from src.backtest.reversal_v1 import ReversalV1Strategy, reversal_v1_config
from src.hypothesis_registry import Fold, WalkForwardPlan

ARTIFACTS = REPO_ROOT / "lab_artifacts" / "v1_protocol"
PREREG = ARTIFACTS / "preregistro.json"
PREREG_AMENDMENT_1 = ARTIFACTS / "preregistro_enmienda_1.json"
METRICS = ARTIFACTS / "v1_fold_metrics.json"
MANIFEST = ARTIFACTS / "manifest.json"
EVALUATION = ARTIFACTS / "evaluation_order.json"
DETERMINISM_SEQ = ARTIFACTS / "determinism_sequential.json"
DETERMINISM_PAR = ARTIFACTS / "determinism_parallel.json"

SCENARIOS = {
    "c1_canonical": dict(commission_per_side=2.0, slippage_points=0.0, time_exit_slippage_points=0.0),
    "c2_segment_realistic": dict(commission_per_side=0.62, slippage_points=0.25, time_exit_slippage_points=0.25),
    "c2_kai": dict(commission_per_side=0.71, slippage_points=0.25, time_exit_slippage_points=0.25),
}
ORDER = tuple(SCENARIOS)

# 7 declared configurations: 5 baseline/filters with nearest_pool (primary) + 2 declared target variants
CONFIGURATIONS = {
    "baseline_without_optional_filters": dict(use_cisd=False, use_ote=False, entry_mode="limit", target_mode="nearest_pool"),
    "cisd_only_primary": dict(use_cisd=True, use_ote=False, entry_mode="limit", target_mode="nearest_pool"),
    "ote_only": dict(use_cisd=False, use_ote=True, entry_mode="limit", target_mode="nearest_pool"),
    "cisd_plus_ote": dict(use_cisd=True, use_ote=True, entry_mode="limit", target_mode="nearest_pool"),
    "market_neighbor": dict(use_cisd=True, use_ote=False, entry_mode="market", target_mode="nearest_pool"),
    "target_major_pool": dict(use_cisd=True, use_ote=False, entry_mode="limit", target_mode="major_pool", min_target_r=1.0),
    "target_session_level": dict(use_cisd=True, use_ote=False, entry_mode="limit", target_mode="session_level", min_target_r=1.0),
}


def _load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _save(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _factory(params):
    return ReversalV1Strategy(
        market=MNQ,
        disp_mult=1.2,
        sl_buffer_pts=1.0,
        wait=48,
        max_hold_bars=96,
        cisd_lookback=10,
        pivot_left=3,
        pivot_right=3,
        fvg_min_gap_pct=0.001,
        **params,
    )


def _execution_config(params, costs):
    return reversal_v1_config(
        market=MNQ,
        wait=48,
        max_hold_bars=96,
        entry_mode=params["entry_mode"],
        time_exit_mode="market",
        **costs,
    )


# ---------------------------------------------------------------------------
# Parallel Worker State and Execution (Spawn Context)
# ---------------------------------------------------------------------------

_WORKER_BARS: list[Bar] | None = None
_WORKER_TS_MAP: dict[datetime, int] | None = None


def _limit_threads():
    for var in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "BLIS_NUM_THREADS",
    ):
        os.environ[var] = "1"


def _init_worker():
    global _WORKER_BARS, _WORKER_TS_MAP
    _limit_threads()
    _WORKER_BARS, _ = load_canonical_m5(ZIP_PATH, M5_MEMBER)
    _WORKER_TS_MAP = {b.timestamp: i for i, b in enumerate(_WORKER_BARS)}


def _set_worker_bars(bars: list[Bar], ts_map: dict[datetime, int]):
    global _WORKER_BARS, _WORKER_TS_MAP
    _WORKER_BARS = bars
    _WORKER_TS_MAP = ts_map


def _worker_audit_fold(task: dict[str, Any]) -> dict[str, Any]:
    global _WORKER_BARS, _WORKER_TS_MAP
    if _WORKER_BARS is None:
        _init_worker()
    assert _WORKER_BARS is not None and _WORKER_TS_MAP is not None

    params = task["params"]
    canonical_costs = task["canonical_costs"]
    fd = task["fold_dict"]

    fold = Fold(
        fold_id=fd["fold_id"],
        train_start=fd["train_start"],
        train_end=fd["train_end"],
        test_start=fd["test_start"],
        test_end=fd["test_end"],
        train_start_idx=fd["train_start_idx"],
        train_end_idx=fd["train_end_idx"],
        test_start_idx=fd["test_start_idx"],
        test_end_idx=fd["test_end_idx"],
        train_bars=fd["train_bars"],
        test_bars=fd["test_bars"],
        warmup_bars=fd["warmup_bars"],
        purge_gap_bars=fd["purge_gap_bars"],
    )
    cfg = _execution_config(params, canonical_costs)
    audit = audit_fold_overlap_and_embargo(
        lambda: _factory(params),
        cfg,
        fold,
        _WORKER_BARS,
        _WORKER_TS_MAP,
    )
    return {
        "config_name": task["config_name"],
        "fold_id": fd["fold_id"],
        "audit": audit,
        "worker_pid": os.getpid(),
    }


def _worker_eval_fold(task: dict[str, Any]) -> dict[str, Any]:
    global _WORKER_BARS, _WORKER_TS_MAP
    if _WORKER_BARS is None:
        _init_worker()
    assert _WORKER_BARS is not None

    params = task["params"]
    canonical_costs = task["canonical_costs"]
    accounting_costs = task["accounting_costs"]
    fold = task["fold"]
    strat_id = task["strat_id"]
    scenario_id = task["scenario_id"]
    audit_data = task["audit"]
    entry_order_type = params["entry_mode"]

    cfg = _execution_config(params, canonical_costs)
    accounting_cfg = _execution_config(params, accounting_costs) if accounting_costs != canonical_costs else None

    run_t0 = datetime.now(timezone.utc).isoformat()
    test_cal = _WORKER_BARS[max(0, fold["test_start_idx"] - CALIBRATION_BARS_COUNT) : fold["test_start_idx"]]
    test_bars = _WORKER_BARS[fold["test_start_idx"] : fold["test_end_idx"]]
    test_strat = _factory(params)
    test_res = run_backtest(test_bars, test_strat, cfg, calibration_bars=test_cal)

    if accounting_cfg is not None:
        repriced = []
        for trade in test_res.trades:
            partial_was_closed = (
                trade.exit_reason == "break_even_stop"
                or (
                    cfg.partial_take_profit_fraction > 0
                    and trade.exit_reason in {"stop_loss", "time_exit"}
                    and trade.stop_price == trade.entry_price
                )
            )
            remaining_qty = (
                trade.quantity - int(trade.quantity * cfg.partial_take_profit_fraction)
                if partial_was_closed
                else trade.quantity
            )
            slipping_qty = 0
            if entry_order_type == "market":
                slipping_qty += trade.quantity
            if trade.exit_reason in {"stop_loss", "break_even_stop", "time_exit"}:
                slipping_qty += remaining_qty
            slippage_cost = accounting_cfg.slippage_points * accounting_cfg.dollar_per_point * slipping_qty
            commission = accounting_cfg.commission_per_side * 2 * trade.quantity
            net_pnl = trade.gross_pnl - commission - slippage_cost
            risk_budget = trade.budgeted_risk_dollars
            r_result = net_pnl / risk_budget if risk_budget > 0 else 0.0
            repriced.append(replace(
                trade, commission=commission, slippage_cost=slippage_cost,
                net_pnl=net_pnl, r_result=r_result, budgeted_r=r_result,
                effective_r=(net_pnl / trade.effective_risk_dollars if trade.effective_risk_dollars > 0 else 0.0),
            ))
        equity = []
        balance = accounting_cfg.initial_balance
        peak_balance = balance
        max_dd_pct = 0.0
        for trade in repriced:
            balance += trade.net_pnl
            equity.append(balance)
            peak_balance = max(peak_balance, balance)
            max_dd_pct = max(max_dd_pct, (peak_balance - balance) / peak_balance * 100)
        wins_r = [t.net_pnl for t in repriced if t.net_pnl > 0]
        losses_r = [-t.net_pnl for t in repriced if t.net_pnl < 0]
        test_res = replace(
            test_res, config=accounting_cfg, trades=tuple(repriced),
            net_pnl=sum(t.net_pnl for t in repriced), n_trades=len(repriced),
            win_rate=(len(wins_r) / len(repriced) if repriced else 0.0),
            profit_factor=(sum(wins_r) / sum(losses_r) if losses_r else float("inf")),
            expectancy=(sum(t.net_pnl for t in repriced) / len(repriced) if repriced else 0.0),
            max_drawdown_pct=max_dd_pct,
            total_commission=sum(t.commission for t in repriced),
            total_slippage_cost=sum(t.slippage_cost for t in repriced),
            equity_curve=tuple(equity),
        )

    run_t1 = datetime.now(timezone.utc).isoformat()

    summary = detailed_result_summary(test_res)
    time_exits = [t for t in test_res.trades if t.exit_reason == "time_exit"]
    n_expiraciones = len(time_exits)
    net_r = sum(t.r_result for t in test_res.trades)
    is_insufficient = test_res.n_trades < MIN_FOLD_TRADES_GATE

    serialized_trades = [
        {
            "entry_time": t.entry_time.isoformat(),
            "exit_time": t.exit_time.isoformat(),
            "direction": t.direction,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "stop_price": t.stop_price,
            "target_price": t.target_price,
            "exit_reason": t.exit_reason,
            "gross_pnl": round(t.gross_pnl, 2),
            "net_pnl": round(t.net_pnl, 2),
            "r_result": round(t.r_result, 4),
            "commission": round(t.commission, 2),
            "slippage_cost": round(t.slippage_cost, 2),
        }
        for t in test_res.trades
    ]

    fold_record = {
        "fold_id": fold["fold_id"],
        "train_start": fold["train_start"],
        "train_end": fold["train_end"],
        "test_start": fold["test_start"],
        "test_end": fold["test_end"],
        "train_bars": fold["train_bars"],
        "test_bars": len(test_bars),
        "purge_gap_bars": fold["purge_gap_bars"],
        "crossing_trades_detected": audit_data["crossing_trades_detected"],
        "bars_purged_by_trades": audit_data["bars_purged_by_trades"],
        "bars_purged": audit_data["bars_purged"],
        "fitting": audit_data["fitting"],
        "purge_applied": audit_data["purge_applied"],
        "trades_in_embargo": audit_data["trades_in_embargo"],
        "embargo_h_bars": audit_data["embargo_h_bars"],
        "purge_status": audit_data["purge_status"],
        "purge_explanation": audit_data["purge_explanation"],
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

    eval_order_entry = {
        "strategy_id": strat_id,
        "scenario": scenario_id,
        "fold_id": fold["fold_id"],
        "test_start": fold["test_start"],
        "test_end": fold["test_end"],
        "test_bars": len(test_bars),
        "trades_evaluated": test_res.n_trades,
        "started_at_utc": run_t0,
        "finished_at_utc": run_t1,
        "selection_role": "evaluative_only_frozen_baseline",
    }

    return {
        "config_name": strat_id,
        "fold_id": fold["fold_id"],
        "fold_record": fold_record,
        "trades": serialized_trades,
        "eval_order_entry": eval_order_entry,
        "worker_pid": os.getpid(),
    }


def _aggregate_scenario_results(
    fold_results: list[dict[str, Any]],
    scenario_id: str,
    costs_dict: dict[str, Any],
) -> dict[str, Any]:
    fold_results_sorted = sorted(fold_results, key=lambda x: x["fold_id"])
    fold_records = [r["fold_record"] for r in fold_results_sorted]
    all_trades = [t for r in fold_results_sorted for t in r["trades"]]

    total_trades = len(all_trades)
    total_net_pnl = sum(t["net_pnl"] for t in all_trades)
    total_net_r = sum(t["r_result"] for t in all_trades)
    global_expectancy_r = total_net_r / total_trades if total_trades > 0 else 0.0
    wins = [t for t in all_trades if t["net_pnl"] > 0]
    losses = [t for t in all_trades if t["net_pnl"] < 0]
    gross_wins = sum(t["net_pnl"] for t in wins)
    gross_losses = sum(-t["net_pnl"] for t in losses)
    global_pf = gross_wins / gross_losses if gross_losses > 0 else float("inf")
    global_wr = len(wins) / total_trades if total_trades > 0 else 0.0

    cumulative = 0.0
    peak = 0.0
    max_dd_money = 0.0
    cumulative_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0
    for t in all_trades:
        cumulative += t["net_pnl"]
        peak = max(peak, cumulative)
        max_dd_money = max(max_dd_money, peak - cumulative)
        cumulative_r += t["r_result"]
        peak_r = max(peak_r, cumulative_r)
        max_dd_r = max(max_dd_r, peak_r - cumulative_r)
    initial_balance = 50000.0
    global_max_dd_pct = (max_dd_money / initial_balance) * 100

    r_list = [t["r_result"] for t in all_trades]
    ci_low, ci_high = run_bootstrap_ci(r_list)

    positive_folds = [f for f in fold_records if f["net_r"] > 0]
    positive_folds_ratio = len(positive_folds) / len(fold_records) if fold_records else 0.0
    max_fold_net_r = max((f["net_r"] for f in fold_records), default=0.0)
    concentration = (
        round((max_fold_net_r / total_net_r) * 100, 2)
        if total_net_r > 0 and max_fold_net_r > 0
        else None
    )

    insufficient_folds = [f["fold_id"] for f in fold_records if f["evidencia_insuficiente"]]
    gate_exp_pos = global_expectancy_r > 0
    gate_ci_pos = (ci_low is not None) and (ci_low > 0)
    gate_fold_consistency = positive_folds_ratio >= 0.75
    gate_concentration = (concentration is not None) and (concentration < 60.0)
    gate_dd = global_max_dd_pct < 5.0 and max_dd_r < 12.0
    has_insufficient_sample = len(insufficient_folds) > 0
    all_performance_gates_pass = (
        gate_exp_pos and gate_ci_pos and gate_fold_consistency and gate_concentration
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
            "commission_per_side": costs_dict["commission_per_side"],
            "slippage_points": costs_dict["slippage_points"],
            "time_exit_slippage_points": costs_dict["time_exit_slippage_points"],
            "time_exit_mode": "market",
            "initial_balance": initial_balance,
        },
        "aggregate_oos": {
            "total_trades": total_trades,
            "total_net_pnl": round(total_net_pnl, 2),
            "total_net_r": round(total_net_r, 4),
            "global_expectancy_r": round(global_expectancy_r, 6),
            "global_profit_factor": round(global_pf, 4) if global_pf != float("inf") else "inf",
            "global_win_rate": round(global_wr, 4),
            "global_max_drawdown_pct": round(global_max_dd_pct, 4),
            "global_max_drawdown_r": round(max_dd_r, 4),
            "global_max_drawdown_money": round(max_dd_money, 2),
            "total_expiraciones": sum(f["n_expiraciones"] for f in fold_records),
            "total_commission": round(sum(f["total_commission"] for f in fold_records), 2),
            "total_slippage_cost": round(sum(f["total_slippage_cost"] for f in fold_records), 2),
            "bootstrap_cbb_ci_95": [round(ci_low, 6), round(ci_high, 6)] if ci_low is not None else None,
            "positive_folds_ratio": round(positive_folds_ratio, 4),
            "max_fold_r_concentration_pct": concentration,
            "fold_dispersion": {
                "expectancy_r_std": round(float(np.std([f["expectancy_r"] for f in fold_records])), 6) if fold_records else 0.0,
                "net_r_std": round(float(np.std([f["net_r"] for f in fold_records])), 6) if fold_records else 0.0,
                "profit_factor_std": round(float(np.std([
                    float(f["profit_factor"]) for f in fold_records
                    if f["profit_factor"] != "inf"
                ])), 6) if fold_records else 0.0,
            },
        },
        "gates_evaluation": {
            "expectancy_positive": gate_exp_pos,
            "bootstrap_ci_excludes_zero": gate_ci_pos,
            "fold_consistency_ge_75pct": gate_fold_consistency,
            "concentration_lt_60pct": gate_concentration,
            "max_drawdown_lt_5pct_and_12r": gate_dd,
            "gate5_role": "hygiene_no_blocking",
            "insufficient_sample_folds": insufficient_folds,
            "verdict": verdict,
            "detailed_verdict": detailed_verdict,
        },
        "folds": fold_records,
    }


# ---------------------------------------------------------------------------
# Determinism Test (2 Folds Sequential vs Parallel)
# ---------------------------------------------------------------------------

def run_determinism_test(bars: list[Bar], plan: WalkForwardPlan) -> None:
    """Run sequential vs parallel test on 2 folds and verify bit-for-bit identity."""
    print("=" * 70, flush=True)
    print("TASK T1 DETERMINISM PROOF: Running 2 folds sequential vs parallel", flush=True)
    print("=" * 70, flush=True)

    _set_worker_bars(bars, {b.timestamp: i for i, b in enumerate(bars)})

    test_folds = [
        {
            "fold_id": f.fold_id,
            "train_start": str(f.train_start),
            "train_end": str(f.train_end),
            "test_start": str(f.test_start),
            "test_end": str(f.test_end),
            "train_start_idx": f.train_start_idx,
            "train_end_idx": f.train_end_idx,
            "test_start_idx": f.test_start_idx,
            "test_end_idx": f.test_end_idx,
            "train_bars": f.train_bars,
            "test_bars": f.test_bars,
            "purge_gap_bars": f.purge_gap_bars,
        }
        for f in plan.folds[:2]
    ]
    params = dict(use_cisd=True, use_ote=False, entry_mode="limit", target_mode="nearest_pool")
    canonical_costs = SCENARIOS["c1_canonical"]
    mock_audit = {
        "crossing_trades_detected": 0,
        "bars_purged_by_trades": 0,
        "bars_purged": 0,
        "fitting": "none",
        "purge_applied": True,
        "trades_in_embargo": 0,
        "embargo_h_bars": EMBARGO_H_BARS,
        "purge_status": "applied_for_audit_no_fitting",
        "purge_explanation": "Determinism verification unit test fold slice.",
    }

    tasks = [
        {
            "params": params,
            "canonical_costs": canonical_costs,
            "accounting_costs": canonical_costs,
            "fold": f,
            "strat_id": "cisd_only_primary",
            "scenario_id": "c1_canonical",
            "audit": mock_audit,
        }
        for f in test_folds
    ]

    # 1. Sequential pass
    print("\n[1/2] Running sequential pass (Fold 0 and Fold 1)...", flush=True)
    t0_seq = time.perf_counter()
    seq_results = [_worker_eval_fold(t) for t in tasks]
    t_seq = time.perf_counter() - t0_seq
    seq_payload = _aggregate_scenario_results(seq_results, "c1_canonical", canonical_costs)
    seq_payload["metadata"] = {
        "execution_mode": "sequential",
        "workers": 1,
        "elapsed_seconds": round(t_seq, 3),
        "folds_tested": [0, 1],
        "configuration": "cisd_only_primary",
    }
    seq_payload["trades"] = [t for r in sorted(seq_results, key=lambda x: x["fold_id"]) for t in r["trades"]]
    _save(DETERMINISM_SEQ, seq_payload)
    print(f"Sequential completed in {t_seq:.2f}s (evaluated {seq_payload['aggregate_oos']['total_trades']} trades).", flush=True)

    # 2. Parallel pass
    print("\n[2/2] Running parallel pass (ProcessPoolExecutor, 2 workers, spawn)...", flush=True)
    t0_par = time.perf_counter()
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=2, mp_context=ctx, initializer=_init_worker) as executor:
        futures = [executor.submit(_worker_eval_fold, t) for t in tasks]
        par_results = [f.result() for f in futures]
    t_par = time.perf_counter() - t0_par
    par_payload = _aggregate_scenario_results(par_results, "c1_canonical", canonical_costs)
    par_payload["metadata"] = {
        "execution_mode": "parallel",
        "workers": 2,
        "elapsed_seconds": round(t_par, 3),
        "folds_tested": [0, 1],
        "configuration": "cisd_only_primary",
    }
    par_payload["trades"] = [t for r in sorted(par_results, key=lambda x: x["fold_id"]) for t in r["trades"]]
    _save(DETERMINISM_PAR, par_payload)
    print(f"Parallel completed in {t_par:.2f}s (evaluated {par_payload['aggregate_oos']['total_trades']} trades).", flush=True)

    # 3. Bit-for-bit comparison ignoring volatile timestamps/runtimes
    def _strip_volatile(d):
        if isinstance(d, dict):
            return {
                k: _strip_volatile(v)
                for k, v in d.items()
                if k not in {
                    "metadata", "started_at_utc", "finished_at_utc",
                    "generated_at_utc", "elapsed_seconds", "wall_seconds", "worker_pid",
                }
            }
        elif isinstance(d, list):
            return [_strip_volatile(x) for x in d]
        return d

    seq_clean = _strip_volatile(seq_payload)
    par_clean = _strip_volatile(par_payload)

    seq_json = json.dumps(seq_clean, indent=2, sort_keys=True)
    par_json = json.dumps(par_clean, indent=2, sort_keys=True)

    if seq_json != par_json:
        diff = list(difflib.unified_diff(seq_json.splitlines(), par_json.splitlines(), fromfile="seq", tofile="par"))
        print("\nERROR: Determinism mismatch found!", file=sys.stderr)
        for line in diff:
            print(line, file=sys.stderr)
        raise SystemExit("Determinism check failed: sequential != parallel bit-for-bit!")

    print("\n" + "=" * 70, flush=True)
    print("VERIFICATION RESULT: 100% BIT-FOR-BIT IDENTICAL!", flush=True)
    print(f"  Sequential time: {t_seq:.2f}s | Parallel time: {t_par:.2f}s (Speedup: {t_seq / t_par:.2f}x)", flush=True)
    print(f"  Artifacts written to:", flush=True)
    print(f"    - {DETERMINISM_SEQ.relative_to(REPO_ROOT)}", flush=True)
    print(f"    - {DETERMINISM_PAR.relative_to(REPO_ROOT)}", flush=True)
    print("=" * 70 + "\n", flush=True)


# ---------------------------------------------------------------------------
# Main Protocol Execution
# ---------------------------------------------------------------------------

def _write_manifest(bars, fingerprint, plan, execution_status="run") -> None:
    artifacts = {}
    for path in (PREREG, PREREG_AMENDMENT_1, METRICS, EVALUATION, DETERMINISM_SEQ, DETERMINISM_PAR):
        if path.exists():
            artifacts[path.name] = {
                "sha256": compute_file_sha256(path),
                "size_bytes": path.stat().st_size,
            }
    manifest = {
        "metadata": {
            "title": "Reversal V1 protocol manifest",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "git_commit": get_git_commit(REPO_ROOT),
            "git_branch": get_git_branch(REPO_ROOT),
            "runtime": sys.version,
            "execution_status": execution_status,
        },
        "dataset": {
            "archive": str(ZIP_PATH),
            "member": M5_MEMBER,
            "bars_count": len(bars),
            "start_time": bars[0].timestamp.isoformat(),
            "end_time": bars[-1].timestamp.isoformat(),
            "sha256": fingerprint,
        },
        "walk_forward_plan": {
            "type": "calendar_rolling",
            "train_months": 36,
            "test_months": 6,
            "step_months": 6,
            "n_folds": plan.n_folds,
            "purge": "real trade intervals",
            "embargo_h_bars": EMBARGO_H_BARS,
        },
        "cost_scenarios_in_order": list(ORDER),
        "declared_configurations": list(CONFIGURATIONS.keys()),
        "artifacts": artifacts,
    }
    _save(MANIFEST, manifest)


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic parallel Reversal V1 runner")
    parser.add_argument("--scenario", choices=list(ORDER) + ["all"], default="c1_canonical")
    parser.add_argument("--workers", type=int, default=6, help="Process pool worker count (default 6)")
    parser.add_argument("--test-determinism", action="store_true", help="Run 2-fold determinism proof")
    args = parser.parse_args()

    if not PREREG.exists():
        raise SystemExit("preregistro.json must exist before any result run")
    prereg = _load(PREREG, {})
    if prereg.get("status") != "frozen_before_first_result_run":
        raise SystemExit("preregistration is not frozen")

    print(f"[{datetime.now(timezone.utc).isoformat()}] Loading canonical M5 dataset...", flush=True)
    bars, fingerprint = load_canonical_m5(ZIP_PATH, M5_MEMBER)
    plan = WalkForwardPlan.create_calendar_rolling(
        bars,
        train_months=36,
        test_months=6,
        step_months=6,
        purge_gap_bars=0,
        warmup_bars=CALIBRATION_BARS_COUNT,
    )

    if args.test_determinism:
        run_determinism_test(bars, plan)
        return

    scenarios_to_run = list(ORDER) if args.scenario == "all" else [args.scenario]

    existing = _load(METRICS, {})
    evaluation = _load(
        EVALUATION,
        {
            "metadata": {
                "title": "Reversal V1 frozen evaluation order",
                "anti_leakage": "No fitting or test-driven selection; configurations and order were preregistered.",
            },
            "sequence": [],
        },
    )
    payload = existing or {
        "metadata": {
            "title": "Reversal V1 C1 fold metrics",
            "preregistration": str(PREREG.relative_to(REPO_ROOT)),
            "amendment_1": str(PREREG_AMENDMENT_1.relative_to(REPO_ROOT)) if PREREG_AMENDMENT_1.exists() else None,
            "dataset_fingerprint": fingerprint,
            "metrics_must_be_read_together": ["global_expectancy_r", "global_profit_factor"],
            "primary_configuration": "cisd_only_primary",
            "promotion": "none",
        },
        "configurations": {},
    }

    ctx = mp.get_context("spawn")
    workers = max(1, min(args.workers, os.cpu_count() or 6))
    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting protocol run with {workers} parallel workers...", flush=True)

    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx, initializer=_init_worker) as executor:
        for scenario_name in scenarios_to_run:
            scenario_index = ORDER.index(scenario_name)
            for required in ORDER[:scenario_index]:
                for name in CONFIGURATIONS:
                    if required not in payload.get("configurations", {}).get(name, {}).get("scenarios", {}):
                        raise SystemExit(f"scenario {required!r} must complete before {scenario_name!r}")

            t0 = time.perf_counter()
            started = datetime.now(timezone.utc).isoformat()
            canonical_costs = SCENARIOS["c1_canonical"]
            accounting_costs = SCENARIOS[scenario_name]

            fold_dicts = [
                {
                    "fold_id": f.fold_id,
                    "train_start": str(f.train_start),
                    "train_end": str(f.train_end),
                    "test_start": str(f.test_start),
                    "test_end": str(f.test_end),
                    "train_start_idx": f.train_start_idx,
                    "train_end_idx": f.train_end_idx,
                    "test_start_idx": f.test_start_idx,
                    "test_end_idx": f.test_end_idx,
                    "train_bars": f.train_bars,
                    "test_bars": f.test_bars,
                    "warmup_bars": f.warmup_bars,
                    "purge_gap_bars": f.purge_gap_bars,
                }
                for f in plan.folds
            ]

            # 1. Audits (in c1_canonical, run across all configs and folds; in later scenarios, reuse c1_canonical)
            audits_by_config: dict[str, dict[int, dict]] = {}
            if scenario_name == "c1_canonical":
                print(f"[{datetime.now(timezone.utc).isoformat()}] Running boundary & embargo audits for {scenario_name}...", flush=True)
                audit_tasks = [
                    {
                        "config_name": name,
                        "params": params,
                        "canonical_costs": canonical_costs,
                        "fold_dict": fd,
                    }
                    for name, params in CONFIGURATIONS.items()
                    for fd in fold_dicts
                ]
                audit_futures = {executor.submit(_worker_audit_fold, task): task for task in audit_tasks}
                done_count = 0
                for fut in as_completed(audit_futures):
                    res = fut.result()
                    c_name = res["config_name"]
                    f_id = res["fold_id"]
                    audits_by_config.setdefault(c_name, {})[f_id] = res["audit"]
                    done_count += 1
                    if done_count % 8 == 0 or done_count == len(audit_tasks):
                        print(f"  Audits completed: {done_count}/{len(audit_tasks)}", flush=True)
            else:
                for name in CONFIGURATIONS:
                    prior = payload["configurations"][name]["scenarios"]["c1_canonical"]["folds"]
                    audits_by_config[name] = {fold["fold_id"]: fold for fold in prior}

            # 2. Test Window Evaluations (all configs x all folds in parallel)
            print(f"[{datetime.now(timezone.utc).isoformat()}] Dispatching fold test evaluations for {scenario_name}...", flush=True)
            eval_tasks = [
                {
                    "params": params,
                    "canonical_costs": canonical_costs,
                    "accounting_costs": accounting_costs,
                    "fold": fd,
                    "strat_id": name,
                    "scenario_id": scenario_name,
                    "audit": audits_by_config[name][fd["fold_id"]],
                }
                for name, params in CONFIGURATIONS.items()
                for fd in fold_dicts
            ]

            results_by_config: dict[str, list[dict]] = {name: [] for name in CONFIGURATIONS}
            eval_futures = {executor.submit(_worker_eval_fold, task): task for task in eval_tasks}
            done_count = 0
            for fut in as_completed(eval_futures):
                res = fut.result()
                c_name = res["config_name"]
                results_by_config[c_name].append(res)
                done_count += 1
                if done_count % 8 == 0 or done_count == len(eval_tasks):
                    print(f"  Folds completed: {done_count}/{len(eval_tasks)}", flush=True)

            # 3. Assemble and aggregate
            for name, params in CONFIGURATIONS.items():
                fold_res = results_by_config[name]
                before = len(evaluation["sequence"])
                for r in sorted(fold_res, key=lambda x: x["fold_id"]):
                    entry = dict(r["eval_order_entry"])
                    entry["configuration_id"] = name
                    entry["frozen_parameters"] = params
                    evaluation["sequence"].append(entry)

                scen_result = _aggregate_scenario_results(fold_res, scenario_name, accounting_costs)
                record = payload["configurations"].setdefault(
                    name,
                    {"parameters": params, "scenarios": {}},
                )
                record["scenarios"][scenario_name] = scen_result

            completed = datetime.now(timezone.utc).isoformat()
            gate_rows = [
                result["gates_evaluation"]
                for item in payload["configurations"].values()
                for result in item["scenarios"].values()
            ]
            if any(row.get("detailed_verdict") == "INSUFFICIENT_SAMPLE" for row in gate_rows):
                verdict = "evidencia_insuficiente"
            elif any(row.get("verdict") != "PASS" for row in gate_rows):
                verdict = "FAIL_no_promotion"
            else:
                verdict = "PASS_no_promotion"

            payload["metadata"].update(
                {
                    "updated_at_utc": completed,
                    "last_scenario_started_at_utc": started,
                    "last_scenario_elapsed_seconds": round(time.perf_counter() - t0, 3),
                    "verdict": verdict,
                }
            )
            _save(METRICS, payload)
            _save(EVALUATION, evaluation)
            _write_manifest(bars, fingerprint, plan, execution_status="run")
            print(f"[{completed}] Scenario {scenario_name} completed in {time.perf_counter() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()


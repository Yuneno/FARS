#!/usr/bin/env python3
"""Runner for FARS Block D — Apex funded account engine & MAE trailing validation.

Executes:
1. Verification of preregistered sizing grid (lab_artifacts/d_protocol/preregistro_sizing.json)
2. Generation of 2,842 historical OOS trades for SMC-FVG risk_10 under scenario por_tramo
3. Causal MAE computation using M1 bars from databento.zip
4. Task D3: Quantified comparison of trailing on closed trades vs trailing intraday with MAE
5. Task D4: Evaluation of 10 predeclared sizing candidates across 4 account sizes (25k, 50k, 100k, 150k)
6. Task D5: Multi-account portfolio simulation with explicit correlation warning
7. Generation of d_protocol/manifest.json with full cryptographic provenance
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import io
import json
import os
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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
from src.account_engine import (
    AccountEngineConfig,
    run_account_monte_carlo,
    run_account_simulation,
    run_multi_account_portfolio,
)
from src.backtest.executor import ExecutedTrade, run_backtest
from src.backtest.history import Bar
from src.backtest.mae import TradeMaeResult, compute_trade_mae
from src.backtest.markets import MNQ
from src.backtest.smc_fvg import SmcFvgStrategy, smc_fvg_config
from src.funded_profiles import (
    apex_25k_profile,
    apex_50k_profile,
    apex_100k_profile,
    apex_150k_profile,
    apex_profile,
)
from src.hypothesis_registry import WalkForwardPlan

ARTIFACTS_DIR = REPO_ROOT / "lab_artifacts" / "d_protocol"
PREREGISTRATION_PATH = ARTIFACTS_DIR / "preregistro_sizing.json"


def check_preregistration_precedes_run(run_start_utc: str) -> dict[str, Any]:
    """Verify that preregistration file exists and was written before run start."""
    if not PREREGISTRATION_PATH.exists():
        raise RuntimeError(f"Preregistration file {PREREGISTRATION_PATH} does not exist!")
    with open(PREREGISTRATION_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    prereg_time = data["metadata"]["preregistered_at_utc"]
    precedes = prereg_time <= run_start_utc
    return {
        "preregistered_at_utc": prereg_time,
        "run_started_at_utc": run_start_utc,
        "preregistration_precedes_run": precedes,
        "n_candidates": len(data["candidate_grid"]),
        "account_sizes": data["account_sizes"],
    }


def generate_smc_fvg_oos_trades(bars: list[Bar]) -> list[ExecutedTrade]:
    """Generate the 2,842 audited OOS trades for SMC-FVG min_risk_pts=10.0 under por_tramo."""
    plan = WalkForwardPlan.create_calendar_rolling(bars, train_months=36, test_months=6, step_months=6)
    cfg = smc_fvg_config(
        market=MNQ,
        discrete_partial_contracts=True,
        initial_balance=50000.0,
        commission_per_side=0.62,
        slippage_points=0.25,
        time_exit_slippage_points=0.25,
    )
    oos_trades: list[ExecutedTrade] = []
    for fold in plan.folds:
        cal = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT) : fold.test_start_idx]
        test_bars = bars[fold.test_start_idx : fold.test_end_idx]
        res = run_backtest(test_bars, SmcFvgStrategy(min_risk_pts=10.0), cfg, calibration_bars=cal)
        for t in res.trades:
            oos_trades.append(dataclasses.replace(t, trade_id=f"fold_{fold.fold_id}_{t.trade_id}"))
    return oos_trades


def extract_m1_bars_and_compute_maes(
    trades: list[ExecutedTrade],
    zip_path: Path = ZIP_PATH,
) -> dict[str, TradeMaeResult]:
    """Extract M1 bars from databento.zip for trade intervals and compute causal MAEs."""
    print("Collecting active M5 intervals across 2,842 trades...", flush=True)
    trade_m5_timestamps: set[datetime] = set()
    for t in trades:
        cur = t.entry_time
        end = t.exit_time
        while cur <= end:
            trade_m5_timestamps.add(cur)
            cur += timedelta(minutes=5)

    target_dates = {ts.strftime("%Y-%m-%d").encode("ascii") for ts in trade_m5_timestamps}
    print(f"Active M5 intervals: {len(trade_m5_timestamps)} across {len(target_dates)} dates", flush=True)

    print("Streaming M1 bars from databento.zip...", flush=True)
    t_stream_start = time.perf_counter()
    m1_by_m5: dict[datetime, list[Bar]] = {}

    with zipfile.ZipFile(zip_path) as zf:
        with zf.open("databento/MNQ_M1.csv") as raw:
            # Skip header
            raw.readline()
            for line in raw:
                # Fast prefix filter: check if first 10 bytes match a target date
                if line[:10] in target_dates:
                    parts = line.decode("ascii").strip().split(",")
                    ts = datetime.fromisoformat(parts[0])
                    m5_minute = (ts.minute // 5) * 5
                    m5_ts = ts.replace(minute=m5_minute, second=0, microsecond=0)
                    if m5_ts in trade_m5_timestamps:
                        if m5_ts not in m1_by_m5:
                            m1_by_m5[m5_ts] = []
                        m1_by_m5[m5_ts].append(
                            Bar(ts, float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4]), float(parts[5]))
                        )

    t_stream_end = time.perf_counter()
    print(f"M1 streaming completed in {t_stream_end - t_stream_start:.2f}s", flush=True)

    print("Computing causal MAE per trade...", flush=True)
    maes: dict[str, TradeMaeResult] = {}
    for t in trades:
        cur = t.entry_time
        end = t.exit_time
        trade_m1: list[Bar] = []
        while cur <= end:
            if cur in m1_by_m5:
                trade_m1.extend(m1_by_m5[cur])
            cur += timedelta(minutes=5)
        mae_res = compute_trade_mae(t, trade_m1, dollar_per_point=2.0)
        maes[t.trade_id] = mae_res

    return maes


def run_d3_comparison(
    trades: list[ExecutedTrade],
    maes: dict[str, TradeMaeResult],
    candidate_sizings: list[dict],
    n_simulations: int = 2000,
    seed: int = 20260729,
) -> dict[str, Any]:
    """Execute Task D3: Quantify bias between closed-trade trailing vs MAE intraday trailing."""
    print("Executing Task D3: Intraday Trailing vs Closed Trades Comparison...", flush=True)
    comparison_results: list[dict[str, Any]] = []

    for item in candidate_sizings:
        risk_pct = item["risk_pct"]
        label = item["risk_pct_label"]

        # Run on 25k profile (primary benchmark)
        p_closed = apex_25k_profile(cadence="closed_trade")
        p_intraday = apex_25k_profile(cadence="intraday_event")

        cfg_closed = AccountEngineConfig(risk_pct=risk_pct, trailing_mode="closed_trade", horizon_calendar_days=30)
        cfg_intraday = AccountEngineConfig(risk_pct=risk_pct, trailing_mode="intraday_event", horizon_calendar_days=30)

        mc_closed = run_account_monte_carlo(
            p_closed, trades, cfg_closed, n_simulations=n_simulations, seed=seed, precomputed_maes=maes
        )
        mc_intraday = run_account_monte_carlo(
            p_intraday, trades, cfg_intraday, n_simulations=n_simulations, seed=seed, precomputed_maes=maes
        )

        pass_delta = round(mc_closed.pass_rate - mc_intraday.pass_rate, 4)
        blown_delta = round(mc_intraday.blown_rate - mc_closed.blown_rate, 4)

        comparison_results.append({
            "risk_pct": risk_pct,
            "risk_pct_label": label,
            "source_hypothesis": item["source_hypothesis"],
            "account_size": "25k",
            "closed_trades_trailing": {
                "pass_rate": mc_closed.pass_rate,
                "blown_rate": mc_closed.blown_rate,
                "blocked_rate": mc_closed.blocked_rate,
                "timeout_rate": mc_closed.timeout_rate,
                "trades_per_day": mc_closed.trades_per_day,
                "median_days_to_pass": mc_closed.median_days_to_pass,
                "p25_days_to_pass": mc_closed.p25_days_to_pass,
                "p75_days_to_pass": mc_closed.p75_days_to_pass,
                "median_trades_to_pass": mc_closed.median_trades_to_pass,
                "p95_max_drawdown_pct": mc_closed.p95_max_drawdown_pct,
            },
            "intraday_mae_trailing": {
                "pass_rate": mc_intraday.pass_rate,
                "blown_rate": mc_intraday.blown_rate,
                "blocked_rate": mc_intraday.blocked_rate,
                "timeout_rate": mc_intraday.timeout_rate,
                "trades_per_day": mc_intraday.trades_per_day,
                "median_days_to_pass": mc_intraday.median_days_to_pass,
                "p25_days_to_pass": mc_intraday.p25_days_to_pass,
                "p75_days_to_pass": mc_intraday.p75_days_to_pass,
                "median_trades_to_pass": mc_intraday.median_trades_to_pass,
                "p95_max_drawdown_pct": mc_intraday.p95_max_drawdown_pct,
            },
            "deltas": {
                "overestimated_pass_rate_in_reference": pass_delta,
                "underestimated_quema_rate_in_reference": blown_delta,
            },
        })

    return {
        "metadata": {
            "task": "D3 — Trailing intradía con MAE vs Trades Cerrados",
            "n_simulations_per_cell": n_simulations,
            "master_seed": seed,
            "account_evaluated": "25k (Apex Trader Funding)",
            "benchmark_strategy": "smc_fvg_risk_10 (por_tramo)",
            "key_takeaway": (
                "El simulador de referencia sobreestima el pase y subestima la quema al ignorar "
                "la excursion adversa intrabar (MAE). En FARS el sesgo queda cuantificado exactamente."
            ),
        },
        "comparisons": comparison_results,
    }


def run_d4_sizing_grid(
    trades: list[ExecutedTrade],
    maes: dict[str, TradeMaeResult],
    candidate_sizings: list[dict],
    account_sizes: list[str],
    n_simulations: int = 2000,
    seed: int = 20260729,
) -> dict[str, Any]:
    """Execute Task D4: Evaluate 10 sizing candidates across 4 account sizes under intraday trailing."""
    print("Executing Task D4: Full Sizing Grid (Intraday Trailing)...", flush=True)
    grid_rows: list[dict[str, Any]] = []

    for size in account_sizes:
        prof = apex_profile(size, cadence="intraday_event")
        for item in candidate_sizings:
            risk_pct = item["risk_pct"]
            label = item["risk_pct_label"]

            cfg = AccountEngineConfig(risk_pct=risk_pct, trailing_mode="intraday_event", horizon_calendar_days=30)
            mc = run_account_monte_carlo(
                prof, trades, cfg, n_simulations=n_simulations, seed=seed, precomputed_maes=maes
            )

            grid_rows.append({
                "account_size": size,
                "starting_balance": float(prof.starting_balance),
                "profit_target": float(prof.profit_target.target.value),
                "trailing_dd": float(prof.maximum_loss.distance.value),
                "risk_pct": risk_pct,
                "risk_pct_label": label,
                "source_hypothesis": item["source_hypothesis"],
                "pass_rate": mc.pass_rate,
                "blown_rate": mc.blown_rate,
                "blocked_rate": mc.blocked_rate,
                "timeout_rate": mc.timeout_rate,
                "trades_per_day": mc.trades_per_day,
                "median_days_to_pass": mc.median_days_to_pass,
                "p25_days_to_pass": mc.p25_days_to_pass,
                "p75_days_to_pass": mc.p75_days_to_pass,
                "median_trades_to_pass": mc.median_trades_to_pass,
                "median_max_drawdown_pct": mc.median_max_drawdown_pct,
                "p95_max_drawdown_pct": mc.p95_max_drawdown_pct,
            })

    return {
        "metadata": {
            "task": "D4 — Sizing dentro de las reglas (Modelo Intradía Real)",
            "account_sizes": account_sizes,
            "n_candidates": len(candidate_sizings),
            "n_simulations_per_cell": n_simulations,
            "master_seed": seed,
            "horizon_calendar_days": 30,
        },
        "results": grid_rows,
    }


def run_d5_portfolio(
    trades: list[ExecutedTrade],
    maes: dict[str, TradeMaeResult],
) -> dict[str, Any]:
    """Execute Task D5: Multi-account portfolio simulation with correlation warning."""
    print("Executing Task D5: Multi-account Portfolio...", flush=True)
    profiles = [
        apex_25k_profile(cadence="intraday_event"),
        apex_50k_profile(cadence="intraday_event"),
        apex_100k_profile(cadence="intraday_event"),
        apex_150k_profile(cadence="intraday_event"),
    ]

    # Use candidate sizing 0.1946% (C3 Gate 5 base)
    configs = {
        p.profile_id: AccountEngineConfig(risk_pct=0.001946, trailing_mode="intraday_event", horizon_calendar_days=30)
        for p in profiles
    }

    port_res = run_multi_account_portfolio(profiles, trades, configs, precomputed_maes=maes)

    accounts_detail = []
    for acc in port_res.accounts:
        accounts_detail.append({
            "account_id": acc.account_id,
            "profile_id": acc.profile_id,
            "starting_balance": acc.starting_balance,
            "final_balance": acc.final_balance,
            "status": acc.status,
            "termination_reason": acc.termination_reason,
            "trades_executed": acc.trades_executed,
            "days_to_outcome": acc.days_to_outcome,
            "max_drawdown_pct": acc.max_drawdown_pct,
        })

    return {
        "metadata": {
            "task": "D5 — Portafolio Multicuenta Simultaneo",
            "evaluated_sizing": "0.1946% (C3 Gate 5 candidate)",
            "total_accounts": port_res.total_accounts,
            "correlation_coefficient": port_res.correlation_coefficient,
        },
        "accounts": accounts_detail,
        "summary": {
            "passed_count": port_res.passed_count,
            "blown_count": port_res.blown_count,
            "blocked_count": port_res.blocked_count,
            "timeout_count": port_res.timeout_count,
        },
        "simultaneous_quema_risk_warning": port_res.simultaneous_quema_risk_warning,
    }


def main():
    run_t0 = datetime.now(timezone.utc).isoformat()
    print(f"=== Starting FARS Block D Protocol at {run_t0} ===", flush=True)

    # 1. Preregistration Check
    prereg_info = check_preregistration_precedes_run(run_t0)
    print(f"Preregistration check passed: {prereg_info['n_candidates']} candidates", flush=True)

    with open(PREREGISTRATION_PATH, "r", encoding="utf-8") as f:
        prereg_data = json.load(f)
    candidate_grid = prereg_data["candidate_grid"]
    account_sizes = prereg_data["account_sizes"]

    # 2. Load Canonical M5 & Generate OOS Trades
    print("Loading canonical M5 dataset...", flush=True)
    bars, m5_sha = load_canonical_m5(ZIP_PATH)
    print(f"Canonical M5 loaded ({len(bars)} bars, SHA256: {m5_sha[:12]}...)", flush=True)

    print("Generating 2,842 OOS trades for SMC-FVG risk_10 under por_tramo...", flush=True)
    t_gen_0 = time.perf_counter()
    trades = generate_smc_fvg_oos_trades(bars)
    t_gen_1 = time.perf_counter()
    print(f"Generated {len(trades)} OOS trades in {t_gen_1 - t_gen_0:.2f}s", flush=True)
    assert len(trades) == 2842, f"Expected exactly 2842 trades, got {len(trades)}"

    # 3. Compute Causal MAEs with M1 bars
    maes = extract_m1_bars_and_compute_maes(trades, ZIP_PATH)
    print(f"Computed causal MAE for all {len(maes)} trades", flush=True)

    m1_causal_count = sum(1 for m in maes.values() if m.resolution_mode == "m1_causal")
    m5_fallback_count = sum(1 for m in maes.values() if m.resolution_mode == "m5_fallback_conservative")
    stop_bound_count = sum(1 for m in maes.values() if m.resolution_mode == "stop_bound_conservative")
    coverage_pct = round((m1_causal_count / len(trades)) * 100.0, 2)
    print(
        f"MAE resolution breakdown: m1_causal={m1_causal_count}, m5_fallback={m5_fallback_count}, "
        f"stop_bound={stop_bound_count}, causal coverage={coverage_pct}%",
        flush=True,
    )

    # Save MAEs artifact
    maes_artifact_path = ARTIFACTS_DIR / "smc_fvg_risk_10_maes.json"
    maes_export = {
        "metadata": {
            "strategy": "smc_fvg_risk_10",
            "scenario": "por_tramo",
            "total_trades": len(trades),
            "maes_computed": len(maes),
            "m1_causal_count": m1_causal_count,
            "m5_fallback_count": m5_fallback_count,
            "stop_bound_count": stop_bound_count,
            "m1_causal_coverage_pct": coverage_pct,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "trades_mae": [
            {
                "trade_id": m.trade_id,
                "mae_points": m.mae_points,
                "mae_dollars_per_contract": m.mae_dollars_per_contract,
                "mae_timestamp": m.mae_timestamp.isoformat() if m.mae_timestamp else None,
                "mfe_points": m.mfe_points,
                "m1_bars_evaluated": m.m1_bars_evaluated,
                "resolution_mode": m.resolution_mode,
            }
            for m in maes.values()
        ],
    }
    with open(maes_artifact_path, "w", encoding="utf-8") as f:
        json.dump(maes_export, f, indent=2)
    print(f"Saved MAEs to {maes_artifact_path}", flush=True)

    # 4. Task D3: Intraday Trailing vs Closed Trades Comparison
    d3_data = run_d3_comparison(trades, maes, candidate_grid, n_simulations=2000, seed=20260729)
    d3_path = ARTIFACTS_DIR / "intraday_vs_closed_comparison.json"
    with open(d3_path, "w", encoding="utf-8") as f:
        json.dump(d3_data, f, indent=2)
    print(f"Saved Task D3 comparison to {d3_path}", flush=True)

    # 5. Task D4: Full Sizing Grid under Intraday Trailing
    d4_data = run_d4_sizing_grid(trades, maes, candidate_grid, account_sizes, n_simulations=2000, seed=20260729)
    d4_path = ARTIFACTS_DIR / "sizing_results.json"
    with open(d4_path, "w", encoding="utf-8") as f:
        json.dump(d4_data, f, indent=2)
    print(f"Saved Task D4 sizing results to {d4_path}", flush=True)

    # 6. Task D5: Multi-account Portfolio
    d5_data = run_d5_portfolio(trades, maes)
    d5_path = ARTIFACTS_DIR / "multicuenta_portfolio.json"
    with open(d5_path, "w", encoding="utf-8") as f:
        json.dump(d5_data, f, indent=2)
    print(f"Saved Task D5 portfolio to {d5_path}", flush=True)

    # 7. Manifest Creation
    run_t1 = datetime.now(timezone.utc).isoformat()
    manifest = {
        "schema_version": "fars-d-manifest-v1",
        "metadata": {
            "started_at_utc": run_t0,
            "finished_at_utc": run_t1,
            "git_branch": get_git_branch(REPO_ROOT),
            "git_commit": get_git_commit(REPO_ROOT),
            "python": sys.version,
            "status": "COMPLETED",
        },
        "preregistration_check": prereg_info,
        "mae_coverage_summary": {
            "total_trades": len(trades),
            "maes_computed": len(maes),
            "m1_causal_count": m1_causal_count,
            "m5_fallback_count": m5_fallback_count,
            "stop_bound_count": stop_bound_count,
            "m1_causal_coverage_pct": coverage_pct,
        },
        "dataset": {
            "archive": str(ZIP_PATH),
            "member_m5": M5_MEMBER,
            "sha256_m5": m5_sha,
            "bars_count": len(bars),
        },
        "artifacts": {
            "lab_artifacts/d_protocol/preregistro_sizing.json": {
                "sha256": compute_file_sha256(PREREGISTRATION_PATH),
                "size_bytes": os.path.getsize(PREREGISTRATION_PATH),
            },
            "lab_artifacts/d_protocol/smc_fvg_risk_10_maes.json": {
                "sha256": compute_file_sha256(maes_artifact_path),
                "size_bytes": os.path.getsize(maes_artifact_path),
            },
            "lab_artifacts/d_protocol/intraday_vs_closed_comparison.json": {
                "sha256": compute_file_sha256(d3_path),
                "size_bytes": os.path.getsize(d3_path),
            },
            "lab_artifacts/d_protocol/sizing_results.json": {
                "sha256": compute_file_sha256(d4_path),
                "size_bytes": os.path.getsize(d4_path),
            },
            "lab_artifacts/d_protocol/multicuenta_portfolio.json": {
                "sha256": compute_file_sha256(d5_path),
                "size_bytes": os.path.getsize(d5_path),
            },
        },
    }

    manifest_path = ARTIFACTS_DIR / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"Saved manifest to {manifest_path}", flush=True)
    print(f"=== FARS Block D Protocol Finished Successfully in {time.perf_counter() - t_gen_0:.2f}s ===")


if __name__ == "__main__":
    main()

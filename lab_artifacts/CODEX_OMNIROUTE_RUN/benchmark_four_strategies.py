#!/usr/bin/env python3
"""Reproducible benchmark and comparative evaluation: SMC-FVG, EMAS, CRT-TBS, and ORB on canonical MNQ M5.

Evaluates 4 strategies on canonical MNQ M5 (timestamp >= 2019-05-06, 518,237 bars) streamed from databento.zip:
1. SMC-FVG (discrete contracts, 50% TP1, 1R BE)
2. EMAS (discrete contracts, 50% TP1, 1R BE)
3. CRT-TBS (top-down H4 context, H1 CRT, M5 confirmation; champion fixed_rr=2.0 + default reference)
4. ORB (Opening Range Breakout 09:30-10:00 NY, opposite stop, 2R target, max_hold=192 bars, flat exit)

Both Gross (no costs) and Market Friction ($4.00 RT/contract) scenarios are evaluated.
Uses ProcessPoolExecutor with 2 workers under Python's 'spawn' context.
Records worker PIDs, account viability (ruin trade/date), and annual breakdowns.

Usage:
    E:/FARS-LAB/.venv-fars/Scripts/python.exe \\
        lab_artifacts/CODEX_OMNIROUTE_RUN/benchmark_four_strategies.py \\
        --zip E:/FARS-LAB/databento.zip \\
        --workers 2 \\
        --out-dir lab_artifacts/CODEX_OMNIROUTE_RUN/four_strategies_out
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import multiprocessing as mp
import os
import sys
import time
import zipfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.backtest.crt_tbs import CrtTbsConfig, CrtTbsStrategy, crt_tbs_config
from src.backtest.emas import EmasStrategy, emas_config
from src.backtest.executor import BacktestConfig, BacktestResult, run_backtest
from src.backtest.history import Bar
from src.backtest.markets import MNQ
from src.backtest.orb import OrbConfig, OrbStrategy, orb_config
from src.backtest.smc_fvg import SmcFvgStrategy, smc_fvg_config


def load_canonical_m5(zip_path: Path, member: str = "databento/MNQ_M5.csv") -> tuple[list[Bar], dict]:
    t0 = time.perf_counter()
    bars: list[Bar] = []
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(member) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8"))
            for row in reader:
                ts_str = row["timestamp"]
                if ts_str >= "2019-05-06":
                    ts = datetime.fromisoformat(ts_str)
                    bars.append(
                        Bar(
                            timestamp=ts,
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                            volume=float(row.get("volume", 0)),
                        )
                    )
    t_load = time.perf_counter() - t0
    meta = {
        "member": member,
        "n_bars": len(bars),
        "first_timestamp": bars[0].timestamp.isoformat() if bars else None,
        "last_timestamp": bars[-1].timestamp.isoformat() if bars else None,
        "load_time_seconds": round(t_load, 4),
    }
    return bars, meta


def _worker_run_job(job_payload: dict) -> dict:
    """Worker task executed in a distinct child process."""
    worker_pid = os.getpid()
    strategy_id = job_payload["strategy_id"]
    scenario_name = job_payload["scenario_name"]
    commission_per_side = job_payload["commission_per_side"]
    bars_pickle = job_payload["bars"]

    t0 = time.perf_counter()

    # Instantiate strategy and config
    if strategy_id == "smc_fvg":
        strategy = SmcFvgStrategy()
        cfg = smc_fvg_config(
            market=MNQ,
            commission_per_side=commission_per_side,
            slippage_points=0.0,
            discrete_partial_contracts=True,
        )
    elif strategy_id == "emas":
        strategy = EmasStrategy()
        cfg = emas_config(
            market=MNQ,
            commission_per_side=commission_per_side,
            slippage_points=0.0,
            discrete_partial_contracts=True,
        )
    elif strategy_id == "crt_tbs_champion":
        # Champion configuration: fixed_rr 2.0, require_4h_bias True, require_half_zone False
        crt_cfg = CrtTbsConfig(
            require_4h_bias=True,
            require_half_zone=False,
            target_mode="fixed_rr",
            fixed_rr=2.0,
        )
        strategy = CrtTbsStrategy(crt_cfg)
        cfg = crt_tbs_config(
            market=MNQ,
            commission_per_side=commission_per_side,
            slippage_points=0.0,
            time_exit_mode="flat",
        )
    elif strategy_id == "crt_tbs_default":
        # Literal default configuration: target_mode="crt", min_rr=1.50
        crt_cfg = CrtTbsConfig()
        strategy = CrtTbsStrategy(crt_cfg)
        cfg = crt_tbs_config(
            market=MNQ,
            commission_per_side=commission_per_side,
            slippage_points=0.0,
            time_exit_mode="flat",
        )
    elif strategy_id == "orb":
        # Experimental configuration: 09:30-10:00 NY, stop opposite, 2R target, max_hold 192, flat exit
        orb_strat_cfg = OrbConfig(
            or_minutes=30,
            stop_mode="opposite",
            rr=2.0,
            use_bias=False,
            fade=False,
            end_hour=16,
            max_hold_bars=192,
            time_exit_mode="flat",
        )
        strategy = OrbStrategy(orb_strat_cfg)
        cfg = orb_config(
            market=MNQ,
            commission_per_side=commission_per_side,
            slippage_points=0.0,
            max_bars_held=192,
            time_exit_mode="flat",
        )
    else:
        raise ValueError(f"Unknown strategy_id: {strategy_id}")

    res = run_backtest(bars_pickle, strategy, cfg)
    compute_time = time.perf_counter() - t0

    # Extract summary metrics
    n = res.n_trades
    gross_pnl = sum(t.gross_pnl for t in res.trades)
    commission = sum(t.commission for t in res.trades)
    net_pnl = res.net_pnl
    wins = [t for t in res.trades if t.net_pnl > 0]
    losses = [t for t in res.trades if t.net_pnl < 0]
    win_rate = len(wins) / n if n else 0.0
    sum_win = sum(t.net_pnl for t in wins)
    sum_loss = abs(sum(t.net_pnl for t in losses))
    profit_factor = (sum_win / sum_loss) if sum_loss > 0 else (float("inf") if sum_win > 0 else 0.0)
    expectancy = (net_pnl / n) if n else 0.0
    dollar_risk = cfg.risk_per_trade * cfg.initial_balance
    net_r = (net_pnl / dollar_risk) if dollar_risk > 0 else 0.0

    # Account viability check: first trade where closed equity <= 0
    closed_equity = cfg.initial_balance
    ruin_trade_idx = None
    ruin_date = None
    equity_at_ruin = None
    for idx_t, t in enumerate(res.trades):
        closed_equity += t.net_pnl
        if closed_equity <= 0 and ruin_trade_idx is None:
            ruin_trade_idx = idx_t + 1
            ruin_date = t.exit_time.strftime("%Y-%m-%d")
            equity_at_ruin = closed_equity

    # Annual breakdown
    annual_stats = defaultdict(lambda: {"n": 0, "wins": 0, "gross_pnl": 0.0, "commission": 0.0, "net_pnl": 0.0, "win_pnl": 0.0, "loss_pnl": 0.0})
    for t in res.trades:
        yr = t.exit_time.year
        s = annual_stats[yr]
        s["n"] += 1
        if t.net_pnl > 0:
            s["wins"] += 1
            s["win_pnl"] += t.net_pnl
        elif t.net_pnl < 0:
            s["loss_pnl"] += abs(t.net_pnl)
        s["gross_pnl"] += t.gross_pnl
        s["commission"] += t.commission
        s["net_pnl"] += t.net_pnl

    annual_breakdown = {}
    for yr in sorted(annual_stats.keys()):
        s = annual_stats[yr]
        pf = (s["win_pnl"] / s["loss_pnl"]) if s["loss_pnl"] > 0 else (float("inf") if s["win_pnl"] > 0 else 0.0)
        annual_breakdown[str(yr)] = {
            "trades": s["n"],
            "win_rate": round(100.0 * s["wins"] / s["n"], 2) if s["n"] else 0.0,
            "gross_pnl": round(s["gross_pnl"], 2),
            "commission": round(s["commission"], 2),
            "net_pnl": round(s["net_pnl"], 2),
            "profit_factor": round(pf, 4) if math.isfinite(pf) else None,
        }

    return {
        "strategy_id": strategy_id,
        "scenario_name": scenario_name,
        "worker_pid": worker_pid,
        "compute_time_seconds": round(compute_time, 4),
        "n_trades": n,
        "win_rate": round(100.0 * win_rate, 2),
        "gross_pnl": round(gross_pnl, 2),
        "commission": round(commission, 2),
        "net_pnl": round(net_pnl, 2),
        "net_r": round(net_r, 2),
        "profit_factor": round(profit_factor, 4) if math.isfinite(profit_factor) else None,
        "expectancy_per_trade": round(expectancy, 2),
        "max_drawdown_pct": round(100.0 * res.max_drawdown_pct, 2),
        "account_viability": {
            "survived": ruin_trade_idx is None,
            "ruin_trade_index": ruin_trade_idx,
            "ruin_date": ruin_date,
            "equity_at_ruin": round(equity_at_ruin, 2) if equity_at_ruin is not None else None,
            "terminal_equity": round(res.equity_curve[-1], 2),
        },
        "annual_breakdown": annual_breakdown,
        "trades_sample": [
            {
                "trade_id": t.trade_id,
                "entry_time": t.entry_time.isoformat(),
                "exit_time": t.exit_time.isoformat(),
                "direction": t.direction,
                "quantity": t.quantity,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "gross_pnl": t.gross_pnl,
                "commission": t.commission,
                "net_pnl": t.net_pnl,
                "exit_reason": t.exit_reason,
            }
            for t in res.trades[:3]
        ]
        + (
            [
                {
                    "trade_id": t.trade_id,
                    "entry_time": t.entry_time.isoformat(),
                    "exit_time": t.exit_time.isoformat(),
                    "direction": t.direction,
                    "quantity": t.quantity,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "gross_pnl": t.gross_pnl,
                    "commission": t.commission,
                    "net_pnl": t.net_pnl,
                    "exit_reason": t.exit_reason,
                }
                for t in res.trades[-3:]
            ]
            if len(res.trades) > 3
            else []
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", default=Path("E:/FARS-LAB/databento.zip"), type=Path)
    parser.add_argument("--workers", default=2, type=int)
    parser.add_argument(
        "--out-dir",
        default=Path("lab_artifacts/CODEX_OMNIROUTE_RUN/four_strategies_out"),
        type=Path,
    )
    args = parser.parse_args()

    zip_path = args.zip.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    num_workers = args.workers
    parent_pid = os.getpid()

    print(f"Parent PID: {parent_pid}")
    print(f"Loading canonical MNQ M5 from {zip_path} ...", flush=True)
    bars, dataset_meta = load_canonical_m5(zip_path)
    print(f"Loaded {len(bars)} bars ({bars[0].timestamp} to {bars[-1].timestamp})", flush=True)

    # Define jobs to execute
    jobs = [
        # 1. SMC-FVG
        {"strategy_id": "smc_fvg", "scenario_name": "gross", "commission_per_side": 0.0, "bars": bars},
        {"strategy_id": "smc_fvg", "scenario_name": "friction_4rt", "commission_per_side": 2.0, "bars": bars},
        # 2. EMAS
        {"strategy_id": "emas", "scenario_name": "gross", "commission_per_side": 0.0, "bars": bars},
        {"strategy_id": "emas", "scenario_name": "friction_4rt", "commission_per_side": 2.0, "bars": bars},
        # 3. CRT-TBS (Champion fixed_rr=2.0)
        {"strategy_id": "crt_tbs_champion", "scenario_name": "gross", "commission_per_side": 0.0, "bars": bars},
        {"strategy_id": "crt_tbs_champion", "scenario_name": "friction_4rt", "commission_per_side": 2.0, "bars": bars},
        # 4. CRT-TBS (Default literal target_mode="crt")
        {"strategy_id": "crt_tbs_default", "scenario_name": "gross", "commission_per_side": 0.0, "bars": bars},
        # 5. ORB (Experimental fixed config)
        {"strategy_id": "orb", "scenario_name": "gross", "commission_per_side": 0.0, "bars": bars},
        {"strategy_id": "orb", "scenario_name": "friction_4rt", "commission_per_side": 2.0, "bars": bars},
    ]

    print(f"\nDispatching {len(jobs)} jobs across {num_workers} workers using spawn multiprocessing...", flush=True)
    t_start = time.perf_counter()

    results_list: list[dict] = []
    worker_pids: set[int] = set()
    used_fallback = False

    try:
        mp_context = mp.get_context("spawn")
        with ProcessPoolExecutor(max_workers=num_workers, mp_context=mp_context) as executor:
            futures = {executor.submit(_worker_run_job, job): (job["strategy_id"], job["scenario_name"]) for job in jobs}
            for fut in as_completed(futures):
                strat, scen = futures[fut]
                res = fut.result()
                results_list.append(res)
                worker_pids.add(res["worker_pid"])
                print(
                    f"  Finished: {strat:18} [{scen:14}] on worker PID {res['worker_pid']} "
                    f"in {res['compute_time_seconds']}s -> {res['n_trades']} trades, WR {res['win_rate']}%, Net ${res['net_pnl']}",
                    flush=True,
                )
    except Exception as exc:
        print(f"Multiprocessing execution failed with error: {exc}", file=sys.stderr)
        print("Executing sequential fallback as documented...", file=sys.stderr)
        used_fallback = True
        results_list.clear()
        for job in jobs:
            res = _worker_run_job(job)
            results_list.append(res)
            worker_pids.add(res["worker_pid"])

    total_wall_time = time.perf_counter() - t_start
    print(f"\nAll jobs finished in {total_wall_time:.2f}s", flush=True)
    print(f"Distinct worker PIDs: {worker_pids} (Parent PID: {parent_pid})")
    print(f"Fallback used: {used_fallback}")

    # Build final comparative benchmark artifact
    # Sort results deterministically by strategy_id then scenario
    results_list.sort(key=lambda x: (x["strategy_id"], x["scenario_name"]))

    benchmark_data = {
        "metadata": {
            "generated_at": datetime.now(UTC).isoformat(),
            "environment": str(REPO_ROOT),
            "dataset": dataset_meta,
            "multiprocessing": {
                "configured_workers": num_workers,
                "parent_pid": parent_pid,
                "worker_pids": sorted(list(worker_pids)),
                "used_fallback": used_fallback,
                "total_wall_time_seconds": round(total_wall_time, 4),
            },
            "parameters_common": {
                "initial_balance": 50000.0,
                "risk_per_trade": 0.01,
                "risk_budget_dollars": 500.0,
                "market": MNQ.symbol,
                "point_value": MNQ.dollar_per_point,
                "tick_size": MNQ.tick_size,
                "friction_assumed_roundtrip": 4.0,
                "sizing_mode": "discrete_integer_contracts",
            },
        },
        "results": results_list,
    }

    out_file = out_dir / "four_strategies_benchmark.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(benchmark_data, f, indent=2)
    print(f"Benchmark JSON written to: {out_file}", flush=True)

    # Also copy to lab_artifacts/CODEX_OMNIROUTE_RUN/four_strategies_benchmark.json
    root_art_file = REPO_ROOT / "lab_artifacts/CODEX_OMNIROUTE_RUN/four_strategies_benchmark.json"
    with open(root_art_file, "w", encoding="utf-8") as f:
        json.dump(benchmark_data, f, indent=2)
    print(f"Artifact copied to: {root_art_file}", flush=True)

    return 0


if __name__ == "__main__":
    mp.freeze_support()
    sys.exit(main())

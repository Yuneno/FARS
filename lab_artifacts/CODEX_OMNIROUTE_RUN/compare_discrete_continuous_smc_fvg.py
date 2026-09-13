#!/usr/bin/env python3
"""Compare Continuous Weighted vs Discrete Contracts execution on SMC-FVG.

Evaluates SmcFvgStrategy on canonical MNQ M5 (timestamp >= 2019-05-06) from databento.zip
under identical signals and parameters, contrasting:
  1) Continuous weighted partials (50% TP1, 50% remaining)
  2) Discrete integer contracts (floor(quantity/2) at TP1, remainder at exit; 1 contract no partial with BE arm)

Usage:
    E:/FARS-LAB/.venv-fars/Scripts/python.exe \
        lab_artifacts/CODEX_OMNIROUTE_RUN/compare_discrete_continuous_smc_fvg.py \
        --zip E:/FARS-LAB/databento.zip \
        --out-dir lab_artifacts/CODEX_OMNIROUTE_RUN/juanca_smc_fvg_out
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lab_artifacts.CODEX_OMNIROUTE_RUN.benchmark_juanca_smc_fvg import (
    load_canonical_m5,
    summarize_result,
)
from src.backtest.executor import run_backtest
from src.backtest.markets import MNQ
from src.backtest.smc_fvg import SmcFvgStrategy, smc_fvg_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", default=Path("E:/FARS-LAB/databento.zip"), type=Path)
    parser.add_argument(
        "--out-dir",
        default=Path("lab_artifacts/CODEX_OMNIROUTE_RUN/juanca_smc_fvg_out"),
        type=Path,
    )
    args = parser.parse_args()

    zip_path = args.zip.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading canonical MNQ M5 from {zip_path} ...", flush=True)
    bars, dataset_meta = load_canonical_m5(zip_path)
    print(f"Loaded {len(bars)} bars ({bars[0].timestamp} to {bars[-1].timestamp})", flush=True)

    commission = MNQ.friction_points * MNQ.dollar_per_point / 2.0  # $2.00/side = $4.00 RT

    scenarios = {
        "continuous_gross": smc_fvg_config(
            market=MNQ,
            commission_per_side=0.0,
            slippage_points=0.0,
            discrete_partial_contracts=False,
        ),
        "discrete_gross": smc_fvg_config(
            market=MNQ,
            commission_per_side=0.0,
            slippage_points=0.0,
            discrete_partial_contracts=True,
        ),
        "continuous_friction": smc_fvg_config(
            market=MNQ,
            commission_per_side=commission,
            slippage_points=0.0,
            discrete_partial_contracts=False,
        ),
        "discrete_friction": smc_fvg_config(
            market=MNQ,
            commission_per_side=commission,
            slippage_points=0.0,
            discrete_partial_contracts=True,
        ),
    }

    results = {}
    trade_sets = {}

    for name, cfg in scenarios.items():
        print(f"Running scenario '{name}' ...", flush=True)
        t0 = time.perf_counter()
        res = run_backtest(bars, SmcFvgStrategy(), cfg)
        elapsed = time.perf_counter() - t0
        summary = summarize_result(res, cfg)
        summary["wall_seconds"] = round(elapsed, 4)
        results[name] = summary
        trade_sets[name] = res.trades
        print(
            f"  {name}: {summary['n_trades']} trades, WR {summary['win_rate']:.2%}, "
            f"Gross ${summary['gross_pnl']:.2f}, Net ${summary['net_pnl']:.2f}, "
            f"PF {summary['profit_factor']}, DD {summary['max_drawdown_pct']:.2%}, "
            f"time {elapsed:.2f}s",
            flush=True,
        )

    # Detailed trade-by-trade comparison between continuous and discrete friction
    t_cont = trade_sets["continuous_friction"]
    t_disc = trade_sets["discrete_friction"]
    assert len(t_cont) == len(t_disc), f"Trade count mismatch: {len(t_cont)} vs {len(t_disc)}"

    deltas = []
    even_deltas = []
    odd_deltas = []
    q1_trades = []

    for c, d in zip(t_cont, t_disc):
        assert c.entry_time == d.entry_time, f"Timing mismatch: {c.entry_time} vs {d.entry_time}"
        assert c.quantity == d.quantity, f"Qty mismatch: {c.quantity} vs {d.quantity}"
        assert c.commission == d.commission, f"Commission mismatch: {c.commission} vs {d.commission}"
        delta_pnl = round(d.net_pnl - c.net_pnl, 4)
        deltas.append(delta_pnl)
        if c.quantity % 2 == 0:
            even_deltas.append(delta_pnl)
        else:
            odd_deltas.append(delta_pnl)
        if c.quantity == 1:
            q1_trades.append({
                "trade_id": c.trade_id,
                "exit_reason": c.exit_reason,
                "cont_gross": c.gross_pnl,
                "disc_gross": d.gross_pnl,
                "cont_net": c.net_pnl,
                "disc_net": d.net_pnl,
            })

    comparison_summary = {
        "generated_utc": datetime.now(UTC).isoformat(),
        "dataset": dataset_meta,
        "scenarios": results,
        "comparison_analysis": {
            "total_trades": len(t_cont),
            "even_quantity_trades": len(even_deltas),
            "even_quantity_max_abs_diff": max(abs(x) for x in even_deltas) if even_deltas else 0.0,
            "odd_quantity_trades": len(odd_deltas),
            "odd_quantity_total_pnl_diff": round(sum(odd_deltas), 2),
            "q1_trade_count": len(q1_trades),
            "q1_examples": q1_trades[:5],
            "commission_identity_verified": True,
            "signal_identity_verified": True,
            "explanation": (
                "For even quantities (Q=2k), floor(2k/2) = k = 0.50*Q, yielding bit-for-bit identical "
                "PnL to the continuous 50% partial model (max diff = 0.00). For odd quantities (Q=2k+1), "
                "discrete closes k contracts at TP1 and k+1 contracts at the final exit, shifting weight "
                "slightly toward the final exit (runner). With Q=1, 0 contracts exit at TP1 while the "
                "stop is trailed to break-even at 1R; winners achieve full 1.5R target rather than being "
                "diluted to 1.25R, while trades stopped at BE realize $0 gross rather than $1R on half a contract."
            ),
        },
    }

    out_file = out_dir / "smc_fvg_discrete_comparison.json"
    with out_file.open("w", encoding="utf-8") as f:
        json.dump(comparison_summary, f, indent=2)
        f.write("\n")
    print(f"Discrete comparison written to {out_file}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

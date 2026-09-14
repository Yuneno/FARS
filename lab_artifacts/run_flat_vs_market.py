#!/usr/bin/env python3
"""Reproducible script for A2.1: Flat vs Market exit comparison for CRT-TBS and ORB.

Evaluates:
- CRT-TBS (Champion: H4 bias, H1 CRT, M5 confirmation, fixed_rr=2.0)
- ORB (09:30-10:00 NY range, opposite stop, 2R target, max_hold=192 bars)
Under both time_exit_mode="flat" and time_exit_mode="market".

Counts expirations (time_exit trades) and attributes the net PnL delta strictly to expirations.
Outputs:
- lab_artifacts/flat_vs_market_comparison.json
- lab_artifacts/flat_vs_market_comparison.csv
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import sys
import time
import zipfile
from datetime import UTC, datetime

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.backtest.crt_tbs import CrtTbsConfig, CrtTbsStrategy, crt_tbs_config
from src.backtest.orb import OrbConfig, OrbStrategy, orb_config
from src.backtest.executor import run_backtest
from src.backtest.history import Bar
from src.backtest.markets import MNQ


def load_canonical_m5(zip_path: Path, member: str = "databento/MNQ_M5.csv") -> list[Bar]:
    bars: list[Bar] = []
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(member) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8"))
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
    return bars


def run_strategy(bars: list[Bar], strat_id: str, mode: str):
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
            time_exit_mode=mode,
        )
    elif strat_id == "orb":
        strat_cfg = OrbConfig(
            or_minutes=30,
            stop_mode="opposite",
            rr=2.0,
            use_bias=False,
            fade=False,
            end_hour=16,
            max_hold_bars=192,
            time_exit_mode=mode,
        )
        strategy = OrbStrategy(strat_cfg)
        cfg = orb_config(
            market=MNQ,
            commission_per_side=2.0,
            slippage_points=0.0,
            max_bars_held=192,
            time_exit_mode=mode,
        )
    else:
        raise ValueError(f"Unknown strategy: {strat_id}")

    res = run_backtest(bars, strategy, cfg)
    time_exits = [t for t in res.trades if t.exit_reason == "time_exit"]
    non_time_exits = [t for t in res.trades if t.exit_reason != "time_exit"]
    return {
        "res": res,
        "n_trades": res.n_trades,
        "n_time_exits": len(time_exits),
        "n_non_time_exits": len(non_time_exits),
        "win_rate": res.win_rate,
        "profit_factor": res.profit_factor,
        "gross_pnl": sum(t.gross_pnl for t in res.trades),
        "commission": sum(t.commission for t in res.trades),
        "net_pnl": res.net_pnl,
        "net_r": sum(t.r_result for t in res.trades),
        "max_drawdown_pct": res.max_drawdown_pct,
        "time_exit_net_pnl": sum(t.net_pnl for t in time_exits),
        "time_exit_gross_pnl": sum(t.gross_pnl for t in time_exits),
        "time_exit_comm": sum(t.commission for t in time_exits),
        "non_time_exit_net_pnl": sum(t.net_pnl for t in non_time_exits),
        "trades": res.trades,
    }


def main():
    zip_path = Path("E:/FARS-LAB/databento.zip")
    print(f"Loading canonical M5 bars from {zip_path}...")
    t0 = time.perf_counter()
    bars = load_canonical_m5(zip_path)
    print(f"Loaded {len(bars)} M5 bars in {time.perf_counter() - t0:.2f}s")

    strategies = [
        ("CRT-TBS (Champion)", "crt_tbs_champion", 1542.0),
        ("ORB (Experimental)", "orb", -205061.5),
    ]

    records = []
    summary_rows = []

    for label, strat_id, expected_flat_pnl in strategies:
        print(f"\nEvaluating {label}...")
        res_flat = run_strategy(bars, strat_id, "flat")
        res_mkt = run_strategy(bars, strat_id, "market")

        # Invariant checks
        assert res_flat["n_trades"] == res_mkt["n_trades"], "Trade count mismatch!"
        assert res_flat["n_time_exits"] == res_mkt["n_time_exits"], "Time exit count mismatch!"
        
        # Verify flat baseline reproduction (+/- $0)
        pnl_flat = res_flat["net_pnl"]
        diff_from_baseline = abs(pnl_flat - expected_flat_pnl)
        assert diff_from_baseline < 1e-6, f"Flat baseline did not reproduce! Got {pnl_flat}, expected {expected_flat_pnl}"

        # Attributed delta check: non-time exits must be identical
        non_time_diff = abs(res_flat["non_time_exit_net_pnl"] - res_mkt["non_time_exit_net_pnl"])
        assert non_time_diff < 1e-6, f"Non-time exits differed between flat and market! diff={non_time_diff}"

        attributed_delta_pnl = res_mkt["net_pnl"] - res_flat["net_pnl"]
        time_exit_delta_pnl = res_mkt["time_exit_net_pnl"] - res_flat["time_exit_net_pnl"]
        assert abs(attributed_delta_pnl - time_exit_delta_pnl) < 1e-6, "Attributed delta not 100% explained by time exits!"

        record = {
            "strategy": label,
            "strategy_id": strat_id,
            "n_trades": res_flat["n_trades"],
            "n_expiraciones": res_flat["n_time_exits"],
            "pct_expiraciones": (res_flat["n_time_exits"] / res_flat["n_trades"]) * 100,
            "flat": {
                "gross_pnl": res_flat["gross_pnl"],
                "commission": res_flat["commission"],
                "net_pnl": res_flat["net_pnl"],
                "profit_factor": res_flat["profit_factor"],
                "win_rate_pct": res_flat["win_rate"] * 100,
                "time_exit_net_pnl": res_flat["time_exit_net_pnl"],
            },
            "market": {
                "gross_pnl": res_mkt["gross_pnl"],
                "commission": res_mkt["commission"],
                "net_pnl": res_mkt["net_pnl"],
                "profit_factor": res_mkt["profit_factor"],
                "win_rate_pct": res_mkt["win_rate"] * 100,
                "time_exit_net_pnl": res_mkt["time_exit_net_pnl"],
            },
            "delta_atribuido": {
                "net_pnl_delta": attributed_delta_pnl,
                "time_exit_pnl_delta": time_exit_delta_pnl,
                "gross_pnl_delta": res_mkt["gross_pnl"] - res_flat["gross_pnl"],
                "commission_delta": res_mkt["commission"] - res_flat["commission"],
                "pf_delta": res_mkt["profit_factor"] - res_flat["profit_factor"],
                "wr_delta_pp": (res_mkt["win_rate"] - res_flat["win_rate"]) * 100,
            }
        }
        records.append(record)

        summary_rows.append({
            "Estrategia": label,
            "Trades": res_flat["n_trades"],
            "Expiraciones": res_flat["n_time_exits"],
            "% Expiraciones": f"{record['pct_expiraciones']:.2f}%",
            "PnL Flat ($)": f"{res_flat['net_pnl']:,.2f}",
            "PF Flat": f"{res_flat['profit_factor']:.4f}",
            "PnL Market ($)": f"{res_mkt['net_pnl']:,.2f}",
            "PF Market": f"{res_mkt['profit_factor']:.4f}",
            "Delta Atribuido ($)": f"{attributed_delta_pnl:+,.2f}",
        })
        print(f"  [OK] {label}: Flat={res_flat['net_pnl']:+,.2f} | Market={res_mkt['net_pnl']:+,.2f} | Delta={attributed_delta_pnl:+,.2f}")

    out_dir = Path("lab_artifacts")
    out_dir.mkdir(exist_ok=True)

    json_path = out_dir / "flat_vs_market_comparison.json"
    json_path.write_text(json.dumps({"comparison": records, "timestamp_utc": datetime.now(UTC).isoformat()}, indent=2), encoding="utf-8")
    print(f"\nSaved JSON to {json_path}")

    csv_path = out_dir / "flat_vs_market_comparison.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)
    print(f"Saved CSV to {csv_path}")


if __name__ == "__main__":
    main()

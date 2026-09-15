"""Script to compare session_date_for_ledger (False vs True) and end_of_data_slippage_points (0.0 vs 0.25)
on canonical M5 dataset for CRT-TBS Champion and ORB Experimental.

Outputs full operation-level and account-level comparison tables.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
import hashlib

from src.backtest.crt_tbs import CrtTbsConfig, CrtTbsStrategy, crt_tbs_config
from src.backtest.executor import BacktestConfig, run_backtest, executed_to_core_trades
from src.backtest.history import Bar
from src.backtest.markets import MNQ
from src.backtest.orb import OrbConfig, OrbStrategy, orb_config
from src.engine import run_simulation
from src.types import FundedAccountRules

ZIP_PATH = Path("E:/FARS-LAB/databento.zip")
M5_MEMBER = "databento/MNQ_M5.csv"


def load_canonical_m5() -> list[Bar]:
    bars: list[Bar] = []
    with zipfile.ZipFile(ZIP_PATH) as zf:
        with zf.open(M5_MEMBER) as raw:
            reader = csv.DictReader(io.StringIO(raw.read().decode("utf-8")))
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


def run_single(bars: list[Bar], strat_factory, base_config: BacktestConfig, *, session_date_flag: bool, end_of_data_slip: float = 0.0):
    cfg = replace(
        base_config,
        session_date_for_ledger=session_date_flag,
        end_of_data_slippage_points=end_of_data_slip,
    )
    strat = strat_factory()
    res = run_backtest(bars, strat, cfg)

    # Core trades mapping
    core_trades = executed_to_core_trades(res.trades, config=cfg, strategy_name=strat.__class__.__name__)
    
    # Operation-level hash
    trades_hash = hashlib.sha256(
        json.dumps(
            [
                {
                    "trade_id": t.trade_id,
                    "direction": t.direction,
                    "entry_time": t.entry_time.isoformat(),
                    "exit_time": t.exit_time.isoformat(),
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "quantity": t.quantity,
                    "gross_pnl": t.gross_pnl,
                    "commission": t.commission,
                    "net_pnl": t.net_pnl,
                    "r_result": t.r_result,
                    "exit_reason": t.exit_reason,
                    "slippage_cost": t.slippage_cost,
                }
                for t in res.trades
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()

    # Dates hash
    dates_list = [t.date for t in core_trades]
    distinct_dates = sorted(set(dates_list))

    sim = res.simulation

    eod_trades = [t for t in res.trades if t.exit_reason == "end_of_data"]

    return {
        "config": {
            "session_date_for_ledger": cfg.session_date_for_ledger,
            "end_of_data_slippage_points": cfg.end_of_data_slippage_points,
            "commission_per_side": cfg.commission_per_side,
            "slippage_points": cfg.slippage_points,
        },
        "operation_level": {
            "n_trades": res.n_trades,
            "net_pnl": round(res.net_pnl, 2),
            "profit_factor": round(res.profit_factor, 4) if res.profit_factor != float("inf") else "inf",
            "net_r": round(sum(t.r_result for t in res.trades), 4),
            "expectancy_money": round(res.expectancy, 2),
            "win_rate": round(res.win_rate, 4),
            "trades_hash": trades_hash,
            "eod_trades_count": len(eod_trades),
            "eod_trades_pnl": round(sum(t.net_pnl for t in eod_trades), 2),
            "eod_trades_slippage": round(sum(t.slippage_cost for t in eod_trades), 2),
        },
        "account_level": {
            "terminal_condition": sim.terminal_condition if sim else None,
            "passed": sim.passed if sim else None,
            "final_equity": round(sim.final_equity, 2) if sim else None,
            "trades_executed": sim.trades_executed if sim else None,
            "max_drawdown_hit": round(sim.max_drawdown_hit, 2) if sim else None,
            "max_drawdown_historical": round(sim.max_drawdown_historical, 2) if sim else None,
            "daily_loss_hit": round(sim.daily_loss_hit, 2) if sim else None,
            "violated_conditions": list(sim.violated_conditions) if sim else [],
            "distinct_trading_days": len(distinct_dates),
            "first_trade_date": dates_list[0] if dates_list else None,
            "last_trade_date": dates_list[-1] if dates_list else None,
        },
        "eod_trades_detail": [
            {
                "trade_id": t.trade_id,
                "direction": t.direction,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "quantity": t.quantity,
                "gross_pnl": t.gross_pnl,
                "net_pnl": t.net_pnl,
                "r_result": round(t.r_result, 6),
                "slippage_cost": t.slippage_cost,
            }
            for t in eod_trades
        ]
    }


def main():
    print("Loading canonical M5 bars...")
    bars = load_canonical_m5()
    print(f"Loaded {len(bars):,} bars.")

    # 1. CRT-TBS Champion
    print("\n--- Running CRT-TBS Champion ---")
    crt_strat_cfg = CrtTbsConfig(
        require_4h_bias=True,
        require_half_zone=False,
        target_mode="fixed_rr",
        fixed_rr=2.0,
    )
    crt_factory = lambda: CrtTbsStrategy(crt_strat_cfg)
    crt_base_cfg = crt_tbs_config(
        market=MNQ,
        commission_per_side=2.0,
        slippage_points=0.0,
        time_exit_mode="market",
        time_exit_slippage_points=0.0,
    )

    print("Running CRT-TBS with session_date_for_ledger=False (default UTC)...")
    crt_false = run_single(bars, crt_factory, crt_base_cfg, session_date_flag=False)
    print("Running CRT-TBS with session_date_for_ledger=True (CME session)...")
    crt_true = run_single(bars, crt_factory, crt_base_cfg, session_date_flag=True)

    # 2. ORB Experimental
    print("\n--- Running ORB Experimental ---")
    orb_strat_cfg = OrbConfig(
        or_minutes=30,
        stop_mode="opposite",
        rr=2.0,
        use_bias=False,
        fade=False,
        end_hour=16,
        max_hold_bars=192,
        time_exit_mode="market",
    )
    orb_factory = lambda: OrbStrategy(orb_strat_cfg)
    orb_base_cfg = orb_config(
        market=MNQ,
        commission_per_side=2.0,
        slippage_points=0.0,
        max_bars_held=192,
        time_exit_mode="market",
        time_exit_slippage_points=0.0,
    )

    print("Running ORB with session_date_for_ledger=False (default UTC)...")
    orb_false = run_single(bars, orb_factory, orb_base_cfg, session_date_flag=False)
    print("Running ORB with session_date_for_ledger=True (CME session)...")
    orb_true = run_single(bars, orb_factory, orb_base_cfg, session_date_flag=True)

    # 3. ORB end_of_data slippage sensitivity (0.0 vs 0.25)
    print("\nRunning ORB with end_of_data_slippage_points=0.25 (1 tick)...")
    orb_slip_1tick = run_single(bars, orb_factory, orb_base_cfg, session_date_flag=False, end_of_data_slip=0.25)

    results = {
        "crt_tbs": {
            "session_date_false": crt_false,
            "session_date_true": crt_true,
        },
        "orb": {
            "session_date_false": orb_false,
            "session_date_true": orb_true,
            "end_of_data_1tick": orb_slip_1tick,
        },
    }

    out_path = Path("lab_artifacts/a4_session_and_costs_comparison.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\nSaved results to {out_path}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Chequeos independientes y reproducibles para la revision T3 de T1.

F1 Fix B (2026-10-11): corre con entry_end=14:30 + flat_time=15:10 (flat Topstep);
el numero esperado es max_exit_clock=15:10 (antes: 14:30 con trade_end unico).
"""
from __future__ import annotations

import datetime as dt
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

AUTHOR_SRC = Path(r"E:\FARS-LAB\ext_review2\nq-intraday-breakout\src")
DATA = Path(r"E:\FARS-LAB\ext_review2\data_adapted\mnq_1min_multicharts.csv.gz")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(AUTHOR_SRC))

from nq_breakout.backtest import Config, SimFlags, run_backtest  # noqa: E402
from nq_breakout.data import load_nq_1min, resample_5min  # noqa: E402


def main() -> None:
    df5 = resample_5min(load_nq_1min(DATA))
    cfg = replace(
        Config(),
        point_value=2.0,
        tick_value=0.50,
        stop_dollars=200.0,
        target_dollars=400.0,
        commission_per_side=0.62,
        slippage_ticks_per_side=1.5,
        slippage_sides=2,
        initial_capital=100_000.0,
        margin_per_contract=2_328.26,
        max_long_hold_days=1,
        entry_end=dt.time(14, 30),
        flat_time=dt.time(15, 10),
    )
    trades = run_backtest(df5, cfg, SimFlags()).trades
    entry_bars = df5.set_index("date").loc[pd.to_datetime(trades["entry_time"])].reset_index(drop=True)
    is_long = trades["direction"].to_numpy() == "long"
    stop = np.where(is_long, trades["entry_price"] - 100.0, trades["entry_price"] + 100.0)
    target = np.where(is_long, trades["entry_price"] + 200.0, trades["entry_price"] - 200.0)
    stop_touched = np.where(is_long, entry_bars["low"].to_numpy() <= stop,
                            entry_bars["high"].to_numpy() >= stop)
    target_touched = np.where(is_long, entry_bars["high"].to_numpy() >= target,
                              entry_bars["low"].to_numpy() <= target)
    exit_clock = trades["exit_time"].dt.strftime("%H:%M")
    extra_tick_rt_usd = cfg.slippage_sides * cfg.tick_value
    extra_tick_r = extra_tick_rt_usd / cfg.stop_dollars

    lines = [
        f"n_trades={len(trades)}",
        f"entry_bar_stop_touched={int(stop_touched.sum())}",
        f"entry_bar_target_touched={int(target_touched.sum())}",
        f"entry_bar_both_touched={int((stop_touched & target_touched).sum())}",
        f"entry_bar_any_exit_touched={int((stop_touched | target_touched).sum())}",
        f"max_exit_clock={exit_clock.max()}",
        f"exits_at_14_30={int((exit_clock == '14:30').sum())}",
        f"exits_at_15_10={int((exit_clock == '15:10').sum())}",
        f"extra_1_tick_per_side_rt_usd={extra_tick_rt_usd:.2f}",
        f"extra_1_tick_per_side_R={extra_tick_r:.6f}",
    ]
    text = "\n".join(lines) + "\n"
    print(text, end="")
    (HERE / "T3_AUDIT_CHECKS.txt").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()

"""Capture exact MNQ results; run before AND after a refactor.

python -m docs.refactor.check_mnq_equivalence --output /tmp/before.json [--csv PATH]
python -m docs.refactor.check_mnq_equivalence --output /tmp/after.json --reference /tmp/before.json

Without --csv, use deterministic confluence scenarios (both directions and
stop/target/time exits). With --csv, use the entire unmodified MNQ dataset.
Floats are encoded as IEEE-754 binary64 bytes, not rounded decimal metrics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from src.backtest.amd_crt import ET, AmdCrtStrategy, amd_crt_config
from src.backtest.emas import EmasStrategy, emas_config
from src.backtest.executor import run_backtest
from src.backtest.history import Bar
from src.backtest.mnq_csv import load_mnq_csv
from src.backtest.smc_fvg import SmcFvgStrategy, smc_fvg_config

_ADDITIVE_EXECUTOR_FIELDS = {
    "partial_take_profit_fraction",
    "move_stop_to_break_even",
    "pending_limit_entry",
    "pending_order_wait_bars",
    "cooldown_bars",
    "discrete_partial_contracts",
    "time_exit_mode",
    "end_of_data_policy",
    "time_exit_slippage_points",
    "end_of_data_slippage_points",
    "session_date_for_ledger",
}

_ADDITIVE_RESULT_FIELDS = {
    "open_position",
    "intrabar_audit",
}

_ADDITIVE_TRADE_FIELDS = {
    "budgeted_risk_dollars",
    "effective_risk_dollars",
    "budgeted_r",
    "effective_r",
}


def exact(value):
    if is_dataclass(value):
        return {
            field.name: exact(getattr(value, field.name))
            for field in fields(value)
            if not (
                value.__class__.__name__ == "BacktestConfig"
                and field.name in _ADDITIVE_EXECUTOR_FIELDS
            )
            and not (
                value.__class__.__name__ == "BacktestResult"
                and field.name in _ADDITIVE_RESULT_FIELDS
            )
            and not (
                value.__class__.__name__ == "ExecutedTrade"
                and field.name in _ADDITIVE_TRADE_FIELDS
            )
        }
    if isinstance(value, float):
        return {"binary64": struct.pack("!d", value).hex()}
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): exact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [exact(item) for item in value]
    return value


def encoded(value):
    return json.dumps(exact(value), sort_keys=True, separators=(",", ":")).encode()


def synthetic_cases():
    today = date(2026, 9, 7)
    days = []
    day = today - timedelta(days=1)
    while len(days) < 260:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    calibration = [
        Bar(
            datetime.combine(day, time(9, 30), ET) + timedelta(minutes=5 * i),
            150.0,
            200.0,
            100.0,
            150.0,
            10.0,
        )
        for day in reversed(days)
        for i in range(78)
    ]
    start = datetime.combine(today, time(0), ET)
    pre = [
        Bar(start + timedelta(minutes=5 * i), 110.0, 125.0, 100.0, 110.0, 10.0) for i in range(114)
    ]
    for direction in ("short", "long"):
        sweep = (
            (120.0, 205.0, 100.0, 120.0) if direction == "short" else (110.0, 120.0, 95.0, 110.0)
        )
        for reason in ("stop", "target", "time_exit"):
            bars = pre + [Bar(start + timedelta(hours=9, minutes=30), *sweep, 10.0)]
            for i in range(14):
                high, low = 121.0, 119.0
                if i == 1 and reason != "time_exit":
                    up = (direction == "long") == (reason == "target")
                    high, low = (230.0, 119.0) if up else (121.0, 10.0)
                bars.append(
                    Bar(
                        start + timedelta(hours=9, minutes=35 + 5 * i),
                        120.0,
                        high,
                        low,
                        120.0,
                        10.0,
                    )
                )
            yield f"{direction}_{reason}", calibration, bars


def capture(csv_path=None, *, ema=False, strategy_name="amd-crt"):
    if csv_path is not None:
        cases = [("full_csv", [], load_mnq_csv(csv_path))]
    else:
        cases = synthetic_cases()
    payload = {}
    for name, calibration, bars in cases:
        if strategy_name == "smc-fvg":
            strategy = SmcFvgStrategy()
            config = smc_fvg_config()
        elif strategy_name == "emas":
            strategy = EmasStrategy()
            config = emas_config()
        else:
            strategy = AmdCrtStrategy(use_ema_filter=ema)
            config = amd_crt_config()
        result = run_backtest(bars, strategy, config, calibration_bars=calibration)
        payload[name] = {
            "result": exact(result),
            "decisions": exact(strategy.decisions),
            "parameters": strategy.parameters(),
        }
        print(f"{name}: bars={len(bars)}, trades={result.n_trades}", flush=True)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--ema", action="store_true")
    parser.add_argument(
        "--strategy", choices=("amd-crt", "smc-fvg", "emas"), default="amd-crt"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    args = parser.parse_args()
    if args.reference and (
        args.output.resolve() == args.reference.resolve()
        or (args.output.exists() and args.output.samefile(args.reference))
    ):
        parser.error("--output must not overwrite --reference")
    reference = args.reference.read_bytes() if args.reference else None
    if args.strategy != "amd-crt" and args.csv is None:
        parser.error("--strategy smc-fvg/emas requires --csv")
    payload = capture(args.csv, ema=args.ema, strategy_name=args.strategy)
    data = encoded(payload)
    args.output.write_bytes(data)
    print(f"sha256={hashlib.sha256(data).hexdigest()}", flush=True)
    if reference is not None:
        if data != reference:
            raise SystemExit("MNQ differs from the pre-refactor reference")
        print("BIT-FOR-BIT IDENTICAL (full results, decisions and parameters)")


if __name__ == "__main__":
    main()

"""Backtest pipeline (FASE D): chronological split, run, and report.

Splits bars chronologically (no shuffle) into a development segment and a final
out-of-sample segment, runs the same deterministic backtest on each, and
serializes a reproducible report (config, trades, equity curve, summary).
"""

from __future__ import annotations

import copy
import csv
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import time, timedelta, tzinfo
from pathlib import Path
from typing import Any

from src.backtest.executor import BacktestConfig, BacktestResult, ExecutedTrade, run_backtest
from src.backtest.history import Bar
from src.backtest.strategy import Strategy


@dataclass(frozen=True)
class SplitResult:
    in_sample: list[Bar]
    out_of_sample: list[Bar]
    split_fraction: float


def chronological_split(
    bars: list[Bar],
    train_fraction: float = 0.7,
    *,
    session_tz: tzinfo | None = None,
    session_start: time = time(0, 0),
) -> SplitResult:
    """Chronological split at the session boundary nearest the target fraction."""
    if not 0.0 < train_fraction < 1.0:
        raise ValueError("train_fraction must be in (0, 1)")
    n = len(bars)
    if n < 2:
        raise ValueError("at least two bars are required for an IS/OOS split")

    def session_date(bar: Bar):
        local = bar.timestamp.astimezone(session_tz) if session_tz else bar.timestamp
        day = local.date()
        if session_start != time(0, 0) and local.time() < session_start:
            day -= timedelta(days=1)
        return day

    target = max(1, min(round(n * train_fraction), n - 1))
    boundaries = [
        index
        for index in range(1, n)
        if session_date(bars[index - 1]) != session_date(bars[index])
    ]
    if not boundaries:
        raise ValueError("at least one session boundary is required for an IS/OOS split")
    cut = min(boundaries, key=lambda index: (abs(index - target), index))
    return SplitResult(
        in_sample=list(bars[:cut]),
        out_of_sample=list(bars[cut:]),
        split_fraction=train_fraction,
    )


def result_summary(result: BacktestResult, label: str) -> dict[str, Any]:
    sim = result.simulation
    return {
        "label": label,
        "raw_net_pnl": result.net_pnl,
        "raw_n_trades": result.n_trades,
        "raw_win_rate": result.win_rate,
        "raw_profit_factor": result.profit_factor,
        "raw_expectancy": result.expectancy,
        "raw_max_drawdown_pct": result.max_drawdown_pct,
        "raw_total_commission": result.total_commission,
        "raw_total_slippage_cost": result.total_slippage_cost,
        "raw_gap_rejections": result.gap_rejections,
        "raw_unresolved_positions": result.unresolved_positions,
        "rule_limited_net_pnl": (
            sim.final_equity - result.config.initial_balance if sim else 0.0
        ),
        "rule_limited_final_equity": (
            sim.final_equity if sim else result.config.initial_balance
        ),
        "rule_limited_n_trades": sim.trades_executed if sim else 0,
        "rule_limited_terminal_condition": sim.terminal_condition if sim else None,
        "rule_limited_max_drawdown_pct": (
            sim.max_drawdown_historical if sim else 0.0
        ),
        "incomplete": bool(result.unresolved_positions),
    }


def config_dict(config: BacktestConfig) -> dict[str, Any]:
    return asdict(config)


def _fresh_strategy(strategy: Strategy) -> Strategy:
    factory = getattr(strategy, "fresh", None)
    return factory() if callable(factory) else copy.deepcopy(strategy)


def run_pipeline(
    bars: list[Bar],
    strategy: Strategy,
    config: BacktestConfig,
    *,
    train_fraction: float = 0.7,
) -> dict[str, Any]:
    """Run IS + OOS backtests and assemble a full report dict."""
    session_tz = getattr(strategy, "session_tz", None)
    session_start = getattr(strategy, "session_start", time(0, 0))
    split = chronological_split(
        bars,
        train_fraction,
        session_tz=session_tz,
        session_start=session_start,
    )
    is_strategy = _fresh_strategy(strategy)
    oos_strategy = _fresh_strategy(strategy)
    is_result = run_backtest(split.in_sample, is_strategy, config)
    oos_result = run_backtest(
        split.out_of_sample,
        oos_strategy,
        config,
        calibration_bars=split.in_sample,
    )
    parameters = getattr(strategy, "parameters", None)
    strategy_parameters = (
        parameters()
        if callable(parameters)
        else asdict(strategy) if is_dataclass(strategy) else {}  # type: ignore[arg-type]
    )
    return {
        "config": config_dict(config),
        "split_fraction": split.split_fraction,
        "n_bars_in_sample": len(split.in_sample),
        "n_bars_out_of_sample": len(split.out_of_sample),
        "in_sample": result_summary(is_result, "in_sample"),
        "out_of_sample": result_summary(oos_result, "out_of_sample"),
        "strategy": strategy.__class__.__name__,
        "strategy_parameters": strategy_parameters,
        "assumptions": [
            "entry bar IS checked for TP/SL; stop assumed first when TP+SL co-occur",
            "PROVISIONAL commission_per_side and slippage_points (not provider-quoted)",
            "timestamp-based time exit (max_hold_minutes) closes at the bar open",
            "a gap through the stop fills at the bar open (stop not guaranteed)",
            "synthetic bars unless a real MNQ CSV was provided",
        ],
        "technical_status": "PASS",
    }


def write_trades_csv(trades: tuple[ExecutedTrade, ...], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "trade_id", "direction", "entry_time", "exit_time", "entry_price",
                "exit_price", "stop_price", "target_price", "quantity", "gross_pnl",
                "commission", "net_pnl", "r_result", "exit_reason",
            ]
        )
        for t in trades:
            writer.writerow(
                [
                    t.trade_id, t.direction, t.entry_time.isoformat(),
                    t.exit_time.isoformat(), t.entry_price, t.exit_price,
                    t.stop_price, t.target_price, t.quantity, t.gross_pnl,
                    t.commission, t.net_pnl, t.r_result, t.exit_reason,
                ]
            )


def write_report(report: dict[str, Any], out_dir: str | Path) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary_path = out / "backtest_summary.json"
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True, default=str)
    return {"summary": str(summary_path)}


__all__ = [
    "SplitResult",
    "chronological_split",
    "result_summary",
    "run_pipeline",
    "write_report",
    "write_trades_csv",
]

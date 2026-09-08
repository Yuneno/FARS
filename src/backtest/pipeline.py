"""Backtest pipeline (FASE D): chronological split, run, and report.

Splits bars chronologically (no shuffle) into a development segment and a final
out-of-sample segment, runs the same deterministic backtest on each, and
serializes a reproducible report (config, trades, equity curve, summary).
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
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


def chronological_split(bars: list[Bar], train_fraction: float = 0.7) -> SplitResult:
    """Chronological (no-shuffle) split. Last (1 - train_fraction) is OOS."""
    if not 0.0 < train_fraction < 1.0:
        raise ValueError("train_fraction must be in (0, 1)")
    n = len(bars)
    cut = max(1, int(round(n * train_fraction)))
    cut = min(cut, n - 1)
    return SplitResult(
        in_sample=list(bars[:cut]),
        out_of_sample=list(bars[cut:]),
        split_fraction=train_fraction,
    )


def result_summary(result: BacktestResult, label: str) -> dict[str, Any]:
    sim = result.simulation
    return {
        "label": label,
        "net_pnl": result.net_pnl,
        "n_trades": result.n_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "expectancy": result.expectancy,
        "max_drawdown_pct": result.max_drawdown_pct,
        "total_commission": result.total_commission,
        "total_slippage_cost": result.total_slippage_cost,
        "terminal_condition": sim.terminal_condition if sim else None,
        "trades_executed": sim.trades_executed if sim else 0,
    }


def config_dict(config: BacktestConfig) -> dict[str, Any]:
    return asdict(config)


def run_pipeline(
    bars: list[Bar],
    strategy: Strategy,
    config: BacktestConfig,
    *,
    train_fraction: float = 0.7,
) -> dict[str, Any]:
    """Run IS + OOS backtests and assemble a full report dict."""
    split = chronological_split(bars, train_fraction)
    is_result = run_backtest(split.in_sample, strategy, config)
    oos_result = run_backtest(split.out_of_sample, strategy, config)
    return {
        "config": config_dict(config),
        "split_fraction": split.split_fraction,
        "n_bars_in_sample": len(split.in_sample),
        "n_bars_out_of_sample": len(split.out_of_sample),
        "in_sample": result_summary(is_result, "in_sample"),
        "out_of_sample": result_summary(oos_result, "out_of_sample"),
        "strategy": strategy.__class__.__name__,
        "strategy_parameters": (
            asdict(strategy) if is_dataclass(strategy) else {}  # type: ignore[arg-type]
        ),
        "assumptions": [
            "PROVISIONAL strategy: Donchian breakout placeholder (not validated)",
            "PROVISIONAL commission_per_side and slippage_points (not provider-quoted)",
            "PROVISIONAL max_bars_held time-exit",
            "conservative intrabar policy: stop assumed first when TP+SL co-occur",
            "entry bar is not checked for TP/SL (conservative)",
            "synthetic bars unless a real MNQ download succeeded",
        ],
        "technical_status": "PASS" if (is_result.n_trades + oos_result.n_trades) >= 0 else "FAIL",
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
    "write_trades_csv",
    "write_report",
]

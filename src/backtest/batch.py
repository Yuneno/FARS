"""Batch / scenario mode (FASE E) reusing the Core Monte Carlo engine.

Explores a grid of commissions, slippage, and strategy parameters, and for each
scenario runs the deterministic backtest on the in-sample segment and then a
seeded Monte Carlo pass-probability estimate over the resulting trades. Results
are appended to a JSONL journal (never overwritten, resumable by scenario key);
an aggregate summary is written at the end.
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from src.backtest.executor import (
    BacktestConfig,
    executed_to_core_trades,
    run_backtest,
)
from src.backtest.history import Bar
from src.backtest.pipeline import chronological_split
from src.backtest.strategy import BreakoutStrategy
from src.monte_carlo import MonteCarloConfig, run_monte_carlo
from src.types import FundedAccountRules


def _rules(config: BacktestConfig) -> FundedAccountRules:
    return FundedAccountRules(
        initial_balance=config.initial_balance,
        profit_target_pct=config.profit_target_pct,
        max_drawdown_pct=config.max_drawdown_pct,
        daily_loss_limit_pct=config.daily_loss_limit_pct,
        risk_per_trade=config.risk_per_trade,
        max_trades=config.max_trades,
    )


def _scenario_key(name: str, seed: int) -> str:
    return f"{name}::seed={seed}"


def run_batch(
    bars: list[Bar],
    *,
    scenarios: list[dict[str, Any]],
    seeds: list[int],
    out_dir: str | Path,
    base_config: BacktestConfig | None = None,
    train_fraction: float = 0.7,
    mc_simulations: int = 1_000,
) -> dict[str, Any]:
    """Run the scenario grid. Returns the aggregate summary dict."""
    base_config = base_config or BacktestConfig()
    split = chronological_split(bars, train_fraction)
    is_bars = split.in_sample
    oos_bars = split.out_of_sample

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    journal = out / "batch_results.jsonl"

    # Load already-computed keys so a re-run only fills in missing scenarios.
    done: set[str] = set()
    if journal.exists():
        with journal.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    done.add(row.get("key", ""))
                except json.JSONDecodeError:
                    continue

    rows: list[dict[str, Any]] = []
    with journal.open("a", encoding="utf-8") as handle:
        for scenario in scenarios:
            name = scenario["name"]
            config = replace(base_config, **scenario.get("config", {}))
            strategy = BreakoutStrategy(**scenario.get("strategy", {}))
            is_result = run_backtest(is_bars, strategy, config)
            oos_result = run_backtest(oos_bars, strategy, config)
            core_trades = executed_to_core_trades(is_result.trades)

            for seed in seeds:
                key = _scenario_key(name, seed)
                if key in done:
                    continue
                mc_result = None
                if core_trades:
                    mc_result = run_monte_carlo(
                        _rules(config),
                        trades=core_trades,
                        assume_iid=True,
                        config=MonteCarloConfig(
                            n_simulations=mc_simulations, seed=seed
                        ),
                    )
                row = {
                    "key": key,
                    "scenario": name,
                    "seed": seed,
                    "config": asdict(config),
                    "strategy": asdict(strategy),
                    "in_sample": {
                        "net_pnl": is_result.net_pnl,
                        "n_trades": is_result.n_trades,
                        "win_rate": is_result.win_rate,
                        "profit_factor": is_result.profit_factor,
                        "expectancy": is_result.expectancy,
                        "max_drawdown_pct": is_result.max_drawdown_pct,
                        "total_commission": is_result.total_commission,
                        "total_slippage_cost": is_result.total_slippage_cost,
                    },
                    "out_of_sample": {
                        "net_pnl": oos_result.net_pnl,
                        "n_trades": oos_result.n_trades,
                        "win_rate": oos_result.win_rate,
                        "profit_factor": oos_result.profit_factor,
                        "expectancy": oos_result.expectancy,
                        "max_drawdown_pct": oos_result.max_drawdown_pct,
                    },
                    "monte_carlo": (
                        {
                            "pass_probability": mc_result.probability_pass,
                            "breach_probability": mc_result.probability_fail,
                            "timeout_probability": mc_result.timeout_probability,
                            "terminal_counts": dict(mc_result.terminal_counts),
                        }
                        if mc_result is not None
                        else None
                    ),
                }
                handle.write(json.dumps(row, default=str) + "\n")
                handle.flush()
                rows.append(row)
                done.add(key)

    summary = {
        "scenarios": len(scenarios),
        "seeds": seeds,
        "train_fraction": train_fraction,
        "n_bars_in_sample": len(is_bars),
        "n_bars_out_of_sample": len(oos_bars),
        "results": rows,
    }
    summary_path = out / "batch_summary.json"
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True, default=str)
    return summary


__all__ = ["run_batch"]

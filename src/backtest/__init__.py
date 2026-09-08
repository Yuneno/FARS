"""FARS backtest MVP: download → strategy → backtest → report → batch/MC.

This package reuses the Core engine (``src.engine``, ``src.monte_carlo``) and
the read-only ProjectX connector. It does not modify RT contracts or the Core
rule engine.
"""

from src.backtest.executor import (
    BacktestConfig,
    BacktestResult,
    ExecutedTrade,
    run_backtest,
)
from src.backtest.history import (
    Bar,
    DownloadResult,
    Manifest,
    download_bars,
    load_bars_csv,
    persist_bars,
    synthetic_bars,
)
from src.backtest.pipeline import (
    chronological_split,
    run_pipeline,
    write_report,
    write_trades_csv,
)
from src.backtest.batch import run_batch
from src.backtest.strategy import BreakoutStrategy, Signal, Strategy

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "ExecutedTrade",
    "run_backtest",
    "Bar",
    "DownloadResult",
    "Manifest",
    "download_bars",
    "load_bars_csv",
    "persist_bars",
    "synthetic_bars",
    "chronological_split",
    "run_pipeline",
    "run_batch",
    "write_report",
    "write_trades_csv",
    "BreakoutStrategy",
    "Signal",
    "Strategy",
]

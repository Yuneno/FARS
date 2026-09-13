"""FARS backtest MVP: download → strategy → backtest → report → batch/MC.

This package reuses the Core engine (``src.engine``, ``src.monte_carlo``) and
the read-only ProjectX connector. It does not modify RT contracts or the Core
rule engine.
"""

from src.backtest.amd_crt import AmdCrtStrategy, amd_crt_config
from src.backtest.batch import run_batch
from src.backtest.emas import EmasStrategy, emas_config
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
from src.backtest.markets import MARKETS, MES, MGC, MNQ, MYM, MarketSpec, get_market_spec
from src.backtest.mnq_csv import load_mnq_csv
from src.backtest.pipeline import (
    chronological_split,
    run_pipeline,
    write_report,
    write_trades_csv,
)
from src.backtest.smc_fvg import SmcFvgStrategy, smc_fvg_config
from src.backtest.strategy import BreakoutStrategy, Signal, Strategy

__all__ = [
    "MARKETS",
    "MES",
    "MGC",
    "MNQ",
    "MYM",
    "AmdCrtStrategy",
    "BacktestConfig",
    "BacktestResult",
    "Bar",
    "BreakoutStrategy",
    "DownloadResult",
    "EmasStrategy",
    "ExecutedTrade",
    "Manifest",
    "MarketSpec",
    "Signal",
    "SmcFvgStrategy",
    "Strategy",
    "amd_crt_config",
    "chronological_split",
    "download_bars",
    "emas_config",
    "get_market_spec",
    "load_bars_csv",
    "load_mnq_csv",
    "persist_bars",
    "run_backtest",
    "run_batch",
    "run_pipeline",
    "smc_fvg_config",
    "synthetic_bars",
    "write_report",
    "write_trades_csv",
]

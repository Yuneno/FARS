"""Terminal entry point for the FARS backtest MVP.

Commands:
  fars-backtest download  --start ... --end ... [--contract-id ID] [--symbol MNQ]
  fars-backtest smoke     [--bars-csv PATH | --synthetic --n-bars N] [--out-dir DIR]
  fars-backtest batch     [--bars-csv PATH | --synthetic --n-bars N] [--seeds 1,2,3]

Credentials are read exclusively from the environment (FARS_PROJECTX_*) or the
``--env-file`` file, and are never printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from src.backtest.amd_crt import AmdCrtStrategy, amd_crt_config
from src.backtest.batch import run_batch
from src.backtest.executor import BacktestConfig, run_backtest
from src.backtest.history import (
    download_bars,
    load_bars_csv,
    persist_bars,
    synthetic_bars,
)
from src.backtest.markets import MARKETS, MNQ, get_market_spec
from src.backtest.mnq_csv import load_mnq_csv
from src.backtest.pipeline import run_pipeline, write_report, write_trades_csv
from src.backtest.strategy import BreakoutStrategy
from src.realtime.config import ProjectXConfigurationError, load_projectx_credentials
from src.realtime.connectors.projectx import ProjectXClient, ProjectXError

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CONFIG = 3
EXIT_PROVIDER = 4
EXIT_INTERNAL = 1


def _aware(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ISO-8601 datetime with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone")
    return parsed


def _float_arg(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    return parsed


def _seeds(value: str) -> list[int]:
    parts = [p.strip() for p in value.split(",") if p.strip()]
    if not parts:
        raise argparse.ArgumentTypeError("at least one seed")
    return [int(p) for p in parts]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fars-backtest")
    sub = parser.add_subparsers(dest="command", required=True)

    dl = sub.add_parser("download", help="download historical MNQ bars (read-only)")
    dl.add_argument("--start", required=True, type=_aware)
    dl.add_argument("--end", required=True, type=_aware)
    dl.add_argument("--contract-id", default=None, help="provider contract id")
    dl.add_argument("--symbol", default="MNQ")
    dl.add_argument("--unit", type=int, default=2, choices=range(1, 7))
    dl.add_argument("--unit-number", type=int, default=1)
    dl.add_argument("--out-dir", default="data/raw")
    dl.add_argument("--env-file", default=".env")

    sm = sub.add_parser("smoke", help="run a single IS/OOS backtest")
    sm.add_argument("--bars-csv", default=None)
    sm.add_argument("--synthetic", action="store_true")
    sm.add_argument("--n-bars", type=int, default=5000)
    sm.add_argument("--out-dir", default="data/processed")
    sm.add_argument("--initial-balance", type=_float_arg, default=50_000.0)
    sm.add_argument("--risk-per-trade", type=_float_arg, default=0.01)
    sm.add_argument("--commission-per-side", type=_float_arg, default=0.62)
    sm.add_argument("--slippage-points", type=_float_arg, default=0.25)
    sm.add_argument("--train-fraction", type=_float_arg, default=0.7)

    bt = sub.add_parser("batch", help="run the scenario grid + Monte Carlo")
    bt.add_argument("--bars-csv", default=None)
    bt.add_argument("--synthetic", action="store_true")
    bt.add_argument("--n-bars", type=int, default=5000)
    bt.add_argument("--out-dir", default="data/processed")
    bt.add_argument("--seeds", type=_seeds, default=[1, 2, 3])
    bt.add_argument("--mc-simulations", type=int, default=1000)

    ac = sub.add_parser("amd-crt", help="run the causal AMD+CRT candidate end-to-end")
    ac.add_argument("--mnq-csv", default=None, help="MNQ OHLCV CSV (time/open/high/low/close)")
    ac.add_argument("--bars-csv", default=None, help="MVP bars CSV (timestamp column)")
    ac.add_argument("--synthetic", action="store_true")
    ac.add_argument("--n-bars", type=int, default=5000)
    ac.add_argument("--out-dir", default="data/processed")
    ac.add_argument("--initial-balance", type=_float_arg, default=50_000.0)
    ac.add_argument("--risk-per-trade", type=_float_arg, default=0.01)
    ac.add_argument("--market", choices=tuple(MARKETS), default=MNQ.symbol)
    ac.add_argument("--friction-pts", type=_float_arg, default=None,
                    help="round-trip friction in points; required for unvalidated market costs")
    ac.add_argument("--train-fraction", type=_float_arg, default=0.7)

    return parser


def _load_bars(args) -> list:
    if args.bars_csv:
        return load_bars_csv(args.bars_csv)
    if getattr(args, "synthetic", False):
        return synthetic_bars(args.n_bars, seed=0)
    return load_bars_csv(args.bars_csv) if args.bars_csv else []


def _download(args) -> int:
    try:
        creds = load_projectx_credentials(args.env_file)
        client = ProjectXClient(creds)
        client.authenticate()
        contract_id = args.contract_id
        contract_name = ""
        if not contract_id:
            contracts = client.search_contracts(args.symbol, live=False)
            if not contracts:
                print(f"no contracts found for {args.symbol!r}", file=sys.stderr)
                return EXIT_PROVIDER
            chosen = next((c for c in contracts if c.active), contracts[0])
            contract_id = chosen.contract_id
            contract_name = chosen.name
        result = download_bars(
            client,
            contract_id,
            start=args.start,
            end=args.end,
            unit=args.unit,
            unit_number=args.unit_number,
            symbol=args.symbol,
        )
        csv_path, manifest_path = persist_bars(result, args.out_dir, symbol=args.symbol)
        manifest = result.manifest
        print(json.dumps({
            "contract_id": contract_id,
            "contract_name": contract_name,
            "bar_count": manifest.bar_count,
            "actual_start": manifest.actual_start.isoformat(),
            "actual_end": manifest.actual_end.isoformat(),
            "duplicates_removed": manifest.duplicates_removed,
            "gaps_detected": manifest.gaps_detected,
            "sha256": manifest.sha256,
            "bars_csv": str(csv_path),
            "manifest_json": str(manifest_path),
        }, indent=2, sort_keys=True))
        return EXIT_OK
    except ProjectXConfigurationError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except ProjectXError as exc:
        print(f"provider error: {exc}", file=sys.stderr)
        return EXIT_PROVIDER


def _smoke(args) -> int:
    bars = _load_bars(args)
    if not bars:
        print("no bars available; pass --bars-csv or --synthetic", file=sys.stderr)
        return EXIT_USAGE
    config = BacktestConfig(
        initial_balance=args.initial_balance,
        risk_per_trade=args.risk_per_trade,
        commission_per_side=args.commission_per_side,
        slippage_points=args.slippage_points,
    )
    strategy = BreakoutStrategy()
    report = run_pipeline(bars, strategy, config, train_fraction=args.train_fraction)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report["_source"] = "synthetic" if getattr(args, "synthetic", False) else (args.bars_csv or "unknown")
    paths = write_report(report, out)
    is_trades = run_backtest(
        bars[: max(1, int(len(bars) * args.train_fraction))], strategy, config
    )
    write_trades_csv(is_trades.trades, out / "in_sample_trades.csv")
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(f"report written to {paths['summary']}", file=sys.stderr)
    return EXIT_OK


def _batch(args) -> int:
    bars = _load_bars(args)
    if not bars:
        print("no bars available; pass --bars-csv or --synthetic", file=sys.stderr)
        return EXIT_USAGE
    scenarios = [
        {"name": "base", "config": {}, "strategy": {}},
        {"name": "high_commission", "config": {"commission_per_side": 1.50}, "strategy": {}},
        {"name": "zero_slippage", "config": {"slippage_points": 0.0}, "strategy": {}},
        {"name": "wide_stop", "config": {}, "strategy": {"stop_atr_mult": 2.0}},
        {"name": "fast_lookback", "config": {}, "strategy": {"lookback": 10}},
    ]
    summary = run_batch(
        bars,
        scenarios=scenarios,
        seeds=args.seeds,
        out_dir=args.out_dir,
        mc_simulations=args.mc_simulations,
    )
    print(json.dumps(
        {"scenarios": summary["scenarios"], "seeds": summary["seeds"],
         "results": summary["results"]}, indent=2, sort_keys=True, default=str))
    return EXIT_OK


def _amd_crt(args) -> int:
    market = get_market_spec(args.market)
    config = amd_crt_config(
        market=market,
        initial_balance=args.initial_balance,
        risk_per_trade=args.risk_per_trade,
        **({"friction_pts": args.friction_pts} if args.friction_pts is not None else {}),
    )
    if args.mnq_csv:
        bars = load_mnq_csv(args.mnq_csv, target_interval_minutes=5)
    elif args.bars_csv:
        bars = load_mnq_csv(args.bars_csv, target_interval_minutes=5)
    elif args.synthetic:
        bars = synthetic_bars(args.n_bars, seed=0, interval_seconds=300)
    else:
        print("no bars; pass --mnq-csv, --bars-csv, or --synthetic", file=sys.stderr)
        return EXIT_USAGE
    if not bars:
        print("no bars loaded", file=sys.stderr)
        return EXIT_USAGE
    strategy = AmdCrtStrategy(market=market)
    report = run_pipeline(bars, strategy, config, train_fraction=args.train_fraction)
    report["_source"] = (
        "mnq_csv" if args.mnq_csv else ("synthetic" if args.synthetic else (args.bars_csv or "unknown"))
    )
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = write_report(report, out)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(f"report written to {paths['summary']}", file=sys.stderr)
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "download":
            return _download(args)
        if args.command == "smoke":
            return _smoke(args)
        if args.command == "batch":
            return _batch(args)
        if args.command == "amd-crt":
            return _amd_crt(args)
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001 - CLI safety boundary
        print(f"internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())

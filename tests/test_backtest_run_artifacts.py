"""Cross-market AMD+CRT artifact and dataset-audit contracts."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.backtest.cli import _parser, main
from src.backtest.executor import BacktestConfig, BacktestResult, ExecutedTrade
from src.backtest.history import Bar
from src.backtest.markets import MES, MNQ
from src.backtest.pipeline import (
    PipelineRun,
    SplitResult,
    execute_pipeline,
    run_pipeline,
    write_trades_csv,
)
from src.backtest.run_manifest import (
    GROSS_ZERO_FRICTION_LABEL,
    audit_ohlcv_csv,
    build_run_manifest,
    validate_ohlcv_audit,
)


def _bar(timestamp: datetime) -> Bar:
    return Bar(timestamp, 100.0, 101.0, 99.0, 100.5, 10.0)


def _trade(identifier: str, entry: datetime, pnl: float) -> ExecutedTrade:
    return ExecutedTrade(
        trade_id=identifier,
        direction="long" if pnl > 0 else "short",
        entry_time=entry,
        exit_time=entry + timedelta(minutes=30),
        entry_price=100.0,
        exit_price=102.0 if pnl > 0 else 98.0,
        stop_price=98.0 if pnl > 0 else 102.0,
        target_price=104.0 if pnl > 0 else 96.0,
        quantity=1,
        gross_pnl=pnl,
        commission=0.0,
        net_pnl=pnl,
        r_result=pnl / 500.0,
        exit_reason="target" if pnl > 0 else "stop",
        stop_risk_dollars=10.0,
        slippage_cost=0.0,
    )


def _result(config: BacktestConfig, trades: tuple[ExecutedTrade, ...]) -> BacktestResult:
    pnl = sum(trade.net_pnl for trade in trades)
    wins = sum(trade.net_pnl for trade in trades if trade.net_pnl > 0)
    losses = -sum(trade.net_pnl for trade in trades if trade.net_pnl < 0)
    return BacktestResult(
        config=config,
        trades=trades,
        simulation=None,
        net_pnl=pnl,
        n_trades=len(trades),
        win_rate=(sum(trade.net_pnl > 0 for trade in trades) / len(trades)) if trades else 0,
        profit_factor=wins / losses if losses else float("inf"),
        expectancy=pnl / len(trades) if trades else 0,
        max_drawdown_pct=0.0,
        total_commission=0.0,
        total_slippage_cost=0.0,
        equity_curve=(config.initial_balance, config.initial_balance + pnl),
    )


def _write_market_csv(path: Path, *, symbol: str = "MES", invalid: bool = False) -> None:
    rows = [
        ("2026-01-02T00:00:00Z", 100, 101, 99, 100),
        ("2026-01-02T00:05:00Z", 100, 101, 99, 100),
        ("2026-01-03T00:00:00Z", 100, 101, 99, 100),
        ("2026-01-03T00:05:00Z", 100, 90 if invalid else 101, 99, 100),
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume", "timeframe", "symbol"])
        for timestamp, open_, high, low, close in rows:
            writer.writerow([timestamp, open_, high, low, close, 10, "M5", symbol])


def test_detailed_trade_exports_are_separate_ordered_and_market_qualified(tmp_path):
    start = datetime(2026, 1, 2, 15, tzinfo=UTC)
    in_sample = (_trade("T000001", start, 10.0), _trade("T000002", start + timedelta(hours=1), -5.0))
    out_of_sample = (_trade("T000001", start + timedelta(days=2), 7.0),)
    is_path = tmp_path / "in_sample_trades.csv"
    oos_path = tmp_path / "out_of_sample_trades.csv"
    write_trades_csv(in_sample, is_path, symbol="MES", strategy_name="AmdCrtStrategy", segment="in_sample")
    write_trades_csv(out_of_sample, oos_path, symbol="MES", strategy_name="AmdCrtStrategy", segment="out_of_sample")

    is_rows = list(csv.DictReader(is_path.open(encoding="utf-8")))
    oos_rows = list(csv.DictReader(oos_path.open(encoding="utf-8")))
    assert [row["entry_time"] for row in is_rows] == sorted(row["entry_time"] for row in is_rows)
    assert [row["entry_time"] for row in oos_rows] == sorted(row["entry_time"] for row in oos_rows)
    assert {row["asset"] for row in is_rows + oos_rows} == {"MES"}
    assert {row["strategy"] for row in is_rows + oos_rows} == {"AmdCrtStrategy"}
    assert {row["segment"] for row in is_rows} == {"in_sample"}
    assert {row["segment"] for row in oos_rows} == {"out_of_sample"}
    signatures = lambda rows: {(row["entry_time"], row["exit_time"], row["direction"]) for row in rows}
    assert signatures(is_rows).isdisjoint(signatures(oos_rows))
    assert "stop_risk_dollars" in is_rows[0] and "slippage_cost" in is_rows[0]


def test_ohlcv_audit_hash_and_invalid_data_detection(tmp_path):
    valid_path = tmp_path / "MES_M5.csv"
    _write_market_csv(valid_path)
    audit = audit_ohlcv_csv(valid_path)
    assert audit.sha256 == hashlib.sha256(valid_path.read_bytes()).hexdigest()
    assert audit.row_count == 4
    assert audit.detected_interval_seconds == 300
    assert audit.timezone_observed == ("UTC",)
    assert audit.symbols_found == ("MES",)
    assert audit.temporal_gaps == 1
    validate_ohlcv_audit(audit, expected_symbol="MES")

    invalid_path = tmp_path / "bad.csv"
    _write_market_csv(invalid_path, invalid=True)
    invalid = audit_ohlcv_csv(invalid_path)
    assert invalid.invalid_ohlc_rows == 1
    with pytest.raises(ValueError, match="invalid_ohlc_rows=1"):
        validate_ohlcv_audit(invalid, expected_symbol="MES")
    with pytest.raises(ValueError, match="symbols_found"):
        validate_ohlcv_audit(audit, expected_symbol="MYM")


def test_manifest_is_stable_except_explicit_execution_time(tmp_path):
    source = tmp_path / "MES_M5.csv"
    _write_market_csv(source)
    audit = audit_ohlcv_csv(source)
    bars = [_bar(datetime(2026, 1, day, tzinfo=UTC)) for day in (1, 2, 3, 4)]
    split = SplitResult(bars[:2], bars[2:], 0.5)
    config = BacktestConfig(dollar_per_point=5.0, tick_size=0.25, commission_per_side=0, slippage_points=0)
    first = _result(config, (_trade("T1", bars[0].timestamp, 10.0),))
    second = _result(config, (_trade("T1", bars[2].timestamp, -5.0),))
    run = PipelineRun({"strategy": "AmdCrtStrategy"}, split, first, second)
    output = tmp_path / "artifact.csv"
    output.write_text("x\n", encoding="utf-8")
    kwargs = {
        "audit": audit,
        "market": MES,
        "config": config,
        "strategy_parameters": {"frozen": True},
        "pipeline_run": run,
        "train_fraction": 0.5,
        "output_paths": {"artifact": output},
        "git_commit": "abc123",
    }
    first_manifest = build_run_manifest(**kwargs, executed_at_utc="2026-01-01T00:00:00+00:00")
    second_manifest = build_run_manifest(**kwargs, executed_at_utc="2026-01-02T00:00:00+00:00")
    assert first_manifest | {"executed_at_utc": None} == second_manifest | {"executed_at_utc": None}
    assert first_manifest["market_spec"] == MES.to_dict()
    assert first_manifest["friction_points"] == 0.0
    assert first_manifest["scenario_label"] == GROSS_ZERO_FRICTION_LABEL


class _NoSignal:
    def evaluate(self, history):
        return None


def test_existing_pipeline_contract_remains_identical():
    bars = [_bar(datetime(2026, 1, day, tzinfo=UTC)) for day in (1, 2, 3, 4)]
    config = BacktestConfig()
    assert run_pipeline(bars, _NoSignal(), config, train_fraction=0.5) == execute_pipeline(
        bars, _NoSignal(), config, train_fraction=0.5
    ).report


def test_write_trades_is_optional_and_mnq_remains_default(tmp_path):
    defaults = _parser().parse_args(["amd-crt"])
    assert defaults.market == MNQ.symbol
    assert defaults.write_trades is False

    source = tmp_path / "MES_M5.csv"
    out = tmp_path / "results"
    _write_market_csv(source)
    assert main([
        "amd-crt", "--market", "MES", "--friction-pts", "0",
        "--bars-csv", str(source), "--out-dir", str(out), "--write-trades",
        "--train-fraction", "0.5",
    ]) == 0
    assert {path.name for path in out.iterdir()} == {
        "backtest_summary.json", "in_sample_trades.csv",
        "out_of_sample_trades.csv", "run_manifest.json",
    }
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["symbol"] == "MES"
    assert manifest["market_spec"] == MES.to_dict()


@pytest.mark.parametrize(
    ("command", "strategy_name"),
    [("smc-fvg", "SmcFvgStrategy"), ("emas", "EmasStrategy")],
)
def test_ported_strategy_cli_writes_trades_and_generic_manifest(
    tmp_path, command, strategy_name
):
    source = tmp_path / "MNQ_M5.csv"
    _write_market_csv(source, symbol="MNQ")
    out = tmp_path / command

    assert main([
        command,
        "--bars-csv",
        str(source),
        "--out-dir",
        str(out),
        "--write-trades",
        "--train-fraction",
        "0.5",
    ]) == 0

    assert {path.name for path in out.iterdir()} == {
        "backtest_summary.json",
        "in_sample_trades.csv",
        "out_of_sample_trades.csv",
        "run_manifest.json",
    }
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "fars-strategy-run-manifest-v1"
    assert manifest["strategy"] == strategy_name
    assert manifest["strategy_parameters"]["timeframe"] == "M5"


def test_market_datasets_are_not_tracked_by_git():
    root = Path(__file__).resolve().parents[1]
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    assert not any(Path(name).name in {"MES_M5.csv", "MYM_M5.csv"} for name in tracked)

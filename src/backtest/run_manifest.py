"""Dataset audit and reproducible manifests for optional AMD+CRT exports."""

from __future__ import annotations

import csv
import hashlib
import math
import platform
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib import metadata
from itertools import pairwise
from pathlib import Path
from typing import Any

from src.backtest.executor import BacktestConfig
from src.backtest.markets import MarketSpec
from src.backtest.pipeline import PipelineRun, config_dict

GROSS_ZERO_FRICTION_LABEL = "GROSS ZERO-FRICTION UPPER BOUND — NOT LIVE-REALISTIC"
_REQUIRED_OHLC = ("open", "high", "low", "close")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class OhlcvAudit:
    path: str
    sha256: str
    size_bytes: int
    row_count: int
    columns_found: tuple[str, ...]
    timestamp_column: str
    first_timestamp: str | None
    last_timestamp: str | None
    timezone_observed: tuple[str, ...]
    detected_interval_seconds: int | None
    duplicate_timestamps: int
    timestamps_out_of_order: int
    invalid_timestamps: int
    invalid_ohlc_rows: int
    exact_duplicate_rows: int
    temporal_gaps: int
    missing_intervals_equivalent: int
    non_multiple_intervals: int
    symbols_found: tuple[str, ...]
    timeframes_found: tuple[str, ...]
    gap_method_limitation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_timestamp(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def _timezone_label(timestamp: datetime) -> str:
    offset = timestamp.utcoffset()
    if offset is None:
        return "naive"
    total_minutes = int(offset.total_seconds() // 60)
    if total_minutes == 0:
        return "UTC"
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    return f"UTC{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def audit_ohlcv_csv(path: str | Path) -> OhlcvAudit:
    """Profile one OHLCV CSV without modifying or copying it."""
    source = Path(path).expanduser().resolve()
    before = source.stat()
    digest = sha256_file(source)

    timestamps: list[datetime] = []
    seen_timestamps: set[datetime] = set()
    duplicate_timestamps = 0
    out_of_order = 0
    invalid_timestamps = 0
    invalid_ohlc = 0
    exact_duplicates = 0
    symbols: set[str] = set()
    timeframes: set[str] = set()
    zones: set[str] = set()
    previous: datetime | None = None
    previous_values: tuple[str, ...] | None = None

    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = tuple(reader.fieldnames or ())
        fields = set(columns)
        has_time = "time" in fields
        has_timestamp = "timestamp" in fields
        if has_time == has_timestamp:
            raise ValueError(
                f"{source} must contain exactly one of 'time' or 'timestamp'"
            )
        missing = [name for name in _REQUIRED_OHLC if name not in fields]
        if missing:
            raise ValueError(f"{source} is missing required columns: {', '.join(missing)}")
        timestamp_column = "time" if has_time else "timestamp"

        for row in reader:
            values = tuple(row.get(name, "") or "" for name in columns)
            if values == previous_values:
                exact_duplicates += 1
            previous_values = values

            try:
                timestamp = _parse_timestamp(row.get(timestamp_column, "") or "")
            except (TypeError, ValueError):
                invalid_timestamps += 1
                timestamp = None
            if timestamp is not None:
                if timestamp in seen_timestamps:
                    duplicate_timestamps += 1
                else:
                    seen_timestamps.add(timestamp)
                if previous is not None and timestamp < previous:
                    out_of_order += 1
                previous = timestamp
                timestamps.append(timestamp)
                zones.add(_timezone_label(timestamp))

            try:
                open_, high, low, close = (
                    float(row.get(name, "") or "") for name in _REQUIRED_OHLC
                )
                if not all(math.isfinite(value) for value in (open_, high, low, close)):
                    raise ValueError("non-finite OHLC")
                if high < max(open_, low, close) or low > min(open_, high, close):
                    raise ValueError("inconsistent OHLC")
            except (TypeError, ValueError):
                invalid_ohlc += 1

            symbol = (row.get("symbol") or "").strip()
            timeframe = (row.get("timeframe") or "").strip()
            if symbol:
                symbols.add(symbol)
            if timeframe:
                timeframes.add(timeframe)

    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"dataset changed while it was being audited: {source}")

    positive_deltas = [
        round((current - prior).total_seconds())
        for prior, current in pairwise(timestamps)
        if current > prior
    ]
    counts = Counter(positive_deltas)
    interval: int | None = None
    if counts:
        highest = max(counts.values())
        candidates = sorted(delta for delta, count in counts.items() if count == highest)
        if len(candidates) == 1:
            interval = candidates[0]
    gaps = 0
    missing_equivalent = 0
    non_multiples = 0
    if interval:
        for delta in positive_deltas:
            if delta > interval:
                gaps += 1
                if delta % interval == 0:
                    missing_equivalent += delta // interval - 1
                else:
                    non_multiples += 1

    limitation = (
        "Gaps are timestamp deltas greater than the unique modal interval. "
        "No exchange calendar is applied, so scheduled closures and holidays "
        "are included and missing_intervals_equivalent is not a feed-loss count."
    )
    return OhlcvAudit(
        path=str(source),
        sha256=digest,
        size_bytes=before.st_size,
        row_count=len(timestamps) + invalid_timestamps,
        columns_found=columns,
        timestamp_column=timestamp_column,
        first_timestamp=min(timestamps).isoformat() if timestamps else None,
        last_timestamp=max(timestamps).isoformat() if timestamps else None,
        timezone_observed=tuple(sorted(zones)),
        detected_interval_seconds=interval,
        duplicate_timestamps=duplicate_timestamps,
        timestamps_out_of_order=out_of_order,
        invalid_timestamps=invalid_timestamps,
        invalid_ohlc_rows=invalid_ohlc,
        exact_duplicate_rows=exact_duplicates,
        temporal_gaps=gaps,
        missing_intervals_equivalent=missing_equivalent,
        non_multiple_intervals=non_multiples,
        symbols_found=tuple(sorted(symbols)),
        timeframes_found=tuple(sorted(timeframes)),
        gap_method_limitation=limitation,
    )


def validate_ohlcv_audit(
    audit: OhlcvAudit,
    *,
    expected_symbol: str,
    expected_interval_seconds: int = 300,
) -> None:
    """Reject structural ambiguity before an evidence-producing run."""
    failures: list[str] = []
    if audit.symbols_found != (expected_symbol,):
        failures.append(
            f"symbols_found={audit.symbols_found!r}, expected only {expected_symbol!r}"
        )
    for field in (
        "invalid_timestamps",
        "duplicate_timestamps",
        "timestamps_out_of_order",
        "invalid_ohlc_rows",
        "non_multiple_intervals",
    ):
        value = getattr(audit, field)
        if value:
            failures.append(f"{field}={value}")
    if audit.detected_interval_seconds != expected_interval_seconds:
        failures.append(
            f"detected_interval_seconds={audit.detected_interval_seconds!r}, "
            f"expected {expected_interval_seconds}"
        )
    if failures:
        raise ValueError("dataset audit failed: " + "; ".join(failures))


def _dependency_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for distribution in ("numpy", "scipy", "arch", "pandas"):
        try:
            versions[distribution] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            versions[distribution] = None
    return versions


def current_git_commit(repo: str | Path | None = None) -> str:
    root = Path(repo) if repo is not None else Path(__file__).resolve().parents[2]
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def output_fingerprint(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).resolve()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def build_run_manifest(
    *,
    audit: OhlcvAudit,
    market: MarketSpec,
    config: BacktestConfig,
    strategy_parameters: dict[str, Any],
    pipeline_run: PipelineRun,
    train_fraction: float,
    output_paths: dict[str, str | Path],
    strategy_name: str = "AmdCrtStrategy",
    git_commit: str | None = None,
    executed_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build a stable manifest; only ``executed_at_utc`` varies by default."""
    friction_points = (
        (2.0 * config.commission_per_side / config.dollar_per_point)
        + (2.0 * config.slippage_points)
    )
    split = pipeline_run.split
    limitations = [
        audit.gap_method_limitation,
        (
            "The CSV identifies a continuous root symbol but has no underlying "
            "contract identifier or rollover marker. Exact transition dates, "
            "roll rule, and price adjustment cannot be independently determined."
        ),
        "Zero friction is a theoretical gross upper bound, not a live cost model.",
    ]
    manifest = {
        "schema_version": (
            "fars-amd-crt-run-manifest-v1"
            if strategy_name == "AmdCrtStrategy"
            else "fars-strategy-run-manifest-v1"
        ),
        "scenario_label": (
            GROSS_ZERO_FRICTION_LABEL if friction_points == 0.0 else None
        ),
        "symbol": market.symbol,
        "dataset": audit.to_dict(),
        "market_spec": market.to_dict(),
        "backtest_config": config_dict(config),
        "friction_points": friction_points,
        "train_fraction": train_fraction,
        "split": {
            "in_sample_bars": len(split.in_sample),
            "out_of_sample_bars": len(split.out_of_sample),
            "in_sample_first": split.in_sample[0].timestamp.isoformat(),
            "in_sample_last": split.in_sample[-1].timestamp.isoformat(),
            "out_of_sample_first": split.out_of_sample[0].timestamp.isoformat(),
            "out_of_sample_last": split.out_of_sample[-1].timestamp.isoformat(),
        },
        "git_commit": git_commit or current_git_commit(),
        "executed_at_utc": executed_at_utc or datetime.now(UTC).isoformat(),
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "dependencies": _dependency_versions(),
        },
        "outputs": {
            name: output_fingerprint(path) for name, path in sorted(output_paths.items())
        },
        "limitations": limitations,
    }
    if strategy_name == "AmdCrtStrategy":
        manifest["amd_crt_parameters"] = strategy_parameters
    else:
        manifest["strategy"] = strategy_name
        manifest["strategy_parameters"] = strategy_parameters
    return manifest


def write_manifest(manifest: dict[str, Any], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        import json

        json.dump(manifest, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")


__all__ = [
    "GROSS_ZERO_FRICTION_LABEL",
    "OhlcvAudit",
    "audit_ohlcv_csv",
    "build_run_manifest",
    "current_git_commit",
    "output_fingerprint",
    "sha256_file",
    "validate_ohlcv_audit",
    "write_manifest",
]

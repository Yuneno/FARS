"""Historical MNQ bar download (FASE A) for the FARS backtest MVP.

Reuses the existing read-only ProjectX/TopstepX REST client
(:class:`src.realtime.connectors.projectx.ProjectXClient`). Adds the pieces the
connector intentionally does not do:

* time-range pagination for ranges larger than ``MAX_BAR_LIMIT``;
* deterministic de-duplication across overlapping/adjacent windows;
* chronological ordering, gap detection, and OHLCV validation;
* persistence into the Phase 8A ``data/raw`` folder plus a reproducible manifest.

Credentials come exclusively from the environment (``FARS_PROJECTX_*``), never
from files committed to the repository, and are never logged.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import time as _time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Protocol

from src.realtime.connectors.projectx import (
    MAX_BAR_LIMIT,
    ProjectXBar,
    ProjectXError,
    ProjectXRateLimitError,
)


class BarsClient(Protocol):
    """Anything that can fetch historical bars (the real client or a test stub)."""

    def retrieve_bars(
        self,
        contract_id: str,
        *,
        start: datetime,
        end: datetime,
        unit: int,
        unit_number: int,
        limit: int,
        include_partial_bar: bool,
        live: bool,
    ) -> tuple[ProjectXBar, ...]: ...

# unit -> seconds-per-bar (unit 6 = month is handled separately; MNQ MVP uses
# unit 2 = minute). Bar unit 2 (minute) with unit_number 1 = 60s.
_BAR_UNIT_SECONDS = {1: 1, 2: 60, 3: 3600, 4: 86400, 5: 604800}


@dataclass(frozen=True)
class Bar:
    """Float OHLCV bar used by the backtest engine (not the RT canonical Bar)."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Manifest:
    """Reproducible description of a downloaded bar dataset."""

    symbol: str
    contract_id: str
    requested_start: datetime
    requested_end: datetime
    actual_start: datetime
    actual_end: datetime
    granularity: str
    bar_count: int
    duplicates_removed: int
    gaps_detected: int
    downloaded_at: datetime
    sha256: str


@dataclass(frozen=True)
class DownloadResult:
    bars: tuple[Bar, ...]
    manifest: Manifest


def _expected_step_seconds(unit: int, unit_number: int) -> int:
    if unit == 6:
        return 0  # monthly bars have no fixed intra-step; gap detection skipped
    return _BAR_UNIT_SECONDS[unit] * unit_number


def to_float_bar(bar: ProjectXBar) -> Bar:
    """Convert a provider Decimal bar to a float bar for backtesting."""
    return Bar(
        timestamp=bar.timestamp,
        open=float(bar.open),
        high=float(bar.high),
        low=float(bar.low),
        close=float(bar.close),
        volume=float(bar.volume),
    )


def _validate_bar(bar: Bar, index: int) -> None:
    """Reject invalid OHLC (non-finite, high<low, high<open/close, low>open/close)."""
    for name in ("open", "high", "low", "close", "volume"):
        value = getattr(bar, name)
        if not math.isfinite(value):
            raise ValueError(f"bar {index} has non-finite {name}={value!r}")
    if bar.high < bar.low:
        raise ValueError(f"bar {index} has high {bar.high} < low {bar.low}")
    if bar.high < bar.open or bar.high < bar.close:
        raise ValueError(f"bar {index} has high below open/close")
    if bar.low > bar.open or bar.low > bar.close:
        raise ValueError(f"bar {index} has low above open/close")


def _dedupe_and_order(bars: list[Bar]) -> tuple[list[Bar], int]:
    """Sort chronologically and drop duplicate timestamps deterministically."""
    ordered = sorted(bars, key=lambda b: b.timestamp)
    seen: set[datetime] = set()
    unique: list[Bar] = []
    duplicates = 0
    for bar in ordered:
        if bar.timestamp in seen:
            duplicates += 1
            continue
        seen.add(bar.timestamp)
        unique.append(bar)
    return unique, duplicates


def _detect_gaps(bars: list[Bar], step_seconds: int) -> int:
    if step_seconds <= 0 or len(bars) < 2:
        return 0
    gaps = 0
    for prev, curr in pairwise(bars):
        delta = (curr.timestamp - prev.timestamp).total_seconds()
        if delta > step_seconds * 1.5:  # allow one half-step tolerance
            gaps += 1
    return gaps


def download_bars(
    client: BarsClient,
    contract_id: str,
    *,
    start: datetime,
    end: datetime,
    unit: int = 2,
    unit_number: int = 1,
    limit: int = MAX_BAR_LIMIT,
    live: bool = False,
    include_partial_bar: bool = False,
    symbol: str = "MNQ",
    retries: int = 3,
) -> DownloadResult:
    """Download a full chronological bar range with pagination and de-dup.

    The provider may return fewer bars than requested (pagination) or may return
    newest-first rows. Requests are advanced by the last received timestamp so
    overlapping windows collapse to duplicates deterministically rather than
    corrupting the sequence.
    """
    if start >= end:
        raise ValueError("start must be earlier than end")
    limit = min(limit, MAX_BAR_LIMIT)

    collected: list[Bar] = []
    cursor = start
    last_seen: datetime | None = None
    while cursor < end:
        attempt = 0
        chunk: tuple[ProjectXBar, ...] | None = None
        while attempt <= retries:
            try:
                chunk = client.retrieve_bars(
                    contract_id,
                    start=cursor,
                    end=end,
                    unit=unit,
                    unit_number=unit_number,
                    limit=limit,
                    include_partial_bar=include_partial_bar,
                    live=live,
                )
                break
            except ProjectXRateLimitError:
                attempt += 1
                if attempt > retries:
                    raise
                _time.sleep(2 ** attempt)
            except ProjectXError:
                raise

        if not chunk:
            break

        for bar in chunk:
            collected.append(to_float_bar(bar))

        newest = chunk[-1].timestamp
        if last_seen is not None and newest <= last_seen:
            # Provider did not advance; avoid an infinite loop on stale windows.
            break
        last_seen = newest
        step_seconds = _expected_step_seconds(unit, unit_number)
        if step_seconds <= 0:
            break
        cursor = newest + timedelta(seconds=step_seconds)
        if len(chunk) < limit:
            break  # provider returned fewer than the limit: end of range

    ordered, duplicates = _dedupe_and_order(collected)
    for index, bar in enumerate(ordered):
        _validate_bar(bar, index)
    gaps = _detect_gaps(ordered, _expected_step_seconds(unit, unit_number))
    if not ordered:
        return DownloadResult(
            (),
            Manifest(
                symbol=symbol,
                contract_id=contract_id,
                requested_start=start,
                requested_end=end,
                actual_start=start,
                actual_end=end,
                granularity=f"{unit_number}{_unit_suffix(unit)}",
                bar_count=0,
                duplicates_removed=duplicates,
                gaps_detected=0,
                downloaded_at=datetime.now(UTC),
                sha256="",
            ),
        )
    manifest = Manifest(
        symbol=symbol,
        contract_id=contract_id,
        requested_start=start,
        requested_end=end,
        actual_start=ordered[0].timestamp,
        actual_end=ordered[-1].timestamp,
        granularity=f"{unit_number}{_unit_suffix(unit)}",
        bar_count=len(ordered),
        duplicates_removed=duplicates,
        gaps_detected=gaps,
        downloaded_at=datetime.now(UTC),
        sha256="",
    )
    return DownloadResult(tuple(ordered), manifest)


def _unit_suffix(unit: int) -> str:
    return {1: "s", 2: "m", 3: "h", 4: "d", 5: "w", 6: "mo"}.get(unit, "?")


def bars_sha256(bars: tuple[Bar, ...]) -> str:
    digest = hashlib.sha256()
    for bar in bars:
        digest.update(
            f"{bar.timestamp.isoformat()},{bar.open},{bar.high},{bar.low},"
            f"{bar.close},{bar.volume}\n".encode()
        )
    return digest.hexdigest()


def persist_bars(
    result: DownloadResult, out_dir: str | Path, *, symbol: str = "MNQ"
) -> tuple[Path, Path]:
    """Write bars CSV + manifest JSON into data/raw; return their paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = result.manifest
    digest = bars_sha256(result.bars)
    manifest = Manifest(
        symbol=manifest.symbol,
        contract_id=manifest.contract_id,
        requested_start=manifest.requested_start,
        requested_end=manifest.requested_end,
        actual_start=manifest.actual_start,
        actual_end=manifest.actual_end,
        granularity=manifest.granularity,
        bar_count=manifest.bar_count,
        duplicates_removed=manifest.duplicates_removed,
        gaps_detected=manifest.gaps_detected,
        downloaded_at=manifest.downloaded_at,
        sha256=digest,
    )
    csv_path = out / f"{symbol}_{manifest.granularity}_bars.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for bar in result.bars:
            writer.writerow(
                [
                    bar.timestamp.isoformat(),
                    repr(bar.open),
                    repr(bar.high),
                    repr(bar.low),
                    repr(bar.close),
                    repr(bar.volume),
                ]
            )
    json_path = out / f"{symbol}_{manifest.granularity}_manifest.json"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "symbol": manifest.symbol,
                "contract_id": manifest.contract_id,
                "requested_start": manifest.requested_start.isoformat(),
                "requested_end": manifest.requested_end.isoformat(),
                "actual_start": manifest.actual_start.isoformat(),
                "actual_end": manifest.actual_end.isoformat(),
                "granularity": manifest.granularity,
                "bar_count": manifest.bar_count,
                "duplicates_removed": manifest.duplicates_removed,
                "gaps_detected": manifest.gaps_detected,
                "downloaded_at": manifest.downloaded_at.isoformat(),
                "sha256": manifest.sha256,
            },
            handle,
            indent=2,
            sort_keys=True,
        )
    return csv_path, json_path


def load_bars_csv(path: str | Path) -> list[Bar]:
    """Load a bars CSV written by :func:`persist_bars` back into Bar objects."""
    rows: list[Bar] = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            ts = row["timestamp"]
            if ts.endswith("Z"):
                ts = ts[:-1] + "+00:00"
            rows.append(
                Bar(
                    timestamp=datetime.fromisoformat(ts),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
    return rows


def synthetic_bars(
    n: int,
    *,
    seed: int = 0,
    start: datetime | None = None,
    interval_seconds: int = 60,
    start_price: float = 20_000.0,
    tick: float = 0.25,
) -> list[Bar]:
    """Deterministic synthetic MNQ bars for the smoke test (NOT real market data).

    A geometric random walk produces a plausible but clearly-labeled OHLCV
    series so the pipeline can run end-to-end without credentials. This must
    never be presented as a real or validated dataset.
    """
    import random

    rng = random.Random(seed)
    start = start or datetime(2026, 8, 1, tzinfo=UTC)
    bars: list[Bar] = []
    price = start_price
    ts = start
    for _ in range(n):
        ret = rng.gauss(0.0, tick * 3.0)
        o = price
        c = price * (1.0 + ret / price)
        hi = max(o, c) + abs(rng.gauss(0.0, tick))
        lo = min(o, c) - abs(rng.gauss(0.0, tick))
        vol = float(rng.randint(1, 500))
        bars.append(Bar(timestamp=ts, open=o, high=hi, low=lo, close=c, volume=vol))
        price = c
        ts = ts + timedelta(seconds=interval_seconds)
    return bars


__all__ = [
    "Bar",
    "DownloadResult",
    "Manifest",
    "bars_sha256",
    "download_bars",
    "load_bars_csv",
    "persist_bars",
    "synthetic_bars",
    "to_float_bar",
]

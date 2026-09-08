"""Load an MNQ OHLCV CSV into backtest :class:`~src.backtest.history.Bar` objects.

The MNQ project's native CSVs use ``time`` = bar OPEN time, UTC with an offset
(e.g. ``2026-05-04 16:07:00+00:00``) and columns ``time,open,high,low,close``
(no volume). The MVP's ``persist_bars`` output uses ``timestamp`` instead. This
loader accepts EITHER ``time`` or ``timestamp`` (rejecting a file that carries
both), and:

* validates that timestamps are strictly increasing (rejects out-of-order and
  duplicate rows);
* detects the real bar interval from the modal consecutive gap and validates
  every timestamp delta against it (or validates an explicit source interval);
* aggregates to the target interval (default 5 minutes) using only COMPLETE
  buckets: a bucket is complete only when it contains exactly the expected
  source timestamps, each once — a missing, duplicate, or unaligned timestamp
  drops the whole bucket rather than fabricating an OHLC.

MNQ is read-only: this module only reads a CSV file and never writes back.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

from src.backtest.history import Bar


def _parse_ts(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, tolerating a trailing ``Z`` for UTC."""
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(
            f"naive timestamp {value!r}; CSV timestamps must be timezone-aware"
        )
    return timestamp


def _source_interval_seconds(
    timestamps: list[datetime], source_interval_minutes: int | None
) -> int:
    """Infer a modal interval or validate the caller's explicit interval."""
    if source_interval_minutes is not None:
        if source_interval_minutes <= 0:
            raise ValueError("source_interval_minutes must be > 0")
        source_seconds = source_interval_minutes * 60
    else:
        if len(timestamps) < 2:
            raise ValueError("cannot detect the bar interval (need >=2 rows)")
        deltas = [
            round((current - previous).total_seconds())
            for previous, current in pairwise(timestamps)
        ]
        counts = Counter(deltas)
        most_common = max(counts.values())
        candidates = [delta for delta, count in counts.items() if count == most_common]
        if len(candidates) != 1:
            raise ValueError(
                "ambiguous source interval; pass source_interval_minutes explicitly"
            )
        source_seconds = candidates[0]

    for previous, current in pairwise(timestamps):
        delta = (current - previous).total_seconds()
        if delta <= 0:
            raise ValueError(
                "timestamps must be strictly increasing "
                f"(found out-of-order or duplicate at {current.isoformat()})"
            )
        if delta % source_seconds != 0:
            raise ValueError(
                f"timestamp delta {delta:g}s is incompatible with the "
                f"{source_seconds:g}s source interval at {current.isoformat()}"
            )
    return source_seconds


def _aggregate(
    rows: list[tuple[datetime, float, float, float, float, float]],
    source_seconds: int,
    target_seconds: int,
) -> list[Bar]:
    """Aggregate finer bars into target bars using only complete buckets.

    A bucket is complete only when it contains exactly the expected source
    timestamps (one per source interval), each exactly once. Missing, duplicate,
    or unaligned timestamps drop the bucket.
    """
    if target_seconds % source_seconds != 0:
        raise ValueError(
            f"target interval ({target_seconds}s) must be a multiple of the "
            f"source interval ({source_seconds}s)"
        )
    factor = target_seconds // source_seconds
    buckets: dict[int, list[tuple]] = defaultdict(list)
    for row in rows:
        buckets[int(row[0].timestamp()) // target_seconds].append(row)

    bars: list[Bar] = []
    for key in sorted(buckets):
        group = sorted(buckets[key], key=lambda r: r[0])
        bucket_start = datetime.fromtimestamp(key * target_seconds, tz=UTC)
        expected = [
            bucket_start + timedelta(seconds=source_seconds * j) for j in range(factor)
        ]
        if [r[0] for r in group] != expected:
            # incomplete bucket (leading/trailing edge, gap, or duplicate)
            continue
        first, last = group[0], group[-1]
        bars.append(
            Bar(
                timestamp=first[0],  # bar OPEN time = first source bar's time
                open=first[1],
                high=max(r[2] for r in group),
                low=min(r[3] for r in group),
                close=last[4],
                volume=sum(r[5] for r in group),
            )
        )
    return bars


def load_mnq_csv(
    path: str | Path,
    *,
    target_interval_minutes: int = 5,
    source_interval_minutes: int | None = None,
) -> list[Bar]:
    """Load an MNQ OHLCV CSV into backtest Bars, aggregating to the target interval.

    Accepts ``time`` or ``timestamp`` as the timestamp column (rejecting both).
    ``source_interval_minutes`` selects and validates an explicit interval. If
    omitted, a unique modal timestamp delta is inferred and all other gaps must
    be exact multiples of it. ``volume`` is optional and defaults to ``0.0``.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        has_time = "time" in fields
        has_timestamp = "timestamp" in fields
        if has_time and has_timestamp:
            raise ValueError(
                f"{path} has both 'time' and 'timestamp' columns; ambiguous source"
            )
        if not has_time and not has_timestamp:
            raise ValueError(f"{path} is missing a 'time'/'timestamp' column")
        ts_col = "time" if has_time else "timestamp"
        has_volume = "volume" in fields

        target_seconds = target_interval_minutes * 60
        if target_interval_minutes <= 0:
            raise ValueError("target_interval_minutes must be > 0")

        rows: list[tuple[datetime, float, float, float, float, float]] = []
        prev: datetime | None = None
        for row in reader:
            ts = _parse_ts(row[ts_col])
            if prev is not None and ts <= prev:
                raise ValueError(
                    "timestamps must be strictly increasing "
                    f"(found out-of-order or duplicate at {ts.isoformat()})"
                )
            prev = ts
            rows.append(
                (
                    ts,
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row["volume"]) if has_volume else 0.0,
                )
            )

    source_seconds = _source_interval_seconds(
        [row[0] for row in rows], source_interval_minutes
    )
    if target_seconds < source_seconds:
        raise ValueError(
            f"target interval ({target_interval_minutes}m) is finer than the "
            f"source interval ({source_seconds // 60}m); cannot upscale"
        )

    if source_seconds == target_seconds:
        return [
            Bar(timestamp=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
            for r in rows
        ]
    return _aggregate(rows, source_seconds, target_seconds)


__all__ = ["load_mnq_csv"]

"""RT-3 event recorder.

Persists canonical events as append-only JSONL so a session can be
reconstructed later. Parquet/DuckDB is the preferred later backend; it is
not wired here because those packages are not in FARS dependencies.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path

from src.realtime.events import (
    AccountSnapshot,
    Bar,
    ExecutionReport,
    MarketTick,
    MarketTrade,
    OrderIntent,
    Quote,
    RiskDecision,
    Signal,
    SystemEvent,
)

SCHEMA_VERSION = "fars-rt3-jsonl-v1"

_EVENT_TYPES = {
    "MarketTick": MarketTick,
    "Quote": Quote,
    "Bar": Bar,
    "MarketTrade": MarketTrade,
    "AccountSnapshot": AccountSnapshot,
    "Signal": Signal,
    "RiskDecision": RiskDecision,
    "OrderIntent": OrderIntent,
    "ExecutionReport": ExecutionReport,
    "SystemEvent": SystemEvent,
}

_DATETIME_FIELDS = ("timestamp", "broker_timestamp", "last_sync")


class RecorderError(ValueError):
    """Malformed record, non-canonical event, or unreadable journal."""


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, str):
        try:
            ts = datetime.fromisoformat(value)
        except ValueError as exc:
            raise RecorderError(f"invalid timestamp {value!r}") from exc
    else:
        raise RecorderError(f"timestamp must be ISO string, got {type(value).__name__}")
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise RecorderError("timestamp must be timezone-aware")
    return ts


def event_to_record(event: object) -> dict:
    if type(event) not in _EVENT_TYPES.values() or not is_dataclass(event):
        raise RecorderError(f"recorder accepts canonical events only, got {type(event).__name__}")
    payload = asdict(event)
    for field in _DATETIME_FIELDS:
        if field in payload and payload[field] is not None:
            payload[field] = payload[field].isoformat()
    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": type(event).__name__,
        "payload": payload,
    }


def record_to_event(record: object) -> object:
    if not isinstance(record, dict):
        raise RecorderError("record must be a JSON object")
    if record.get("schema_version") != SCHEMA_VERSION:
        raise RecorderError(f"unsupported schema_version {record.get('schema_version')!r}")
    event_type = record.get("event_type")
    cls = _EVENT_TYPES.get(event_type) if isinstance(event_type, str) else None
    if cls is None:
        raise RecorderError(f"unsupported event_type {event_type!r}")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        raise RecorderError("payload must be a JSON object")
    data = dict(payload)
    for field in _DATETIME_FIELDS:
        if field in data:
            data[field] = _parse_datetime(data[field])
    try:
        return cls(**data)
    except (TypeError, ValueError) as exc:
        raise RecorderError(f"cannot reconstruct {event_type}: {exc}") from exc


class FileEventRecorder:
    """Append-only JSONL journal of canonical events."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: object) -> None:
        line = json.dumps(event_to_record(event), separators=(",", ":"), ensure_ascii=False)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())


def reconstruct_events(path: str | Path) -> tuple:
    journal = Path(path)
    if not journal.exists():
        raise RecorderError(f"journal does not exist: {journal}")
    events = []
    with journal.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            text = raw.strip()
            if text == "":
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RecorderError(f"malformed JSON at line {line_no}") from exc
            events.append(record_to_event(record))
    return tuple(events)

"""Strategy-agnostic CSV ingestion and audit for FARS 1.2 Phase 8A.

The loader deliberately separates data availability from analysis validity.
Valid rows remain inspectable when another row is rejected, but capability
statuses prevent callers from silently treating a selectively reduced sample
as analysis-ready.
"""

from __future__ import annotations

import csv
import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.types import Trade


Severity = Literal["error", "warning", "info"]

CANONICAL_INPUT_FIELDS = frozenset(
    {
        "r_result",
        "trade_id",
        "timestamp",
        "asset",
        "direction",
        "entry_price",
        "stop_price",
        "exit_price",
        "strategy",
    }
)
_OPTIONAL_NUMERIC_FIELDS = ("entry_price", "stop_price", "exit_price")
_CAPABILITIES = ("core_metrics", "temporal_analysis", "daily_rule_simulation")
_SCHEMA_VERSION = "1.2-phase8a"


class TradeDataError(ValueError):
    """Raised when a CSV or mapping is structurally impossible to interpret."""


def validate_csv_delimiter(value: object) -> str:
    """Return a delimiter that is safe for FARS' fixed CSV dialect.

    ``csv.reader`` changed its constructor validation across supported Python
    versions: Python 3.11/3.12 accept quote, carriage-return, and newline as
    delimiters, while Python 3.13 rejects them.  FARS therefore enforces the
    dialect contract explicitly instead of inheriting version-specific
    behavior from the standard library.
    """
    if not isinstance(value, str) or len(value) != 1:
        raise TradeDataError("delimiter must be exactly one character")
    if value in {'"', "\r", "\n"}:
        raise TradeDataError(
            "delimiter must not conflict with the CSV quote character or line endings"
        )

    # Retain the standard-library check for any additional dialect constraint
    # introduced by the active interpreter, while keeping the explicit checks
    # above authoritative across every supported version.
    try:
        csv.reader([], delimiter=value)
    except (TypeError, ValueError) as exc:
        raise TradeDataError(f"invalid CSV delimiter {value!r}: {exc}") from exc
    return value


@dataclass(frozen=True)
class AuditIssue:
    """One stable, machine-readable ingestion finding."""

    severity: Severity
    code: str
    message: str
    row_number: int | None = None
    field: str | None = None

    def __post_init__(self) -> None:
        if self.severity not in ("error", "warning", "info"):
            raise ValueError(f"unsupported audit severity: {self.severity!r}")
        if not self.code:
            raise ValueError("audit issue code must be non-empty")


@dataclass(frozen=True)
class CapabilityStatus:
    """Whether one analysis capability is safe for this dataset."""

    available: bool
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(self.reasons))
        if self.available and self.reasons:
            raise ValueError("an available capability cannot have unavailable reasons")
        if not self.available and not self.reasons:
            raise ValueError("an unavailable capability must explain why")


@dataclass(frozen=True)
class TradeAuditReport:
    """Immutable summary of structural and row-level validation."""

    total_rows: int
    accepted_rows: int
    rejected_rows: int
    issues: tuple[AuditIssue, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues", tuple(self.issues))
        for name in ("total_rows", "accepted_rows", "rejected_rows"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.accepted_rows + self.rejected_rows != self.total_rows:
            raise ValueError("accepted_rows + rejected_rows must equal total_rows")

    @property
    def error_count(self) -> int:
        return sum(issue.severity == "error" for issue in self.issues)

    @property
    def warning_count(self) -> int:
        return sum(issue.severity == "warning" for issue in self.issues)

    @property
    def has_errors(self) -> bool:
        return self.error_count > 0


@dataclass(frozen=True)
class IngestionProvenance:
    """Immutable information needed to reproduce one CSV adaptation."""

    schema_version: str
    source_path: str
    source_sha256: str
    source_size_bytes: int
    resolved_mapping: Mapping[str, str]
    outcomes_finalized: bool
    analysis_timezone: str | None
    delimiter: str
    encoding: str

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {self.schema_version!r}")
        if len(self.source_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.source_sha256
        ):
            raise ValueError("source_sha256 must be a lowercase SHA-256 hex digest")
        if (
            not isinstance(self.source_size_bytes, int)
            or isinstance(self.source_size_bytes, bool)
            or self.source_size_bytes < 0
        ):
            raise ValueError("source_size_bytes must be a non-negative integer")
        if self.outcomes_finalized is not True:
            raise ValueError("provenance requires explicit finalized outcomes")
        object.__setattr__(
            self,
            "resolved_mapping",
            MappingProxyType(dict(self.resolved_mapping)),
        )


@dataclass(frozen=True)
class CanonicalTradeDataset:
    """Accepted canonical trades plus audit and capability information."""

    trades: tuple[Trade, ...]
    audit: TradeAuditReport
    capabilities: Mapping[str, CapabilityStatus]
    provenance: IngestionProvenance
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "trades", tuple(self.trades))
        normalized = dict(self.capabilities)
        if set(normalized) != set(_CAPABILITIES):
            raise ValueError(f"capabilities must be exactly {_CAPABILITIES!r}")
        object.__setattr__(self, "capabilities", MappingProxyType(normalized))
        if len(self.trades) != self.audit.accepted_rows:
            raise ValueError("trade count must equal audit.accepted_rows")

    def require_capability(self, name: str) -> None:
        """Raise a clear error unless ``name`` is available."""
        try:
            status = self.capabilities[name]
        except KeyError as exc:
            raise ValueError(f"unknown capability {name!r}") from exc
        if not status.available:
            raise TradeDataError(
                f"capability {name!r} is unavailable: " + "; ".join(status.reasons)
            )


def _validate_mapping(
    headers: list[str], column_mapping: Mapping[str, str] | None
) -> dict[str, str]:
    """Return a source-column -> canonical-field mapping."""
    if column_mapping is not None and not isinstance(column_mapping, Mapping):
        raise TradeDataError("column_mapping must be a mapping or None")
    supplied = {} if column_mapping is None else dict(column_mapping)
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in supplied.items()
    ):
        raise TradeDataError("column_mapping keys and values must be strings")

    missing_sources = sorted(set(supplied) - set(headers))
    if missing_sources:
        raise TradeDataError(f"mapped source columns are missing: {missing_sources!r}")

    unsupported = sorted(set(supplied.values()) - CANONICAL_INPUT_FIELDS)
    if unsupported:
        raise TradeDataError(f"unsupported canonical mapping targets: {unsupported!r}")

    resolved = dict(supplied)
    for header in headers:
        if header in CANONICAL_INPUT_FIELDS and header not in resolved:
            resolved[header] = header

    targets = list(resolved.values())
    collisions = sorted({target for target in targets if targets.count(target) > 1})
    if collisions:
        raise TradeDataError(f"multiple source columns map to: {collisions!r}")
    if "r_result" not in resolved.values():
        raise TradeDataError(
            "no source column maps to required canonical field 'r_result'"
        )
    return resolved


def _optional_float(
    raw: str,
    *,
    field_name: str,
    row_number: int,
    issues: list[AuditIssue],
    invalid_metadata: dict[str, str],
) -> float | None:
    if raw.strip() == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = math.nan
    if not math.isfinite(value):
        issues.append(
            AuditIssue(
                "warning",
                "INVALID_OPTIONAL_NUMBER",
                f"{field_name} is not a finite real number and was omitted",
                row_number,
                field_name,
            )
        )
        invalid_metadata[field_name] = raw
        return None
    return value


def _parse_timestamp(
    raw: str, row_number: int, issues: list[AuditIssue], invalid_metadata: dict[str, str]
) -> tuple[datetime | None, bool]:
    if raw.strip() == "":
        issues.append(
            AuditIssue(
                "warning",
                "MISSING_TIMESTAMP",
                "timestamp is missing; temporal capabilities are unavailable",
                row_number,
                "timestamp",
            )
        )
        return None, False
    normalized = raw.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        issues.append(
            AuditIssue(
                "warning",
                "INVALID_TIMESTAMP",
                "timestamp is not valid ISO-8601",
                row_number,
                "timestamp",
            )
        )
        invalid_metadata["timestamp"] = raw
        return None, False
    if parsed.utcoffset() is None:
        issues.append(
            AuditIssue(
                "warning",
                "NAIVE_TIMESTAMP",
                "timestamp has no UTC offset; no timezone was assumed",
                row_number,
                "timestamp",
            )
        )
        return parsed, False
    return parsed, True


def _file_fingerprint(path: Path) -> tuple[str, int]:
    """Return SHA-256 and byte size for a source file without loading it at once."""
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise TradeDataError(f"cannot fingerprint CSV {path}: {exc}") from exc
    return digest.hexdigest(), size


def load_trade_csv(
    path: str | Path,
    *,
    outcomes_finalized: bool | None = None,
    column_mapping: Mapping[str, str] | None = None,
    analysis_timezone: str | None = None,
    delimiter: str = ",",
    encoding: str = "utf-8-sig",
) -> CanonicalTradeDataset:
    """Load and audit a strategy-agnostic historical-trade CSV.

    ``column_mapping`` maps source column names to canonical field names, for
    example ``{"PnL_R": "r_result", "Time": "timestamp"}``. Exact canonical
    headers map to themselves. ``outcomes_finalized=True`` is an explicit user
    attestation that every supplied ``r_result`` is a closed, final outcome.
    """
    if outcomes_finalized is not True:
        raise TradeDataError(
            "outcomes_finalized=True is required to attest that every r_result "
            "is a closed, final outcome"
        )
    delimiter = validate_csv_delimiter(delimiter)

    timezone: ZoneInfo | None = None
    if analysis_timezone is not None:
        if not isinstance(analysis_timezone, str) or not analysis_timezone:
            raise TradeDataError("analysis_timezone must be a non-empty IANA name or None")
        try:
            timezone = ZoneInfo(analysis_timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise TradeDataError(
                f"unknown IANA analysis timezone {analysis_timezone!r}"
            ) from exc

    source_path = Path(path)
    initial_sha256, initial_size = _file_fingerprint(source_path)
    try:
        handle = source_path.open("r", encoding=encoding, newline="")
    except (LookupError, OSError, UnicodeError) as exc:
        raise TradeDataError(f"cannot read CSV {source_path}: {exc}") from exc

    with handle:
        reader = csv.reader(handle, delimiter=delimiter, strict=True)
        try:
            headers = next(reader)
        except StopIteration as exc:
            raise TradeDataError("CSV is empty and has no header") from exc
        except (csv.Error, UnicodeError) as exc:
            raise TradeDataError(f"cannot decode CSV header: {exc}") from exc

        if not headers or any(header == "" for header in headers):
            raise TradeDataError("CSV headers must be non-empty")
        duplicates = sorted({header for header in headers if headers.count(header) > 1})
        if duplicates:
            raise TradeDataError(f"CSV contains duplicate headers: {duplicates!r}")

        resolved_mapping = _validate_mapping(headers, column_mapping)
        canonical_to_source = {
            canonical: source for source, canonical in resolved_mapping.items()
        }
        unknown_headers = [header for header in headers if header not in resolved_mapping]

        issues: list[AuditIssue] = []
        trades: list[Trade] = []
        rejected_rows = 0
        seen_ids: dict[str, int] = {}
        duplicate_ids = False
        temporal_complete = True
        chronological = True
        daily_dates_complete = True
        previous_timestamp: datetime | None = None
        total_rows = 0

        if "timestamp" not in canonical_to_source:
            temporal_complete = False
            issues.append(
                AuditIssue(
                    "warning",
                    "MISSING_TIMESTAMP_COLUMN",
                    "no timestamp column is mapped; temporal capabilities are unavailable",
                    field="timestamp",
                )
            )

        try:
            rows = enumerate(reader, start=2)
            for _, values in rows:
                row_number = reader.line_num
                total_rows += 1
                if len(values) != len(headers):
                    rejected_rows += 1
                    issues.append(
                        AuditIssue(
                            "error",
                            "COLUMN_COUNT_MISMATCH",
                            f"row has {len(values)} values but header has {len(headers)}",
                            row_number,
                        )
                    )
                    continue

                raw_row = dict(zip(headers, values, strict=True))
                raw_r = raw_row[canonical_to_source["r_result"]]
                try:
                    r_result = float(raw_r)
                except (TypeError, ValueError):
                    r_result = math.nan
                if raw_r.strip() == "" or not math.isfinite(r_result):
                    rejected_rows += 1
                    issues.append(
                        AuditIssue(
                            "error",
                            "INVALID_R_RESULT",
                            "r_result must be a finite real number",
                            row_number,
                            "r_result",
                        )
                    )
                    continue

                canonical_raw = {
                    canonical: raw_row[source]
                    for canonical, source in canonical_to_source.items()
                }
                invalid_metadata: dict[str, str] = {}

                trade_id = canonical_raw.get("trade_id", "").strip()
                if trade_id:
                    if trade_id in seen_ids:
                        duplicate_ids = True
                        issues.append(
                            AuditIssue(
                                "error",
                                "DUPLICATE_TRADE_ID",
                                f"trade_id duplicates source row {seen_ids[trade_id]}",
                                row_number,
                                "trade_id",
                            )
                        )
                    else:
                        seen_ids[trade_id] = row_number

                timestamp: datetime | None = None
                timestamp_is_aware = False
                if "timestamp" in canonical_raw:
                    timestamp, timestamp_is_aware = _parse_timestamp(
                        canonical_raw["timestamp"],
                        row_number,
                        issues,
                        invalid_metadata,
                    )
                if not timestamp_is_aware:
                    temporal_complete = False
                elif previous_timestamp is not None and timestamp < previous_timestamp:
                    chronological = False
                    issues.append(
                        AuditIssue(
                            "warning",
                            "NON_CHRONOLOGICAL_TIMESTAMP",
                            "timestamp precedes the previous accepted row; input was not reordered",
                            row_number,
                            "timestamp",
                        )
                    )
                if timestamp_is_aware:
                    previous_timestamp = timestamp

                direction_raw = canonical_raw.get("direction", "").strip().lower()
                direction: Literal["long", "short"] | None = None
                if direction_raw:
                    if direction_raw in ("long", "short"):
                        direction = direction_raw  # type: ignore[assignment]
                    else:
                        invalid_metadata["direction"] = canonical_raw["direction"]
                        issues.append(
                            AuditIssue(
                                "warning",
                                "INVALID_DIRECTION",
                                "direction must be 'long' or 'short' and was omitted",
                                row_number,
                                "direction",
                            )
                        )

                optional_numbers = {
                    name: _optional_float(
                        canonical_raw.get(name, ""),
                        field_name=name,
                        row_number=row_number,
                        issues=issues,
                        invalid_metadata=invalid_metadata,
                    )
                    for name in _OPTIONAL_NUMERIC_FIELDS
                }
                unknown = {header: raw_row[header] for header in unknown_headers}
                metadata = {
                    "source_row": row_number,
                    "unknown_fields": unknown,
                    "invalid_optional_fields": invalid_metadata,
                }

                date = ""
                if timestamp_is_aware and timezone is not None:
                    try:
                        date = timestamp.astimezone(timezone).date().isoformat()
                    except (OverflowError, ValueError):
                        daily_dates_complete = False
                        issues.append(
                            AuditIssue(
                                "warning",
                                "DATE_CONVERSION_FAILED",
                                "timestamp cannot be represented in analysis_timezone",
                                row_number,
                                "timestamp",
                            )
                        )

                trades.append(
                    Trade(
                        r_result=r_result,
                        trade_id=trade_id,
                        timestamp=timestamp,
                        date=date,
                        asset=canonical_raw.get("asset", "").strip(),
                        direction=direction,
                        entry_price=optional_numbers["entry_price"],
                        stop_price=optional_numbers["stop_price"],
                        exit_price=optional_numbers["exit_price"],
                        strategy=canonical_raw.get("strategy", "").strip(),
                        metadata=metadata,
                    )
                )
        except (csv.Error, UnicodeError) as exc:
            raise TradeDataError(
                f"malformed CSV near source line {reader.line_num}: {exc}"
            ) from exc

    if total_rows == 0:
        issues.append(
            AuditIssue(
                "error",
                "NO_DATA_ROWS",
                "CSV contains a header but no trade rows",
            )
        )

    accepted_rows = len(trades)
    required_sample_complete = rejected_rows == 0 and not duplicate_ids
    core_reasons: list[str] = []
    if accepted_rows == 0:
        core_reasons.append("no accepted trades")
    if rejected_rows:
        core_reasons.append("one or more source rows were rejected")
    if duplicate_ids:
        core_reasons.append("duplicate non-empty trade_id values require review")

    core_available = required_sample_complete and accepted_rows > 0
    temporal_reasons = list(core_reasons)
    if not temporal_complete:
        temporal_reasons.append("every trade needs a timezone-aware timestamp")
    if not chronological:
        temporal_reasons.append("timestamps are not non-decreasing in source order")
    temporal_available = core_available and temporal_complete and chronological

    daily_reasons = [] if temporal_available else list(temporal_reasons)
    if timezone is None:
        daily_reasons.append("an explicit analysis_timezone is required")
    if not daily_dates_complete:
        daily_reasons.append("one or more dates cannot be represented in analysis_timezone")
    daily_available = (
        temporal_available and timezone is not None and daily_dates_complete
    )

    capabilities = {
        "core_metrics": CapabilityStatus(core_available, tuple(core_reasons)),
        "temporal_analysis": CapabilityStatus(
            temporal_available, () if temporal_available else tuple(temporal_reasons)
        ),
        "daily_rule_simulation": CapabilityStatus(
            daily_available, () if daily_available else tuple(daily_reasons)
        ),
    }
    audit = TradeAuditReport(
        total_rows=total_rows,
        accepted_rows=accepted_rows,
        rejected_rows=rejected_rows,
        issues=tuple(issues),
    )
    final_sha256, final_size = _file_fingerprint(source_path)
    if (final_sha256, final_size) != (initial_sha256, initial_size):
        raise TradeDataError("CSV changed while it was being loaded; retry the ingestion")
    provenance = IngestionProvenance(
        schema_version=_SCHEMA_VERSION,
        source_path=str(source_path.resolve()),
        source_sha256=final_sha256,
        source_size_bytes=final_size,
        resolved_mapping=resolved_mapping,
        outcomes_finalized=True,
        analysis_timezone=analysis_timezone,
        delimiter=delimiter,
        encoding=encoding,
    )
    return CanonicalTradeDataset(
        trades=tuple(trades),
        audit=audit,
        capabilities=capabilities,
        provenance=provenance,
        source=str(source_path),
    )

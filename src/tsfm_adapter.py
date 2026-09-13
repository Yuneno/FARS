"""TSFM-to-FARS trade conversion adapter (P1-B1).

Converts in-memory TSFM trade records into FARS ``Trade`` objects with
structured per-record error reporting.  No file I/O, CLI, metrics, or
bootstrap in this module.

TSFM input contract (per record dict):
    t           – timestamp string (ISO-8601, timezone-aware recommended)
    strategy    – strategy identifier string
    side        – int: -1 (short) or +1 (long)
    entry       – float: entry price
    stop        – float: initial stop price
    exit_price  – float: exit / fill price
    pnl_net     – float: net P&L (units determined by config.pnl_units)

FARS output contract: ``src.types.Trade`` with r_result, trade_id,
timestamp, date, asset, direction, entry_price, stop_price, exit_price,
strategy, and metadata.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.types import Trade

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VALID_SYMBOLS = frozenset({"MNQ", "MES", "MYM", "MGC", "NQ", "YM", "ES", "GC"})
_IDENTITY_SCHEMA_VERSION = "v2"


def _canonical_number(v: float) -> float:
    """Normalize a numeric value for canonical fingerprinting.

    Ensures 100 == 100.0 and -0.0 == 0.0 in canonical form.
    """
    f = float(v)
    if f == 0.0:
        return 0.0  # normalize -0.0 to 0.0
    return f


@dataclass(frozen=True)
class ConversionConfig:
    """Parameters that govern one conversion batch.

    source_timezone: IANA zone name used to interpret naive timestamps.
        Required when records contain naive (offset-less) timestamps.
        Does NOT apply to aware timestamps — those carry their own offset.
    analysis_timezone: IANA zone name used to derive the ``date`` field
        for daily-rule analysis.  Applied after UTC normalisation.
    ambiguous_timestamp_fold: 0 or 1 — disambiguation strategy for local
        times that occur twice during DST fall-back.  0 = first occurrence
        (pre-transition), 1 = second occurrence (post-transition).
        Ambiguous times are rejected unless this is explicitly set.
    """

    symbol: str | None = None
    pnl_units: Literal["points", "monetary"] = "points"
    quantity: int | None = None
    dollar_per_point: float | None = None
    source_timezone: str | None = None
    analysis_timezone: str | None = None
    ambiguous_timestamp_fold: int = -1
    namespace: str = "tsfm"
    allowed_outcomes: frozenset[str] | None = None
    outcomes_finalized: bool = False
    source_dataset: str | None = None


# ---------------------------------------------------------------------------
# Per-record error
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConversionError:
    """One rejection reason for a single input record."""

    code: str
    message: str
    record_index: int
    field: str | None = None


@dataclass(frozen=True)
class ConversionWarning:
    """One non-finding for a single input record."""

    code: str
    message: str
    record_index: int


# ---------------------------------------------------------------------------
# Batch result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConversionResult:
    """Structured output of convert_trades()."""

    trades: tuple[Trade, ...]
    errors: tuple[ConversionError, ...]
    warnings: tuple[ConversionWarning, ...]
    total_records: int
    accepted: int
    rejected: int
    deduplicated: int
    symbol_used: str | None
    pnl_units_used: str

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_aware(dt: datetime) -> bool:
    return dt.tzinfo is not None and dt.utcoffset() is not None


def _parse_timestamp(
    value: Any,
    *,
    source_tz: ZoneInfo | None,
    analysis_tz: ZoneInfo | None,
    fold: int,
    record_index: int,
    errors: list[ConversionError],
) -> datetime | None:
    """Parse an ISO-8601 timestamp into an aware UTC datetime.

    Returns None when the value is missing/empty/unparseable; in that case
    a MISSING_TIMESTAMP error is appended *by the caller*.

    Supported precision: up to microseconds (Python datetime limit).
    Sub-microsecond precision cannot be preserved and is not silently truncated;
    Python's fromisoformat() raises ValueError for such inputs.

    DST handling:
      - Aware timestamps (with explicit offset) are normalised to UTC directly.
      - Naive timestamps require source_tz.  They are validated via round-trip:
        the local time must exist in the zone (non-existent → error) and must
        be unambiguous unless config.ambiguous_timestamp_fold is explicitly set.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Precision check: reject >6 fractional digits (Python 3.13 truncates silently)
        if "," in s:
            errors.append(
                ConversionError(
                    code="INVALID_TIMESTAMP_FORMAT",
                    message=(
                        f"timestamp {value!r} contains a comma; "
                        f"ISO-8601 uses '.' for fractional seconds"
                    ),
                    record_index=record_index,
                    field="t",
                )
            )
            return None
        dot_pos = s.find(".")
        if dot_pos >= 0:
            # Find end of fractional part (before +, -, Z, or end)
            frac_end = dot_pos + 1
            while frac_end < len(s) and s[frac_end] not in ("+", "-", "Z", "z", "T"):
                frac_end += 1
            frac_len = frac_end - dot_pos - 1
            if frac_len > 6:
                errors.append(
                    ConversionError(
                        code="UNSUPPORTED_TIMESTAMP_PRECISION",
                        message=(
                            f"timestamp {value!r} has {frac_len} fractional "
                            f"digits; maximum supported precision is 6 (microseconds)"
                        ),
                        record_index=record_index,
                        field="t",
                    )
                )
                return None
        try:
            dt = datetime.fromisoformat(s)
        except (ValueError, TypeError):
            return None
    else:
        return None

    if _is_aware(dt):
        # --- Aware timestamp: normalise to UTC directly ---
        return dt.astimezone(timezone.utc)

    # --- Naive timestamp ---
    if source_tz is None:
        errors.append(
            ConversionError(
                code="MISSING_TIMESTAMP",
                message=(
                    f"naive timestamp {value!r} has no offset and "
                    f"no source_timezone was configured"
                ),
                record_index=record_index,
                field="t",
            )
        )
        return None

    # fold < 0 means "not explicitly set" — use 0 for replace but track it
    effective_fold = fold if fold >= 0 else 0

    # Attach the zone with the requested fold for disambiguation
    local_dt = dt.replace(tzinfo=source_tz, fold=effective_fold)

    # Validate via round-trip: convert to UTC then back to local
    utc_dt = local_dt.astimezone(timezone.utc)
    reconstructed = utc_dt.astimezone(source_tz)

    if reconstructed.replace(tzinfo=None) != dt:
        # The local time does not exist (spring-forward gap)
        errors.append(
            ConversionError(
                code="NONEXISTENT_TIMESTAMP",
                message=(
                    f"timestamp {value!r} does not exist in "
                    f"{source_tz.key} (DST spring-forward gap)"
                ),
                record_index=record_index,
                field="t",
            )
        )
        return None

    # Check if the time is ambiguous (fall-back: same local time, different offset)
    # Compare the *zone's* UTC offset at fold=0 vs fold=1
    local_fold0 = dt.replace(tzinfo=source_tz, fold=0)
    local_fold1 = dt.replace(tzinfo=source_tz, fold=1)
    if local_fold0.utcoffset() != local_fold1.utcoffset():
        # Ambiguous — only accept if fold was explicitly set (0 or 1)
        if fold < 0:
            # fold not explicitly configured; reject ambiguous time
            errors.append(
                ConversionError(
                    code="AMBIGUOUS_TIMESTAMP",
                    message=(
                        f"timestamp {value!r} is ambiguous in "
                        f"{source_tz.key} (DST fall-back); "
                        f"use ambiguous_timestamp_fold=0 or 1 to disambiguate"
                    ),
                    record_index=record_index,
                    field="t",
                )
            )
            return None

    return utc_dt


def _content_fingerprint(
    *,
    asset: str | None,
    ts: datetime | None,
    strategy: Any,
    direction: str | None,
    entry: float | None,
    stop: float | None,
    exit_price: float | None,
    pnl_net: float | None,
    pnl_units: str,
    quantity: int | None,
    dollar_per_point: float | None,
    normalised_outcome: str | None,
    source_dataset: str | None,
) -> str:
    """Compute SHA-256 content fingerprint of normalized semantic fields.

    Uses deterministic JSON serialization with a versioned schema key.
    Numeric values are canonicalized: 100 == 100.0, -0.0 == 0.0.
    Timestamps are represented as UTC ISO-8601 strings.
    """
    import json as _json

    # Canonical strategy: strip whitespace, required non-empty string
    strat_canonical = strategy.strip() if isinstance(strategy, str) else ""

    # Canonical timestamp: UTC ISO format
    ts_canonical = ""
    if ts is not None:
        ts_canonical = ts.astimezone(timezone.utc).isoformat()

    # Build the canonical dict in sorted order
    canonical: dict[str, Any] = {
        "schema_version": _IDENTITY_SCHEMA_VERSION,
        "asset": asset or "",
        "timestamp_utc": ts_canonical,
        "strategy": strat_canonical,
        "direction": direction or "",
        "entry": _canonical_number(entry) if entry is not None else None,
        "stop": _canonical_number(stop) if stop is not None else None,
        "exit_price": _canonical_number(exit_price) if exit_price is not None else None,
        "pnl_net": _canonical_number(pnl_net) if pnl_net is not None else None,
        "pnl_units": pnl_units,
    }
    if pnl_units == "monetary":
        canonical["quantity"] = quantity
        canonical["dollar_per_point"] = (
            _canonical_number(dollar_per_point) if dollar_per_point is not None else None
        )
    canonical["outcome"] = normalised_outcome
    if source_dataset is not None:
        canonical["source_dataset"] = source_dataset

    raw = _json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _canonical_id(
    *,
    namespace: str,
    source_id: str | None,
    content_fingerprint: str,
) -> str:
    """Build a collision-resistant identity from structured components.

    Uses JSON-serialized object with schema version, normalized namespace,
    identity type, and the source identifier or content fingerprint.
    This eliminates delimiter-based collisions (e.g. ns:sub/X vs ns/sub:X).
    """
    import json as _json

    identity_obj: dict[str, Any] = {
        "v": _IDENTITY_SCHEMA_VERSION,
        "ns": namespace.strip(),
        "type": "source" if source_id is not None else "content",
    }
    if source_id is not None:
        identity_obj["id"] = source_id
    else:
        identity_obj["fp"] = content_fingerprint

    raw = _json.dumps(identity_obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _validate_side(
    side: Any, *, record_index: int
) -> tuple[str | None, ConversionError | None]:
    """Validate and normalise side to 'long'/'short'. Returns (direction, error)."""
    if isinstance(side, bool):
        return None, ConversionError(
            code="INVALID_SIDE_TYPE",
            message=f"side must be int, got bool {side!r}",
            record_index=record_index,
            field="side",
        )
    if isinstance(side, float) and not side.is_integer():
        return None, ConversionError(
            code="SIDE_NOT_INTEGER",
            message=f"side must be an exact integer, got {side!r}",
            record_index=record_index,
            field="side",
        )
    # Reject non-integer Decimals before int() truncation
    try:
        from decimal import Decimal as _Dec
        if isinstance(side, _Dec) and side != int(side):
            return None, ConversionError(
                code="SIDE_NOT_INTEGER",
                message=f"side must be an exact integer, got {side!r}",
                record_index=record_index,
                field="side",
            )
    except (TypeError, ValueError):
        pass
    try:
        value = int(side)
    except (TypeError, ValueError):
        return None, ConversionError(
            code="INVALID_SIDE_TYPE",
            message=f"side must be an integer, got {side!r}",
            record_index=record_index,
            field="side",
        )
    if value == 0:
        return None, ConversionError(
            code="SIDE_ZERO",
            message="side must be -1 or +1, got 0",
            record_index=record_index,
            field="side",
        )
    if value not in (-1, 1):
        return None, ConversionError(
            code="SIDE_OUT_OF_RANGE",
            message=f"side must be -1 or +1, got {value}",
            record_index=record_index,
            field="side",
        )
    return "long" if value > 0 else "short", None


def _validate_numeric(
    value: Any,
    *,
    name: str,
    record_index: int,
    required: bool = True,
    positive: bool = False,
) -> tuple[float | None, ConversionError | None]:
    """Validate a numeric field. Returns (float_value, error)."""
    if value is None:
        if required:
            return None, ConversionError(
                code=f"MISSING_{name.upper()}",
                message=f"{name} is required but missing",
                record_index=record_index,
                field=name,
            )
        return None, None
    if isinstance(value, bool):
        return None, ConversionError(
            code=f"INVALID_{name.upper()}_TYPE",
            message=f"{name} must be numeric, got bool {value!r}",
            record_index=record_index,
            field=name,
        )
    try:
        fval = float(value)
    except (TypeError, ValueError):
        return None, ConversionError(
            code=f"NON_NUMERIC_{name.upper()}",
            message=f"{name} must be numeric, got {value!r}",
            record_index=record_index,
            field=name,
        )
    if not math.isfinite(fval):
        return None, ConversionError(
            code=f"NON_FINITE_{name.upper()}",
            message=f"{name} must be finite, got {fval!r}",
            record_index=record_index,
            field=name,
        )
    if positive and fval <= 0:
        return None, ConversionError(
            code=f"NON_POSITIVE_{name.upper()}",
            message=f"{name} must be strictly positive, got {fval}",
            record_index=record_index,
            field=name,
        )
    return fval, None


_UNFINALISED = frozenset({"pending", "no_fill", "unresolved", "partial", "open"})


def _validate_outcome(
    record: dict[str, Any],
    *,
    record_index: int,
    allowed: frozenset[str],
    outcomes_finalized: bool,
    source_dataset: str | None,
) -> tuple[str | None, ConversionError | None]:
    """Validate and normalise outcome. Returns (normalised_outcome, error)."""
    outcome = record.get("outcome")

    if outcome is None:
        if outcomes_finalized and source_dataset:
            return None, None
        return None, ConversionError(
            code="MISSING_OUTCOME",
            message=(
                "record has no 'outcome' field; provide a finalised "
                "outcome or set outcomes_finalized=True with source_dataset"
            ),
            record_index=record_index,
            field="outcome",
        )

    if not isinstance(outcome, str):
        return None, ConversionError(
            code="INVALID_OUTCOME_TYPE",
            message=f"outcome must be a string, got {type(outcome).__name__}",
            record_index=record_index,
            field="outcome",
        )

    normalised = outcome.strip().lower()

    if not normalised:
        return None, ConversionError(
            code="MISSING_OUTCOME",
            message="outcome is empty or whitespace-only",
            record_index=record_index,
            field="outcome",
        )

    if normalised in _UNFINALISED:
        return None, ConversionError(
            code="UNFINALISED_OUTCOME",
            message=f"outcome {outcome!r} is not a finalised result",
            record_index=record_index,
            field="outcome",
        )

    if normalised not in allowed:
        return None, ConversionError(
            code="UNKNOWN_OUTCOME",
            message=f"outcome {outcome!r} is not in the allowed set: {sorted(allowed)}",
            record_index=record_index,
            field="outcome",
        )

    return normalised, None


# ---------------------------------------------------------------------------
# Intermediate record for two-pass duplicate resolution
# ---------------------------------------------------------------------------

@dataclass
class _ValidatedRecord:
    """Holds a record that passed individual validation, pending group resolution."""
    idx: int
    record: dict[str, Any]
    trade_id: str
    content_fingerprint: str
    source_id: str | None
    r_result: float
    ts: datetime | None
    date_str: str
    analysis_tz_name: str | None
    direction: str
    entry: float
    stop: float
    exit_price: float
    pnl_net: float
    sym: str | None
    raw_strategy: str
    normalised_outcome: str | None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def convert_trades(
    records: list[dict[str, Any]],
    *,
    config: ConversionConfig | None = None,
) -> ConversionResult:
    """Convert a list of TSFM trade dicts to FARS Trade objects.

    Returns a ``ConversionResult`` with accepted trades, per-record errors,
    warnings, and summary counts.  No file I/O is performed.

    Parameters
    ----------
    records : list[dict[str, Any]]
        TSFM trade records.  Each dict must contain at minimum
        ``side``, ``entry``, ``stop``, ``exit_price``, ``pnl_net``.
    config : ConversionConfig, optional
        Conversion parameters.  Defaults to points-based PnL with no
        fixed symbol.
    """
    config = config or ConversionConfig()

    # --- Config-level type validation ---
    config_errors: list[ConversionError] = []
    if config.symbol is not None and not isinstance(config.symbol, str):
        config_errors.append(
            ConversionError(
                code="INVALID_CONFIG_SYMBOL_TYPE",
                message=f"config.symbol must be a string or None, got {type(config.symbol).__name__}: {config.symbol!r}",
                record_index=0,
                field="symbol",
            )
        )
    if config.source_timezone is not None and not isinstance(config.source_timezone, str):
        config_errors.append(
            ConversionError(
                code="INVALID_CONFIG_TIMEZONE",
                message=f"config.source_timezone must be a string or None, got {type(config.source_timezone).__name__}",
                record_index=0,
                field="source_timezone",
            )
        )
    if config.analysis_timezone is not None and not isinstance(config.analysis_timezone, str):
        config_errors.append(
            ConversionError(
                code="INVALID_CONFIG_TIMEZONE",
                message=f"config.analysis_timezone must be a string or None, got {type(config.analysis_timezone).__name__}",
                record_index=0,
                field="analysis_timezone",
            )
        )
    if config.symbol is not None and isinstance(config.symbol, str) and config.symbol not in VALID_SYMBOLS:
        config_errors.append(
            ConversionError(
                code="INVALID_CONFIG_SYMBOL",
                message=f"config.symbol {config.symbol!r} is not a supported contract; valid: {sorted(VALID_SYMBOLS)}",
                record_index=0,
                field="symbol",
            )
        )

    # PnL units validation
    if config.pnl_units not in ("points", "monetary"):
        config_errors.append(
            ConversionError(
                code="INVALID_PNL_UNITS",
                message=f"pnl_units must be 'points' or 'monetary', got {config.pnl_units!r}",
                record_index=0,
                field="pnl_units",
            )
        )

    # Monetary parameter validation
    if config.pnl_units == "monetary":
        q = config.quantity
        dpp = config.dollar_per_point
        if q is None or dpp is None:
            config_errors.append(
                ConversionError(
                    code="MISSING_MONETARY_PARAMS",
                    message="pnl_units='monetary' requires both quantity and dollar_per_point",
                    record_index=0,
                    field="quantity",
                )
            )
        else:
            if isinstance(q, bool) or not isinstance(q, int) or q <= 0:
                config_errors.append(
                    ConversionError(
                        code="INVALID_QUANTITY",
                        message=f"quantity must be a positive integer, got {q!r} ({type(q).__name__})",
                        record_index=0,
                        field="quantity",
                    )
                )
            if isinstance(dpp, bool):
                config_errors.append(
                    ConversionError(
                        code="INVALID_DOLLAR_PER_POINT",
                        message=f"dollar_per_point must be a number, got bool {dpp!r}",
                        record_index=0,
                        field="dollar_per_point",
                    )
                )
            elif not isinstance(dpp, (int, float)):
                config_errors.append(
                    ConversionError(
                        code="INVALID_DOLLAR_PER_POINT",
                        message=f"dollar_per_point must be numeric, got {type(dpp).__name__}: {dpp!r}",
                        record_index=0,
                        field="dollar_per_point",
                    )
                )
            elif not math.isfinite(dpp) or dpp <= 0:
                config_errors.append(
                    ConversionError(
                        code="INVALID_DOLLAR_PER_POINT",
                        message=f"dollar_per_point must be finite and > 0, got {dpp!r}",
                        record_index=0,
                        field="dollar_per_point",
                    )
                )

    # Source dataset validation
    # Strip source_dataset whitespace before validation
    if config.source_dataset is not None and isinstance(config.source_dataset, str):
        _stripped = config.source_dataset.strip()
        if not _stripped:
            # Simulate __init__ with source_dataset=None
            import dataclasses
            config = dataclasses.replace(config, source_dataset=None)
        elif _stripped != config.source_dataset:
            import dataclasses
            config = dataclasses.replace(config, source_dataset=_stripped)

    if config.outcomes_finalized and not config.source_dataset:
        config_errors.append(
            ConversionError(
                code="MISSING_SOURCE_DATASET",
                message="outcomes_finalized=True requires a non-empty source_dataset",
                record_index=0,
                field="source_dataset",
            )
        )

    # Namespace validation
    if not isinstance(config.namespace, str):
        config_errors.append(
            ConversionError(
                code="INVALID_NAMESPACE_TYPE",
                message=f"namespace must be a string, got {type(config.namespace).__name__}: {config.namespace!r}",
                record_index=0,
                field="namespace",
            )
        )
    elif not config.namespace.strip():
        config_errors.append(
            ConversionError(
                code="EMPTY_NAMESPACE",
                message="namespace must be a non-empty string after stripping whitespace",
                record_index=0,
                field="namespace",
            )
        )

    if config_errors:
        return ConversionResult(
            trades=(),
            errors=tuple(config_errors),
            warnings=(),
            total_records=0,
            accepted=0,
            rejected=0,
            deduplicated=0,
            symbol_used=None,
            pnl_units_used=config.pnl_units,
        )

    errors: list[ConversionError] = []
    warnings: list[ConversionWarning] = []
    accepted: list[Trade] = []
    candidates: list[_ValidatedRecord] = []

    resolved_symbol: str | None = config.symbol
    source_tz: ZoneInfo | None = None
    if config.source_timezone:
        try:
            source_tz = ZoneInfo(config.source_timezone)
        except ZoneInfoNotFoundError:
            raise ValueError(
                f"source_timezone {config.source_timezone!r} is not a valid IANA zone"
            )

    analysis_tz: ZoneInfo | None = None
    if config.analysis_timezone:
        try:
            analysis_tz = ZoneInfo(config.analysis_timezone)
        except ZoneInfoNotFoundError:
            raise ValueError(
                f"analysis_timezone {config.analysis_timezone!r} is not a valid IANA zone"
            )

    for idx, record in enumerate(records):
        row_errors: list[ConversionError] = []

        # --- Symbol ---
        raw_asset = record.get("asset")
        raw_sym = record.get("symbol")

        # Type check: reject non-string asset/symbol
        if raw_asset is not None and not isinstance(raw_asset, str):
            row_errors.append(
                ConversionError(
                    code="INVALID_SYMBOL_TYPE",
                    message=f"asset must be a string, got {type(raw_asset).__name__}: {raw_asset!r}",
                    record_index=idx,
                    field="asset",
                )
            )
        if raw_sym is not None and not isinstance(raw_sym, str):
            row_errors.append(
                ConversionError(
                    code="INVALID_SYMBOL_TYPE",
                    message=f"symbol must be a string, got {type(raw_sym).__name__}: {raw_sym!r}",
                    record_index=idx,
                    field="symbol",
                )
            )

        record_asset = raw_asset.strip().upper() if isinstance(raw_asset, str) and raw_asset.strip() else None
        record_symbol = raw_sym.strip().upper() if isinstance(raw_sym, str) and raw_sym.strip() else None

        # Req 3: if both asset and symbol are present, they must agree
        if record_asset is not None and record_symbol is not None:
            if record_asset != record_symbol:
                row_errors.append(
                    ConversionError(
                        code="SYMBOL_MISMATCH",
                        message=(
                            f"record asset {record_asset!r} differs from "
                            f"record symbol {record_symbol!r}"
                        ),
                        record_index=idx,
                        field="asset",
                    )
                )
                sym = record_asset  # will be rejected below anyway
            else:
                sym = record_asset
        else:
            sym = record_asset or record_symbol

        if sym is not None:
            # Req 1: no aliases; just validate against known set
            if sym not in VALID_SYMBOLS:
                row_errors.append(
                    ConversionError(
                        code="INVALID_SYMBOL",
                        message=f"symbol {sym!r} is not a supported contract",
                        record_index=idx,
                        field="asset",
                    )
                )
            # Req 3+5: if config.symbol set, record must agree
            elif config.symbol is not None and sym != config.symbol:
                row_errors.append(
                    ConversionError(
                        code="SYMBOL_CONFLICT",
                        message=(
                            f"record symbol {sym!r} conflicts with "
                            f"config symbol {config.symbol!r}"
                        ),
                        record_index=idx,
                        field="asset",
                    )
                )
            else:
                # Valid symbol; accept into batch
                if resolved_symbol is None:
                    resolved_symbol = sym
                elif sym != resolved_symbol:
                    # Req 5: mixed batch allowed, symbol_used becomes None
                    resolved_symbol = None
        elif config.symbol is not None:
            # Req 4: no symbol on record, use config
            sym = config.symbol
            if resolved_symbol is None:
                resolved_symbol = sym
            elif sym != resolved_symbol:
                resolved_symbol = None
        elif not row_errors:
            # Req 4: no symbol on record AND no config → reject
            row_errors.append(
                ConversionError(
                    code="MISSING_SYMBOL",
                    message="record has no symbol and no config.symbol was provided",
                    record_index=idx,
                    field="asset",
                )
            )

        # --- Side ---
        direction, side_err = _validate_side(record.get("side"), record_index=idx)
        if side_err:
            row_errors.append(side_err)

        # --- Numeric fields ---
        entry, entry_err = _validate_numeric(
            record.get("entry"), name="entry", record_index=idx
        )
        if entry_err:
            row_errors.append(entry_err)

        stop, stop_err = _validate_numeric(
            record.get("stop"), name="stop", record_index=idx
        )
        if stop_err:
            row_errors.append(stop_err)

        exit_price, exit_err = _validate_numeric(
            record.get("exit_price"), name="exit_price", record_index=idx
        )
        if exit_err:
            row_errors.append(exit_err)

        pnl_net, pnl_err = _validate_numeric(
            record.get("pnl_net"), name="pnl_net", record_index=idx
        )
        if pnl_err:
            row_errors.append(pnl_err)

        # --- Risk validation (requires entry + stop + side) ---
        if entry is not None and stop is not None and direction is not None:
            risk = abs(entry - stop)
            if not math.isfinite(risk) or risk <= 0:
                row_errors.append(
                    ConversionError(
                        code="ZERO_OR_NON_FINITE_RISK",
                        message=(
                            f"risk (|entry-stop|) must be finite and > 0, "
                            f"got {risk}"
                        ),
                        record_index=idx,
                        field="stop",
                    )
                )
            elif direction == "long" and stop >= entry:
                row_errors.append(
                    ConversionError(
                        code="STOP_MISPLACED_LONG",
                        message=(
                            f"long stop ({stop}) must be below entry ({entry})"
                        ),
                        record_index=idx,
                        field="stop",
                    )
                )
            elif direction == "short" and stop <= entry:
                row_errors.append(
                    ConversionError(
                        code="STOP_MISPLACED_SHORT",
                        message=(
                            f"short stop ({stop}) must be above entry ({entry})"
                        ),
                        record_index=idx,
                        field="stop",
                    )
                )

        # --- Outcome ---
        _allowed = config.allowed_outcomes if config.allowed_outcomes is not None else frozenset({"closed"})
        normalised_outcome, outcome_err = _validate_outcome(
            record,
            record_index=idx,
            allowed=_allowed,
            outcomes_finalized=config.outcomes_finalized,
            source_dataset=config.source_dataset,
        )
        if outcome_err:
            row_errors.append(outcome_err)

        # --- If hard errors exist, skip R computation and trade creation ---
        if row_errors:
            errors.extend(row_errors)
            continue

        # --- Compute R ---
        assert entry is not None and stop is not None and pnl_net is not None
        risk = abs(entry - stop)
        if config.pnl_units == "points":
            r_result = pnl_net / risk
        else:
            dollar_risk = risk * config.quantity * config.dollar_per_point
            r_result = pnl_net / dollar_risk

        if not math.isfinite(r_result):
            errors.append(
                ConversionError(
                    code="NON_FINITE_R_RESULT",
                    message=f"computed r_result is not finite: {r_result!r}",
                    record_index=idx,
                )
            )
            continue

        # --- Timestamp ---
        ts = _parse_timestamp(
            record.get("t"),
            source_tz=source_tz,
            analysis_tz=analysis_tz,
            fold=config.ambiguous_timestamp_fold,
            record_index=idx,
            errors=row_errors,
        )
        if ts is None and not row_errors:
            # Shouldn't happen, but safety net
            row_errors.append(
                ConversionError(
                    code="MISSING_TIMESTAMP",
                    message="timestamp could not be parsed",
                    record_index=idx,
                    field="t",
                )
            )
        # Derive date: use analysis_tz if set, else UTC
        if ts is not None:
            effective_tz = analysis_tz or timezone.utc
            date_str = ts.astimezone(effective_tz).strftime("%Y-%m-%d")
            analysis_tz_name = config.analysis_timezone or "UTC"
        else:
            date_str = ""
            analysis_tz_name = None

        # --- Timestamp guard: if errors, skip trade creation ---
        if row_errors:
            errors.extend(row_errors)
            continue

        # --- Strategy validation ---
        raw_strategy = record.get("strategy")
        if raw_strategy is None:
            row_errors.append(
                ConversionError(
                    code="MISSING_STRATEGY",
                    message="strategy is required but missing or None",
                    record_index=idx,
                    field="strategy",
                )
            )
        elif not isinstance(raw_strategy, str):
            row_errors.append(
                ConversionError(
                    code="INVALID_STRATEGY_TYPE",
                    message=f"strategy must be a string, got {type(raw_strategy).__name__}: {raw_strategy!r}",
                    record_index=idx,
                    field="strategy",
                )
            )
        elif not raw_strategy.strip():
            row_errors.append(
                ConversionError(
                    code="EMPTY_STRATEGY",
                    message="strategy must be a non-empty string after stripping whitespace",
                    record_index=idx,
                    field="strategy",
                )
            )

        # --- Source ID extraction (explicit, not or-selection) ---
        raw_trade_id = record.get("trade_id")
        raw_id = record.get("id")
        source_id = None
        if raw_trade_id is not None:
            if not isinstance(raw_trade_id, str):
                row_errors.append(
                    ConversionError(
                        code="INVALID_SOURCE_ID_TYPE",
                        message=f"trade_id must be a string, got {type(raw_trade_id).__name__}: {raw_trade_id!r}",
                        record_index=idx,
                        field="trade_id",
                    )
                )
            elif not raw_trade_id.strip():
                row_errors.append(
                    ConversionError(
                        code="EMPTY_SOURCE_ID",
                        message="trade_id must be a non-empty string after stripping whitespace",
                        record_index=idx,
                        field="trade_id",
                    )
                )
            else:
                source_id = raw_trade_id.strip()
        if raw_id is not None:
            if not isinstance(raw_id, str):
                row_errors.append(
                    ConversionError(
                        code="INVALID_SOURCE_ID_TYPE",
                        message=f"id must be a string, got {type(raw_id).__name__}: {raw_id!r}",
                        record_index=idx,
                        field="id",
                    )
                )
            elif not raw_id.strip():
                row_errors.append(
                    ConversionError(
                        code="EMPTY_SOURCE_ID",
                        message="id must be a non-empty string after stripping whitespace",
                        record_index=idx,
                        field="id",
                    )
                )
            else:
                id_val = raw_id.strip()
                if source_id is not None and id_val != source_id:
                    row_errors.append(
                        ConversionError(
                            code="SOURCE_ID_MISMATCH",
                            message=(
                                f"trade_id {source_id!r} and id {id_val!r} "
                                f"differ; they must match when both are provided"
                            ),
                            record_index=idx,
                            field="trade_id",
                        )
                    )
                elif source_id is None:
                    source_id = id_val

        # If errors from strategy/source_id, skip to next record
        if row_errors:
            errors.extend(row_errors)
            continue

        # --- Trade ID (computed after fingerprint) ---
        # strategy_str is set below in Build FARS Trade; compute cfp here
        _cfp_for_id = _content_fingerprint(
            asset=sym, ts=ts, strategy=raw_strategy, direction=direction,
            entry=entry, stop=stop, exit_price=exit_price, pnl_net=pnl_net,
            pnl_units=config.pnl_units, quantity=config.quantity,
            dollar_per_point=config.dollar_per_point,
            normalised_outcome=normalised_outcome,
            source_dataset=config.source_dataset,
        )
        trade_id = _canonical_id(
            namespace=config.namespace, source_id=source_id,
            content_fingerprint=_cfp_for_id,
        )

        # --- Collect validated record (duplicate resolution is batched) ---
        candidates.append(
            _ValidatedRecord(
                idx=idx, record=record, trade_id=trade_id,
                content_fingerprint=_cfp_for_id, source_id=source_id,
                r_result=r_result, ts=ts, date_str=date_str,
                analysis_tz_name=analysis_tz_name, direction=direction,
                entry=entry, stop=stop, exit_price=exit_price,
                pnl_net=pnl_net, sym=sym, raw_strategy=raw_strategy,
                normalised_outcome=normalised_outcome,
            )
        )

    # -----------------------------------------------------------------
    # Phase 2: Duplicate / conflict resolution (batch, order-independent)
    # -----------------------------------------------------------------
    from collections import defaultdict as _dd
    groups: dict[str, list[_ValidatedRecord]] = _dd(list)
    for cand in candidates:
        groups[cand.trade_id].append(cand)

    resolved_accepted: list[_ValidatedRecord] = []
    dedup_count = 0

    for trade_id, group in groups.items():
        if len(group) == 1:
            resolved_accepted.append(group[0])
            continue

        # Multiple records share the same trade_id.
        fingerprints = {c.content_fingerprint for c in group}
        has_source = any(c.source_id is not None for c in group)

        if len(fingerprints) == 1 and has_source:
            # Rule A: same source_id + same fingerprint -> keep first, deduplicate rest
            # Sort by original index for deterministic "first"
            group_sorted = sorted(group, key=lambda c: c.idx)
            resolved_accepted.append(group_sorted[0])
            for dup in group_sorted[1:]:
                dedup_count += 1
                warnings.append(
                    ConversionWarning(
                        code="DEDUPLICATED",
                        message=(
                            f"record at index {dup.idx} is a duplicate of "
                            f"index {group_sorted[0].idx} (same source_id "
                            f"and content fingerprint)"
                        ),
                        record_index=dup.idx,
                    )
                )
        elif len(fingerprints) > 1 and has_source:
            # Rule B: same source_id but different fingerprints -> reject ALL
            for cand in group:
                errors.append(
                    ConversionError(
                        code="SOURCE_ID_CONTENT_CONFLICT",
                        message=(
                            f"trade_id {trade_id!r} has conflicting content "
                            f"fingerprints across {len(group)} records"
                        ),
                        record_index=cand.idx,
                    )
                )
        elif len(fingerprints) == 1 and not has_source:
            # Rule C: no source_id, same fingerprint -> reject all as ambiguous
            for cand in group:
                errors.append(
                    ConversionError(
                        code="AMBIGUOUS_DUPLICATE_WITHOUT_SOURCE_ID",
                        message=(
                            f"record at index {cand.idx} has the same content "
                            f"fingerprint as {len(group) - 1} other record(s) "
                            f"without a source_id to distinguish them"
                        ),
                        record_index=cand.idx,
                    )
                )
        else:
            # Rule D/E: different trade_ids (shouldn't happen in same group)
            # or different fingerprints without source_id -> keep all
            for cand in group:
                resolved_accepted.append(cand)

    # -----------------------------------------------------------------
    # Phase 3: Build FARS Trade objects
    # -----------------------------------------------------------------
    ns_normalized = config.namespace.strip()
    for cand in resolved_accepted:
        asset_str = cand.sym or resolved_symbol or ""
        strategy_str = cand.raw_strategy.strip()
        _meta = {
            "namespace": ns_normalized,
            "pnl_units": config.pnl_units,
            "original_side": cand.record.get("side"),
            "original_pnl_net": cand.pnl_net,
            "analysis_timezone": cand.analysis_tz_name,
            "outcome": cand.normalised_outcome,
            "content_fingerprint": cand.content_fingerprint,
            "identity_schema_version": _IDENTITY_SCHEMA_VERSION,
        }
        if config.pnl_units == "monetary":
            _meta["quantity"] = config.quantity
            _meta["dollar_per_point"] = config.dollar_per_point
        if cand.normalised_outcome is None:
            _meta["source_dataset"] = config.source_dataset
            _meta["outcomes_finalized"] = config.outcomes_finalized
        accepted.append(
            Trade(
                r_result=cand.r_result,
                trade_id=cand.trade_id,
                timestamp=cand.ts,
                date=cand.date_str,
                asset=asset_str,
                direction=cand.direction,  # type: ignore[arg-type]
                entry_price=cand.entry,
                stop_price=cand.stop,
                exit_price=cand.exit_price,
                strategy=strategy_str,
                metadata=_meta,
            )
        )

    return ConversionResult(
        trades=tuple(accepted),
        errors=tuple(errors),
        warnings=tuple(warnings),
        total_records=len(records),
        accepted=len(accepted),
        rejected=len(records) - len(accepted) - dedup_count,
        deduplicated=dedup_count,
        symbol_used=resolved_symbol,
        pnl_units_used=config.pnl_units,
    )


__all__ = [
    "ConversionConfig",
    "ConversionError",
    "ConversionResult",
    "ConversionWarning",
    "convert_trades",
]


"""Provider-neutral monetary account records for FARS 1.2 Phase 11A.

This module deliberately does not reuse :class:`src.types.Trade`.  Core trades
represent completed analytical outcomes in R-multiples; account trades preserve
currency P&L, costs, and the evidence needed to decide whether an R value can be
used or derived.

The CSV adapter accepts only rows explicitly declared to be completed round
trips.  Fill aggregation, partial fills, reversals, and provider-specific order
semantics remain unsupported until their deterministic contracts are approved.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, DecimalException, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from src.ingestion import AuditIssue, CapabilityStatus, validate_csv_delimiter


Direction = Literal["long", "short"]
RValueOrigin = Literal["supplied", "derived"]
CostReconciliation = Literal["verified", "not_evaluable", "failed"]

ACCOUNT_TRADE_SCHEMA_VERSION = "1.2-phase11a-account-trade-v1"
R_DERIVATION_VERSION = "fars-1.2-phase11a-r-v1"
R_DERIVATION_FORMULA = "r_result = net_pnl / initial_risk_amount"
R_DERIVATION_SOURCE_FIELDS = ("net_pnl", "initial_risk_amount")
ROW_SEMANTICS_COMPLETED_ROUND_TRIP = "completed_round_trip"

ACCOUNT_CANONICAL_INPUT_FIELDS = frozenset(
    {
        "trade_id",
        "opened_at",
        "closed_at",
        "instrument",
        "contract_multiplier",
        "direction",
        "quantity",
        "entry_price",
        "exit_price",
        "currency",
        "gross_pnl",
        "commission",
        "exchange_fees",
        "other_fees",
        "net_pnl",
        "initial_risk_amount",
        "r_result",
        "strategy",
    }
)

_CAPABILITIES = (
    "account_pnl",
    "cost_reconciliation",
    "r_multiple_analysis",
    "closed_trade_replay",
    "intraday_rule_replay",
)
_COST_FIELDS = ("commission", "exchange_fees", "other_fees")
_SIGNED_MONEY_FIELDS = ("gross_pnl", "net_pnl")
_POSITIVE_FIELDS = ("contract_multiplier", "quantity", "initial_risk_amount")
_PRICE_FIELDS = ("entry_price", "exit_price")
_CREDENTIAL_HEADER_MARKERS = (
    "apikey",
    "accesskey",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "oauth",
    "password",
    "passwd",
    "privatekey",
    "secret",
    "sessionid",
    "token",
)


class AccountTradeDataError(ValueError):
    """Raised when monetary account data are structurally unsafe to interpret."""


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _validate_digest(value: str, name: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def _immutable_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


def _deep_immutable(value: Any) -> Any:
    """Recursively freeze nested mappings/sequences so a frozen record cannot
    be mutated through its metadata.

    ``_immutable_mapping`` wraps only the outer mapping; nested dicts remain
    plain mutable dicts, so a frozen :class:`CanonicalAccountTrade` or
    :class:`AccountEquityEvent` could be silently rewritten through its
    ``metadata``. Deep-freeze so no caller can mutate history after the fact.
    """
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _deep_immutable(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_immutable(item) for item in value)
    return value


def _validate_decimal(value: Decimal | None, name: str) -> None:
    if value is not None and (
        not isinstance(value, Decimal) or not value.is_finite()
    ):
        raise ValueError(f"{name} must be a finite Decimal or None")


@dataclass(frozen=True)
class RValueProvenance:
    """Origin and formula disclosure for one R-multiple value."""

    origin: RValueOrigin
    source_fields: tuple[str, ...]
    formula: str | None = None
    method_version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_fields", tuple(self.source_fields))
        if self.origin not in ("supplied", "derived"):
            raise ValueError(f"unsupported R origin {self.origin!r}")
        if not self.source_fields:
            raise ValueError("R provenance requires at least one source field")
        if self.origin == "derived":
            if self.source_fields != R_DERIVATION_SOURCE_FIELDS:
                raise ValueError("derived R provenance has unsupported source fields")
            if self.formula != R_DERIVATION_FORMULA:
                raise ValueError("derived R provenance requires the approved formula")
            if self.method_version != R_DERIVATION_VERSION:
                raise ValueError(
                    "derived R provenance requires the approved method version"
                )
        else:
            if self.source_fields != ("r_result",):
                raise ValueError("supplied R provenance must identify r_result")
            if self.formula is not None:
                raise ValueError("supplied R provenance must not claim a formula")
            if self.method_version is not None:
                raise ValueError(
                    "supplied R provenance must not claim a method version"
                )


@dataclass(frozen=True)
class AccountRecordFingerprint:
    """Trace one normalized record back to its decoded provider row."""

    source_row_number: int
    raw_row_sha256: str
    normalized_record_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source_row_number, int)
            or isinstance(self.source_row_number, bool)
            or self.source_row_number < 2
        ):
            raise ValueError("source_row_number must identify a CSV data row")
        _validate_digest(self.raw_row_sha256, "raw_row_sha256")
        _validate_digest(self.normalized_record_sha256, "normalized_record_sha256")


@dataclass(frozen=True)
class CanonicalAccountTrade:
    """One provider-neutral, completed monetary round trip.

    Currency amounts use :class:`~decimal.Decimal` in ``currency`` units; no
    binary-float conversion is introduced. Prices remain instrument price
    units, quantity is contracts, and ``contract_multiplier`` is currency per
    price unit per contract. ``r_result`` is dimensionless and always paired
    with explicit provenance. Optional fields remain absent instead of
    receiving invented zeroes.
    """

    currency: str
    fingerprint: AccountRecordFingerprint

    trade_id: str = ""
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    instrument: str = ""
    contract_multiplier: Decimal | None = None
    direction: Direction | None = None
    quantity: Decimal | None = None
    entry_price: Decimal | None = None
    exit_price: Decimal | None = None
    gross_pnl: Decimal | None = None
    commission: Decimal | None = None
    exchange_fees: Decimal | None = None
    other_fees: Decimal | None = None
    net_pnl: Decimal | None = None
    initial_risk_amount: Decimal | None = None
    r_result: Decimal | None = None
    r_provenance: RValueProvenance | None = None
    strategy: str = ""
    cost_reconciliation: CostReconciliation = "not_evaluable"
    cost_reconciliation_tolerance: Decimal = Decimal("0")
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise ValueError("currency must be an explicit non-empty string")
        object.__setattr__(self, "currency", self.currency.strip().upper())
        for name in (
            "contract_multiplier",
            "quantity",
            "entry_price",
            "exit_price",
            "gross_pnl",
            "commission",
            "exchange_fees",
            "other_fees",
            "net_pnl",
            "initial_risk_amount",
            "r_result",
            "cost_reconciliation_tolerance",
        ):
            _validate_decimal(getattr(self, name), name)
        if not isinstance(self.cost_reconciliation_tolerance, Decimal):
            raise ValueError("cost_reconciliation_tolerance must be a Decimal")
        for name in _POSITIVE_FIELDS:
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be strictly positive when supplied")
        for name in _COST_FIELDS:
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be a non-negative magnitude")
        for name in ("opened_at", "closed_at"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, datetime) or not _is_aware(value)
            ):
                raise ValueError(f"{name} must be timezone-aware when supplied")
        if (
            self.opened_at is not None
            and self.closed_at is not None
            and self.opened_at > self.closed_at
        ):
            raise ValueError("opened_at must not be after closed_at")
        if self.direction not in (None, "long", "short"):
            raise ValueError("direction must be 'long', 'short', or None")
        if self.cost_reconciliation not in (
            "verified",
            "not_evaluable",
            "failed",
        ):
            raise ValueError("unsupported cost_reconciliation state")
        if self.cost_reconciliation_tolerance < 0:
            raise ValueError("cost_reconciliation_tolerance must be non-negative")
        reconciliation_values = (
            self.gross_pnl,
            self.commission,
            self.exchange_fees,
            self.other_fees,
            self.net_pnl,
        )
        reconciliation_complete = all(
            value is not None for value in reconciliation_values
        )
        if reconciliation_complete:
            assert all(value is not None for value in reconciliation_values)
            expected_net = (
                self.gross_pnl
                - self.commission
                - self.exchange_fees
                - self.other_fees
            )
            difference = abs(self.net_pnl - expected_net)
            expected_state = (
                "verified"
                if difference <= self.cost_reconciliation_tolerance
                else "failed"
            )
            if self.cost_reconciliation != expected_state:
                raise ValueError(
                    "cost_reconciliation does not match the monetary identity"
                )
        elif self.cost_reconciliation != "not_evaluable":
            raise ValueError(
                "cost_reconciliation requires gross, net, and every cost field"
            )
        if (self.r_result is None) != (self.r_provenance is None):
            raise ValueError("r_result and r_provenance must be present together")
        if self.r_provenance is not None and not isinstance(
            self.r_provenance, RValueProvenance
        ):
            raise ValueError("r_provenance must be RValueProvenance or None")
        if self.r_provenance is not None and self.r_provenance.origin == "derived":
            if (
                self.cost_reconciliation != "verified"
                or self.net_pnl is None
                or self.initial_risk_amount is None
            ):
                raise ValueError(
                    "derived R requires reconciled net_pnl and initial_risk_amount"
                )
            try:
                expected_r = self.net_pnl / self.initial_risk_amount
            except DecimalException as exc:
                raise ValueError("derived R calculation failed") from exc
            if not expected_r.is_finite() or self.r_result != expected_r:
                raise ValueError(
                    "derived r_result does not equal net_pnl / initial_risk_amount"
                )
        if not isinstance(self.fingerprint, AccountRecordFingerprint):
            raise ValueError("fingerprint must be AccountRecordFingerprint")
        expected_fingerprint = _sha256_json(
            _normalized_payload(_normalized_trade_values(self))
        )
        if self.fingerprint.normalized_record_sha256 != expected_fingerprint:
            raise ValueError("normalized record fingerprint does not match trade content")
        object.__setattr__(self, "metadata", _deep_immutable(self.metadata))


@dataclass(frozen=True)
class AccountEquityEvent:
    """Optional observed equity state for future intraday rule replay.

    ``equity`` and ``balance`` are amounts in the explicitly named ``currency``.
    """

    event_id: str
    source: str
    timestamp: datetime
    currency: str
    equity: Decimal
    balance: Decimal | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id must be non-empty")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be non-empty")
        if not isinstance(self.timestamp, datetime) or not _is_aware(self.timestamp):
            raise ValueError("timestamp must be timezone-aware")
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise ValueError("currency must be explicit")
        object.__setattr__(self, "currency", self.currency.strip().upper())
        _validate_decimal(self.equity, "equity")
        if not isinstance(self.equity, Decimal):
            raise ValueError("equity must be a finite Decimal")
        _validate_decimal(self.balance, "balance")
        object.__setattr__(self, "metadata", _deep_immutable(self.metadata))


@dataclass(frozen=True)
class AccountTradeAuditReport:
    """Immutable Phase 11A row-level validation summary."""

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
class AccountTradeIngestionProvenance:
    """Source and adaptation settings for a monetary trade CSV."""

    schema_version: str
    source_path: str
    source_sha256: str
    source_size_bytes: int
    resolved_mapping: Mapping[str, str]
    row_semantics: str
    currency_tolerance: Decimal
    delimiter: str
    encoding: str

    def __post_init__(self) -> None:
        if self.schema_version != ACCOUNT_TRADE_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {self.schema_version!r}")
        _validate_digest(self.source_sha256, "source_sha256")
        if (
            not isinstance(self.source_size_bytes, int)
            or isinstance(self.source_size_bytes, bool)
            or self.source_size_bytes < 0
        ):
            raise ValueError("source_size_bytes must be a non-negative integer")
        if self.row_semantics != ROW_SEMANTICS_COMPLETED_ROUND_TRIP:
            raise ValueError("only completed_round_trip row semantics are supported")
        _validate_decimal(self.currency_tolerance, "currency_tolerance")
        if not isinstance(self.currency_tolerance, Decimal):
            raise ValueError("currency_tolerance must be a Decimal")
        if self.currency_tolerance < 0:
            raise ValueError("currency_tolerance must be non-negative")
        object.__setattr__(
            self, "resolved_mapping", _immutable_mapping(self.resolved_mapping)
        )


@dataclass(frozen=True)
class CanonicalAccountTradeDataset:
    """Audited monetary trades with explicit analysis capabilities."""

    trades: tuple[CanonicalAccountTrade, ...]
    audit: AccountTradeAuditReport
    capabilities: Mapping[str, CapabilityStatus]
    provenance: AccountTradeIngestionProvenance
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "trades", tuple(self.trades))
        normalized = dict(self.capabilities)
        if set(normalized) != set(_CAPABILITIES):
            raise ValueError(f"capabilities must be exactly {_CAPABILITIES!r}")
        object.__setattr__(self, "capabilities", _immutable_mapping(normalized))
        if len(self.trades) != self.audit.accepted_rows:
            raise ValueError("trade count must equal audit.accepted_rows")

    def require_capability(self, name: str) -> None:
        try:
            status = self.capabilities[name]
        except KeyError as exc:
            raise ValueError(f"unknown capability {name!r}") from exc
        if not status.available:
            raise AccountTradeDataError(
                f"capability {name!r} is unavailable: " + "; ".join(status.reasons)
            )


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_fingerprint(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise AccountTradeDataError(f"cannot fingerprint CSV {path}: {exc}") from exc
    return digest.hexdigest(), size


def _validate_mapping(
    headers: list[str], column_mapping: Mapping[str, str] | None
) -> dict[str, str]:
    if column_mapping is not None and not isinstance(column_mapping, Mapping):
        raise AccountTradeDataError("column_mapping must be a mapping or None")
    supplied = {} if column_mapping is None else dict(column_mapping)
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in supplied.items()
    ):
        raise AccountTradeDataError("column_mapping keys and values must be strings")
    missing_sources = sorted(set(supplied) - set(headers))
    if missing_sources:
        raise AccountTradeDataError(
            f"mapped source columns are missing: {missing_sources!r}"
        )
    unsupported = sorted(
        set(supplied.values()) - ACCOUNT_CANONICAL_INPUT_FIELDS
    )
    if unsupported:
        raise AccountTradeDataError(
            f"unsupported canonical mapping targets: {unsupported!r}"
        )
    resolved = dict(supplied)
    for header in headers:
        if header in ACCOUNT_CANONICAL_INPUT_FIELDS and header not in resolved:
            resolved[header] = header
    targets = list(resolved.values())
    collisions = sorted({target for target in targets if targets.count(target) > 1})
    if collisions:
        raise AccountTradeDataError(
            f"multiple source columns map to: {collisions!r}"
        )
    if "currency" not in resolved.values():
        raise AccountTradeDataError(
            "no source column maps to required canonical field 'currency'"
        )
    return resolved


def _reject_credential_headers(headers: list[str]) -> None:
    prohibited = sorted(
        header
        for header in headers
        if any(
            marker
            in "".join(
                character.lower() for character in header if character.isalnum()
            )
            for marker in _CREDENTIAL_HEADER_MARKERS
        )
    )
    if prohibited:
        raise AccountTradeDataError(
            "CSV contains prohibited credential columns; remove them before ingestion: "
            f"{prohibited!r}"
        )


def _parse_decimal(
    raw: str,
    *,
    field_name: str,
    row_number: int,
    issues: list[AuditIssue],
    invalid_metadata: dict[str, str],
    strictly_positive: bool = False,
    non_negative: bool = False,
) -> Decimal | None:
    if raw.strip() == "":
        return None
    try:
        value = Decimal(raw.strip())
    except InvalidOperation:
        value = Decimal("NaN")
    invalid = not value.is_finite()
    if not invalid and strictly_positive and value <= 0:
        invalid = True
    if not invalid and non_negative and value < 0:
        invalid = True
    if invalid:
        requirement = "finite"
        if strictly_positive:
            requirement = "finite and strictly positive"
        elif non_negative:
            requirement = "a finite non-negative magnitude"
        issues.append(
            AuditIssue(
                "warning",
                "INVALID_OPTIONAL_MONETARY_VALUE",
                f"{field_name} must be {requirement} and was omitted",
                row_number,
                field_name,
            )
        )
        invalid_metadata[field_name] = raw
        return None
    return value


def _parse_timestamp(
    raw: str,
    *,
    field_name: str,
    row_number: int,
    issues: list[AuditIssue],
    invalid_metadata: dict[str, str],
) -> datetime | None:
    if raw.strip() == "":
        return None
    normalized = raw.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        value = datetime.fromisoformat(normalized)
    except ValueError:
        value = None
    if value is None or not _is_aware(value):
        issues.append(
            AuditIssue(
                "warning",
                "INVALID_ACCOUNT_TIMESTAMP",
                f"{field_name} must be a timezone-aware ISO-8601 timestamp and was omitted",
                row_number,
                field_name,
            )
        )
        invalid_metadata[field_name] = raw
        return None
    return value


def _normalized_payload(values: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for name, value in values.items():
        if isinstance(value, Decimal):
            normalized[name] = str(value)
        elif isinstance(value, datetime):
            normalized[name] = value.isoformat()
        elif isinstance(value, RValueProvenance):
            normalized[name] = {
                "origin": value.origin,
                "source_fields": list(value.source_fields),
                "formula": value.formula,
                "method_version": value.method_version,
            }
        else:
            normalized[name] = value
    return normalized


def _normalized_trade_values(trade: CanonicalAccountTrade) -> dict[str, Any]:
    return {
        "currency": trade.currency,
        "trade_id": trade.trade_id,
        "opened_at": trade.opened_at,
        "closed_at": trade.closed_at,
        "instrument": trade.instrument,
        "contract_multiplier": trade.contract_multiplier,
        "direction": trade.direction,
        "quantity": trade.quantity,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "gross_pnl": trade.gross_pnl,
        "commission": trade.commission,
        "exchange_fees": trade.exchange_fees,
        "other_fees": trade.other_fees,
        "net_pnl": trade.net_pnl,
        "initial_risk_amount": trade.initial_risk_amount,
        "r_result": trade.r_result,
        "r_provenance": trade.r_provenance,
        "strategy": trade.strategy,
        "cost_reconciliation": trade.cost_reconciliation,
        "cost_reconciliation_tolerance": trade.cost_reconciliation_tolerance,
    }


def _currency_tolerance(value: Decimal | str) -> Decimal:
    if isinstance(value, float) or isinstance(value, bool):
        raise AccountTradeDataError(
            "currency_tolerance must be Decimal or str, not binary float"
        )
    try:
        result = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AccountTradeDataError("currency_tolerance is not a valid decimal") from exc
    if not result.is_finite() or result < 0:
        raise AccountTradeDataError(
            "currency_tolerance must be finite and non-negative"
        )
    return result


def _capability(available: bool, reasons: list[str]) -> CapabilityStatus:
    return CapabilityStatus(available, () if available else tuple(reasons))


def load_account_trade_csv(
    path: str | Path,
    *,
    row_semantics: str,
    column_mapping: Mapping[str, str] | None = None,
    currency_tolerance: Decimal | str = Decimal("0"),
    delimiter: str = ",",
    encoding: str = "utf-8-sig",
) -> CanonicalAccountTradeDataset:
    """Load completed monetary round trips without provider-specific guesses.

    ``row_semantics`` must explicitly be ``"completed_round_trip"``.  Other
    values, including fills, partial fills, orders, and reversals, are refused
    because Phase 11A has no approved aggregation contract for them.

    A missing ``r_result`` is derived only when net P&L has been reconciled from
    complete gross/cost fields and ``initial_risk_amount`` is strictly positive.
    """

    if row_semantics != ROW_SEMANTICS_COMPLETED_ROUND_TRIP:
        raise AccountTradeDataError(
            "Phase 11A accepts only row_semantics='completed_round_trip'; "
            "fill/order/partial-fill/reversal reconstruction is unsupported"
        )
    tolerance = _currency_tolerance(currency_tolerance)
    try:
        delimiter = validate_csv_delimiter(delimiter)
    except ValueError as exc:
        raise AccountTradeDataError(str(exc)) from exc

    source_path = Path(path)
    initial_sha256, initial_size = _file_fingerprint(source_path)
    try:
        handle = source_path.open("r", encoding=encoding, newline="")
    except (LookupError, OSError, UnicodeError) as exc:
        raise AccountTradeDataError(f"cannot read CSV {source_path}: {exc}") from exc

    with handle:
        reader = csv.reader(handle, delimiter=delimiter, strict=True)
        try:
            headers = next(reader)
        except StopIteration as exc:
            raise AccountTradeDataError("CSV is empty and has no header") from exc
        except (csv.Error, UnicodeError) as exc:
            raise AccountTradeDataError(f"cannot decode CSV header: {exc}") from exc
        if not headers or any(header == "" for header in headers):
            raise AccountTradeDataError("CSV headers must be non-empty")
        duplicates = sorted(
            {header for header in headers if headers.count(header) > 1}
        )
        if duplicates:
            raise AccountTradeDataError(
                f"CSV contains duplicate headers: {duplicates!r}"
            )
        _reject_credential_headers(headers)

        resolved_mapping = _validate_mapping(headers, column_mapping)
        canonical_to_source = {
            canonical: source for source, canonical in resolved_mapping.items()
        }
        unknown_headers = [header for header in headers if header not in resolved_mapping]
        issues: list[AuditIssue] = []
        trades: list[CanonicalAccountTrade] = []
        seen_ids: dict[str, int] = {}
        duplicate_ids = False
        rejected_rows = 0
        total_rows = 0
        chronological = True
        temporal_relationships_valid = True
        previous_closed_at: datetime | None = None

        try:
            for values in reader:
                row_number = reader.line_num
                total_rows += 1
                if len(values) != len(headers):
                    rejected_rows += 1
                    issues.append(
                        AuditIssue(
                            "error",
                            "ACCOUNT_COLUMN_COUNT_MISMATCH",
                            f"row has {len(values)} values but header has {len(headers)}",
                            row_number,
                        )
                    )
                    continue
                raw_row = dict(zip(headers, values, strict=True))
                currency = raw_row[canonical_to_source["currency"]].strip()
                if not currency:
                    rejected_rows += 1
                    issues.append(
                        AuditIssue(
                            "error",
                            "MISSING_CURRENCY",
                            "currency is required for every monetary record",
                            row_number,
                            "currency",
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
                                "DUPLICATE_ACCOUNT_TRADE_ID",
                                f"trade_id duplicates source row {seen_ids[trade_id]}",
                                row_number,
                                "trade_id",
                            )
                        )
                    else:
                        seen_ids[trade_id] = row_number

                opened_at = _parse_timestamp(
                    canonical_raw.get("opened_at", ""),
                    field_name="opened_at",
                    row_number=row_number,
                    issues=issues,
                    invalid_metadata=invalid_metadata,
                )
                closed_at = _parse_timestamp(
                    canonical_raw.get("closed_at", ""),
                    field_name="closed_at",
                    row_number=row_number,
                    issues=issues,
                    invalid_metadata=invalid_metadata,
                )
                if (
                    opened_at is not None
                    and closed_at is not None
                    and opened_at > closed_at
                ):
                    issues.append(
                        AuditIssue(
                            "warning",
                            "ACCOUNT_TRADE_TIME_REVERSED",
                            "opened_at is after closed_at; replay is unavailable",
                            row_number,
                            "opened_at",
                        )
                    )
                    temporal_relationships_valid = False
                    invalid_metadata["temporal_relationship"] = "opened_at_after_closed_at"
                    opened_at = None
                if (
                    closed_at is not None
                    and previous_closed_at is not None
                    and closed_at < previous_closed_at
                ):
                    chronological = False
                    issues.append(
                        AuditIssue(
                            "warning",
                            "NON_CHRONOLOGICAL_ACCOUNT_TRADE",
                            "closed_at precedes the previous accepted row; input was not reordered",
                            row_number,
                            "closed_at",
                        )
                    )
                if closed_at is not None:
                    previous_closed_at = closed_at

                direction_raw = canonical_raw.get("direction", "").strip().lower()
                direction: Direction | None = None
                if direction_raw:
                    if direction_raw in ("long", "short"):
                        direction = direction_raw  # type: ignore[assignment]
                    else:
                        issues.append(
                            AuditIssue(
                                "warning",
                                "INVALID_ACCOUNT_DIRECTION",
                                "direction must be 'long' or 'short' and was omitted",
                                row_number,
                                "direction",
                            )
                        )
                        invalid_metadata["direction"] = canonical_raw["direction"]

                decimals: dict[str, Decimal | None] = {}
                for name in _SIGNED_MONEY_FIELDS + _PRICE_FIELDS:
                    decimals[name] = _parse_decimal(
                        canonical_raw.get(name, ""),
                        field_name=name,
                        row_number=row_number,
                        issues=issues,
                        invalid_metadata=invalid_metadata,
                    )
                for name in _COST_FIELDS:
                    decimals[name] = _parse_decimal(
                        canonical_raw.get(name, ""),
                        field_name=name,
                        row_number=row_number,
                        issues=issues,
                        invalid_metadata=invalid_metadata,
                        non_negative=True,
                    )
                for name in _POSITIVE_FIELDS:
                    decimals[name] = _parse_decimal(
                        canonical_raw.get(name, ""),
                        field_name=name,
                        row_number=row_number,
                        issues=issues,
                        invalid_metadata=invalid_metadata,
                        strictly_positive=True,
                    )
                supplied_r_raw = canonical_raw.get("r_result", "")
                supplied_r = _parse_decimal(
                    supplied_r_raw,
                    field_name="r_result",
                    row_number=row_number,
                    issues=issues,
                    invalid_metadata=invalid_metadata,
                )

                reconciliation: CostReconciliation = "not_evaluable"
                reconciliation_values = [
                    decimals["gross_pnl"],
                    decimals["commission"],
                    decimals["exchange_fees"],
                    decimals["other_fees"],
                    decimals["net_pnl"],
                ]
                if all(value is not None for value in reconciliation_values):
                    gross = decimals["gross_pnl"]
                    commission = decimals["commission"]
                    exchange_fees = decimals["exchange_fees"]
                    other_fees = decimals["other_fees"]
                    net = decimals["net_pnl"]
                    assert all(
                        value is not None
                        for value in (gross, commission, exchange_fees, other_fees, net)
                    )
                    expected_net = gross - commission - exchange_fees - other_fees
                    difference = abs(net - expected_net)
                    if difference <= tolerance:
                        reconciliation = "verified"
                    else:
                        reconciliation = "failed"
                        issues.append(
                            AuditIssue(
                                "warning",
                                "NET_PNL_RECONCILIATION_FAILED",
                                "net_pnl differs from gross_pnl minus costs "
                                "beyond currency_tolerance",
                                row_number,
                                "net_pnl",
                            )
                        )

                r_result = supplied_r
                r_provenance: RValueProvenance | None = None
                if supplied_r is not None:
                    r_provenance = RValueProvenance("supplied", ("r_result",))
                elif supplied_r_raw.strip() == "":
                    net = decimals["net_pnl"]
                    initial_risk = decimals["initial_risk_amount"]
                    if (
                        reconciliation == "verified"
                        and net is not None
                        and initial_risk is not None
                    ):
                        try:
                            candidate_r = net / initial_risk
                        except DecimalException:
                            candidate_r = Decimal("NaN")
                        if candidate_r.is_finite():
                            r_result = candidate_r
                            r_provenance = RValueProvenance(
                                "derived",
                                R_DERIVATION_SOURCE_FIELDS,
                                R_DERIVATION_FORMULA,
                                R_DERIVATION_VERSION,
                            )

                unknown = {
                    header: raw_row[header] for header in unknown_headers
                }
                metadata = {
                    "unknown_fields": MappingProxyType(unknown),
                    "invalid_optional_fields": MappingProxyType(invalid_metadata),
                }
                normalized_values: dict[str, Any] = {
                    "currency": currency.strip().upper(),
                    "trade_id": trade_id,
                    "opened_at": opened_at,
                    "closed_at": closed_at,
                    "instrument": canonical_raw.get("instrument", "").strip(),
                    "contract_multiplier": decimals["contract_multiplier"],
                    "direction": direction,
                    "quantity": decimals["quantity"],
                    "entry_price": decimals["entry_price"],
                    "exit_price": decimals["exit_price"],
                    "gross_pnl": decimals["gross_pnl"],
                    "commission": decimals["commission"],
                    "exchange_fees": decimals["exchange_fees"],
                    "other_fees": decimals["other_fees"],
                    "net_pnl": decimals["net_pnl"],
                    "initial_risk_amount": decimals["initial_risk_amount"],
                    "r_result": r_result,
                    "r_provenance": r_provenance,
                    "strategy": canonical_raw.get("strategy", "").strip(),
                    "cost_reconciliation": reconciliation,
                    "cost_reconciliation_tolerance": tolerance,
                }
                raw_payload = [[header, raw_row[header]] for header in headers]
                fingerprint = AccountRecordFingerprint(
                    source_row_number=row_number,
                    raw_row_sha256=_sha256_json(raw_payload),
                    normalized_record_sha256=_sha256_json(
                        _normalized_payload(normalized_values)
                    ),
                )
                trades.append(
                    CanonicalAccountTrade(
                        **normalized_values,
                        fingerprint=fingerprint,
                        metadata=metadata,
                    )
                )
        except (csv.Error, UnicodeError) as exc:
            raise AccountTradeDataError(
                f"malformed CSV near source line {reader.line_num}: {exc}"
            ) from exc

    if total_rows == 0:
        issues.append(
            AuditIssue(
                "error",
                "NO_ACCOUNT_TRADE_ROWS",
                "CSV contains a header but no account trade rows",
            )
        )

    accepted_rows = len(trades)
    base_reasons: list[str] = []
    if accepted_rows == 0:
        base_reasons.append("no accepted account trades")
    if rejected_rows:
        base_reasons.append("one or more source rows were rejected")
    if duplicate_ids:
        base_reasons.append("duplicate non-empty trade_id values require review")

    currencies = {trade.currency for trade in trades}
    pnl_reasons = list(base_reasons)
    if any(trade.net_pnl is None for trade in trades):
        pnl_reasons.append("every account trade needs explicit net_pnl")
    if any(trade.cost_reconciliation == "failed" for trade in trades):
        pnl_reasons.append("one or more net P&L cost identities failed")
    if len(currencies) > 1:
        pnl_reasons.append("account P&L cannot combine multiple currencies")

    cost_reasons = list(base_reasons)
    if any(trade.cost_reconciliation != "verified" for trade in trades):
        cost_reasons.append(
            "every account trade needs gross_pnl, net_pnl, and all cost fields reconciled"
        )

    r_reasons = list(base_reasons)
    if any(trade.r_result is None for trade in trades):
        r_reasons.append("every account trade needs a supplied or safely derived r_result")
    r_origins = {
        trade.r_provenance.origin
        for trade in trades
        if trade.r_provenance is not None
    }
    if len(r_origins) > 1:
        r_reasons.append("supplied and derived R values cannot be mixed automatically")

    replay_reasons = list(pnl_reasons)
    if any(not trade.trade_id for trade in trades):
        replay_reasons.append("every account trade needs a non-empty trade_id for replay")
    if any(trade.closed_at is None for trade in trades):
        replay_reasons.append(
            "every account trade needs a timezone-aware closed_at for replay"
        )
    if not chronological:
        replay_reasons.append("closed_at values are not non-decreasing in source order")
    if not temporal_relationships_valid:
        replay_reasons.append("one or more opened_at values occur after closed_at")

    intraday_reasons = [
        "closed-trade CSV data cannot reconstruct intraday equity high-water marks"
    ]
    capabilities = {
        "account_pnl": _capability(not pnl_reasons, pnl_reasons),
        "cost_reconciliation": _capability(not cost_reasons, cost_reasons),
        "r_multiple_analysis": _capability(not r_reasons, r_reasons),
        "closed_trade_replay": _capability(not replay_reasons, replay_reasons),
        "intraday_rule_replay": _capability(False, intraday_reasons),
    }
    audit = AccountTradeAuditReport(
        total_rows=total_rows,
        accepted_rows=accepted_rows,
        rejected_rows=rejected_rows,
        issues=tuple(issues),
    )
    final_sha256, final_size = _file_fingerprint(source_path)
    if (final_sha256, final_size) != (initial_sha256, initial_size):
        raise AccountTradeDataError(
            "CSV changed while it was being loaded; retry the ingestion"
        )
    provenance = AccountTradeIngestionProvenance(
        schema_version=ACCOUNT_TRADE_SCHEMA_VERSION,
        source_path=str(source_path.resolve()),
        source_sha256=final_sha256,
        source_size_bytes=final_size,
        resolved_mapping=resolved_mapping,
        row_semantics=row_semantics,
        currency_tolerance=tolerance,
        delimiter=delimiter,
        encoding=encoding,
    )
    return CanonicalAccountTradeDataset(
        trades=tuple(trades),
        audit=audit,
        capabilities=capabilities,
        provenance=provenance,
        source=str(source_path),
    )

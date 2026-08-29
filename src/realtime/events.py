"""Canonical realtime event contracts (RT-0).

Market events are not Core ``Trade`` records. ``Trade.r_result`` remains a
completed analytical outcome; ticks, quotes, bars, and exchange prints stay
in this module.

Events are frozen. Live and replay share these types; origin is provenance,
not a separate pipeline.

Timestamp policy (RT-0):
- ``timestamp`` is source event time: provider event time when live, recorded
  event time when replay. It is not local receipt time.
- Receipt time is not a canonical RT-0 field.
- ``AccountSnapshot.broker_timestamp`` is the provider account-state time when
  the provider supplies one distinct from ``timestamp``.
- ``AccountSnapshot.last_sync`` is the last successful account-sync time.
- Those clocks are never silently treated as the same instant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Literal, Union

ORIGIN_LIVE = "live"
ORIGIN_REPLAY = "replay"
_ALLOWED_ORIGIN = {ORIGIN_LIVE, ORIGIN_REPLAY}

SIGNAL_LONG = "LONG"
SIGNAL_SHORT = "SHORT"
SIGNAL_FLAT = "FLAT"
_ALLOWED_SIGNAL_ACTIONS = {SIGNAL_LONG, SIGNAL_SHORT, SIGNAL_FLAT}

EXEC_ACCEPTED = "accepted"
EXEC_REJECTED = "rejected"
EXEC_FILLED = "filled"
EXEC_CANCELLED = "cancelled"
EXEC_PARTIAL = "partial"
_ALLOWED_EXEC_STATUS = {
    EXEC_ACCEPTED,
    EXEC_REJECTED,
    EXEC_FILLED,
    EXEC_CANCELLED,
    EXEC_PARTIAL,
}

SYSTEM_CONNECTOR_DISCONNECTED = "connector_disconnected"
SYSTEM_CONNECTOR_RECONNECTED = "connector_reconnected"
SYSTEM_STALE_MARKET_DATA = "stale_market_data"
SYSTEM_SEQUENCE_GAP = "sequence_gap"
SYSTEM_RECONCILIATION_MISMATCH = "reconciliation_mismatch"
SYSTEM_CIRCUIT_BREAKER = "circuit_breaker_triggered"
SYSTEM_HALTED = "system_halted"
_ALLOWED_SYSTEM_KINDS = {
    SYSTEM_CONNECTOR_DISCONNECTED,
    SYSTEM_CONNECTOR_RECONNECTED,
    SYSTEM_STALE_MARKET_DATA,
    SYSTEM_SEQUENCE_GAP,
    SYSTEM_RECONCILIATION_MISMATCH,
    SYSTEM_CIRCUIT_BREAKER,
    SYSTEM_HALTED,
}


def _require_non_empty_str(name: str, value: object) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")
    return value


def _require_aware_datetime(name: str, value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware, got naive {value!r}")
    return value


def _require_sequence(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"sequence must be a non-negative integer, got {value!r}")
    return value


def _require_origin(value: object) -> str:
    if value not in _ALLOWED_ORIGIN:
        raise ValueError(
            f"origin must be one of {sorted(_ALLOWED_ORIGIN)}, got {value!r}"
        )
    return value  # type: ignore[return-value]


def _require_finite(
    name: str,
    value: object,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    if positive and number <= 0.0:
        raise ValueError(f"{name} must be finite and > 0, got {value!r}")
    if non_negative and number < 0.0:
        raise ValueError(f"{name} must be finite and >= 0, got {value!r}")
    return number


def _optional_aware_datetime(name: str, value: object) -> datetime | None:
    if value is None:
        return None
    return _require_aware_datetime(name, value)


def _optional_finite(name: str, value: object) -> float | None:
    if value is None:
        return None
    return _require_finite(name, value)


def _canonical_leaf(name: str, value: object) -> str | int | float:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{name} cannot contain {type(value).__name__}")
    if isinstance(value, str):
        if value.strip() == "":
            raise ValueError(f"{name} cannot contain an empty string")
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(f"{name} cannot contain a non-finite number")
        return value
    raise ValueError(
        f"{name} cannot contain provider objects, got {type(value).__name__}"
    )


def _freeze_canonical(name: str, value: object, depth: int = 0) -> tuple | str | int | float:
    if value is None:
        return ()
    if depth == 0 and not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a tuple or list, got {type(value).__name__}")
    if isinstance(value, dict):
        raise ValueError(f"{name} cannot contain mappings")
    if isinstance(value, (list, tuple)):
        if depth > 3:
            raise ValueError(f"{name} nested too deeply")
        return tuple(_freeze_canonical(name, item, depth + 1) for item in value)
    return _canonical_leaf(name, value)


def _validate_envelope(
    *,
    event_id: object,
    source: object,
    timestamp: object,
    sequence: object,
    origin: object,
) -> None:
    _require_non_empty_str("event_id", event_id)
    _require_non_empty_str("source", source)
    _require_aware_datetime("timestamp", timestamp)
    _require_sequence(sequence)
    _require_origin(origin)


def identity_key(event: object) -> tuple[str, str]:
    """Stable identity for duplicate detection: (source, event_id)."""
    event_id = getattr(event, "event_id", None)
    source = getattr(event, "source", None)
    if not isinstance(event_id, str) or not isinstance(source, str):
        raise TypeError(f"event is missing source/event_id identity: {type(event)!r}")
    return (source, event_id)


def stream_key(event: object) -> tuple[str, int]:
    """Per-source stream position: (source, sequence)."""
    source = getattr(event, "source", None)
    sequence = getattr(event, "sequence", None)
    if not isinstance(source, str) or not isinstance(sequence, int) or isinstance(
        sequence, bool
    ):
        raise TypeError(f"event is missing source/sequence: {type(event)!r}")
    return (source, sequence)


def is_authorized(decision: object) -> bool:
    """Fail-closed authorization.

    Only an explicit ``RiskDecision`` with ``approved is True`` authorizes.
    ``None``, missing decisions, and any non-decision object deny.
    Truthy non-bool values never authorize: they cannot construct a decision.
    """
    return isinstance(decision, RiskDecision) and decision.approved is True


@dataclass(frozen=True)
class MarketTick:
    """Observed market last-price update. Not a Core Trade."""

    event_id: str
    source: str
    timestamp: datetime
    sequence: int
    symbol: str
    price: float
    volume: float
    origin: Literal["live", "replay"] = ORIGIN_LIVE

    def __post_init__(self) -> None:
        _validate_envelope(
            event_id=self.event_id,
            source=self.source,
            timestamp=self.timestamp,
            sequence=self.sequence,
            origin=self.origin,
        )
        _require_non_empty_str("symbol", self.symbol)
        _require_finite("price", self.price, positive=True)
        _require_finite("volume", self.volume, non_negative=True)


@dataclass(frozen=True)
class Quote:
    """Observed bid/ask state. Crossed quotes are allowed (not reordered)."""

    event_id: str
    source: str
    timestamp: datetime
    sequence: int
    symbol: str
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    origin: Literal["live", "replay"] = ORIGIN_LIVE

    def __post_init__(self) -> None:
        _validate_envelope(
            event_id=self.event_id,
            source=self.source,
            timestamp=self.timestamp,
            sequence=self.sequence,
            origin=self.origin,
        )
        _require_non_empty_str("symbol", self.symbol)
        _require_finite("bid_price", self.bid_price, positive=True)
        _require_finite("ask_price", self.ask_price, positive=True)
        _require_finite("bid_size", self.bid_size, non_negative=True)
        _require_finite("ask_size", self.ask_size, non_negative=True)


@dataclass(frozen=True)
class Bar:
    """OHLCV interval. Timestamp policy is explicit on the event, not inferred."""

    event_id: str
    source: str
    timestamp: datetime
    sequence: int
    symbol: str
    interval: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    origin: Literal["live", "replay"] = ORIGIN_LIVE

    def __post_init__(self) -> None:
        _validate_envelope(
            event_id=self.event_id,
            source=self.source,
            timestamp=self.timestamp,
            sequence=self.sequence,
            origin=self.origin,
        )
        _require_non_empty_str("symbol", self.symbol)
        _require_non_empty_str("interval", self.interval)
        open_px = _require_finite("open", self.open, positive=True)
        high_px = _require_finite("high", self.high, positive=True)
        low_px = _require_finite("low", self.low, positive=True)
        close_px = _require_finite("close", self.close, positive=True)
        _require_finite("volume", self.volume, non_negative=True)
        if high_px < low_px:
            raise ValueError(f"high must be >= low, got high={high_px} low={low_px}")
        if high_px < open_px or high_px < close_px:
            raise ValueError("high must be >= open and close")
        if low_px > open_px or low_px > close_px:
            raise ValueError("low must be <= open and close")


@dataclass(frozen=True)
class MarketTrade:
    """Exchange/provider-reported print. Not ``src.types.Trade``."""

    event_id: str
    source: str
    timestamp: datetime
    sequence: int
    symbol: str
    price: float
    size: float
    origin: Literal["live", "replay"] = ORIGIN_LIVE

    def __post_init__(self) -> None:
        _validate_envelope(
            event_id=self.event_id,
            source=self.source,
            timestamp=self.timestamp,
            sequence=self.sequence,
            origin=self.origin,
        )
        _require_non_empty_str("symbol", self.symbol)
        _require_finite("price", self.price, positive=True)
        _require_finite("size", self.size, positive=True)


@dataclass(frozen=True)
class AccountSnapshot:
    """Externally observed account state. Not simulation ``AccountState``."""

    event_id: str
    source: str
    timestamp: datetime
    sequence: int
    origin: Literal["live", "replay"] = ORIGIN_LIVE
    balance: float | None = None
    equity: float | None = None
    realized_pnl: float | None = None
    unrealized_pnl: float | None = None
    positions: tuple = ()
    working_orders: tuple = ()
    peak_equity: float | None = None
    broker_timestamp: datetime | None = None  # provider account time, not timestamp
    last_sync: datetime | None = None  # last successful sync, not timestamp

    def __post_init__(self) -> None:
        _validate_envelope(
            event_id=self.event_id,
            source=self.source,
            timestamp=self.timestamp,
            sequence=self.sequence,
            origin=self.origin,
        )
        _optional_finite("balance", self.balance)
        _optional_finite("equity", self.equity)
        _optional_finite("realized_pnl", self.realized_pnl)
        _optional_finite("unrealized_pnl", self.unrealized_pnl)
        _optional_finite("peak_equity", self.peak_equity)
        _optional_aware_datetime("broker_timestamp", self.broker_timestamp)
        _optional_aware_datetime("last_sync", self.last_sync)
        object.__setattr__(
            self, "positions", _freeze_canonical("positions", self.positions)
        )
        object.__setattr__(
            self,
            "working_orders",
            _freeze_canonical("working_orders", self.working_orders),
        )


@dataclass(frozen=True)
class Signal:
    """Strategy proposal. Not an order and not authorization to execute."""

    event_id: str
    source: str
    timestamp: datetime
    sequence: int
    symbol: str
    action: str
    origin: Literal["live", "replay"] = ORIGIN_LIVE

    def __post_init__(self) -> None:
        _validate_envelope(
            event_id=self.event_id,
            source=self.source,
            timestamp=self.timestamp,
            sequence=self.sequence,
            origin=self.origin,
        )
        _require_non_empty_str("symbol", self.symbol)
        if self.action not in _ALLOWED_SIGNAL_ACTIONS:
            raise ValueError(
                f"action must be one of {sorted(_ALLOWED_SIGNAL_ACTIONS)}, "
                f"got {self.action!r}"
            )


@dataclass(frozen=True)
class RiskDecision:
    """Explicit risk result. Absence of this object is not approval."""

    event_id: str
    source: str
    timestamp: datetime
    sequence: int
    signal_id: str
    signal_source: str
    approved: bool
    reason: str
    origin: Literal["live", "replay"] = ORIGIN_LIVE

    def __post_init__(self) -> None:
        _validate_envelope(
            event_id=self.event_id,
            source=self.source,
            timestamp=self.timestamp,
            sequence=self.sequence,
            origin=self.origin,
        )
        _require_non_empty_str("signal_id", self.signal_id)
        _require_non_empty_str("signal_source", self.signal_source)
        _require_non_empty_str("reason", self.reason)
        if not isinstance(self.approved, bool):
            raise ValueError(
                f"approved must be an explicit bool, got {self.approved!r}"
            )


@dataclass(frozen=True)
class OrderIntent:
    """Order proposal that already has a linked risk decision identity.

    Construction does not prove approval. ``ExecutionAdapter.submit`` is the
    veto: it binds Signal identity/origin/symbol/action before execution.
    """

    event_id: str
    source: str
    timestamp: datetime
    sequence: int
    symbol: str
    action: str
    risk_decision_id: str
    origin: Literal["live", "replay"] = ORIGIN_LIVE

    def __post_init__(self) -> None:
        _validate_envelope(
            event_id=self.event_id,
            source=self.source,
            timestamp=self.timestamp,
            sequence=self.sequence,
            origin=self.origin,
        )
        _require_non_empty_str("symbol", self.symbol)
        _require_non_empty_str("risk_decision_id", self.risk_decision_id)
        if self.action not in _ALLOWED_SIGNAL_ACTIONS:
            raise ValueError(
                f"action must be one of {sorted(_ALLOWED_SIGNAL_ACTIONS)}, "
                f"got {self.action!r}"
            )


@dataclass(frozen=True)
class ExecutionReport:
    """Adapter/broker response. Duplicate reports share identity_key."""

    event_id: str
    source: str
    timestamp: datetime
    sequence: int
    order_intent_id: str
    status: str
    origin: Literal["live", "replay"] = ORIGIN_LIVE
    reason: str = ""

    def __post_init__(self) -> None:
        _validate_envelope(
            event_id=self.event_id,
            source=self.source,
            timestamp=self.timestamp,
            sequence=self.sequence,
            origin=self.origin,
        )
        _require_non_empty_str("order_intent_id", self.order_intent_id)
        if self.status not in _ALLOWED_EXEC_STATUS:
            raise ValueError(
                f"status must be one of {sorted(_ALLOWED_EXEC_STATUS)}, "
                f"got {self.status!r}"
            )
        if not isinstance(self.reason, str):
            raise ValueError(f"reason must be a string, got {self.reason!r}")


@dataclass(frozen=True)
class SystemEvent:
    """Operational event. Not a trading signal."""

    event_id: str
    source: str
    timestamp: datetime
    sequence: int
    kind: str
    origin: Literal["live", "replay"] = ORIGIN_LIVE
    detail: str = ""
    symbol: str | None = None

    def __post_init__(self) -> None:
        _validate_envelope(
            event_id=self.event_id,
            source=self.source,
            timestamp=self.timestamp,
            sequence=self.sequence,
            origin=self.origin,
        )
        if self.kind not in _ALLOWED_SYSTEM_KINDS:
            raise ValueError(
                f"kind must be one of {sorted(_ALLOWED_SYSTEM_KINDS)}, "
                f"got {self.kind!r}"
            )
        if not isinstance(self.detail, str):
            raise ValueError(f"detail must be a string, got {self.detail!r}")
        if self.symbol is not None:
            _require_non_empty_str("symbol", self.symbol)


CanonicalEvent = Union[
    MarketTick,
    Quote,
    Bar,
    MarketTrade,
    AccountSnapshot,
    Signal,
    RiskDecision,
    OrderIntent,
    ExecutionReport,
    SystemEvent,
]

"""RT-1 market-data connector: normalize external payloads to canonical events.

The first concrete adapter is a replay/historical source. A live broker
adapter is not implemented here: the spec requires confirming actual API
access before choosing Tradovate, TradeSea, or similar.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Iterator, Mapping

from src.realtime.clock import Clock
from src.realtime.events import (
    ORIGIN_REPLAY,
    SYSTEM_CONNECTOR_DISCONNECTED,
    SYSTEM_CONNECTOR_RECONNECTED,
    SYSTEM_STALE_MARKET_DATA,
    AccountSnapshot,
    Bar,
    CanonicalEvent,
    MarketTick,
    MarketTrade,
    Quote,
    SystemEvent,
)

_MARKET_TYPES = frozenset({"tick", "quote", "bar", "trade", "snapshot"})


class ConnectorError(ValueError):
    """Invalid payload or illegal connector lifecycle use."""


_SCALAR = (str, int, float, datetime, type(None))


def _copy_mapping(payload: object) -> dict:
    if not isinstance(payload, Mapping):
        raise ConnectorError(f"payload must be a mapping, got {type(payload).__name__}")
    copied: dict = {}
    for key, value in payload.items():
        if isinstance(value, bool) or isinstance(value, _SCALAR):
            copied[key] = value
            continue
        raise ConnectorError(
            f"payload field {key!r} is not a canonical scalar, got {type(value).__name__}"
        )
    return copied


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, str):
        try:
            ts = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ConnectorError(f"invalid timestamp {value!r}") from exc
    else:
        raise ConnectorError(f"timestamp must be datetime or ISO string, got {type(value).__name__}")
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ConnectorError("timestamp must be timezone-aware")
    return ts


def normalize_market_payload(
    payload: object,
    *,
    source: str,
    origin: str = ORIGIN_REPLAY,
) -> CanonicalEvent:
    """Translate a canonical-shaped mapping into a FARS market event.

    Provider-specific keys are not interpreted. Unknown types and nested
    objects are rejected so they cannot leak downstream.
    """
    data = _copy_mapping(payload)
    kind = data.pop("type", None)
    if kind not in _MARKET_TYPES:
        raise ConnectorError(f"unsupported market payload type {kind!r}")
    data.pop("source", None)
    data.pop("origin", None)
    data["source"] = source
    data["origin"] = origin
    data["timestamp"] = _parse_timestamp(data.get("timestamp"))
    try:
        if kind == "tick":
            return MarketTick(**data)
        if kind == "quote":
            return Quote(**data)
        if kind == "bar":
            return Bar(**data)
        if kind == "trade":
            return MarketTrade(**data)
        return AccountSnapshot(**data)
    except TypeError as exc:
        raise ConnectorError(f"invalid {kind} payload: {exc}") from exc
    except ValueError as exc:
        raise ConnectorError(str(exc)) from exc


class ReplayMarketConnector:
    """Historical/replay adapter. Restarts the payload stream on reconnect."""

    def __init__(
        self,
        payloads: Iterable[Mapping[str, object]],
        *,
        source: str,
        clock: Clock,
        stale_after: timedelta | None = None,
    ) -> None:
        if not isinstance(source, str) or source.strip() == "":
            raise ConnectorError("source must be a non-empty string")
        if stale_after is not None and stale_after <= timedelta(0):
            raise ConnectorError("stale_after must be positive when set")
        self._source = source
        self._clock = clock
        self._stale_after = stale_after
        self._payloads = tuple(dict(item) for item in payloads)
        self._connected = False
        self._stream: Iterator[Mapping[str, object]] | None = None
        self._pending: list[CanonicalEvent] = []
        self._sys_seq = 0
        self._ever_connected = False

    def connect(self) -> None:
        if self._connected:
            raise ConnectorError("connector already connected")
        self._connected = True
        self._stream = iter(self._payloads)
        if self._ever_connected:
            self._pending.append(self._system(SYSTEM_CONNECTOR_RECONNECTED))
        self._ever_connected = True

    def disconnect(self) -> None:
        if not self._connected:
            raise ConnectorError("connector is not connected")
        self._connected = False
        self._stream = None
        self._pending.append(self._system(SYSTEM_CONNECTOR_DISCONNECTED))

    def next_event(self) -> CanonicalEvent | None:
        if self._pending:
            return self._pending.pop(0)
        if not self._connected or self._stream is None:
            raise ConnectorError("connector is not connected")
        try:
            payload = next(self._stream)
        except StopIteration:
            return None
        event = normalize_market_payload(payload, source=self._source, origin=ORIGIN_REPLAY)
        if self._is_stale(event):
            return self._system(
                SYSTEM_STALE_MARKET_DATA,
                symbol=getattr(event, "symbol", None),
                detail=f"stale event_id={event.event_id} sequence={event.sequence}",
            )
        return event

    def _is_stale(self, event: CanonicalEvent) -> bool:
        if self._stale_after is None:
            return False
        age = self._clock.now() - event.timestamp
        return age > self._stale_after

    def _system(
        self, kind: str, symbol: str | None = None, detail: str = ""
    ) -> SystemEvent:
        self._sys_seq += 1
        return SystemEvent(
            event_id=f"{self._source}-sys-{self._sys_seq}",
            source=f"{self._source}/system",
            timestamp=self._clock.now(),
            sequence=self._sys_seq,
            kind=kind,
            origin=ORIGIN_REPLAY,
            detail=detail,
            symbol=symbol,
        )

"""Read-only ProjectX Market Hub (SignalR) adapter.

User Hub is not implemented. No order methods. LIVE_EXECUTION_ENABLED stays
False. Quotes and exchange prints map to RT Quote / MarketTrade / MarketTick.
Unknown or incomplete payloads are skipped; nothing is invented.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from src.realtime.connectors.projectx import (
    ProjectXAuthenticationError,
    ProjectXContract,
    ProjectXError,
    ProjectXResponseError,
)
from src.realtime.events import (
    ORIGIN_LIVE,
    CanonicalEvent,
    MarketTick,
    MarketTrade,
    Quote,
    SystemEvent,
)
from src.realtime.interfaces import LIVE_EXECUTION_ENABLED

RECORD_SEPARATOR = "\x1e"
DEFAULT_MARKET_HUB = "https://rtc.topstepx.com/hubs/market"
SUBSCRIBE_QUOTES = "SubscribeContractQuotes"
SUBSCRIBE_TRADES = "SubscribeContractTrades"
_QUOTE_TARGETS = frozenset({"gatewayquote", "quote", "quotes"})
_TRADE_TARGETS = frozenset({"gatewaytrade", "trade", "trades", "gatewayprint"})
_TICK_TARGETS = frozenset({"gatewaylast", "last", "lastprice"})
_HANDSHAKE = {"protocol": "json", "version": 1}


class HubSocket(Protocol):
    def send(self, data: str) -> None: ...

    def recv(self) -> str: ...

    def close(self) -> None: ...


class HubSocketFactory(Protocol):
    def __call__(self, url: str, headers: Mapping[str, str], timeout: float) -> HubSocket: ...


@dataclass
class StreamSequencer:
    contract_id: str
    source_root: str = "projectx"

    def __post_init__(self) -> None:
        if not self.contract_id.strip() or not self.source_root.strip():
            raise ValueError("contract_id and source_root must be non-empty")
        self.contract_id = self.contract_id.strip()
        self.source_root = self.source_root.strip()
        self._next = {"quote": 1, "trade": 1, "tick": 1, "system": 1}
        self._assigned: dict[tuple[str, str], int] = {}

    def source(self, kind: str) -> str:
        if kind == "system":
            return f"{self.source_root}/{self.contract_id}/system"
        if kind not in self._next:
            raise ValueError(f"unknown stream kind {kind}")
        return f"{self.source_root}/{self.contract_id}/{kind}"

    def next_seq(self, kind: str) -> int:
        if kind not in self._next:
            raise ValueError(f"unknown stream kind {kind}")
        sequence = self._next[kind]
        self._next[kind] = sequence + 1
        return sequence

    def assign(self, kind: str, event_id: str) -> int:
        key = (kind, event_id)
        existing = self._assigned.get(key)
        if existing is not None:
            return existing
        sequence = self.next_seq(kind)
        self._assigned[key] = sequence
        return sequence


def encode_signalr(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), separators=(",", ":")) + RECORD_SEPARATOR


def split_signalr_frames(buffer: str) -> tuple[tuple[str, ...], str]:
    parts = buffer.split(RECORD_SEPARATOR)
    return tuple(part for part in parts[:-1] if part != ""), parts[-1]


def parse_signalr_frame(frame: str) -> Mapping[str, Any]:
    try:
        decoded = json.loads(frame)
    except json.JSONDecodeError as exc:
        raise ProjectXResponseError("Market Hub frame is not JSON") from exc
    if not isinstance(decoded, dict):
        raise ProjectXResponseError("Market Hub frame must be a JSON object")
    return decoded


def _https_to_wss(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "wss"}:
        raise ValueError("Market Hub URL must use HTTPS/WSS")
    scheme = "wss" if parsed.scheme == "https" else parsed.scheme
    return urlunparse((scheme, parsed.netloc, parsed.path, "", parsed.query, ""))


def negotiate_market_hub(
    token: str,
    *,
    hub_url: str = DEFAULT_MARKET_HUB,
    timeout: float = 10.0,
) -> dict[str, Any]:
    if LIVE_EXECUTION_ENABLED is not False:
        raise ProjectXError("live execution is locked; Market Hub stays read-only")
    if not token.strip():
        raise ProjectXAuthenticationError("authenticate before calling ProjectX")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    negotiate_url = hub_url.rstrip("/") + "/negotiate?negotiateVersion=1"
    request = Request(
        negotiate_url,
        data=b"{}",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token.strip()}",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise ProjectXAuthenticationError(
                f"Market Hub rejected the session (HTTP {exc.code})"
            ) from exc
        raise ProjectXError(f"Market Hub negotiate HTTP error {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        raise ProjectXError("Market Hub negotiate failed or timed out") from exc
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectXResponseError("Market Hub negotiate returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise ProjectXResponseError("Market Hub negotiate must return a JSON object")
    return decoded


def market_hub_socket_url(hub_url: str, negotiate: Mapping[str, Any], token: str) -> str:
    connection_token = negotiate.get("connectionToken") or negotiate.get("connectionId")
    if not isinstance(connection_token, str) or not connection_token.strip():
        raise ProjectXResponseError("Market Hub negotiate missing connection token")
    parsed = urlparse(_https_to_wss(hub_url.rstrip("/")))
    query = urlencode({"id": connection_token.strip(), "access_token": token.strip()})
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", query, ""))


def redact_secrets(text: str, *secrets: str) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "<redacted>")
    return redacted


def select_mnq_contract(contracts: tuple[ProjectXContract, ...]) -> ProjectXContract:
    matches = tuple(
        contract
        for contract in contracts
        if contract.active
        and (
            "MNQ" in contract.contract_id.upper()
            or "MNQ" in contract.name.upper()
            or "MNQ" in contract.symbol_id.upper()
        )
    )
    if len(matches) == 1:
        return matches[0]
    exact = tuple(contract for contract in matches if contract.name.upper() == "MNQ")
    if len(exact) == 1:
        return exact[0]
    if not matches:
        raise ProjectXResponseError("no active MNQ contract from Contract/search")
    raise ProjectXResponseError(
        "ambiguous MNQ contracts: " + ", ".join(item.contract_id for item in matches)
    )


def _decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ProjectXResponseError(f"missing or invalid {field_name}")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ProjectXResponseError(f"invalid decimal field {field_name}") from exc
    if not parsed.is_finite():
        raise ProjectXResponseError(f"non-finite decimal field {field_name}")
    return parsed


def _timestamp(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ProjectXResponseError(f"invalid timestamp field {field_name}") from exc
    else:
        raise ProjectXResponseError(f"missing timestamp field {field_name}")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProjectXResponseError(f"timestamp field {field_name} lacks timezone")
    return parsed.astimezone(UTC)


def _first(row: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return None


def _payloads(message: Mapping[str, Any]) -> tuple[str, tuple[Mapping[str, Any], ...]]:
    target = message.get("target")
    if not isinstance(target, str) or not target.strip():
        return "", ()
    arguments = message.get("arguments")
    if not isinstance(arguments, list):
        return target, ()
    rows: list[Mapping[str, Any]] = []
    for item in arguments:
        if isinstance(item, dict):
            rows.append(item)
        elif isinstance(item, list):
            rows.extend(row for row in item if isinstance(row, dict))
    return target, tuple(rows)


def _quote_event(
    row: Mapping[str, Any],
    *,
    contract_id: str,
    sequencer: StreamSequencer,
) -> Quote:
    timestamp = _timestamp(
        _first(row, ("timestamp", "lastUpdated", "lastUpdatedUtc", "t")),
        "quote.timestamp",
    )
    bid = _decimal(_first(row, ("bestBid", "bidPrice", "bid")), "quote.bid")
    ask = _decimal(_first(row, ("bestAsk", "askPrice", "ask")), "quote.ask")
    bid_size = _decimal(
        _first(row, ("bestBidSize", "bidSize", "bidVolume")), "quote.bidSize"
    )
    ask_size = _decimal(
        _first(row, ("bestAskSize", "askSize", "askVolume")), "quote.askSize"
    )
    event_id = (
        f"{contract_id}:quote:{timestamp.isoformat()}:"
        f"{bid}:{ask}:{bid_size}:{ask_size}"
    )
    sequence = sequencer.assign("quote", event_id)
    return Quote(
        event_id=event_id,
        source=sequencer.source("quote"),
        timestamp=timestamp,
        sequence=sequence,
        symbol=contract_id,
        bid_price=float(bid),
        ask_price=float(ask),
        bid_size=float(bid_size),
        ask_size=float(ask_size),
        origin=ORIGIN_LIVE,
    )


def _trade_event(
    row: Mapping[str, Any],
    *,
    contract_id: str,
    sequencer: StreamSequencer,
) -> MarketTrade:
    timestamp = _timestamp(
        _first(row, ("timestamp", "creationTimestamp", "t")),
        "trade.timestamp",
    )
    price = _decimal(_first(row, ("price", "lastPrice", "p")), "trade.price")
    size = _decimal(_first(row, ("size", "volume", "v")), "trade.size")
    trade_id = _first(row, ("id", "tradeId", "token"))
    identity = str(trade_id).strip() if trade_id is not None else timestamp.isoformat()
    event_id = f"{contract_id}:trade:{identity}:{price}:{size}"
    sequence = sequencer.assign("trade", event_id)
    return MarketTrade(
        event_id=event_id,
        source=sequencer.source("trade"),
        timestamp=timestamp,
        sequence=sequence,
        symbol=contract_id,
        price=float(price),
        size=float(size),
        origin=ORIGIN_LIVE,
    )


def _tick_event(
    row: Mapping[str, Any],
    *,
    contract_id: str,
    sequencer: StreamSequencer,
) -> MarketTick:
    timestamp = _timestamp(
        _first(row, ("timestamp", "lastUpdated", "t")),
        "tick.timestamp",
    )
    price = _decimal(_first(row, ("lastPrice", "price", "p")), "tick.price")
    volume = _decimal(_first(row, ("volume", "size", "v")), "tick.volume")
    event_id = f"{contract_id}:tick:{timestamp.isoformat()}:{price}:{volume}"
    sequence = sequencer.assign("tick", event_id)
    return MarketTick(
        event_id=event_id,
        source=sequencer.source("tick"),
        timestamp=timestamp,
        sequence=sequence,
        symbol=contract_id,
        price=float(price),
        volume=float(volume),
        origin=ORIGIN_LIVE,
    )


def map_hub_message(
    message: Mapping[str, Any],
    *,
    contract_id: str,
    sequencer: StreamSequencer,
) -> tuple[CanonicalEvent, ...]:
    """Map one SignalR invocation to canonical market events. Skip junk."""
    if int(message.get("type") or 0) not in {1, 2}:
        return ()
    target, rows = _payloads(message)
    key = target.strip().lower()
    events: list[CanonicalEvent] = []
    for row in rows:
        try:
            if key in _QUOTE_TARGETS:
                events.append(
                    _quote_event(row, contract_id=contract_id, sequencer=sequencer)
                )
            elif key in _TRADE_TARGETS:
                events.append(
                    _trade_event(row, contract_id=contract_id, sequencer=sequencer)
                )
            elif key in _TICK_TARGETS:
                events.append(
                    _tick_event(row, contract_id=contract_id, sequencer=sequencer)
                )
        except (ProjectXResponseError, ValueError, TypeError):
            continue
    return tuple(events)


def system_event(
    sequencer: StreamSequencer,
    *,
    kind: str,
    clock_now: datetime,
    detail: str = "",
    symbol: str | None = None,
) -> SystemEvent:
    sequence = sequencer.next_seq("system")
    return SystemEvent(
        event_id=f"{sequencer.contract_id}:system:{kind}:{sequence}",
        source=sequencer.source("system"),
        timestamp=clock_now,
        sequence=sequence,
        kind=kind,
        origin=ORIGIN_LIVE,
        detail=detail,
        symbol=symbol,
    )


def handshake_frames() -> str:
    return encode_signalr(_HANDSHAKE)


def subscribe_frames(contract_id: str) -> tuple[str, str]:
    quotes = encode_signalr(
        {
            "type": 1,
            "invocationId": "sub-quotes",
            "target": SUBSCRIBE_QUOTES,
            "arguments": [contract_id],
        }
    )
    trades = encode_signalr(
        {
            "type": 1,
            "invocationId": "sub-trades",
            "target": SUBSCRIBE_TRADES,
            "arguments": [contract_id],
        }
    )
    return quotes, trades


def ping_frame() -> str:
    return encode_signalr({"type": 6})


def iter_mapped_events(
    frames: tuple[str, ...],
    *,
    contract_id: str,
    sequencer: StreamSequencer,
) -> Iterator[CanonicalEvent]:
    for frame in frames:
        try:
            message = parse_signalr_frame(frame)
        except ProjectXResponseError:
            continue
        yield from map_hub_message(
            message, contract_id=contract_id, sequencer=sequencer
        )

"""Read-only ProjectX/TopstepX Gateway client.

Provider DTOs stay here. Canonical RT events are built through the existing
Bar / AccountSnapshot contracts. Fills are never Core Trade.

This module exposes no order-placement, modification, cancellation, or
position-closing method. LIVE_EXECUTION_ENABLED remains False.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.realtime.clock import Clock, SystemClock
from src.realtime.config import ProjectXCredentials
from src.realtime.connector import ConnectorError
from src.realtime.events import ORIGIN_LIVE, AccountSnapshot, Bar, CanonicalEvent
from src.realtime.interfaces import LIVE_EXECUTION_ENABLED

DEFAULT_API_URL = "https://api.topstepx.com"
PROJECTX_SOURCE = "projectx"
MAX_BAR_LIMIT = 20_000
BAR_UNITS = frozenset({1, 2, 3, 4, 5, 6})
_BAR_UNIT_SUFFIX = {1: "s", 2: "m", 3: "h", 4: "d", 5: "w", 6: "mo"}
_FORBIDDEN_METHODS = (
    "place_order",
    "submit_order",
    "cancel_order",
    "modify_order",
    "close_position",
    "close_positions",
    "flatten",
    "buy",
    "sell",
)
_READ_ONLY_PATHS = frozenset(
    {
        "/api/Auth/loginKey",
        "/api/Auth/validate",
        "/api/Account/search",
        "/api/Contract/search",
        "/api/History/retrieveBars",
        "/api/Position/searchOpen",
        "/api/Trade/search",
    }
)


class ProjectXError(RuntimeError):
    """Base error for ProjectX connection and response failures."""


class ProjectXAuthenticationError(ProjectXError):
    """Authentication or session failure."""


class ProjectXRateLimitError(ProjectXError):
    """The provider returned HTTP 429."""


class ProjectXResponseError(ProjectXError):
    """The provider returned malformed or unsuccessful data."""


class JsonTransport(Protocol):
    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class ProjectXAccount:
    """Provider account row. Not RT AccountSnapshot and not Core AccountState."""

    account_id: int
    name: str
    balance: Decimal
    can_trade: bool
    is_visible: bool
    simulated: bool | None


@dataclass(frozen=True)
class ProjectXContract:
    contract_id: str
    name: str
    description: str
    tick_size: Decimal
    tick_value: Decimal
    active: bool
    symbol_id: str


@dataclass(frozen=True)
class ProjectXBar:
    """Provider OHLCV row with Decimal prices. Map via canonical_bars()."""

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class ProjectXPosition:
    position_id: int
    account_id: int
    contract_id: str
    created_at: datetime
    position_type: int
    size: int
    average_price: Decimal


@dataclass(frozen=True)
class ProjectXFill:
    """Provider fill. Not MarketTrade. Not Core Trade. No round-trip aggregation."""

    trade_id: int
    account_id: int
    contract_id: str
    created_at: datetime
    price: Decimal
    profit_and_loss: Decimal | None
    fees: Decimal
    commissions: Decimal | None
    side: int
    size: int
    voided: bool
    order_id: int


class UrllibJsonTransport:
    """Standard-library JSON transport with bounded requests."""

    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        request = Request(
            url,
            data=json.dumps(dict(payload), separators=(",", ":")).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            if exc.code == 429:
                raise ProjectXRateLimitError(
                    "ProjectX rate limit reached; back off before retrying"
                ) from exc
            if exc.code in (401, 403):
                raise ProjectXAuthenticationError(
                    f"ProjectX rejected the session (HTTP {exc.code})"
                ) from exc
            raise ProjectXError(f"ProjectX HTTP error {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise ProjectXError("ProjectX request failed or timed out") from exc
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectXResponseError("ProjectX returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise ProjectXResponseError("ProjectX response must be a JSON object")
        return decoded


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


def _integer(value: Any, field_name: str, *, positive: bool = True) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProjectXResponseError(f"invalid integer field {field_name}")
    if positive and value <= 0:
        raise ProjectXResponseError(f"{field_name} must be positive")
    return value


def _boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ProjectXResponseError(f"invalid boolean field {field_name}")
    return value


def _timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ProjectXResponseError(f"missing timestamp field {field_name}")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ProjectXResponseError(f"invalid timestamp field {field_name}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProjectXResponseError(f"timestamp field {field_name} lacks timezone")
    return parsed.astimezone(UTC)


def _iso_utc(value: datetime, field_name: str) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _require_int(
    value: object,
    name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    allowed: frozenset[int] | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if allowed is not None and value not in allowed:
        raise ValueError(f"{name} must be between 1 (second) and 6 (month)")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return value


def _require_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a bool")
    return value


def projectx_stream_source(
    contract_id: str, interval: str, *, source: str = PROJECTX_SOURCE
) -> str:
    """Per-contract/interval source so sequences do not collide on the bus."""
    if not contract_id.strip() or not interval.strip():
        raise ValueError("contract_id and interval must be non-empty")
    return f"{source}/{contract_id.strip()}/{interval}"


def _bar_sequence(timestamp: datetime) -> int:
    """Stable sequence from UTC time. Not the index inside one HTTP response."""
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    delta = timestamp.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def bar_interval(unit: int, unit_number: int) -> str:
    suffix = _BAR_UNIT_SUFFIX.get(unit)
    if suffix is None:
        raise ValueError("unit must be between 1 (second) and 6 (month)")
    return f"{unit_number}{suffix}"


def _require_same_account(requested: int, found: Any, kind: str) -> None:
    mismatched = [account_id for account_id in found if account_id != requested]
    if mismatched:
        raise ProjectXResponseError(
            f"{kind} accountId {mismatched[0]} does not match requested {requested}"
        )


def _chronological_bars(
    bars: tuple[ProjectXBar, ...], *, error: type[Exception]
) -> tuple[ProjectXBar, ...]:
    """Newest-first provider rows become oldest-first. Duplicate times fail closed."""
    ordered = tuple(sorted(bars, key=lambda bar: bar.timestamp))
    seen: set[datetime] = set()
    for bar in ordered:
        if bar.timestamp in seen:
            raise error("duplicate bar timestamps")
        seen.add(bar.timestamp)
    return ordered


def canonical_bars(
    bars: tuple[ProjectXBar, ...],
    *,
    contract_id: str,
    unit: int,
    unit_number: int,
    source: str = PROJECTX_SOURCE,
) -> tuple[Bar, ...]:
    """Adapt provider bars to RT-0 Bar. Does not invent round trips."""
    if not contract_id.strip():
        raise ValueError("contract_id must not be empty")
    unit = _require_int(unit, "unit", allowed=BAR_UNITS)
    unit_number = _require_int(unit_number, "unit_number", minimum=1)
    interval = bar_interval(unit, unit_number)
    ordered = _chronological_bars(bars, error=ValueError)
    stream_source = projectx_stream_source(contract_id, interval, source=source)
    canonical: list[Bar] = []
    for bar in ordered:
        timestamp = bar.timestamp.astimezone(UTC)
        canonical.append(
            Bar(
                event_id=f"{contract_id}:{interval}:{timestamp.isoformat()}",
                source=stream_source,
                timestamp=timestamp,
                sequence=_bar_sequence(timestamp),
                symbol=contract_id,
                interval=interval,
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=float(bar.volume),
                origin=ORIGIN_LIVE,
            )
        )
    return tuple(canonical)


def canonical_account_snapshot(
    account: ProjectXAccount,
    positions: tuple[ProjectXPosition, ...],
    *,
    event_id: str,
    sequence: int,
    clock: Clock,
    source: str = PROJECTX_SOURCE,
) -> AccountSnapshot:
    """Map provider account+positions. equity/trades_applied stay unknown."""
    _require_same_account(
        account.account_id,
        (position.account_id for position in positions),
        "position",
    )
    frozen_positions = tuple(
        (
            position.contract_id,
            position.position_type,
            position.size,
            str(position.average_price),
        )
        for position in positions
    )
    now = clock.now()
    return AccountSnapshot(
        event_id=event_id,
        source=source,
        timestamp=now,
        sequence=sequence,
        origin=ORIGIN_LIVE,
        balance=float(account.balance),
        equity=None,
        positions=frozen_positions,
        working_orders=(),
        last_sync=now,
        trades_applied=None,
    )


class ProjectXClient:
    """Authenticated read-only client for official TopstepX endpoints."""

    execution_allowed = False

    def __init__(
        self,
        credentials: ProjectXCredentials,
        *,
        transport: JsonTransport | None = None,
        base_url: str = DEFAULT_API_URL,
        timeout: float = 10.0,
    ) -> None:
        if LIVE_EXECUTION_ENABLED is not False:
            raise ProjectXError("live execution is locked; ProjectX stays read-only")
        if not base_url.startswith("https://"):
            raise ValueError("ProjectX base_url must use HTTPS")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        for name in _FORBIDDEN_METHODS:
            if name in type(self).__dict__:
                raise ProjectXError(f"ProjectXClient must not define {name}")
        self._credentials = credentials
        self._transport = transport or UrllibJsonTransport()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._token: str | None = None

    @property
    def authenticated(self) -> bool:
        return self._token is not None

    def authenticate(self) -> None:
        response = self._post(
            "/api/Auth/loginKey",
            {
                "userName": self._credentials.username,
                "apiKey": self._credentials.api_key,
            },
            authenticated=False,
        )
        token = response.get("token")
        if not isinstance(token, str) or not token.strip():
            raise ProjectXAuthenticationError("ProjectX did not return a session token")
        self._token = token.strip()

    def validate_session(self) -> None:
        response = self._post("/api/Auth/validate", {}, authenticated=True)
        new_token = response.get("newToken")
        if isinstance(new_token, str) and new_token.strip():
            self._token = new_token.strip()

    def list_accounts(self, *, only_active: bool = True) -> tuple[ProjectXAccount, ...]:
        only_active = _require_bool(only_active, "only_active")
        rows = self._list_field(
            "/api/Account/search",
            {"onlyActiveAccounts": only_active},
            "accounts",
        )
        accounts = []
        for row in rows:
            accounts.append(
                ProjectXAccount(
                    account_id=_integer(row.get("id"), "account.id"),
                    name=str(row.get("name", "")),
                    balance=_decimal(row.get("balance"), "account.balance"),
                    can_trade=_boolean(row.get("canTrade"), "account.canTrade"),
                    is_visible=_boolean(row.get("isVisible"), "account.isVisible"),
                    simulated=(
                        _boolean(row["simulated"], "account.simulated")
                        if "simulated" in row
                        else None
                    ),
                )
            )
        return tuple(accounts)

    def select_account(
        self,
        accounts: tuple[ProjectXAccount, ...],
        *,
        account_name: str | None = None,
    ) -> ProjectXAccount:
        requested = account_name or self._credentials.account_name
        if requested:
            matches = [account for account in accounts if account.name == requested]
            if len(matches) != 1:
                raise ProjectXResponseError(
                    f"expected exactly one account named {requested!r}, found {len(matches)}"
                )
            return matches[0]
        if len(accounts) != 1:
            raise ProjectXResponseError(
                "account selection is ambiguous; set FARS_PROJECTX_ACCOUNT_NAME"
            )
        return accounts[0]

    def search_contracts(self, search_text: str, *, live: bool = False) -> tuple[ProjectXContract, ...]:
        if not search_text.strip():
            raise ValueError("search_text must not be empty")
        live = _require_bool(live, "live")
        rows = self._list_field(
            "/api/Contract/search",
            {"searchText": search_text.strip(), "live": live},
            "contracts",
        )
        return tuple(self._parse_contract(row) for row in rows)

    def retrieve_bars(
        self,
        contract_id: str,
        *,
        start: datetime,
        end: datetime,
        unit: int = 2,
        unit_number: int = 1,
        limit: int = 1_000,
        include_partial_bar: bool = False,
        live: bool = False,
    ) -> tuple[ProjectXBar, ...]:
        if not contract_id.strip():
            raise ValueError("contract_id must not be empty")
        unit = _require_int(unit, "unit", allowed=BAR_UNITS)
        unit_number = _require_int(unit_number, "unit_number", minimum=1)
        limit = _require_int(limit, "limit", minimum=1, maximum=MAX_BAR_LIMIT)
        include_partial_bar = _require_bool(include_partial_bar, "include_partial_bar")
        live = _require_bool(live, "live")
        start_iso = _iso_utc(start, "start")
        end_iso = _iso_utc(end, "end")
        if start >= end:
            raise ValueError("start must be earlier than end")
        rows = self._list_field(
            "/api/History/retrieveBars",
            {
                "contractId": contract_id.strip(),
                "live": live,
                "startTime": start_iso,
                "endTime": end_iso,
                "unit": unit,
                "unitNumber": unit_number,
                "limit": limit,
                "includePartialBar": include_partial_bar,
            },
            "bars",
        )
        parsed = tuple(
            ProjectXBar(
                timestamp=_timestamp(row.get("t"), "bar.t"),
                open=_decimal(row.get("o"), "bar.o"),
                high=_decimal(row.get("h"), "bar.h"),
                low=_decimal(row.get("l"), "bar.l"),
                close=_decimal(row.get("c"), "bar.c"),
                volume=_decimal(row.get("v"), "bar.v"),
            )
            for row in rows
        )
        return _chronological_bars(parsed, error=ProjectXResponseError)

    def list_open_positions(self, account_id: int) -> tuple[ProjectXPosition, ...]:
        _integer(account_id, "account_id")
        rows = self._list_field(
            "/api/Position/searchOpen", {"accountId": account_id}, "positions"
        )
        positions = tuple(
            ProjectXPosition(
                position_id=_integer(row.get("id"), "position.id"),
                account_id=_integer(row.get("accountId"), "position.accountId"),
                contract_id=str(row.get("contractId", "")),
                created_at=_timestamp(row.get("creationTimestamp"), "position.creationTimestamp"),
                position_type=_integer(row.get("type"), "position.type"),
                size=_integer(row.get("size"), "position.size"),
                average_price=_decimal(row.get("averagePrice"), "position.averagePrice"),
            )
            for row in rows
        )
        _require_same_account(account_id, (item.account_id for item in positions), "position")
        return positions

    def list_trades(
        self,
        account_id: int,
        *,
        start: datetime,
        end: datetime | None = None,
    ) -> tuple[ProjectXFill, ...]:
        _integer(account_id, "account_id")
        start_iso = _iso_utc(start, "start")
        payload: dict[str, Any] = {
            "accountId": account_id,
            "startTimestamp": start_iso,
        }
        if end is not None:
            end_iso = _iso_utc(end, "end")
            if start >= end:
                raise ValueError("start must be earlier than end")
            payload["endTimestamp"] = end_iso
        rows = self._list_field("/api/Trade/search", payload, "trades")
        fills = tuple(self._parse_fill(row) for row in rows)
        _require_same_account(account_id, (item.account_id for item in fills), "trade")
        return fills

    def _post(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        authenticated: bool,
    ) -> Mapping[str, Any]:
        if path not in _READ_ONLY_PATHS:
            raise ProjectXError(f"refusing non-read-only ProjectX path {path}")
        headers = {"Accept": "text/plain", "Content-Type": "application/json"}
        if authenticated:
            if self._token is None:
                raise ProjectXAuthenticationError("authenticate before calling ProjectX")
            headers["Authorization"] = f"Bearer {self._token}"
        response = self._transport.post(
            f"{self._base_url}{path}", headers, payload, self._timeout
        )
        if response.get("success") is not True:
            error_code = response.get("errorCode")
            error_message = response.get("errorMessage") or "unspecified provider error"
            error_type = (
                ProjectXAuthenticationError
                if path.startswith("/api/Auth/")
                else ProjectXResponseError
            )
            raise error_type(f"ProjectX error {error_code}: {error_message}")
        return response

    def _list_field(
        self, path: str, payload: Mapping[str, Any], field_name: str
    ) -> list[Mapping[str, Any]]:
        response = self._post(path, payload, authenticated=True)
        rows = response.get(field_name)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ProjectXResponseError(f"ProjectX response has invalid {field_name}")
        return rows

    @staticmethod
    def _parse_contract(row: Mapping[str, Any]) -> ProjectXContract:
        return ProjectXContract(
            contract_id=str(row.get("id", "")),
            name=str(row.get("name", "")),
            description=str(row.get("description", "")),
            tick_size=_decimal(row.get("tickSize"), "contract.tickSize"),
            tick_value=_decimal(row.get("tickValue"), "contract.tickValue"),
            active=_boolean(row.get("activeContract"), "contract.activeContract"),
            symbol_id=str(row.get("symbolId", "")),
        )

    @staticmethod
    def _parse_fill(row: Mapping[str, Any]) -> ProjectXFill:
        pnl = row.get("profitAndLoss")
        commissions = row.get("commissions")
        if commissions is None:
            commissions = row.get("commission")
        return ProjectXFill(
            trade_id=_integer(row.get("id"), "trade.id"),
            account_id=_integer(row.get("accountId"), "trade.accountId"),
            contract_id=str(row.get("contractId", "")),
            created_at=_timestamp(row.get("creationTimestamp"), "trade.creationTimestamp"),
            price=_decimal(row.get("price"), "trade.price"),
            profit_and_loss=None if pnl is None else _decimal(pnl, "trade.profitAndLoss"),
            fees=_decimal(row.get("fees"), "trade.fees"),
            commissions=(
                None if commissions is None else _decimal(commissions, "trade.commissions")
            ),
            side=_integer(row.get("side"), "trade.side", positive=False),
            size=_integer(row.get("size"), "trade.size"),
            voided=_boolean(row.get("voided"), "trade.voided"),
            order_id=_integer(row.get("orderId"), "trade.orderId"),
        )


class ProjectXHistoricalBarConnector:
    """RT-1 MarketDataConnector over ProjectX historical bars. Not SignalR."""

    def __init__(
        self,
        client: ProjectXClient,
        contract_id: str,
        *,
        start: datetime,
        end: datetime,
        clock: Clock | None = None,
        unit: int = 2,
        unit_number: int = 1,
        limit: int = 1_000,
        include_partial_bar: bool = False,
        live: bool = False,
        source: str = PROJECTX_SOURCE,
    ) -> None:
        if not contract_id.strip():
            raise ConnectorError("contract_id must not be empty")
        self._client = client
        self._contract_id = contract_id.strip()
        self._start = start
        self._end = end
        self._clock = clock or SystemClock()
        self._unit = unit
        self._unit_number = unit_number
        self._limit = limit
        self._include_partial_bar = include_partial_bar
        self._live = live
        self._source = source
        self._connected = False
        self._stream: Iterator[Bar] | None = None

    def connect(self) -> None:
        if self._connected:
            raise ConnectorError("connector already connected")
        if not self._client.authenticated:
            self._client.authenticate()
        bars = self._client.retrieve_bars(
            self._contract_id,
            start=self._start,
            end=self._end,
            unit=self._unit,
            unit_number=self._unit_number,
            limit=self._limit,
            include_partial_bar=self._include_partial_bar,
            live=self._live,
        )
        events = canonical_bars(
            bars,
            contract_id=self._contract_id,
            unit=self._unit,
            unit_number=self._unit_number,
            source=self._source,
        )
        self._stream = iter(events)
        self._connected = True

    def disconnect(self) -> None:
        if not self._connected:
            raise ConnectorError("connector is not connected")
        self._connected = False
        self._stream = None

    def next_event(self) -> CanonicalEvent | None:
        if not self._connected or self._stream is None:
            raise ConnectorError("connector is not connected")
        try:
            return next(self._stream)
        except StopIteration:
            return None

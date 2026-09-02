"""Read-only ProjectX/TopstepX Gateway client.

This module intentionally exposes no order-placement, position-closing, or
order-cancellation method.  It implements only the authenticated data path
needed by RT-1 while paper/live execution remains blocked.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..config import ProjectXCredentials
from ..events import AccountSnapshot, Bar, Contract, OpenPosition, ProviderTrade

DEFAULT_API_URL = "https://api.topstepx.com"
MAX_BAR_LIMIT = 20_000
BAR_UNITS = frozenset({1, 2, 3, 4, 5, 6})


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


class UrllibJsonTransport:
    """Small standard-library JSON transport with bounded requests."""

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
    return parsed


def _iso_utc(value: datetime, field_name: str) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ProjectXClient:
    """Authenticated read-only client for the official TopstepX endpoints."""

    execution_allowed = False

    def __init__(
        self,
        credentials: ProjectXCredentials,
        *,
        transport: JsonTransport | None = None,
        base_url: str = DEFAULT_API_URL,
        timeout: float = 10.0,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("ProjectX base_url must use HTTPS")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
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

    def list_accounts(self, *, only_active: bool = True) -> tuple[AccountSnapshot, ...]:
        rows = self._list_field(
            "/api/Account/search",
            {"onlyActiveAccounts": only_active},
            "accounts",
        )
        accounts = []
        for row in rows:
            accounts.append(
                AccountSnapshot(
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
        accounts: tuple[AccountSnapshot, ...],
        *,
        account_name: str | None = None,
    ) -> AccountSnapshot:
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

    def search_contracts(self, search_text: str, *, live: bool = False) -> tuple[Contract, ...]:
        if not search_text.strip():
            raise ValueError("search_text must not be empty")
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
    ) -> tuple[Bar, ...]:
        if not contract_id.strip():
            raise ValueError("contract_id must not be empty")
        if unit not in BAR_UNITS:
            raise ValueError("unit must be between 1 (second) and 6 (month)")
        if isinstance(unit_number, bool) or unit_number <= 0:
            raise ValueError("unit_number must be positive")
        if isinstance(limit, bool) or not 1 <= limit <= MAX_BAR_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_BAR_LIMIT}")
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
        return tuple(
            Bar(
                timestamp=_timestamp(row.get("t"), "bar.t"),
                open=_decimal(row.get("o"), "bar.o"),
                high=_decimal(row.get("h"), "bar.h"),
                low=_decimal(row.get("l"), "bar.l"),
                close=_decimal(row.get("c"), "bar.c"),
                volume=_decimal(row.get("v"), "bar.v"),
            )
            for row in rows
        )

    def list_open_positions(self, account_id: int) -> tuple[OpenPosition, ...]:
        _integer(account_id, "account_id")
        rows = self._list_field(
            "/api/Position/searchOpen", {"accountId": account_id}, "positions"
        )
        return tuple(
            OpenPosition(
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

    def list_trades(
        self,
        account_id: int,
        *,
        start: datetime,
        end: datetime | None = None,
    ) -> tuple[ProviderTrade, ...]:
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
        return tuple(self._parse_trade(row) for row in rows)

    def _post(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        authenticated: bool,
    ) -> Mapping[str, Any]:
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
    def _parse_contract(row: Mapping[str, Any]) -> Contract:
        return Contract(
            contract_id=str(row.get("id", "")),
            name=str(row.get("name", "")),
            description=str(row.get("description", "")),
            tick_size=_decimal(row.get("tickSize"), "contract.tickSize"),
            tick_value=_decimal(row.get("tickValue"), "contract.tickValue"),
            active=_boolean(row.get("activeContract"), "contract.activeContract"),
            symbol_id=str(row.get("symbolId", "")),
        )

    @staticmethod
    def _parse_trade(row: Mapping[str, Any]) -> ProviderTrade:
        pnl = row.get("profitAndLoss")
        return ProviderTrade(
            trade_id=_integer(row.get("id"), "trade.id"),
            account_id=_integer(row.get("accountId"), "trade.accountId"),
            contract_id=str(row.get("contractId", "")),
            created_at=_timestamp(row.get("creationTimestamp"), "trade.creationTimestamp"),
            price=_decimal(row.get("price"), "trade.price"),
            profit_and_loss=(
                None if pnl is None else _decimal(pnl, "trade.profitAndLoss")
            ),
            fees=_decimal(row.get("fees"), "trade.fees"),
            side=_integer(row.get("side"), "trade.side", positive=False),
            size=_integer(row.get("size"), "trade.size"),
            voided=_boolean(row.get("voided"), "trade.voided"),
            order_id=_integer(row.get("orderId"), "trade.orderId"),
        )

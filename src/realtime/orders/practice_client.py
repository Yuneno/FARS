"""Practice Order Client for TopstepX / ProjectX Gateway.

Enforces:
1. Reuses authenticated session/token from ProjectXClient without relaxing
   read-only guards of the connector.
2. Separate permission gate: PRACTICE_EXECUTION_ENABLED (default False) +
   PRACTICE_ACCOUNT_ALLOWLIST (only allowed practice accounts; combine/live forbidden).
3. LIVE_EXECUTION_ENABLED strictly remains False.
4. Anti-OrderPending discipline: unique client_order_id (customTag) per placement,
   mandatory search before concluding or retrying upon timeout/ambiguity,
   zero blind resends.
5. Fail-closed on any unauthorized state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
import uuid
from zoneinfo import ZoneInfo

from src.realtime.clock import Clock, SystemClock
from src.realtime.connectors.projectx import (
    DEFAULT_API_URL,
    JsonTransport,
    ProjectXAuthenticationError,
    ProjectXError,
    ProjectXRateLimitError,
    ProjectXResponseError,
    UrllibJsonTransport,
)
from src.realtime.interfaces import LIVE_EXECUTION_ENABLED

CHICAGO_TZ = ZoneInfo("America/Chicago")


def is_past_daily_close_cutoff(
    now: datetime,
    cutoff_hour: int = 15,
    cutoff_minute: int = 10,
) -> bool:
    """Check if current time is past the daily 15:10 CT market close cutoff.

    On trading weekdays (Monday through Friday), between 15:10 CT and 17:00 CT,
    new orders are strictly prohibited to enforce the daily account closing rules.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    ct_now = now.astimezone(CHICAGO_TZ)
    # Check weekday (Monday=0 ... Friday=4)
    if ct_now.weekday() in (0, 1, 2, 3, 4):
        cutoff_time = ct_now.replace(hour=cutoff_hour, minute=cutoff_minute, second=0, microsecond=0)
        reopen_time = ct_now.replace(hour=17, minute=0, second=0, microsecond=0)
        if cutoff_time <= ct_now < reopen_time:
            return True
    return False


def is_cme_market_open(now: datetime) -> tuple[bool, str]:
    """Check if CME futures equity index market (NQ/MNQ) is currently open.

    Schedule (in Central Time):
    - Opens Sunday at 17:00 CT.
    - Closes Friday at 16:00 CT.
    - Daily maintenance halt: Monday through Thursday 16:00 to 17:00 CT.
    - Weekend break: Friday 16:00 CT to Sunday 17:00 CT.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    ct_now = now.astimezone(CHICAGO_TZ)
    weekday = ct_now.weekday()  # Monday=0 ... Sunday=6
    hour = ct_now.hour

    # Saturday: completely closed
    if weekday == 5:
        return False, "CME market is CLOSED (Saturday weekend break; opens Sunday 17:00 CT)"

    # Sunday: closed until 17:00 CT
    if weekday == 6:
        if hour < 17:
            return False, f"CME market is CLOSED (Sunday pre-open; opens at 17:00 CT, current CT: {ct_now.strftime('%H:%M')})"
        return True, "CME market is OPEN (Sunday evening session)"

    # Friday: closed after 16:00 CT
    if weekday == 4:
        if hour >= 16:
            return False, f"CME market is CLOSED (Friday weekend close at 16:00 CT; current CT: {ct_now.strftime('%H:%M')})"

    # Monday through Thursday daily maintenance halt: 16:00 - 17:00 CT
    if weekday in (0, 1, 2, 3):
        if 16 <= hour < 17:
            return False, "CME market is in DAILY HALT (16:00 - 17:00 CT maintenance break; reopens 17:00 CT)"

    return True, f"CME market is OPEN (current CT: {ct_now.strftime('%A %H:%M %Z')})"


# Order types official TopstepX Gateway
ORDER_TYPE_LIMIT = 1
ORDER_TYPE_MARKET = 2
ORDER_TYPE_STOP = 4
ORDER_TYPE_TRAILING_STOP = 5

# Order sides
ORDER_SIDE_BUY = 0   # Long
ORDER_SIDE_SELL = 1  # Short

# Order status codes from TopstepX
ORDER_STATUS_WORKING = 0
ORDER_STATUS_FILLED = 1
ORDER_STATUS_CANCELLED = 2
ORDER_STATUS_REJECTED = 3
ORDER_STATUS_EXPIRED = 4

# Module-level default safety gates
DEFAULT_PRACTICE_EXECUTION_ENABLED = False
DEFAULT_PRACTICE_ACCOUNT_ALLOWLIST: tuple[str | int, ...] = (
    "PRAC-V2-673085-85699223",
    27765990,
)

# Forbidden accounts (Combines, live, etc.)
FORBIDDEN_ACCOUNT_PATTERNS = ("1.5KCHCR", "COMBINE", "LIVE")


class PracticeOrderError(RuntimeError):
    """Base error for practice order operations."""


class OrderPendingAmbiguityError(PracticeOrderError):
    """Raised when an order placement outcome is ambiguous and cannot be confirmed."""


class UnauthorizedAccountError(PracticeOrderError):
    """Raised when an operation targets an unauthorized or forbidden account."""


@dataclass(frozen=True)
class BracketConfig:
    ticks: int
    order_type: int = ORDER_TYPE_STOP

    def __post_init__(self) -> None:
        if self.ticks <= 0:
            raise ValueError("bracket ticks must be positive")
        if self.order_type not in (ORDER_TYPE_LIMIT, ORDER_TYPE_STOP, ORDER_TYPE_TRAILING_STOP):
            raise ValueError(f"invalid bracket order type: {self.order_type}")

    def to_payload(self) -> dict[str, Any]:
        return {
            "ticks": int(self.ticks),
            "type": int(self.order_type),
        }


@dataclass(frozen=True)
class PracticeOrderResult:
    order_id: int
    account_id: int
    contract_id: str
    order_type: int
    side: int
    size: int
    custom_tag: str
    status: int
    limit_price: float | None = None
    stop_price: float | None = None
    success: bool = True
    error_message: str | None = None
    raw_response: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class PracticePosition:
    position_id: int
    account_id: int
    contract_id: str
    position_type: int  # 1 = Long, 2 = Short
    size: int
    average_price: float


def generate_client_order_id(prefix: str = "fars-prac") -> str:
    """Generate unique client order id tag for anti-OrderPending tracking."""
    short_uuid = uuid.uuid4().hex[:10]
    ts_ms = int(datetime.now(UTC).timestamp() * 1000)
    return f"{prefix}-{short_uuid}-{ts_ms}"


def is_account_forbidden(account_id_or_name: str | int) -> bool:
    """Check whether an account matches forbidden patterns (Combines, live)."""
    acc_str = str(account_id_or_name).upper()
    for pattern in FORBIDDEN_ACCOUNT_PATTERNS:
        if pattern in acc_str:
            return True
    return False


class PracticeOrderClient:
    """Practice order placement and management client for TopstepX / ProjectX Gateway."""

    def __init__(
        self,
        *,
        token_provider: str | Callable[[], str],
        transport: JsonTransport | None = None,
        base_url: str = DEFAULT_API_URL,
        timeout: float = 10.0,
        practice_execution_enabled: bool = DEFAULT_PRACTICE_EXECUTION_ENABLED,
        account_allowlist: tuple[str | int, ...] = DEFAULT_PRACTICE_ACCOUNT_ALLOWLIST,
        clock: Clock | None = None,
    ) -> None:
        if LIVE_EXECUTION_ENABLED is not False:
            raise PracticeOrderError("live execution is locked; PracticeOrderClient refuses to run")
        if not base_url.startswith("https://"):
            raise ValueError("base_url must use HTTPS")
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        self._token_provider = token_provider
        self._transport = transport or UrllibJsonTransport()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._practice_execution_enabled = bool(practice_execution_enabled)
        self._account_allowlist = tuple(account_allowlist)
        self._clock = clock or SystemClock()

    @property
    def practice_execution_enabled(self) -> bool:
        return self._practice_execution_enabled

    @property
    def account_allowlist(self) -> tuple[str | int, ...]:
        return self._account_allowlist

    def _get_token(self) -> str:
        if callable(self._token_provider):
            token = self._token_provider()
        else:
            token = self._token_provider
        if not token or not isinstance(token, str):
            raise ProjectXAuthenticationError("valid bearer token is required")
        return token

    def _verify_account_authorized(self, account_id: int, account_name: str | None = None) -> None:
        """Enforce strict account allowlist and forbidden account checks."""
        if is_account_forbidden(account_id) or (account_name and is_account_forbidden(account_name)):
            raise UnauthorizedAccountError(
                f"Account {account_id} ({account_name}) is a FORBIDDEN account (combine/live); orders strictly rejected"
            )

        if not self._account_allowlist:
            raise UnauthorizedAccountError("PRACTICE_ACCOUNT_ALLOWLIST is empty; fail-closed")

        account_matched = False
        if account_id in self._account_allowlist:
            account_matched = True
        elif str(account_id) in [str(x) for x in self._account_allowlist]:
            account_matched = True
        elif account_name and account_name in self._account_allowlist:
            account_matched = True

        if not account_matched:
            raise UnauthorizedAccountError(
                f"Account {account_id} ({account_name}) not in allowlist {self._account_allowlist!r}"
            )

    def _post(self, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if LIVE_EXECUTION_ENABLED is not False:
            raise PracticeOrderError("live execution is locked")
        token = self._get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = f"{self._base_url}{path}"
        return self._transport.post(url, headers, payload, self._timeout)

    def place_order(
        self,
        *,
        account_id: int,
        contract_id: str,
        order_type: int,
        side: int,
        size: int,
        account_name: str | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
        custom_tag: str | None = None,
        stop_loss_bracket: BracketConfig | None = None,
        take_profit_bracket: BracketConfig | None = None,
    ) -> PracticeOrderResult:
        """Place an order on the practice account with anti-OrderPending discipline."""
        # 1. Permission checks
        if not self._practice_execution_enabled:
            raise PracticeOrderError("PRACTICE_EXECUTION_ENABLED is False; order placement denied")
        self._verify_account_authorized(account_id, account_name)

        # 2. Parameter validations
        if not contract_id or not isinstance(contract_id, str):
            raise ValueError("contract_id must be a non-empty string")
        if size != 1:
            raise PracticeOrderError(
                f"DECLARED_LIMIT_VIOLATION: Practice orders strictly limited to 1 micro contract, got size={size}"
            )
        if side not in (ORDER_SIDE_BUY, ORDER_SIDE_SELL):
            raise ValueError(f"invalid side: {side}")
        if order_type not in (ORDER_TYPE_LIMIT, ORDER_TYPE_MARKET, ORDER_TYPE_STOP, ORDER_TYPE_TRAILING_STOP):
            raise ValueError(f"invalid order_type: {order_type}")
        if order_type == ORDER_TYPE_LIMIT and limit_price is None:
            raise ValueError("limitPrice is required for limit orders")
        if order_type == ORDER_TYPE_STOP and stop_price is None:
            raise ValueError("stopPrice is required for stop orders")

        # 3. Unique client_order_id (customTag)
        tag = custom_tag or generate_client_order_id()

        payload: dict[str, Any] = {
            "accountId": int(account_id),
            "contractId": str(contract_id),
            "type": int(order_type),
            "side": int(side),
            "size": int(size),
            "customTag": tag,
        }
        if limit_price is not None:
            payload["limitPrice"] = float(limit_price)
        if stop_price is not None:
            payload["stopPrice"] = float(stop_price)
        if stop_loss_bracket is not None:
            payload["stopLossBracket"] = stop_loss_bracket.to_payload()
        if take_profit_bracket is not None:
            payload["takeProfitBracket"] = take_profit_bracket.to_payload()

        # 4. Transmission with anti-OrderPending timeout resolution
        try:
            raw = self._post("/api/Order/place", payload)
        except (TimeoutError, ProjectXError) as exc:
            # Transport failed or timed out.
            # CRITICAL DISCIPLINE: Do NOT blindly resend. Search open orders first.
            recovered_order = self._search_order_by_custom_tag(account_id, tag)
            if recovered_order is not None:
                return recovered_order
            raise OrderPendingAmbiguityError(
                f"Order placement timed out or failed ({exc!r}); search did not locate customTag {tag!r}. "
                f"Fail-closed: order state is ambiguous; refusing blind resend."
            ) from exc

        # Extract order id safely from various TopstepX response formats
        order_id = raw.get("orderId") or raw.get("id") or raw.get("data")
        success = raw.get("success", True)
        error_msg = raw.get("errorMessage")

        if not success or order_id is None:
            raise PracticeOrderError(f"Gateway rejected order placement: {error_msg or raw!r}")

        return PracticeOrderResult(
            order_id=int(order_id),
            account_id=account_id,
            contract_id=contract_id,
            order_type=order_type,
            side=side,
            size=size,
            custom_tag=tag,
            status=ORDER_STATUS_WORKING,
            limit_price=limit_price,
            stop_price=stop_price,
            success=True,
            raw_response=raw,
        )

    def _search_order_by_custom_tag(self, account_id: int, custom_tag: str) -> PracticeOrderResult | None:
        """Check if an order with the specified customTag exists on the gateway."""
        try:
            open_orders = self.search_open_orders(account_id)
            for order in open_orders:
                if order.get("customTag") == custom_tag:
                    return PracticeOrderResult(
                        order_id=int(order.get("id", order.get("orderId", 0))),
                        account_id=account_id,
                        contract_id=str(order.get("contractId", "")),
                        order_type=int(order.get("type", 1)),
                        side=int(order.get("side", 0)),
                        size=int(order.get("size", 1)),
                        custom_tag=custom_tag,
                        status=int(order.get("status", ORDER_STATUS_WORKING)),
                        limit_price=order.get("limitPrice"),
                        stop_price=order.get("stopPrice"),
                        success=True,
                        raw_response=order,
                    )
        except Exception:
            pass

        # Also search historical orders from today
        try:
            start_ts = (self._clock.now() - timedelta(hours=12)).astimezone(UTC).isoformat().replace("+00:00", "Z")
            historical = self.search_orders(account_id, start_timestamp=start_ts)
            for order in historical:
                if order.get("customTag") == custom_tag:
                    return PracticeOrderResult(
                        order_id=int(order.get("id", order.get("orderId", 0))),
                        account_id=account_id,
                        contract_id=str(order.get("contractId", "")),
                        order_type=int(order.get("type", 1)),
                        side=int(order.get("side", 0)),
                        size=int(order.get("size", 1)),
                        custom_tag=custom_tag,
                        status=int(order.get("status", ORDER_STATUS_WORKING)),
                        limit_price=order.get("limitPrice"),
                        stop_price=order.get("stopPrice"),
                        success=True,
                        raw_response=order,
                    )
        except Exception:
            pass

        return None

    def cancel_order(
        self,
        *,
        account_id: int,
        order_id: int,
        account_name: str | None = None,
    ) -> bool:
        """Cancel a working order by ID."""
        if not self._practice_execution_enabled:
            raise PracticeOrderError("PRACTICE_EXECUTION_ENABLED is False; order cancellation denied")
        self._verify_account_authorized(account_id, account_name)

        if order_id <= 0:
            raise ValueError("order_id must be positive")

        payload = {
            "accountId": int(account_id),
            "orderId": int(order_id),
        }
        raw = self._post("/api/Order/cancel", payload)
        success = raw.get("success", True)
        if not success:
            error_msg = raw.get("errorMessage")
            raise PracticeOrderError(f"Gateway failed to cancel order {order_id}: {error_msg}")
        return True

    def cancel_all_orders(
        self,
        account_id: int,
        *,
        account_name: str | None = None,
    ) -> list[int]:
        """Cancel all open working orders for the authorized account."""
        self._verify_account_authorized(account_id, account_name)
        open_orders = self.search_open_orders(account_id, account_name=account_name)
        cancelled_ids: list[int] = []
        cancel_errors: list[str] = []
        for order in open_orders:
            oid = order.get("id") or order.get("orderId")
            if oid is not None:
                try:
                    self.cancel_order(account_id=account_id, order_id=int(oid), account_name=account_name)
                    cancelled_ids.append(int(oid))
                except Exception as exc:
                    cancel_errors.append(f"order {oid}: {exc}")
        if cancel_errors:
            raise PracticeOrderError(
                f"cancel_all_orders failed on {len(cancel_errors)} orders: {'; '.join(cancel_errors)}"
            )
        return cancelled_ids

    def search_orders(
        self,
        account_id: int,
        *,
        start_timestamp: str,
        end_timestamp: str | None = None,
        account_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search order history for the account."""
        self._verify_account_authorized(account_id, account_name)
        payload: dict[str, Any] = {
            "accountId": int(account_id),
            "startTimestamp": str(start_timestamp),
        }
        if end_timestamp is not None:
            payload["endTimestamp"] = str(end_timestamp)

        raw = self._post("/api/Order/search", payload)
        if isinstance(raw, list):
            return list(raw)
        if isinstance(raw, dict) and "orders" in raw:
            return list(raw["orders"])
        if isinstance(raw, dict) and "data" in raw and isinstance(raw["data"], list):
            return list(raw["data"])
        return []

    def search_open_orders(
        self,
        account_id: int,
        *,
        account_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve all currently working open orders for the account."""
        self._verify_account_authorized(account_id, account_name)
        payload = {"accountId": int(account_id)}
        raw = self._post("/api/Order/searchOpen", payload)
        if isinstance(raw, list):
            return list(raw)
        if isinstance(raw, dict) and "orders" in raw:
            return list(raw["orders"])
        if isinstance(raw, dict) and "data" in raw and isinstance(raw["data"], list):
            return list(raw["data"])
        return []

    def search_open_positions(
        self,
        account_id: int,
        *,
        account_name: str | None = None,
    ) -> list[PracticePosition]:
        """Search open positions for the account."""
        self._verify_account_authorized(account_id, account_name)
        payload = {"accountId": int(account_id)}
        raw = self._post("/api/Position/searchOpen", payload)
        items = raw if isinstance(raw, list) else raw.get("positions", raw.get("data", []))

        positions: list[PracticePosition] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            positions.append(
                PracticePosition(
                    position_id=int(item.get("id", 0)),
                    account_id=int(item.get("accountId", account_id)),
                    contract_id=str(item.get("contractId", "")),
                    position_type=int(item.get("type", 1)),
                    size=int(item.get("size", 0)),
                    average_price=float(item.get("averagePrice", 0.0)),
                )
            )
        return positions

    def flatten_contract(
        self,
        account_id: int,
        contract_id: str,
        *,
        account_name: str | None = None,
    ) -> bool:
        """Close/flatten open position for a specific contract."""
        if not self._practice_execution_enabled:
            raise PracticeOrderError("PRACTICE_EXECUTION_ENABLED is False; flatten denied")
        self._verify_account_authorized(account_id, account_name)
        if not contract_id:
            raise ValueError("contract_id must not be empty")

        payload = {
            "accountId": int(account_id),
            "contractId": str(contract_id),
        }
        raw = self._post("/api/Position/closeContract", payload)
        success = raw.get("success", True)
        if not success:
            error_msg = raw.get("errorMessage")
            raise PracticeOrderError(f"Gateway failed to close contract {contract_id}: {error_msg}")
        return True

    def flatten_all(
        self,
        account_id: int,
        *,
        account_name: str | None = None,
    ) -> list[str]:
        """Cancel all working orders and flatten all open positions for the account."""
        self._verify_account_authorized(account_id, account_name)
        # 1. Cancel all working orders first
        self.cancel_all_orders(account_id, account_name=account_name)

        # 2. Close each open position
        open_positions = self.search_open_positions(account_id, account_name=account_name)
        closed_contracts: list[str] = []
        flatten_errors: list[str] = []
        for pos in open_positions:
            if pos.size != 0 and pos.contract_id:
                try:
                    self.flatten_contract(account_id, pos.contract_id, account_name=account_name)
                    closed_contracts.append(pos.contract_id)
                except Exception as exc:
                    flatten_errors.append(f"contract {pos.contract_id}: {exc}")
        if flatten_errors:
            raise PracticeOrderError(
                f"flatten_all failed on {len(flatten_errors)} positions: {'; '.join(flatten_errors)}"
            )
        return closed_contracts

    def resolve_active_contract(self, symbol_id: str = "MNQ") -> str:
        """Resolve current active contract ID from TopstepX gateway."""
        payload = {
            "symbolId": str(symbol_id),
            "onlyActive": True,
        }
        raw = self._post("/api/Contract/search", payload)
        items = raw if isinstance(raw, list) else raw.get("contracts", raw.get("data", []))
        for item in items:
            if isinstance(item, dict) and item.get("active") is True:
                cid = item.get("id") or item.get("contractId")
                if cid:
                    return str(cid)
        raise PracticeOrderError(f"No active contract found for symbol {symbol_id!r}")

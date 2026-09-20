"""Offline comprehensive test suite for RT-9 Practice orders.

Tests all failure modes, permission gates, payload schemas, anti-OrderPending
discipline, bracket placement, flatten kill-switch, and circuit breaker integration
WITHOUT network access using JsonTransport mocks.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
import pytest

from src.realtime.clock import FrozenClock
from src.realtime.connectors.projectx import JsonTransport
from src.realtime.events import (
    EXEC_FILLED,
    EXEC_REJECTED,
    ORIGIN_LIVE,
    OrderIntent,
    REASON_MAX_RISK_PER_ORDER,
    RiskDecision,
    SIGNAL_LONG,
    SIGNAL_SHORT,
    Signal,
)
from src.realtime.risk import REASON_APPROVED
from src.realtime.interfaces import LIVE_EXECUTION_ENABLED
from src.realtime.orders.practice_client import (
    DEFAULT_PRACTICE_ACCOUNT_ALLOWLIST,
    DEFAULT_PRACTICE_EXECUTION_ENABLED,
    ORDER_SIDE_BUY,
    ORDER_SIDE_SELL,
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_FILLED,
    ORDER_STATUS_WORKING,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_STOP,
    BracketConfig,
    OrderPendingAmbiguityError,
    PracticeOrderClient,
    PracticeOrderError,
    UnauthorizedAccountError,
    generate_client_order_id,
    is_account_forbidden,
)
from src.realtime.practice_adapter import PracticeExecutionAdapter
from src.types import FundedAccountRules, create_practice_rules

TEST_ACCOUNT_NAME = "PRAC-V2-673085-85699223"
TEST_ACCOUNT_ID = 27765990
TEST_CONTRACT_ID = "CON_MNQ_202612"
FORBIDDEN_COMBINE_NAME = "1.5KCHCR-TEST-COMBINE"
FORBIDDEN_COMBINE_ID = 99999999


class RecordingTransport(JsonTransport):
    """Mock JSON transport that records requests and returns canned responses."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.canned_responses: dict[str, Any] = {}
        self.timeout_on_path: str | None = None
        self.raise_on_path: tuple[str, Exception] | None = None

    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        endpoint = url.split("api.topstepx.com")[-1] if "api.topstepx.com" in url else url
        self.calls.append((endpoint, dict(payload)))

        if self.timeout_on_path and endpoint == self.timeout_on_path:
            self.timeout_on_path = None
            raise TimeoutError(f"Simulated timeout on {endpoint}")

        if self.raise_on_path and endpoint == self.raise_on_path[0]:
            exc = self.raise_on_path[1]
            self.raise_on_path = None
            raise exc

        if endpoint in self.canned_responses:
            return self.canned_responses[endpoint]

        # Standard default responses
        if endpoint == "/api/Order/place":
            return {"orderId": 88880001, "success": True, "errorMessage": None}
        if endpoint == "/api/Order/cancel":
            return {"success": True, "errorMessage": None}
        if endpoint == "/api/Order/searchOpen":
            return []
        if endpoint == "/api/Order/search":
            return []
        if endpoint == "/api/Position/searchOpen":
            return []
        if endpoint == "/api/Position/closeContract":
            return {"success": True, "errorMessage": None}
        if endpoint == "/api/Contract/search":
            return [{"id": TEST_CONTRACT_ID, "name": "MNQZ6", "active": True}]

        return {"success": True}


# ---------------------------------------------------------------------------
# 1. Safety Gates & Forbidden Accounts
# ---------------------------------------------------------------------------

def test_live_execution_remains_strictly_false() -> None:
    assert LIVE_EXECUTION_ENABLED is False


def test_practice_execution_disabled_by_default() -> None:
    assert DEFAULT_PRACTICE_EXECUTION_ENABLED is False
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=False,
    )
    with pytest.raises(PracticeOrderError, match="PRACTICE_EXECUTION_ENABLED is False"):
        client.place_order(
            account_id=TEST_ACCOUNT_ID,
            contract_id=TEST_CONTRACT_ID,
            order_type=ORDER_TYPE_MARKET,
            side=ORDER_SIDE_BUY,
            size=1,
            account_name=TEST_ACCOUNT_NAME,
        )

    with pytest.raises(PracticeOrderError, match="PRACTICE_EXECUTION_ENABLED is False"):
        client.cancel_order(
            account_id=TEST_ACCOUNT_ID,
            order_id=12345,
            account_name=TEST_ACCOUNT_NAME,
        )

    with pytest.raises(PracticeOrderError, match="PRACTICE_EXECUTION_ENABLED is False"):
        client.flatten_contract(
            account_id=TEST_ACCOUNT_ID,
            contract_id=TEST_CONTRACT_ID,
            account_name=TEST_ACCOUNT_NAME,
        )


def test_account_allowlist_enforcement() -> None:
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )

    # Authorized account passes
    res = client.place_order(
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        order_type=ORDER_TYPE_MARKET,
        side=ORDER_SIDE_BUY,
        size=1,
        account_name=TEST_ACCOUNT_NAME,
    )
    assert res.order_id == 88880001

    # Unauthorized account fails
    with pytest.raises(UnauthorizedAccountError, match="not in allowlist"):
        client.place_order(
            account_id=11111111,
            contract_id=TEST_CONTRACT_ID,
            order_type=ORDER_TYPE_MARKET,
            side=ORDER_SIDE_BUY,
            size=1,
            account_name="OTHER-ACCOUNT",
        )


def test_combine_accounts_strictly_forbidden() -> None:
    assert is_account_forbidden(FORBIDDEN_COMBINE_NAME) is True
    assert is_account_forbidden("1.5kchcr-evaluation-account") is True
    assert is_account_forbidden(TEST_ACCOUNT_NAME) is False

    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID, FORBIDDEN_COMBINE_NAME),
    )

    with pytest.raises(UnauthorizedAccountError, match="FORBIDDEN account"):
        client.place_order(
            account_id=FORBIDDEN_COMBINE_ID,
            contract_id=TEST_CONTRACT_ID,
            order_type=ORDER_TYPE_MARKET,
            side=ORDER_SIDE_BUY,
            size=1,
            account_name=FORBIDDEN_COMBINE_NAME,
        )


# ---------------------------------------------------------------------------
# 2. Payload Correctness: Place, Brackets, Cancel, Search
# ---------------------------------------------------------------------------

def test_place_limit_order_payload() -> None:
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_ID,),
    )

    res = client.place_order(
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        order_type=ORDER_TYPE_LIMIT,
        side=ORDER_SIDE_BUY,
        size=1,
        limit_price=20450.25,
        custom_tag="fars-test-custom-tag-123",
    )
    assert res.order_id == 88880001
    assert res.custom_tag == "fars-test-custom-tag-123"

    endpoint, payload = transport.calls[0]
    assert endpoint == "/api/Order/place"
    assert payload["accountId"] == TEST_ACCOUNT_ID
    assert payload["contractId"] == TEST_CONTRACT_ID
    assert payload["type"] == ORDER_TYPE_LIMIT
    assert payload["side"] == ORDER_SIDE_BUY
    assert payload["size"] == 1
    assert payload["limitPrice"] == 20450.25
    assert payload["customTag"] == "fars-test-custom-tag-123"


def test_place_market_order_with_brackets_payload() -> None:
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_ID,),
    )

    client.place_order(
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        order_type=ORDER_TYPE_MARKET,
        side=ORDER_SIDE_SELL,
        size=1,
        stop_loss_bracket=BracketConfig(ticks=40, order_type=ORDER_TYPE_STOP),
        take_profit_bracket=BracketConfig(ticks=80, order_type=ORDER_TYPE_LIMIT),
    )

    endpoint, payload = transport.calls[0]
    assert endpoint == "/api/Order/place"
    assert payload["type"] == ORDER_TYPE_MARKET
    assert payload["side"] == ORDER_SIDE_SELL
    assert payload["size"] == 1
    assert payload["stopLossBracket"] == {"ticks": 40, "type": ORDER_TYPE_STOP}
    assert payload["takeProfitBracket"] == {"ticks": 80, "type": ORDER_TYPE_LIMIT}


def test_cancel_order_payload() -> None:
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_ID,),
    )

    success = client.cancel_order(account_id=TEST_ACCOUNT_ID, order_id=88880005)
    assert success is True
    endpoint, payload = transport.calls[0]
    assert endpoint == "/api/Order/cancel"
    assert payload["accountId"] == TEST_ACCOUNT_ID
    assert payload["orderId"] == 88880005


def test_flatten_contract_and_flatten_all_payload() -> None:
    transport = RecordingTransport()
    # Mock search_open_positions to return an open position
    transport.canned_responses["/api/Position/searchOpen"] = [
        {
            "id": 9001,
            "accountId": TEST_ACCOUNT_ID,
            "contractId": TEST_CONTRACT_ID,
            "type": 1,
            "size": 1,
            "averagePrice": 20500.0,
        }
    ]
    # Mock search_open_orders to return a working order
    transport.canned_responses["/api/Order/searchOpen"] = [
        {
            "id": 77770001,
            "accountId": TEST_ACCOUNT_ID,
            "contractId": TEST_CONTRACT_ID,
            "status": ORDER_STATUS_WORKING,
        }
    ]

    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_ID,),
    )

    closed = client.flatten_all(TEST_ACCOUNT_ID)
    assert closed == [TEST_CONTRACT_ID]

    # Verify calls: search_open_orders, cancel_order, search_open_positions, closeContract
    called_endpoints = [c[0] for c in transport.calls]
    assert "/api/Order/searchOpen" in called_endpoints
    assert "/api/Order/cancel" in called_endpoints
    assert "/api/Position/searchOpen" in called_endpoints
    assert "/api/Position/closeContract" in called_endpoints


# ---------------------------------------------------------------------------
# 3. Anti-OrderPending Discipline: Timeout and Search Before Resending
# ---------------------------------------------------------------------------

def test_anti_order_pending_recovers_order_when_found_in_search() -> None:
    transport = RecordingTransport()
    tag = "fars-test-pending-1"
    transport.timeout_on_path = "/api/Order/place"

    # In search_open_orders, simulate gateway having placed the order
    transport.canned_responses["/api/Order/searchOpen"] = [
        {
            "id": 99990001,
            "accountId": TEST_ACCOUNT_ID,
            "contractId": TEST_CONTRACT_ID,
            "type": ORDER_TYPE_LIMIT,
            "side": ORDER_SIDE_BUY,
            "size": 1,
            "status": ORDER_STATUS_WORKING,
            "customTag": tag,
            "limitPrice": 20400.0,
        }
    ]

    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_ID,),
    )

    res = client.place_order(
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        order_type=ORDER_TYPE_LIMIT,
        side=ORDER_SIDE_BUY,
        size=1,
        limit_price=20400.0,
        custom_tag=tag,
    )
    # Recovered safely without re-posting
    assert res.order_id == 99990001
    assert res.custom_tag == tag

    # Check that place_order was NOT called twice (no blind resend)
    place_calls = [c for c in transport.calls if c[0] == "/api/Order/place"]
    assert len(place_calls) == 1


def test_anti_order_pending_fails_closed_when_order_not_found() -> None:
    transport = RecordingTransport()
    tag = "fars-test-pending-not-found"
    transport.timeout_on_path = "/api/Order/place"
    transport.canned_responses["/api/Order/searchOpen"] = []
    transport.canned_responses["/api/Order/search"] = []

    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_ID,),
    )

    with pytest.raises(OrderPendingAmbiguityError, match="ambiguous; refusing blind resend"):
        client.place_order(
            account_id=TEST_ACCOUNT_ID,
            contract_id=TEST_CONTRACT_ID,
            order_type=ORDER_TYPE_LIMIT,
            side=ORDER_SIDE_BUY,
            size=1,
            limit_price=20400.0,
            custom_tag=tag,
        )

    # Ensure no duplicate place was attempted
    place_calls = [c for c in transport.calls if c[0] == "/api/Order/place"]
    assert len(place_calls) == 1


# ---------------------------------------------------------------------------
# 4. PracticeExecutionAdapter Integration with PracticeOrderClient
# ---------------------------------------------------------------------------

def test_practice_adapter_dispatches_through_order_client() -> None:
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(datetime(2026, 9, 20, 14, tzinfo=UTC))
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
        max_risk_dollars_per_order=200.0,
    )

    sig = Signal(
        event_id="sig-gw-1",
        source="strategy",
        timestamp=clock.now(),
        sequence=1,
        symbol=TEST_CONTRACT_ID,
        action=SIGNAL_LONG,
        origin=ORIGIN_LIVE,
    )
    dec = RiskDecision(
        event_id="decision-gw-1",
        source="risk",
        timestamp=clock.now(),
        sequence=1,
        signal_id="sig-gw-1",
        signal_source="strategy",
        approved=True,
        reason=REASON_APPROVED,
        origin=ORIGIN_LIVE,
    )
    intent = OrderIntent(
        event_id="intent-gw-1",
        source="strategy",
        timestamp=clock.now(),
        sequence=1,
        symbol=TEST_CONTRACT_ID,
        action=SIGNAL_LONG,
        risk_decision_id="decision-gw-1",
        origin=ORIGIN_LIVE,
        size=1,
        entry_price=20500.0,
        stop_price=20450.0,  # 50 pts * $2/pt * 1 = $100 <= $200
        target_price=20600.0,
    )

    report = adapter.submit(sig, dec, intent)
    assert report.status == EXEC_FILLED
    assert "GATEWAY_ORDER_PLACED" in report.reason
    assert adapter.open_positions[TEST_CONTRACT_ID] == 1

    # Check transport received the order with brackets
    place_calls = [c for c in transport.calls if c[0] == "/api/Order/place"]
    assert len(place_calls) == 1
    p = place_calls[0][1]
    assert p["accountId"] == TEST_ACCOUNT_ID
    assert p["contractId"] == TEST_CONTRACT_ID
    assert p["side"] == ORDER_SIDE_BUY
    assert p["size"] == 1
    assert p["stopLossBracket"] == {"ticks": 200, "type": ORDER_TYPE_STOP}  # 50 pts / 0.25 = 200 ticks


def test_practice_adapter_precheck_reduces_size_and_dispatches() -> None:
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(datetime(2026, 9, 20, 14, tzinfo=UTC))
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
        max_risk_dollars_per_order=200.0,
    )

    # Size 3 with 50 pts risk: 50 * $2 * 3 = $300 > $200
    # At 1 micro: 50 * $2 * 1 = $100 <= $200 -> reduces to 1 micro
    sig = Signal(
        event_id="sig-gw-2",
        source="strategy",
        timestamp=clock.now(),
        sequence=2,
        symbol=TEST_CONTRACT_ID,
        action=SIGNAL_LONG,
        origin=ORIGIN_LIVE,
    )
    dec = RiskDecision(
        event_id="decision-gw-2",
        source="risk",
        timestamp=clock.now(),
        sequence=2,
        signal_id="sig-gw-2",
        signal_source="strategy",
        approved=True,
        reason=REASON_APPROVED,
        origin=ORIGIN_LIVE,
    )
    intent = OrderIntent(
        event_id="intent-gw-reduce",
        source="strategy",
        timestamp=clock.now(),
        sequence=2,
        symbol=TEST_CONTRACT_ID,
        action=SIGNAL_LONG,
        risk_decision_id="decision-gw-2",
        origin=ORIGIN_LIVE,
        size=3,
        entry_price=20500.0,
        stop_price=20450.0,
    )

    report = adapter.submit(sig, dec, intent)
    assert report.status == EXEC_FILLED
    assert "REDUCED_TO_1_MICRO" in report.reason
    assert adapter.open_positions[TEST_CONTRACT_ID] == 1

    place_calls = [c for c in transport.calls if c[0] == "/api/Order/place"]
    assert place_calls[0][1]["size"] == 1


def test_practice_adapter_precheck_vetoes_when_1_micro_exceeds_max_risk() -> None:
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(datetime(2026, 9, 20, 14, tzinfo=UTC))
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
        max_risk_dollars_per_order=200.0,
    )

    # 150 pts risk * $2/pt * 1 = $300 > $200 -> VETO
    sig = Signal(
        event_id="sig-gw-3",
        source="strategy",
        timestamp=clock.now(),
        sequence=3,
        symbol=TEST_CONTRACT_ID,
        action=SIGNAL_LONG,
        origin=ORIGIN_LIVE,
    )
    dec = RiskDecision(
        event_id="decision-gw-3",
        source="risk",
        timestamp=clock.now(),
        sequence=3,
        signal_id="sig-gw-3",
        signal_source="strategy",
        approved=True,
        reason=REASON_APPROVED,
        origin=ORIGIN_LIVE,
    )
    intent = OrderIntent(
        event_id="intent-gw-veto",
        source="strategy",
        timestamp=clock.now(),
        sequence=3,
        symbol=TEST_CONTRACT_ID,
        action=SIGNAL_LONG,
        risk_decision_id="decision-gw-3",
        origin=ORIGIN_LIVE,
        size=1,
        entry_price=20500.0,
        stop_price=20350.0,
    )

    report = adapter.submit(sig, dec, intent)
    assert report.status == EXEC_REJECTED
    assert REASON_MAX_RISK_PER_ORDER in report.reason

    # Zero orders sent to gateway
    place_calls = [c for c in transport.calls if c[0] == "/api/Order/place"]
    assert len(place_calls) == 0


def test_practice_adapter_flatten_invokes_client_cancel_all_and_close() -> None:
    transport = RecordingTransport()
    transport.canned_responses["/api/Position/searchOpen"] = [
        {
            "id": 9001,
            "accountId": TEST_ACCOUNT_ID,
            "contractId": TEST_CONTRACT_ID,
            "type": 1,
            "size": 1,
            "averagePrice": 20500.0,
        }
    ]
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(datetime(2026, 9, 20, 14, tzinfo=UTC))
    flatten_called = False

    def on_flatten() -> None:
        nonlocal flatten_called
        flatten_called = True

    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
        flatten_handler=on_flatten,
    )

    flattened = adapter.flatten(TEST_CONTRACT_ID)
    assert flattened == [TEST_CONTRACT_ID]
    assert flatten_called is True
    assert adapter.open_positions.get(TEST_CONTRACT_ID, 0) == 0

    called_endpoints = [c[0] for c in transport.calls]
    assert "/api/Order/searchOpen" in called_endpoints
    assert "/api/Position/closeContract" in called_endpoints

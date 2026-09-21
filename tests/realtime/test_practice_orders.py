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
    EXEC_ACCEPTED,
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
            if not getattr(self, "keep_positions_on_close", False):
                self.canned_responses["/api/Position/searchOpen"] = []
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
    assert report.status == EXEC_ACCEPTED
    assert "GATEWAY_ORDER_PLACED" in report.reason
    assert adapter.open_positions.get(TEST_CONTRACT_ID, 0) == 0

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
    assert report.status == EXEC_ACCEPTED
    assert "REDUCED_TO_1_MICRO" in report.reason
    assert adapter.open_positions.get(TEST_CONTRACT_ID, 0) == 0

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


# ---------------------------------------------------------------------------
# 5. Fixes Verification: 1 Micro Clamp, Missing Prices Veto, Verified Flatten
# ---------------------------------------------------------------------------

def test_order_with_size_3_small_stop_clamped_to_1_micro() -> None:
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

    # Size 3 with tiny 10 pt stop: 10 * $2 * 3 = $60 <= $200 (fits risk, but violates 1 micro limit)
    sig = Signal("sig-clamp", "strategy", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-clamp", "risk", clock.now(), 1, "sig-clamp", "strategy", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent(
        event_id="intent-clamp",
        source="strategy",
        timestamp=clock.now(),
        sequence=1,
        symbol=TEST_CONTRACT_ID,
        action=SIGNAL_LONG,
        risk_decision_id="dec-clamp",
        origin=ORIGIN_LIVE,
        size=3,
        entry_price=20500.0,
        stop_price=20490.0,
    )

    report = adapter.submit(sig, dec, intent)
    assert report.status == EXEC_ACCEPTED
    assert "CLAMPED_TO_1_MICRO" in report.reason

    place_calls = [c for c in transport.calls if c[0] == "/api/Order/place"]
    assert len(place_calls) == 1
    assert place_calls[0][1]["size"] == 1


def test_client_directly_rejects_size_greater_than_1() -> None:
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    with pytest.raises(PracticeOrderError, match="DECLARED_LIMIT_VIOLATION"):
        client.place_order(
            account_id=TEST_ACCOUNT_ID,
            contract_id=TEST_CONTRACT_ID,
            order_type=ORDER_TYPE_LIMIT,
            side=ORDER_SIDE_BUY,
            size=2,
            limit_price=20450.0,
        )


def test_practice_adapter_vetoes_gateway_intent_without_prices() -> None:
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
    )

    # Missing both entry and stop
    sig = Signal("sig-noprice", "strategy", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-noprice", "risk", clock.now(), 1, "sig-noprice", "strategy", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent(
        event_id="intent-noprice",
        source="strategy",
        timestamp=clock.now(),
        sequence=1,
        symbol=TEST_CONTRACT_ID,
        action=SIGNAL_LONG,
        risk_decision_id="dec-noprice",
        origin=ORIGIN_LIVE,
        size=1,
        entry_price=None,
        stop_price=None,
    )

    report = adapter.submit(sig, dec, intent)
    assert report.status == EXEC_REJECTED
    assert "MISSING_ENTRY_OR_STOP_PRICE" in report.reason


def test_practice_adapter_vetoes_gateway_intent_with_stop_without_entry() -> None:
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
    )

    # Stop without entry
    sig = Signal("sig-noentry", "strategy", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-noentry", "risk", clock.now(), 1, "sig-noentry", "strategy", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent(
        event_id="intent-noentry",
        source="strategy",
        timestamp=clock.now(),
        sequence=1,
        symbol=TEST_CONTRACT_ID,
        action=SIGNAL_LONG,
        risk_decision_id="dec-noentry",
        origin=ORIGIN_LIVE,
        size=1,
        entry_price=None,
        stop_price=20400.0,
    )

    report = adapter.submit(sig, dec, intent)
    assert report.status == EXEC_REJECTED
    assert "MISSING_ENTRY_OR_STOP_PRICE" in report.reason


def test_flatten_unconfirmed_raises_and_preserves_position() -> None:
    transport = RecordingTransport()
    transport.keep_positions_on_close = True
    # Mock position that persists after closeContract call
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
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
    )
    # Give adapter a tracked open position
    adapter._open_positions[TEST_CONTRACT_ID] = 1

    with pytest.raises(PracticeOrderError, match="FLATTEN_UNCONFIRMED"):
        adapter.flatten(TEST_CONTRACT_ID)

    # Position is preserved, NOT marked 0
    assert adapter.open_positions[TEST_CONTRACT_ID] == 1


def test_market_close_cutoff_1510_ct_vetoes_new_orders() -> None:
    from zoneinfo import ZoneInfo
    chi_tz = ZoneInfo("America/Chicago")
    # Set clock to a Monday at 15:15 CT
    t_cutoff = datetime(2026, 9, 21, 15, 15, tzinfo=chi_tz)
    clock = FrozenClock(t_cutoff)

    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )

    sig = Signal("sig-cutoff", "strategy", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-cutoff", "risk", clock.now(), 1, "sig-cutoff", "strategy", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent(
        event_id="intent-cutoff",
        source="strategy",
        timestamp=clock.now(),
        sequence=1,
        symbol=TEST_CONTRACT_ID,
        action=SIGNAL_LONG,
        risk_decision_id="dec-cutoff",
        origin=ORIGIN_LIVE,
        size=1,
        entry_price=20500.0,
        stop_price=20450.0,
    )

    report = adapter.submit(sig, dec, intent)
    assert report.status == EXEC_REJECTED
    assert "MARKET_CLOSE_CUTOFF_REACHED" in report.reason


def test_market_close_cutoff_triggers_flatten() -> None:
    from zoneinfo import ZoneInfo
    chi_tz = ZoneInfo("America/Chicago")
    # Monday at 15:15 CT
    t_cutoff = datetime(2026, 9, 21, 15, 15, tzinfo=chi_tz)
    clock = FrozenClock(t_cutoff)

    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    adapter._open_positions[TEST_CONTRACT_ID] = 1

    flattened = adapter.check_market_close_cutoff()
    assert flattened is True
    assert adapter.open_positions[TEST_CONTRACT_ID] == 0


def test_live_acceptance_path_offline_with_doubles(tmp_path: Any) -> None:
    from pathlib import Path
    from zoneinfo import ZoneInfo
    from src.realtime.acceptance_rt9 import MockTopstepXGatewayTransport, run_live_acceptance
    from src.realtime.connectors.projectx import ProjectXClient, ProjectXCredentials

    # Mock gateway transport supporting both ProjectXClient list_accounts and PracticeOrderClient orders
    mock_transport = MockTopstepXGatewayTransport(target_account_id=TEST_ACCOUNT_ID)
    orig_mock_post = mock_transport.post

    def patched_mock_post(url: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout: float) -> Mapping[str, Any]:
        endpoint = url.split("api.topstepx.com")[-1] if "api.topstepx.com" in url else url
        if endpoint == "/api/Contract/search":
            mock_transport.requests_log.append({
                "endpoint": endpoint,
                "payload": dict(payload),
                "timestamp": datetime.now(UTC).isoformat(),
            })
            return {
                "success": True,
                "errorCode": 0,
                "errorMessage": None,
                "contracts": [
                    {
                        "id": "CON_MNQ_202612",
                        "name": "MNQZ6",
                        "description": "Micro E-mini Nasdaq-100",
                        "tickSize": 0.25,
                        "tickValue": 0.50,
                        "activeContract": True,
                        "symbolId": "MNQ",
                    }
                ],
            }
        return orig_mock_post(url, headers, payload, timeout)

    mock_transport.post = patched_mock_post  # type: ignore[assignment]

    # Real ProjectXClient with mock transport (exercises authenticate + list_accounts)
    credentials = ProjectXCredentials(username="test_live_user", api_key="test_live_key")
    px_client = ProjectXClient(credentials, transport=mock_transport)
    px_client.authenticate()

    # Real PracticeOrderClient with mock transport
    practice_client = PracticeOrderClient(
        token_provider=px_client.session_token,
        transport=mock_transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )

    # Monday 10:00 CT (CME market open)
    chi_tz = ZoneInfo("America/Chicago")
    t_open = datetime(2026, 9, 21, 10, 0, tzinfo=chi_tz)
    clock = FrozenClock(t_open)

    log_file = Path(tmp_path) / "acceptance_live_session.jsonl"
    report_file = Path(tmp_path) / "acceptance_live_report.json"

    # Execute the live acceptance path with doubles
    report = run_live_acceptance(
        force_market_check=False,  # verifies CME market hours check naturally passes
        auto_confirm=True,
        px_client=px_client,
        order_client=practice_client,
        clock=clock,
        log_path=log_file,
        report_path=report_file,
    )

    assert report.status == "PASS"
    assert len(report.steps) == 8
    assert all(s.passed for s in report.steps)
    assert report.account_id == TEST_ACCOUNT_ID
    assert report.account_name == TEST_ACCOUNT_NAME
    assert log_file.exists()
    assert report_file.exists()


# ===========================================================================
# RT-9 F2: Contract/search exact payload and fail-closed parsing tests
# ===========================================================================


class StrictContractSearchTransport(JsonTransport):
    """Strict mock transport simulating real TopstepX /api/Contract/search behavior."""

    def __init__(self, response: Mapping[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response = response or {
            "success": True,
            "errorCode": 0,
            "errorMessage": None,
            "contracts": [
                {
                    "id": "CON.F.US.MNQ.Z26",
                    "name": "MNQZ6",
                    "description": "Micro E-mini Nasdaq-100: December 2026",
                    "tickSize": 0.25,
                    "tickValue": 0.5,
                    "activeContract": True,
                    "symbolId": "F.US.MNQ",
                }
            ],
        }

    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        endpoint = url.split("api.topstepx.com")[-1] if "api.topstepx.com" in url else url
        self.calls.append({"endpoint": endpoint, "payload": dict(payload)})

        if endpoint == "/api/Contract/search":
            # Real TopstepX rejects obsolete symbolId and onlyActive with HTTP 400
            if "symbolId" in payload or "onlyActive" in payload:
                raise PracticeOrderError("ProjectX HTTP error 400: obsolete params symbolId/onlyActive")
            # Real TopstepX requires exact {"searchText": ..., "live": False}
            if payload.get("searchText") != "MNQ" or payload.get("live") is not False:
                raise PracticeOrderError(f"Unexpected payload: {payload}")
            return self.response

        return {"success": True}


def test_resolve_active_contract_payload_and_http400_on_obsolete_params() -> None:
    """RED 1: TopstepX rejects symbolId/onlyActive with HTTP 400; requires searchText and live=False."""
    transport = StrictContractSearchTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
    )

    contract_id = client.resolve_active_contract("MNQ")
    assert contract_id == "CON.F.US.MNQ.Z26"
    assert len(transport.calls) == 1
    assert transport.calls[0]["endpoint"] == "/api/Contract/search"
    assert transport.calls[0]["payload"] == {"searchText": "MNQ", "live": False}


def test_resolve_active_contract_fails_on_unsuccessful_response() -> None:
    """RED 2.1: Gateway returning success=False raises PracticeOrderError with actionable message."""
    class ErrorTransport(JsonTransport):
        def post(self, url: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout: float) -> Mapping[str, Any]:
            return {"success": False, "errorMessage": "Invalid search query", "errorCode": 400}

    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=ErrorTransport(),
        practice_execution_enabled=True,
    )
    with pytest.raises(PracticeOrderError, match="Contract/search failed: Invalid search query"):
        client.resolve_active_contract("MNQ")


@pytest.mark.parametrize(
    "bad_contracts_payload",
    [
        {"success": True},  # missing contracts field
        {"success": True, "contracts": "not-a-list"},  # string instead of list
        {"success": True, "contracts": None},  # None instead of list
        {"success": True, "contracts": [123, "invalid"]},  # non-dict items in list
    ],
)
def test_resolve_active_contract_fails_on_missing_or_invalid_contracts_field(
    bad_contracts_payload: Mapping[str, Any],
) -> None:
    """RED 2.2: Missing or invalid contracts field raises PracticeOrderError without raising TypeError."""
    class BadEnvelopeTransport(JsonTransport):
        def post(self, url: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout: float) -> Mapping[str, Any]:
            return bad_contracts_payload

    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=BadEnvelopeTransport(),
        practice_execution_enabled=True,
    )
    with pytest.raises(PracticeOrderError, match="Contract/search response missing or invalid 'contracts' list"):
        client.resolve_active_contract("MNQ")


def test_resolve_active_contract_fails_when_zero_active_contracts() -> None:
    """RED 2.3: Zero active contracts raises PracticeOrderError."""
    class InactiveTransport(JsonTransport):
        def post(self, url: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout: float) -> Mapping[str, Any]:
            return {
                "success": True,
                "contracts": [
                    {
                        "id": "CON.INACTIVE",
                        "name": "MNQU6",
                        "activeContract": False,
                        "symbolId": "F.US.MNQ",
                    }
                ],
            }

    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=InactiveTransport(),
        practice_execution_enabled=True,
    )
    with pytest.raises(PracticeOrderError, match="no active contract found for symbol 'MNQ'"):
        client.resolve_active_contract("MNQ")


def test_resolve_active_contract_fails_when_ambiguous_active_contracts() -> None:
    """RED 2.4: More than one active contract raises PracticeOrderError fail-closed."""
    class AmbiguousTransport(JsonTransport):
        def post(self, url: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout: float) -> Mapping[str, Any]:
            return {
                "success": True,
                "contracts": [
                    {
                        "id": "CON.MNQ.1",
                        "name": "MNQZ6",
                        "activeContract": True,
                        "symbolId": "F.US.MNQ",
                    },
                    {
                        "id": "CON.MNQ.2",
                        "name": "MNQH7",
                        "activeContract": True,
                        "symbolId": "F.US.MNQ",
                    },
                ],
            }

    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=AmbiguousTransport(),
        practice_execution_enabled=True,
    )
    with pytest.raises(PracticeOrderError, match="ambiguous active contracts for symbol 'MNQ'"):
        client.resolve_active_contract("MNQ")


def test_resolve_active_contract_requires_active_contract_field_ignoring_obsolete_active() -> None:
    """RED 2.5: Must use activeContract field, ignoring obsolete active field."""
    # Case A: active=True but activeContract=False -> must be rejected as inactive
    class ObsoleteActiveTransport(JsonTransport):
        def post(self, url: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout: float) -> Mapping[str, Any]:
            return {
                "success": True,
                "contracts": [
                    {
                        "id": "CON.OBSOLETE",
                        "name": "MNQZ6",
                        "active": True,  # obsolete field
                        "activeContract": False,  # real field
                        "symbolId": "F.US.MNQ",
                    }
                ],
            }

    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=ObsoleteActiveTransport(),
        practice_execution_enabled=True,
    )
    with pytest.raises(PracticeOrderError, match="no active contract found for symbol 'MNQ'"):
        client.resolve_active_contract("MNQ")

    # Case B: active=False but activeContract=True -> must be accepted based on activeContract
    class RealActiveContractTransport(JsonTransport):
        def post(self, url: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout: float) -> Mapping[str, Any]:
            return {
                "success": True,
                "contracts": [
                    {
                        "id": "CON.VALID",
                        "name": "MNQZ6",
                        "active": False,  # obsolete field false
                        "activeContract": True,  # real field true
                        "symbolId": "F.US.MNQ",
                    }
                ],
            }

    client_b = PracticeOrderClient(
        token_provider="dummy-token",
        transport=RealActiveContractTransport(),
        practice_execution_enabled=True,
    )
    assert client_b.resolve_active_contract("MNQ") == "CON.VALID"


def test_resolve_active_contract_strict_symbol_matching_prevents_substring_matches() -> None:
    """Matching: Search for MNQ must not match NQ, and search for NQ must not match MNQ."""
    class MixedContractsTransport(JsonTransport):
        def post(self, url: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout: float) -> Mapping[str, Any]:
            return {
                "success": True,
                "contracts": [
                    {
                        "id": "CON.F.US.NQ.Z26",
                        "name": "NQZ6",
                        "activeContract": True,
                        "symbolId": "F.US.NQ",
                    },
                    {
                        "id": "CON.F.US.MNQ.Z26",
                        "name": "MNQZ6",
                        "activeContract": True,
                        "symbolId": "F.US.MNQ",
                    },
                ],
            }

    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=MixedContractsTransport(),
        practice_execution_enabled=True,
    )

    # Resolving MNQ must match ONLY the MNQ contract, not NQ
    assert client.resolve_active_contract("MNQ") == "CON.F.US.MNQ.Z26"

    # Resolving NQ must match ONLY the NQ contract, not MNQ
    assert client.resolve_active_contract("NQ") == "CON.F.US.NQ.Z26"


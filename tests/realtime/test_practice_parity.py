"""Tests for Practice parity and honest fills (Bloque P1).

Verifies:
1. Prerequisite parity: fallback rejects intents without prices identically to gateway.
2. Broker state parity: gateway returning WORKING produces EXEC_ACCEPTED without position mutation.
3. Risk pre-check & 1 micro clamp parity across fallback and gateway.
4. Fail-closed handling for unknown states and idempotency preservation.
"""

from __future__ import annotations

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
    RiskDecision,
    SIGNAL_FLAT,
    SIGNAL_LONG,
    SIGNAL_SHORT,
    Signal,
)
from src.realtime.orders.practice_client import (
    ORDER_SIDE_BUY,
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_EXPIRED,
    ORDER_STATUS_FILLED,
    ORDER_STATUS_REJECTED,
    ORDER_STATUS_WORKING,
    ORDER_TYPE_LIMIT,
    PracticeOrderClient,
    PracticeOrderResult,
)
from src.realtime.practice_adapter import PracticeExecutionAdapter
from src.realtime.risk import REASON_APPROVED

TEST_ACCOUNT_NAME = "PRAC-V2-673085-85699223"
TEST_ACCOUNT_ID = 27765990
TEST_CONTRACT_ID = "CON_MNQ_202612"
TEST_TIME = datetime(2026, 9, 21, 14, 0, tzinfo=UTC)


# ===========================================================================
# Cycle 1: Fallback rejects intent without prices (Parity Requirement A)
# ===========================================================================

def test_fallback_rejects_intent_without_prices() -> None:
    """Practice fallback (no order_client) must reject intents missing entry or stop price."""
    clock = FrozenClock(TEST_TIME)
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=None,  # Fallback offline simulation
    )

    sig = Signal("sig-fb-1", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-fb-1", "risk", clock.now(), 1, "sig-fb-1", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent(
        event_id="oi-fb-1",
        source="strat",
        timestamp=clock.now(),
        sequence=1,
        symbol=TEST_CONTRACT_ID,
        action=SIGNAL_LONG,
        risk_decision_id="dec-fb-1",
        origin=ORIGIN_LIVE,
        size=1,
        entry_price=None,
        stop_price=None,
    )

    report = adapter.submit(sig, dec, intent)
    assert report.status == EXEC_REJECTED
    assert "MISSING_ENTRY_OR_STOP_PRICE" in report.reason
    assert adapter.open_positions.get(TEST_CONTRACT_ID, 0) == 0


# ===========================================================================
# Cycle 2: Gateway WORKING -> EXEC_ACCEPTED and untouched position (Requirement B)
# ===========================================================================

class RecordingTransport(JsonTransport):
    """Minimal mock transport recording calls and returning canned responses."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.canned_responses: dict[str, Any] = {}
        self.order_id_sequence: list[int] = []

    def post(
        self,
        url: str,
        headers: Any,
        payload: Any,
        timeout: float,
    ) -> dict[str, Any]:
        endpoint = url.split("api.topstepx.com")[-1] if "api.topstepx.com" in url else url
        self.calls.append((endpoint, dict(payload)))
        if endpoint in self.canned_responses:
            resp = self.canned_responses[endpoint]
            if callable(resp):
                return resp(payload)
            return resp
        if endpoint == "/api/Order/place":
            if self.order_id_sequence:
                return {"orderId": self.order_id_sequence.pop(0), "success": True, "errorMessage": None}
            return {"orderId": 88880001, "success": True, "errorMessage": None}
        return {"success": True}


def test_gateway_working_order_returns_exec_accepted_and_leaves_position_untouched() -> None:
    """When gateway returns status WORKING, adapter must return EXEC_ACCEPTED and not mutate position."""
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(TEST_TIME)
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
        max_risk_dollars_per_order=200.0,
    )

    sig = Signal("sig-gw-1", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-gw-1", "risk", clock.now(), 1, "sig-gw-1", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent(
        event_id="oi-gw-1",
        source="strat",
        timestamp=clock.now(),
        sequence=1,
        symbol=TEST_CONTRACT_ID,
        action=SIGNAL_LONG,
        risk_decision_id="dec-gw-1",
        origin=ORIGIN_LIVE,
        size=1,
        entry_price=20500.0,
        stop_price=20450.0,  # 50 pts * $2 * 1 = $100 <= $200
        target_price=20600.0,
    )

    report = adapter.submit(sig, dec, intent)
    assert report.status == EXEC_ACCEPTED
    assert "GATEWAY_ORDER_PLACED" in report.reason
    assert adapter.open_positions.get(TEST_CONTRACT_ID, 0) == 0


# ===========================================================================
# Cycle 3: Risk pre-check & clamp parity across fallback and gateway (Requirement A)
# ===========================================================================

def test_risk_precheck_veto_parity_fallback_and_gateway() -> None:
    """When risk exceeds limit, both fallback and gateway must reject identically with MAX_RISK_PER_ORDER."""
    clock = FrozenClock(TEST_TIME)

    # 1. Fallback adapter
    fb_adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=None,
        max_risk_dollars_per_order=200.0,
    )

    # 2. Gateway adapter
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    gw_adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
        max_risk_dollars_per_order=200.0,
    )

    # 150 pts risk: 150 * $2 * 1 = $300 > $200 -> VETO
    sig = Signal("sig-veto", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-veto", "risk", clock.now(), 1, "sig-veto", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent(
        event_id="oi-veto",
        source="strat",
        timestamp=clock.now(),
        sequence=1,
        symbol=TEST_CONTRACT_ID,
        action=SIGNAL_LONG,
        risk_decision_id="dec-veto",
        origin=ORIGIN_LIVE,
        size=1,
        entry_price=20500.0,
        stop_price=20350.0,
    )

    rep_fb = fb_adapter.submit(sig, dec, intent)
    rep_gw = gw_adapter.submit(sig, dec, intent)

    # Both must reject with MAX_RISK_PER_ORDER
    assert rep_fb.status == EXEC_REJECTED
    assert rep_gw.status == EXEC_REJECTED
    assert "MAX_RISK_PER_ORDER" in rep_fb.reason
    assert "MAX_RISK_PER_ORDER" in rep_gw.reason
    assert fb_adapter.open_positions.get(TEST_CONTRACT_ID, 0) == 0
    assert gw_adapter.open_positions.get(TEST_CONTRACT_ID, 0) == 0
    assert len(transport.calls) == 0


def test_size_clamp_and_reduction_parity_fallback_and_gateway() -> None:
    """Both fallback and gateway clamp size > 1 to 1 micro and note REDUCED / CLAMPED."""
    clock = FrozenClock(TEST_TIME)

    fb_adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=None,
        max_risk_dollars_per_order=200.0,
    )

    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    gw_adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
        max_risk_dollars_per_order=200.0,
    )

    # Case A: Size 3 with 50 pt stop ($300 > $200, 1 micro = $100 <= $200) -> REDUCED_TO_1_MICRO
    sig_a = Signal("sig-red", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec_a = RiskDecision("dec-red", "risk", clock.now(), 1, "sig-red", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent_a = OrderIntent(
        event_id="oi-red",
        source="strat",
        timestamp=clock.now(),
        sequence=1,
        symbol=TEST_CONTRACT_ID,
        action=SIGNAL_LONG,
        risk_decision_id="dec-red",
        origin=ORIGIN_LIVE,
        size=3,
        entry_price=20500.0,
        stop_price=20450.0,
    )

    rep_fb_a = fb_adapter.submit(sig_a, dec_a, intent_a)
    rep_gw_a = gw_adapter.submit(sig_a, dec_a, intent_a)

    assert "REDUCED_TO_1_MICRO" in rep_fb_a.reason
    assert "REDUCED_TO_1_MICRO" in rep_gw_a.reason
    # Fallback fills size 1
    assert rep_fb_a.status == EXEC_FILLED
    assert fb_adapter.open_positions[TEST_CONTRACT_ID] == 1
    # Gateway accepts (working) without mutating position
    assert rep_gw_a.status == EXEC_ACCEPTED
    assert gw_adapter.open_positions.get(TEST_CONTRACT_ID, 0) == 0
    assert transport.calls[0][1]["size"] == 1


# ===========================================================================
# Cycle 4: Error handling, edge cases & idempotency protection
# ===========================================================================

def test_fallback_rejects_intent_with_entry_without_stop() -> None:
    """Fallback must reject intent that has entry_price but missing stop_price."""
    clock = FrozenClock(TEST_TIME)
    adapter = PracticeExecutionAdapter(clock=clock, account_name=TEST_ACCOUNT_NAME, account_id=TEST_ACCOUNT_ID)
    sig = Signal("sig-part-1", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-part-1", "risk", clock.now(), 1, "sig-part-1", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent("oi-part-1", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, "dec-part-1", origin=ORIGIN_LIVE, entry_price=20500.0, stop_price=None)

    report = adapter.submit(sig, dec, intent)
    assert report.status == EXEC_REJECTED
    assert "MISSING_ENTRY_OR_STOP_PRICE" in report.reason
    assert adapter.open_positions.get(TEST_CONTRACT_ID, 0) == 0


def test_fallback_rejects_intent_with_stop_without_entry() -> None:
    """Fallback must reject intent that has stop_price but missing entry_price."""
    clock = FrozenClock(TEST_TIME)
    adapter = PracticeExecutionAdapter(clock=clock, account_name=TEST_ACCOUNT_NAME, account_id=TEST_ACCOUNT_ID)
    sig = Signal("sig-part-2", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_SHORT, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-part-2", "risk", clock.now(), 1, "sig-part-2", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent("oi-part-2", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_SHORT, "dec-part-2", origin=ORIGIN_LIVE, entry_price=None, stop_price=20550.0)

    report = adapter.submit(sig, dec, intent)
    assert report.status == EXEC_REJECTED
    assert "MISSING_ENTRY_OR_STOP_PRICE" in report.reason
    assert adapter.open_positions.get(TEST_CONTRACT_ID, 0) == 0


def test_gateway_confirmed_fill_produces_exec_filled_and_updates_position() -> None:
    """When gateway confirms ORDER_STATUS_FILLED, adapter returns EXEC_FILLED and mutates position."""
    transport = RecordingTransport()
    # Configure mock gateway to return filled status
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(TEST_TIME)
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
    )

    # Monkeypatch client.place_order to return FILLED result
    import dataclasses
    original_place = client.place_order
    def mock_place(*args: Any, **kwargs: Any) -> PracticeOrderResult:
        res = original_place(*args, **kwargs)
        return dataclasses.replace(res, status=ORDER_STATUS_FILLED)
    client.place_order = mock_place  # type: ignore[assignment]

    sig = Signal("sig-fill", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-fill", "risk", clock.now(), 1, "sig-fill", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent("oi-fill", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, "dec-fill", origin=ORIGIN_LIVE, entry_price=20500.0, stop_price=20450.0)

    report = adapter.submit(sig, dec, intent)
    assert report.status == EXEC_FILLED
    assert adapter.open_positions[TEST_CONTRACT_ID] == 1


def test_gateway_unknown_status_fails_closed_without_fill() -> None:
    """When gateway returns an unexpected/unknown status, adapter fails closed with EXEC_REJECTED."""
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(TEST_TIME)
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
    )

    # Monkeypatch client.place_order to return unknown status (e.g. 99)
    import dataclasses
    original_place = client.place_order
    def mock_place(*args: Any, **kwargs: Any) -> PracticeOrderResult:
        res = original_place(*args, **kwargs)
        return dataclasses.replace(res, status=99)
    client.place_order = mock_place  # type: ignore[assignment]

    sig = Signal("sig-unk", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-unk", "risk", clock.now(), 1, "sig-unk", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent("oi-unk", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, "dec-unk", origin=ORIGIN_LIVE, entry_price=20500.0, stop_price=20450.0)

    report = adapter.submit(sig, dec, intent)
    assert report.status == EXEC_REJECTED
    assert "UNKNOWN_ORDER_STATUS" in report.reason
    assert adapter.open_positions.get(TEST_CONTRACT_ID, 0) == 0


def test_idempotency_resubmitting_same_intent_returns_cached_report() -> None:
    """Resubmitting identical intent must return identical cached report without duplicate actions."""
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(TEST_TIME)
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
    )

    sig = Signal("sig-idem", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-idem", "risk", clock.now(), 1, "sig-idem", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent("oi-idem", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, "dec-idem", origin=ORIGIN_LIVE, entry_price=20500.0, stop_price=20450.0)

    report1 = adapter.submit(sig, dec, intent)
    report2 = adapter.submit(sig, dec, intent)

    assert report1 == report2
    assert report1.status == EXEC_ACCEPTED
    assert len(transport.calls) == 1


# ===========================================================================
# P1.1 Hardening Tests (W1 - W7)
# ===========================================================================

def test_working_order_tracked_and_cutoff_flattens_even_with_zero_local_position() -> None:
    """W1: WORKING order is tracked; cutoff triggers flatten even when local position is zero."""
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    # Start at 14:00 CT (19:00 UTC) - before cutoff
    clock = FrozenClock(datetime(2026, 9, 21, 19, 0, tzinfo=UTC))
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
    )

    sig = Signal("sig-w1-1", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-w1-1", "risk", clock.now(), 1, "sig-w1-1", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent("oi-w1-1", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, "dec-w1-1", origin=ORIGIN_LIVE, entry_price=20500.0, stop_price=20450.0)

    report = adapter.submit(sig, dec, intent)
    assert report.status == EXEC_ACCEPTED
    assert adapter.open_positions.get(TEST_CONTRACT_ID, 0) == 0

    # Assert working order is tracked
    assert 88880001 in adapter.working_order_ids
    assert adapter.working_order_count == 1

    # Configure transport so gateway returns the working order in searchOpen
    transport.canned_responses["/api/Order/searchOpen"] = [
        {"id": 88880001, "orderId": 88880001, "accountId": TEST_ACCOUNT_ID, "contractId": TEST_CONTRACT_ID}
    ]

    # Advance clock past cutoff (15:15 CT = 20:15 UTC)
    clock.set(datetime(2026, 9, 21, 20, 15, tzinfo=UTC))

    # Trigger cutoff check
    flattened = adapter.check_market_close_cutoff()
    assert flattened is True

    # Working order tracking must be cleared after verified flatten
    assert adapter.working_order_count == 0
    assert 88880001 not in adapter.working_order_ids

    # Verify cancel was dispatched on gateway for the working order
    endpoints = [call[0] for call in transport.calls]
    assert "/api/Order/cancel" in endpoints
    cancel_calls = [c for c in transport.calls if c[0] == "/api/Order/cancel"]
    assert cancel_calls[0][1]["orderId"] == 88880001


def test_flatten_failure_preserves_working_order_tracking() -> None:
    """W1: If flatten or verification fails, PracticeOrderError is raised and tracking preserved."""
    from src.realtime.orders.practice_client import PracticeOrderError

    transport = RecordingTransport()
    # Configure transport to simulate persisting position on gateway (verification failure)
    transport.canned_responses["/api/Position/searchOpen"] = {
        "positions": [
            {"id": 101, "accountId": TEST_ACCOUNT_ID, "contractId": TEST_CONTRACT_ID, "size": 1, "averagePrice": 20500.0}
        ]
    }
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(TEST_TIME)
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
    )

    sig = Signal("sig-w1-fail", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-w1-fail", "risk", clock.now(), 1, "sig-w1-fail", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent("oi-w1-fail", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, "dec-w1-fail", origin=ORIGIN_LIVE, entry_price=20500.0, stop_price=20450.0)

    adapter.submit(sig, dec, intent)
    assert 88880001 in adapter.working_order_ids

    with pytest.raises(PracticeOrderError, match="FLATTEN_UNCONFIRMED"):
        adapter.flatten()

    # Tracking must NOT be cleared when flatten fails
    assert 88880001 in adapter.working_order_ids
    assert adapter.working_order_count == 1


def test_symbol_flatten_clears_working_orders_globally() -> None:
    """F2: Symbol flatten executes global cancel_all on gateway; working set must be cleared globally."""
    transport = RecordingTransport()
    transport.order_id_sequence = [88880001, 88880002]
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(TEST_TIME)
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
    )

    # Place order 1 on MNQ
    sig1 = Signal("sig-f2-1", "strat", clock.now(), 1, "CON_MNQ", SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec1 = RiskDecision("dec-f2-1", "risk", clock.now(), 1, "sig-f2-1", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent1 = OrderIntent("oi-f2-1", "strat", clock.now(), 1, "CON_MNQ", SIGNAL_LONG, "dec-f2-1", origin=ORIGIN_LIVE, entry_price=20500.0, stop_price=20450.0)
    adapter.submit(sig1, dec1, intent1)

    # Place order 2 on MES
    sig2 = Signal("sig-f2-2", "strat", clock.now(), 2, "CON_MES", SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec2 = RiskDecision("dec-f2-2", "risk", clock.now(), 2, "sig-f2-2", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent2 = OrderIntent("oi-f2-2", "strat", clock.now(), 2, "CON_MES", SIGNAL_LONG, "dec-f2-2", origin=ORIGIN_LIVE, entry_price=5800.0, stop_price=5790.0)
    adapter.submit(sig2, dec2, intent2)

    assert adapter.working_order_ids == frozenset({88880001, 88880002})

    # Flatten only symbol "CON_MNQ"
    adapter.flatten(symbol="CON_MNQ")

    # Because cancel_all cancelled all working orders globally, working tracking must be empty
    assert adapter.working_order_ids == frozenset()
    assert adapter.working_order_count == 0


def test_cancel_all_orders_exception_propagates_and_preserves_working_set() -> None:
    """F2: Exception in cancel_all_orders propagates, preserves working set, and allows subsequent retry."""
    from src.realtime.orders.practice_client import PracticeOrderError

    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(datetime(2026, 9, 21, 19, 0, tzinfo=UTC))
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
    )

    sig = Signal("sig-f2-exc", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-f2-exc", "risk", clock.now(), 1, "sig-f2-exc", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent("oi-f2-exc", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, "dec-f2-exc", origin=ORIGIN_LIVE, entry_price=20500.0, stop_price=20450.0)

    adapter.submit(sig, dec, intent)
    assert 88880001 in adapter.working_order_ids

    # Configure cancel_all_orders to fail
    original_cancel_all = client.cancel_all_orders
    def failing_cancel_all(*args: Any, **kwargs: Any) -> list[int]:
        raise PracticeOrderError("Gateway cancel failure")
    client.cancel_all_orders = failing_cancel_all  # type: ignore[assignment]

    with pytest.raises(PracticeOrderError, match="Gateway cancel failure"):
        adapter.flatten()

    # Tracking must be strictly preserved
    assert 88880001 in adapter.working_order_ids
    assert adapter.working_order_count == 1

    # Restore cancel_all_orders and verify subsequent cutoff retries and succeeds
    client.cancel_all_orders = original_cancel_all  # type: ignore[assignment]
    clock.set(datetime(2026, 9, 21, 20, 15, tzinfo=UTC))
    flattened = adapter.check_market_close_cutoff()
    assert flattened is True
    assert adapter.working_order_count == 0


def test_cutoff_repeated_after_success_returns_false() -> None:
    """F2: First cutoff returns True and flattens; repeated call with 0 positions/workings returns False."""
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(datetime(2026, 9, 21, 19, 0, tzinfo=UTC))  # before cutoff
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
    )

    sig = Signal("sig-f2-rep", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-f2-rep", "risk", clock.now(), 1, "sig-f2-rep", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent("oi-f2-rep", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, "dec-f2-rep", origin=ORIGIN_LIVE, entry_price=20500.0, stop_price=20450.0)

    adapter.submit(sig, dec, intent)
    assert adapter.working_order_count == 1

    # Advance clock past cutoff (15:15 CT = 20:15 UTC)
    clock.set(datetime(2026, 9, 21, 20, 15, tzinfo=UTC))

    # First call: triggers flatten and returns True
    first_call = adapter.check_market_close_cutoff()
    assert first_call is True
    assert adapter.working_order_count == 0

    # Second call: nothing left to flatten, returns False
    second_call = adapter.check_market_close_cutoff()
    assert second_call is False


def test_full_flatten_single_cancel_all_call_count() -> None:
    """F3: Full flatten (symbol=None) must execute cancel_all_orders exactly once via flatten_all."""
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(TEST_TIME)
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
    )

    # Call flatten(None)
    adapter.flatten()

    # /api/Order/searchOpen is invoked by cancel_all_orders to discover working orders
    search_open_calls = [c for c in transport.calls if c[0] == "/api/Order/searchOpen"]
    assert len(search_open_calls) == 1


def test_symbol_flatten_single_cancel_all_call_count() -> None:
    """F3: Symbol flatten (symbol=X) must execute cancel_all_orders exactly once before flatten_contract."""
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(TEST_TIME)
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
    )

    # Call flatten(symbol="CON_MNQ")
    adapter.flatten(symbol="CON_MNQ")

    search_open_calls = [c for c in transport.calls if c[0] == "/api/Order/searchOpen"]
    assert len(search_open_calls) == 1
    # flatten_contract endpoint invoked
    contract_close_calls = [c for c in transport.calls if c[0] == "/api/Position/closeContract"]
    assert len(contract_close_calls) == 1


def test_gateway_rejects_flat_signal_without_placing_order() -> None:
    """W2: SIGNAL_FLAT with active gateway must be rejected fail-closed; no order placed."""
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(TEST_TIME)
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
    )

    sig = Signal("sig-w2-flat", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_FLAT, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-w2-flat", "risk", clock.now(), 1, "sig-w2-flat", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent(
        "oi-w2-flat", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_FLAT, "dec-w2-flat", origin=ORIGIN_LIVE
    )

    report = adapter.submit(sig, dec, intent)
    assert report.status == EXEC_REJECTED
    assert "FLAT_REQUIRES_EXPLICIT_FLATTEN" in report.reason
    assert len(transport.calls) == 0
    assert adapter.open_positions.get(TEST_CONTRACT_ID, 0) == 0


def test_conflicting_fingerprint_same_identity_raises_value_error() -> None:
    """W3: Same intent identity with a different fingerprint must raise ValueError."""
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(TEST_TIME)
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
    )

    sig = Signal("sig-w3-conf", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-w3-conf", "risk", clock.now(), 1, "sig-w3-conf", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)

    # First intent
    intent1 = OrderIntent(
        "oi-w3-conf", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, "dec-w3-conf", origin=ORIGIN_LIVE,
        size=1, entry_price=20500.0, stop_price=20450.0
    )
    rep1 = adapter.submit(sig, dec, intent1)
    assert rep1.status == EXEC_ACCEPTED

    # Second intent with same (source, event_id) but different stop_price (conflicting fingerprint)
    intent2 = OrderIntent(
        "oi-w3-conf", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, "dec-w3-conf", origin=ORIGIN_LIVE,
        size=1, entry_price=20500.0, stop_price=20400.0  # changed stop_price!
    )

    with pytest.raises(ValueError, match="conflicting fingerprint"):
        adapter.submit(sig, dec, intent2)

    # No second order placed on transport
    assert len(transport.calls) == 1


def test_conflicting_fingerprint_raises_when_fingerprint_missing_from_cache() -> None:
    """F1: If _reports[key] exists but fingerprint is missing, submit must fail-closed with ValueError."""
    from src.realtime.events import identity_key

    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(TEST_TIME)
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
    )

    sig = Signal("sig-f1", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-f1", "risk", clock.now(), 1, "sig-f1", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent(
        "oi-f1", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, "dec-f1", origin=ORIGIN_LIVE,
        size=1, entry_price=20500.0, stop_price=20450.0
    )

    rep = adapter.submit(sig, dec, intent)
    assert rep.status == EXEC_ACCEPTED
    assert len(transport.calls) == 1

    # Simulate missing internal fingerprint state for this key
    key = identity_key(intent)
    del adapter._fingerprints[key]

    # Resubmitting must raise ValueError fail-closed, not return cached report
    with pytest.raises(ValueError, match="conflicting fingerprint"):
        adapter.submit(sig, dec, intent)

    # No second call to gateway
    assert len(transport.calls) == 1


def test_distinct_intents_with_same_order_id_produce_unique_report_ids() -> None:
    """W4: Distinct intents receiving the same order_id from gateway must have distinct report event_ids."""
    transport = RecordingTransport()
    # Mock always returns orderId 88880001
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(TEST_TIME)
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
    )

    sig1 = Signal("sig-w4-1", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec1 = RiskDecision("dec-w4-1", "risk", clock.now(), 1, "sig-w4-1", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent1 = OrderIntent("oi-w4-1", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, "dec-w4-1", origin=ORIGIN_LIVE, entry_price=20500.0, stop_price=20450.0)

    sig2 = Signal("sig-w4-2", "strat", clock.now(), 2, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec2 = RiskDecision("dec-w4-2", "risk", clock.now(), 2, "sig-w4-2", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent2 = OrderIntent("oi-w4-2", "strat", clock.now(), 2, TEST_CONTRACT_ID, SIGNAL_LONG, "dec-w4-2", origin=ORIGIN_LIVE, entry_price=20510.0, stop_price=20460.0)

    rep1 = adapter.submit(sig1, dec1, intent1)
    rep2 = adapter.submit(sig2, dec2, intent2)

    assert rep1.event_id != rep2.event_id
    assert rep1.order_intent_id == "oi-w4-1"
    assert rep2.order_intent_id == "oi-w4-2"

    # Resubmitting intent1 returns cached report with same event_id
    rep1_cached = adapter.submit(sig1, dec1, intent1)
    assert rep1_cached == rep1
    assert rep1_cached.event_id == rep1.event_id


@pytest.mark.parametrize(
    ("status_code", "expected_reason"),
    [
        (ORDER_STATUS_CANCELLED, "GATEWAY_ORDER_CANCELLED"),
        (ORDER_STATUS_REJECTED, "GATEWAY_ORDER_REJECTED"),
        (ORDER_STATUS_EXPIRED, "GATEWAY_ORDER_EXPIRED"),
    ],
)
def test_terminal_statuses_cancelled_rejected_expired_produce_explicit_rejected_reasons(
    status_code: int, expected_reason: str
) -> None:
    """W5: Terminal gateway statuses (2, 3, 4) map to EXEC_REJECTED with explicit reasons."""
    import dataclasses
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    original_place = client.place_order
    def mock_place(*args: Any, **kwargs: Any) -> PracticeOrderResult:
        res = original_place(*args, **kwargs)
        return dataclasses.replace(res, status=status_code)
    client.place_order = mock_place  # type: ignore[assignment]

    clock = FrozenClock(TEST_TIME)
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
    )

    sig = Signal(f"sig-w5-{status_code}", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision(f"dec-w5-{status_code}", "risk", clock.now(), 1, f"sig-w5-{status_code}", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent(f"oi-w5-{status_code}", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, f"dec-w5-{status_code}", origin=ORIGIN_LIVE, entry_price=20500.0, stop_price=20450.0)

    report = adapter.submit(sig, dec, intent)
    assert report.status == EXEC_REJECTED
    assert expected_reason in report.reason
    assert adapter.open_positions.get(TEST_CONTRACT_ID, 0) == 0

    # Cached on resubmit
    rep_cached = adapter.submit(sig, dec, intent)
    assert rep_cached == report


def test_timeout_recovery_historical_filled_updates_position_once() -> None:
    """W7: Transport-level timeout followed by search finding FILLED updates position once."""
    class TimeoutTransport(JsonTransport):
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []
            self.captured_tag: str | None = None

        def post(self, url: str, headers: Any, payload: Any, timeout: float) -> dict[str, Any]:
            endpoint = url.split("api.topstepx.com")[-1] if "api.topstepx.com" in url else url
            self.calls.append((endpoint, dict(payload)))
            if endpoint == "/api/Order/place":
                self.captured_tag = payload.get("customTag")
                raise TimeoutError("Simulated gateway place timeout")
            if endpoint == "/api/Order/searchOpen":
                return []
            if endpoint == "/api/Order/search":
                return [
                    {
                        "id": 99990001,
                        "orderId": 99990001,
                        "customTag": self.captured_tag,
                        "status": 1,  # ORDER_STATUS_FILLED
                        "contractId": TEST_CONTRACT_ID,
                        "side": 0,
                        "size": 1,
                    }
                ]
            return {"success": True}

    transport = TimeoutTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(TEST_TIME)
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
    )

    sig = Signal("sig-w7-fill", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-w7-fill", "risk", clock.now(), 1, "sig-w7-fill", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent("oi-w7-fill", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, "dec-w7-fill", origin=ORIGIN_LIVE, entry_price=20500.0, stop_price=20450.0)

    report = adapter.submit(sig, dec, intent)
    assert report.status == EXEC_FILLED
    assert adapter.open_positions[TEST_CONTRACT_ID] == 1

    # F4: Assert exactly one /api/Order/place call
    place_calls = [c for c in transport.calls if c[0] == "/api/Order/place"]
    assert len(place_calls) == 1

    # Resubmitting returns cached report without updating position again or making new calls
    calls_before = len(transport.calls)
    rep_cached = adapter.submit(sig, dec, intent)
    assert rep_cached == report
    assert adapter.open_positions[TEST_CONTRACT_ID] == 1
    assert len(transport.calls) == calls_before


def test_timeout_recovery_unknown_status_fails_closed_and_cached() -> None:
    """W7: Transport-level timeout followed by search finding unknown status (99) fails closed."""
    class TimeoutUnknownTransport(JsonTransport):
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []
            self.captured_tag: str | None = None

        def post(self, url: str, headers: Any, payload: Any, timeout: float) -> dict[str, Any]:
            endpoint = url.split("api.topstepx.com")[-1] if "api.topstepx.com" in url else url
            self.calls.append((endpoint, dict(payload)))
            if endpoint == "/api/Order/place":
                self.captured_tag = payload.get("customTag")
                raise TimeoutError("Simulated gateway place timeout")
            if endpoint == "/api/Order/searchOpen":
                return []
            if endpoint == "/api/Order/search":
                return [
                    {
                        "id": 99990002,
                        "orderId": 99990002,
                        "customTag": self.captured_tag,
                        "status": 99,  # Unknown status from gateway
                        "contractId": TEST_CONTRACT_ID,
                        "side": 0,
                        "size": 1,
                    }
                ]
            return {"success": True}

    transport = TimeoutUnknownTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(TEST_TIME)
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
    )

    sig = Signal("sig-w7-unk", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-w7-unk", "risk", clock.now(), 1, "sig-w7-unk", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent("oi-w7-unk", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, "dec-w7-unk", origin=ORIGIN_LIVE, entry_price=20500.0, stop_price=20450.0)

    report = adapter.submit(sig, dec, intent)
    assert report.status == EXEC_REJECTED
    assert "UNKNOWN_ORDER_STATUS" in report.reason
    assert adapter.open_positions.get(TEST_CONTRACT_ID, 0) == 0

    # Resubmit is cached and prevents re-sending
    calls_before = len(transport.calls)
    rep_cached = adapter.submit(sig, dec, intent)
    assert rep_cached == report
    assert len(transport.calls) == calls_before


@pytest.mark.parametrize(
    ("status_code", "expected_reason"),
    [
        (ORDER_STATUS_CANCELLED, "GATEWAY_ORDER_CANCELLED"),
        (ORDER_STATUS_REJECTED, "GATEWAY_ORDER_REJECTED"),
        (ORDER_STATUS_EXPIRED, "GATEWAY_ORDER_EXPIRED"),
    ],
)
def test_terminal_statuses_via_transport_recovery(
    status_code: int, expected_reason: str
) -> None:
    """F5: Terminal statuses (2, 3, 4) reached via timeout -> search recovery must map to EXEC_REJECTED."""
    class TerminalTimeoutTransport(JsonTransport):
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []
            self.captured_tag: str | None = None

        def post(self, url: str, headers: Any, payload: Any, timeout: float) -> dict[str, Any]:
            endpoint = url.split("api.topstepx.com")[-1] if "api.topstepx.com" in url else url
            self.calls.append((endpoint, dict(payload)))
            if endpoint == "/api/Order/place":
                self.captured_tag = payload.get("customTag")
                raise TimeoutError("Simulated gateway place timeout")
            if endpoint == "/api/Order/searchOpen":
                return []
            if endpoint == "/api/Order/search":
                return [
                    {
                        "id": 99990000 + status_code,
                        "orderId": 99990000 + status_code,
                        "customTag": self.captured_tag,
                        "status": status_code,
                        "contractId": TEST_CONTRACT_ID,
                        "side": 0,
                        "size": 1,
                    }
                ]
            return {"success": True}

    transport = TerminalTimeoutTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(TEST_TIME)
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
    )

    sig = Signal(f"sig-f5-{status_code}", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision(f"dec-f5-{status_code}", "risk", clock.now(), 1, f"sig-f5-{status_code}", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent(f"oi-f5-{status_code}", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, f"dec-f5-{status_code}", origin=ORIGIN_LIVE, entry_price=20500.0, stop_price=20450.0)

    report = adapter.submit(sig, dec, intent)
    assert report.status == EXEC_REJECTED
    assert expected_reason in report.reason
    assert adapter.open_positions.get(TEST_CONTRACT_ID, 0) == 0

    # Exactly one place call
    place_calls = [c for c in transport.calls if c[0] == "/api/Order/place"]
    assert len(place_calls) == 1

    # Resubmitting returns cached report without new transport calls
    calls_before = len(transport.calls)
    rep_cached = adapter.submit(sig, dec, intent)
    assert rep_cached == report
    assert len(transport.calls) == calls_before


def test_market_close_cutoff_propagates_practice_order_error_and_preserves_tracking() -> None:
    """F6: check_market_close_cutoff must propagate PracticeOrderError fail-closed and preserve tracking."""
    from src.realtime.orders.practice_client import PracticeOrderError

    transport = RecordingTransport()
    # Configure gateway to return persisting position on verification (triggers FLATTEN_UNCONFIRMED)
    transport.canned_responses["/api/Position/searchOpen"] = {
        "positions": [
            {"id": 201, "accountId": TEST_ACCOUNT_ID, "contractId": TEST_CONTRACT_ID, "size": 1, "averagePrice": 20500.0}
        ]
    }
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    clock = FrozenClock(datetime(2026, 9, 21, 19, 0, tzinfo=UTC))  # before cutoff
    adapter = PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=client,
    )

    sig = Signal("sig-f6-prop", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-f6-prop", "risk", clock.now(), 1, "sig-f6-prop", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    intent = OrderIntent("oi-f6-prop", "strat", clock.now(), 1, TEST_CONTRACT_ID, SIGNAL_LONG, "dec-f6-prop", origin=ORIGIN_LIVE, entry_price=20500.0, stop_price=20450.0)

    adapter.submit(sig, dec, intent)
    assert 88880001 in adapter.working_order_ids
    assert adapter.working_order_count == 1

    # Advance past cutoff
    clock.set(datetime(2026, 9, 21, 20, 15, tzinfo=UTC))

    # check_market_close_cutoff MUST propagate PracticeOrderError, NOT silence it
    with pytest.raises(PracticeOrderError, match="FLATTEN_UNCONFIRMED"):
        adapter.check_market_close_cutoff()

    # Tracking must be strictly preserved fail-closed
    assert 88880001 in adapter.working_order_ids
    assert adapter.working_order_count == 1







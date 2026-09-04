"""Deterministic tests for the read-only ProjectX boundary."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any
from urllib.error import HTTPError

import pytest

from src.realtime.clock import FrozenClock
from src.realtime.config import ProjectXCredentials
from src.realtime.connector import ConnectorError
from src.realtime.connectors.projectx import (
    ProjectXAccount,
    ProjectXAuthenticationError,
    ProjectXBar,
    ProjectXClient,
    ProjectXError,
    ProjectXFill,
    ProjectXHistoricalBarConnector,
    ProjectXPosition,
    ProjectXRateLimitError,
    ProjectXResponseError,
    UrllibJsonTransport,
    canonical_account_snapshot,
    canonical_bars,
)
from src.realtime.events import AccountSnapshot, Bar
from src.realtime.interfaces import LIVE_EXECUTION_ENABLED, MarketDataConnector
from src.types import Trade


class FakeTransport:
    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post(self, url, headers, payload, timeout):
        self.calls.append(
            {"url": url, "headers": dict(headers), "payload": dict(payload), "timeout": timeout}
        )
        return self.responses.pop(0)


def _credentials() -> ProjectXCredentials:
    return ProjectXCredentials("test-user", "super-secret", "TEST-ACCOUNT")


def _authenticated_client(*responses: Mapping[str, Any]):
    transport = FakeTransport(
        [{"success": True, "errorCode": 0, "token": "session-token"}, *responses]
    )
    client = ProjectXClient(_credentials(), transport=transport)
    client.authenticate()
    return client, transport


def test_credentials_repr_never_contains_api_key():
    assert "super-secret" not in repr(_credentials())


def test_live_execution_stays_locked_and_order_methods_are_absent():
    assert LIVE_EXECUTION_ENABLED is False
    client, _ = _authenticated_client()
    assert client.execution_allowed is False
    for name in (
        "place_order",
        "submit_order",
        "cancel_order",
        "modify_order",
        "close_position",
        "buy",
        "sell",
    ):
        assert not hasattr(client, name)


def test_authentication_sends_key_only_to_login_and_stores_token():
    client, transport = _authenticated_client()

    assert client.authenticated
    assert transport.calls[0]["url"].endswith("/api/Auth/loginKey")
    assert transport.calls[0]["payload"] == {
        "userName": "test-user",
        "apiKey": "super-secret",
    }
    assert "Authorization" not in transport.calls[0]["headers"]


def test_data_request_requires_authentication():
    client = ProjectXClient(_credentials(), transport=FakeTransport([]))
    with pytest.raises(ProjectXAuthenticationError):
        client.list_accounts()


def test_validate_session_rotates_token_for_future_requests():
    client, transport = _authenticated_client(
        {"success": True, "errorCode": 0, "newToken": "rotated-token"},
        {"success": True, "errorCode": 0, "accounts": []},
    )
    client.validate_session()
    assert client.list_accounts() == ()
    assert transport.calls[-1]["headers"]["Authorization"] == "Bearer rotated-token"


def test_accounts_are_strictly_parsed_and_selected_by_exact_name():
    client, transport = _authenticated_client(
        {
            "success": True,
            "errorCode": 0,
            "accounts": [
                {
                    "id": 1001,
                    "name": "TEST-ACCOUNT",
                    "balance": 0,
                    "canTrade": True,
                    "isVisible": True,
                    "simulated": True,
                }
            ],
        }
    )

    accounts = client.list_accounts()
    selected = client.select_account(accounts)

    assert selected.account_id == 1001
    assert selected.balance == Decimal(0)
    assert selected.simulated is True
    assert transport.calls[1]["headers"]["Authorization"] == "Bearer session-token"
    assert transport.calls[1]["payload"] == {"onlyActiveAccounts": True}


def test_ambiguous_account_selection_fails_closed():
    client, _ = _authenticated_client()
    accounts = (
        type("Account", (), {"name": "A"})(),
        type("Account", (), {"name": "B"})(),
    )
    with pytest.raises(ProjectXResponseError, match="exactly one"):
        client.select_account(accounts, account_name="missing")


def test_contract_and_bar_values_use_decimal_not_binary_float():
    client, transport = _authenticated_client(
        {
            "success": True,
            "errorCode": 0,
            "contracts": [
                {
                    "id": "CON.TEST.MNQ.Z99",
                    "name": "MNQZ9",
                    "description": "Micro E-mini Nasdaq-100 test",
                    "tickSize": 0.25,
                    "tickValue": 0.5,
                    "activeContract": True,
                    "symbolId": "F.US.MNQ",
                }
            ],
        },
        {
            "success": True,
            "errorCode": 0,
            "bars": [
                {
                    "t": "2026-09-02T14:30:00Z",
                    "o": 23000.25,
                    "h": 23001.0,
                    "l": 22999.75,
                    "c": 23000.5,
                    "v": 12,
                }
            ],
        },
    )

    contract = client.search_contracts("MNQ")[0]
    bars = client.retrieve_bars(
        contract.contract_id,
        start=datetime(2026, 9, 2, 14, tzinfo=UTC),
        end=datetime(2026, 9, 2, 15, tzinfo=UTC),
        limit=60,
    )

    assert contract.tick_size == Decimal("0.25")
    assert contract.tick_value == Decimal("0.5")
    assert bars[0].close == Decimal("23000.5")
    assert transport.calls[-1]["payload"]["contractId"] == "CON.TEST.MNQ.Z99"
    assert transport.calls[-1]["payload"]["includePartialBar"] is False

    mapped = canonical_bars(bars, contract_id=contract.contract_id, unit=2, unit_number=1)
    assert isinstance(mapped[0], Bar)
    assert mapped[0].source == "projectx/CON.TEST.MNQ.Z99/1m"
    assert mapped[0].interval == "1m"
    assert mapped[0].origin == "live"


def test_bar_request_rejects_naive_dates_and_excessive_limits():
    client, _ = _authenticated_client()
    aware = datetime(2026, 9, 2, tzinfo=UTC)
    with pytest.raises(ValueError, match="timezone-aware"):
        client.retrieve_bars(
            "CON.TEST", start=datetime(2026, 9, 1), end=aware, limit=1  # noqa: DTZ001
        )
    with pytest.raises(ValueError, match="limit"):
        client.retrieve_bars(
            "CON.TEST", start=aware, end=datetime(2026, 9, 3, tzinfo=UTC), limit=20001
        )


def test_provider_failure_does_not_expose_credentials():
    transport = FakeTransport(
        [{"success": False, "errorCode": 1, "errorMessage": "invalid credentials"}]
    )
    client = ProjectXClient(_credentials(), transport=transport)
    with pytest.raises(ProjectXAuthenticationError) as captured:
        client.authenticate()
    assert "super-secret" not in str(captured.value)


def test_missing_balance_is_not_invented_as_zero():
    client, _ = _authenticated_client(
        {
            "success": True,
            "errorCode": 0,
            "accounts": [{"id": 1, "name": "A", "canTrade": True, "isVisible": True}],
        }
    )
    with pytest.raises(ProjectXResponseError, match="account.balance"):
        client.list_accounts()


def test_fills_are_not_core_trades():
    client, _ = _authenticated_client(
        {
            "success": True,
            "errorCode": 0,
            "trades": [
                {
                    "id": 9,
                    "accountId": 1001,
                    "contractId": "CON.TEST.MNQ.Z99",
                    "creationTimestamp": "2026-09-02T14:30:00Z",
                    "price": 23000.25,
                    "profitAndLoss": None,
                    "fees": 0.62,
                    "side": 0,
                    "size": 1,
                    "voided": False,
                    "orderId": 77,
                }
            ],
        }
    )
    fills = client.list_trades(
        1001, start=datetime(2026, 9, 2, tzinfo=UTC), end=datetime(2026, 9, 3, tzinfo=UTC)
    )
    assert isinstance(fills[0], ProjectXFill)
    assert fills[0].profit_and_loss is None
    assert fills[0].commissions is None
    assert not isinstance(fills[0], Trade)
    assert not isinstance(fills[0], Bar)


def test_canonical_snapshot_does_not_infer_equity_or_round_trips():
    client, _ = _authenticated_client(
        {
            "success": True,
            "errorCode": 0,
            "accounts": [
                {
                    "id": 1001,
                    "name": "TEST-ACCOUNT",
                    "balance": "150000.00",
                    "canTrade": True,
                    "isVisible": True,
                    "simulated": True,
                }
            ],
        },
        {
            "success": True,
            "errorCode": 0,
            "positions": [
                {
                    "id": 4,
                    "accountId": 1001,
                    "contractId": "CON.TEST.MNQ.Z99",
                    "creationTimestamp": "2026-09-02T14:00:00Z",
                    "type": 1,
                    "size": 2,
                    "averagePrice": "23000.25",
                }
            ],
        },
    )
    account = client.select_account(client.list_accounts())
    positions = client.list_open_positions(account.account_id)
    clock = FrozenClock(datetime(2026, 9, 2, 15, tzinfo=UTC))
    snapshot = canonical_account_snapshot(
        account, positions, event_id="px-snap-1", sequence=1, clock=clock
    )
    assert isinstance(snapshot, AccountSnapshot)
    assert snapshot.balance == 150000.0
    assert snapshot.equity is None
    assert snapshot.realized_pnl is None
    assert snapshot.unrealized_pnl is None
    assert snapshot.peak_equity is None
    assert snapshot.trades_applied is None
    assert snapshot.positions == (("CON.TEST.MNQ.Z99", 1, 2, "23000.25"),)


def test_empty_positions_do_not_copy_balance_into_equity():
    account = ProjectXAccount(
        1001, "TEST-ACCOUNT", Decimal("150000.00"), True, True, True
    )
    clock = FrozenClock(datetime(2026, 9, 2, 15, tzinfo=UTC))
    snapshot = canonical_account_snapshot(
        account, (), event_id="px-snap-flat", sequence=1, clock=clock
    )
    assert snapshot.balance == 150000.0
    assert snapshot.equity is None
    assert snapshot.unrealized_pnl is None
    assert snapshot.positions == ()


def test_undocumented_account_mark_fields_are_not_mapped_to_equity():
    client, _ = _authenticated_client(
        {
            "success": True,
            "errorCode": 0,
            "accounts": [
                {
                    "id": 1001,
                    "name": "TEST-ACCOUNT",
                    "balance": "150000.00",
                    "canTrade": True,
                    "isVisible": True,
                    "simulated": True,
                    "equity": "151000.00",
                    "unrealizedPnl": "1000.00",
                    "peakEquity": "160000.00",
                }
            ],
        }
    )
    account = client.select_account(client.list_accounts())
    clock = FrozenClock(datetime(2026, 9, 2, 15, tzinfo=UTC))
    snapshot = canonical_account_snapshot(
        account, (), event_id="px-snap-extra", sequence=1, clock=clock
    )
    assert account.balance == Decimal("150000.00")
    assert snapshot.balance == 150000.0
    assert snapshot.equity is None
    assert snapshot.unrealized_pnl is None
    assert snapshot.peak_equity is None


def test_historical_connector_implements_market_data_protocol():
    client, _ = _authenticated_client(
        {
            "success": True,
            "errorCode": 0,
            "bars": [
                {
                    "t": "2026-09-02T14:30:00Z",
                    "o": 23000.25,
                    "h": 23001.0,
                    "l": 22999.75,
                    "c": 23000.5,
                    "v": 12,
                }
            ],
        }
    )
    connector = ProjectXHistoricalBarConnector(
        client,
        "CON.TEST.MNQ.Z99",
        start=datetime(2026, 9, 2, 14, tzinfo=UTC),
        end=datetime(2026, 9, 2, 15, tzinfo=UTC),
    )
    assert isinstance(connector, MarketDataConnector)
    connector.connect()
    event = connector.next_event()
    assert isinstance(event, Bar)
    assert connector.next_event() is None
    connector.disconnect()
    with pytest.raises(ConnectorError):
        connector.next_event()


def test_real_http_transport_serializes_json_without_logging_secret(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"success":true,"errorCode":0,"token":"jwt"}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = request.data
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("src.realtime.connectors.projectx.urlopen", fake_urlopen)
    client = ProjectXClient(_credentials(), transport=UrllibJsonTransport())
    client.authenticate()

    decoded = json.loads(captured["body"])
    assert captured["url"] == "https://api.topstepx.com/api/Auth/loginKey"
    assert decoded == {"userName": "test-user", "apiKey": "super-secret"}
    assert captured["timeout"] == 10.0


def test_real_http_transport_maps_401_to_authentication_error(monkeypatch):
    def rejected(*args, **kwargs):
        raise HTTPError("https://api.topstepx.com", 401, "Unauthorized", {}, BytesIO())

    monkeypatch.setattr("src.realtime.connectors.projectx.urlopen", rejected)
    client = ProjectXClient(_credentials(), transport=UrllibJsonTransport())
    with pytest.raises(ProjectXAuthenticationError, match="HTTP 401"):
        client.authenticate()


def test_real_http_transport_maps_429_to_rate_limit_error(monkeypatch):
    def limited(*args, **kwargs):
        raise HTTPError("https://api.topstepx.com", 429, "Too Many Requests", {}, BytesIO())

    monkeypatch.setattr("src.realtime.connectors.projectx.urlopen", limited)
    client = ProjectXClient(_credentials(), transport=UrllibJsonTransport())
    with pytest.raises(ProjectXRateLimitError, match="rate limit"):
        client.authenticate()


def test_private_post_refuses_order_routes():
    client, transport = _authenticated_client()
    with pytest.raises(ProjectXError, match="non-read-only"):
        client._post(
            "/api/Order/place",
            {"accountId": 1, "contractId": "CON.TEST", "size": 1},
            authenticated=True,
        )
    assert transport.calls[-1]["url"].endswith("/api/Auth/loginKey")
    assert not any("Order/place" in call["url"] for call in transport.calls)


def test_newest_first_bars_are_emitted_oldest_first_with_stable_ids():
    newer = ProjectXBar(
        datetime(2026, 9, 2, 14, tzinfo=UTC),
        Decimal(2),
        Decimal(3),
        Decimal(1),
        Decimal(2),
        Decimal(1),
    )
    older = ProjectXBar(
        datetime(2026, 9, 2, 13, tzinfo=UTC),
        Decimal(2),
        Decimal(3),
        Decimal(1),
        Decimal(2),
        Decimal(1),
    )
    first = canonical_bars((newer, older), contract_id="CON.TEST", unit=2, unit_number=1)
    second = canonical_bars((older, newer), contract_id="CON.TEST", unit=2, unit_number=1)
    assert [bar.timestamp for bar in first] == [
        datetime(2026, 9, 2, 13, tzinfo=UTC),
        datetime(2026, 9, 2, 14, tzinfo=UTC),
    ]
    assert first[0].sequence < first[1].sequence
    assert first[0].sequence == second[0].sequence
    assert first[1].sequence == second[1].sequence
    assert first[0].source == "projectx/CON.TEST/1m"
    assert first[0].event_id == "CON.TEST:1m:2026-09-02T13:00:00+00:00"
    assert first[0].event_id == second[0].event_id
    assert first[1].event_id == second[1].event_id


def test_retrieve_bars_normalizes_provider_descending_order():
    client, _ = _authenticated_client(
        {
            "success": True,
            "errorCode": 0,
            "bars": [
                {
                    "t": "2026-09-02T14:00:00Z",
                    "o": 2,
                    "h": 3,
                    "l": 1,
                    "c": 2,
                    "v": 1,
                },
                {
                    "t": "2026-09-02T13:00:00Z",
                    "o": 2,
                    "h": 3,
                    "l": 1,
                    "c": 2,
                    "v": 1,
                },
            ],
        }
    )
    bars = client.retrieve_bars(
        "CON.TEST",
        start=datetime(2026, 9, 2, 12, tzinfo=UTC),
        end=datetime(2026, 9, 2, 15, tzinfo=UTC),
    )
    assert [bar.timestamp for bar in bars] == [
        datetime(2026, 9, 2, 13, tzinfo=UTC),
        datetime(2026, 9, 2, 14, tzinfo=UTC),
    ]


def test_foreign_account_rows_fail_closed():
    client, _ = _authenticated_client(
        {
            "success": True,
            "errorCode": 0,
            "positions": [
                {
                    "id": 1,
                    "accountId": 999,
                    "contractId": "CON.TEST",
                    "creationTimestamp": "2026-09-02T14:00:00Z",
                    "type": 1,
                    "size": 1,
                    "averagePrice": "1.0",
                }
            ],
        },
        {
            "success": True,
            "errorCode": 0,
            "trades": [
                {
                    "id": 9,
                    "accountId": 999,
                    "contractId": "CON.TEST",
                    "creationTimestamp": "2026-09-02T14:00:00Z",
                    "price": 1,
                    "profitAndLoss": None,
                    "fees": 0.1,
                    "side": 0,
                    "size": 1,
                    "voided": False,
                    "orderId": 7,
                }
            ],
        },
    )
    with pytest.raises(ProjectXResponseError, match="does not match requested 123"):
        client.list_open_positions(123)
    with pytest.raises(ProjectXResponseError, match="does not match requested 123"):
        client.list_trades(123, start=datetime(2026, 9, 2, tzinfo=UTC))


def test_canonical_snapshot_rejects_foreign_positions():
    account = ProjectXAccount(123, "TEST-ACCOUNT", Decimal(0), True, True, True)
    foreign = ProjectXPosition(
        1,
        999,
        "CON.TEST",
        datetime(2026, 9, 2, 14, tzinfo=UTC),
        1,
        1,
        Decimal("1.0"),
    )
    with pytest.raises(ProjectXResponseError, match="does not match requested 123"):
        canonical_account_snapshot(
            account,
            (foreign,),
            event_id="snap",
            sequence=1,
            clock=FrozenClock(datetime(2026, 9, 2, 15, tzinfo=UTC)),
        )


def test_commissions_are_kept_when_provider_sends_them():
    client, _ = _authenticated_client(
        {
            "success": True,
            "errorCode": 0,
            "trades": [
                {
                    "id": 9,
                    "accountId": 1001,
                    "contractId": "CON.TEST.MNQ.Z99",
                    "creationTimestamp": "2026-09-02T14:30:00Z",
                    "price": 23000.25,
                    "profitAndLoss": None,
                    "fees": 0.62,
                    "commissions": 1.25,
                    "side": 0,
                    "size": 1,
                    "voided": False,
                    "orderId": 77,
                }
            ],
        }
    )
    fills = client.list_trades(1001, start=datetime(2026, 9, 2, tzinfo=UTC))
    assert fills[0].fees == Decimal("0.62")
    assert fills[0].commissions == Decimal("1.25")


def _px_bar(hour: int, minute: int = 0) -> ProjectXBar:
    return ProjectXBar(
        datetime(2026, 9, 2, hour, minute, tzinfo=UTC),
        Decimal(2),
        Decimal(3),
        Decimal(1),
        Decimal(2),
        Decimal(1),
    )


def test_canonical_bars_do_not_conflict_across_contracts_or_windows():
    from src.realtime.ordering import OrderingClass, SequenceTracker, requires_halt

    tracker = SequenceTracker()
    mnq = canonical_bars((_px_bar(13), _px_bar(14)), contract_id="CON.MNQ", unit=2, unit_number=1)
    nq = canonical_bars((_px_bar(13),), contract_id="CON.NQ", unit=2, unit_number=1)
    overlap = canonical_bars((_px_bar(14), _px_bar(13)), contract_id="CON.MNQ", unit=2, unit_number=1)

    assert tracker.classify(mnq[0]) is OrderingClass.ORDERED
    gap = tracker.classify(mnq[1])
    assert gap is OrderingClass.SEQUENCE_GAP
    assert requires_halt(gap) is False
    assert tracker.classify(nq[0]) is OrderingClass.ORDERED
    assert tracker.classify(overlap[0]) is OrderingClass.DUPLICATE
    assert tracker.classify(overlap[1]) is OrderingClass.DUPLICATE
    assert mnq[0].source != nq[0].source
    assert mnq[1].event_id == overlap[1].event_id
    assert mnq[1].sequence == overlap[1].sequence


def test_consecutive_one_minute_bars_are_ordered_without_bus_gaps():
    import asyncio

    from src.realtime.bus import AsyncIOEventBus
    from src.realtime.ordering import OrderingClass, SequenceTracker

    bars = canonical_bars(
        (_px_bar(13, 0), _px_bar(13, 1)),
        contract_id="CON.TEST",
        unit=2,
        unit_number=1,
    )
    assert bars[1].sequence == bars[0].sequence + 1
    tracker = SequenceTracker()
    assert tracker.classify(bars[0]) is OrderingClass.ORDERED
    assert tracker.classify(bars[1]) is OrderingClass.ORDERED

    async def _run() -> None:
        bus = AsyncIOEventBus(maxsize=8)
        seen: list[str] = []
        bus.subscribe(lambda event: seen.append(event.event_id))
        await bus.start()
        await bus.publish(bars[0])
        await bus.publish(bars[1])
        await bus.shutdown()
        assert seen == [bars[0].event_id, bars[1].event_id]
        assert bus.gaps == 0
        assert bus.conflicts == 0

    asyncio.run(_run())


def test_equivalent_offsets_share_identity_and_do_not_halt_the_bus():
    from datetime import timedelta, timezone

    from src.realtime.ordering import OrderingClass, SequenceTracker, requires_halt

    est = timezone(timedelta(hours=-4))
    utc_bar = _px_bar(13)
    offset_bar = ProjectXBar(
        datetime(2026, 9, 2, 9, tzinfo=est),
        Decimal(2),
        Decimal(3),
        Decimal(1),
        Decimal(2),
        Decimal(1),
    )
    first = canonical_bars((utc_bar,), contract_id="CON.TEST", unit=2, unit_number=1)[0]
    second = canonical_bars((offset_bar,), contract_id="CON.TEST", unit=2, unit_number=1)[0]
    assert first.event_id == second.event_id
    assert first.sequence == second.sequence
    assert first.timestamp == second.timestamp
    tracker = SequenceTracker()
    assert tracker.classify(first) is OrderingClass.ORDERED
    dup = tracker.classify(second)
    assert dup is OrderingClass.DUPLICATE
    assert requires_halt(dup) is False


def test_retrieve_bars_rejects_non_bool_and_non_int_params():
    client, _ = _authenticated_client()
    start = datetime(2026, 9, 2, tzinfo=UTC)
    end = datetime(2026, 9, 3, tzinfo=UTC)
    with pytest.raises(TypeError, match="unit must be an integer"):
        client.retrieve_bars("CON.TEST", start=start, end=end, unit=2.0)
    with pytest.raises(TypeError, match="unit_number must be an integer"):
        client.retrieve_bars("CON.TEST", start=start, end=end, unit_number=1.5)
    with pytest.raises(TypeError, match="limit must be an integer"):
        client.retrieve_bars("CON.TEST", start=start, end=end, limit=1.5)
    with pytest.raises(TypeError, match="live must be a bool"):
        client.retrieve_bars("CON.TEST", start=start, end=end, live=1)
    with pytest.raises(TypeError, match="include_partial_bar must be a bool"):
        client.retrieve_bars("CON.TEST", start=start, end=end, include_partial_bar=1)

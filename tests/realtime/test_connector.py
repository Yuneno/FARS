"""RT-1 replay market connector tests."""

from datetime import datetime, timedelta, timezone

import pytest

from src.realtime.clock import FrozenClock
from src.realtime.connector import ConnectorError, ReplayMarketConnector, normalize_market_payload
from src.realtime.events import AccountSnapshot, MarketTick, Quote, SystemEvent
from src.realtime.interfaces import MarketDataConnector

TS = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)


def _tick_payload(**overrides):
    payload = {
        "type": "tick",
        "event_id": "e1",
        "timestamp": TS,
        "sequence": 1,
        "symbol": "MNQ",
        "price": 18000.25,
        "volume": 1.0,
    }
    payload.update(overrides)
    return payload


def test_normalize_rejects_provider_objects_and_unknown_types():
    with pytest.raises(ConnectorError, match="canonical scalar"):
        normalize_market_payload(
            _tick_payload(nested={"tradovate": 1}),
            source="replay",
        )
    with pytest.raises(ConnectorError, match="unsupported"):
        normalize_market_payload({"type": "tradovate_fill"}, source="replay")


def test_connector_sets_source_and_does_not_leak_payload():
    payload = _tick_payload(source="broker-raw")
    event = normalize_market_payload(payload, source="replay-mnq")
    assert isinstance(event, MarketTick)
    assert event.source == "replay-mnq"
    assert event.origin == "replay"
    payload["price"] = 1.0
    assert event.price == 18000.25


def test_lifecycle_and_reconnect_restart():
    clock = FrozenClock(TS)
    connector = ReplayMarketConnector([_tick_payload()], source="replay", clock=clock)
    assert isinstance(connector, MarketDataConnector)
    with pytest.raises(ConnectorError, match="not connected"):
        connector.next_event()
    connector.connect()
    first = connector.next_event()
    assert isinstance(first, MarketTick)
    assert connector.next_event() is None
    connector.disconnect()
    disconnected = connector.next_event()
    assert isinstance(disconnected, SystemEvent)
    assert disconnected.kind == "connector_disconnected"
    connector.connect()
    reconnected = connector.next_event()
    assert isinstance(reconnected, SystemEvent)
    assert reconnected.kind == "connector_reconnected"
    restarted = connector.next_event()
    assert isinstance(restarted, MarketTick)


def test_stale_payload_emits_system_event_not_tick():
    clock = FrozenClock(TS + timedelta(seconds=30))
    connector = ReplayMarketConnector(
        [_tick_payload()],
        source="replay",
        clock=clock,
        stale_after=timedelta(seconds=5),
    )
    connector.connect()
    event = connector.next_event()
    assert isinstance(event, SystemEvent)
    assert event.kind == "stale_market_data"
    assert "e1" in event.detail


def test_invalid_payload_does_not_emit_market_event():
    clock = FrozenClock(TS)
    connector = ReplayMarketConnector(
        [_tick_payload(price=-1.0)],
        source="replay",
        clock=clock,
    )
    connector.connect()
    with pytest.raises(ConnectorError):
        connector.next_event()


def test_naive_timestamp_is_rejected():
    with pytest.raises(ConnectorError, match="timezone-aware"):
        normalize_market_payload(
            _tick_payload(timestamp=datetime(2024, 1, 15, 9, 30)),
            source="replay",
        )


def test_lifecycle_system_events_use_a_separate_source_stream():
    clock = FrozenClock(TS)
    connector = ReplayMarketConnector([_tick_payload()], source="replay", clock=clock)
    connector.connect()
    tick = connector.next_event()
    connector.disconnect()
    disconnected = connector.next_event()
    assert isinstance(tick, MarketTick)
    assert isinstance(disconnected, SystemEvent)
    assert tick.source == "replay"
    assert disconnected.source == "replay/system"


def test_quote_normalizes():
    event = normalize_market_payload(
        {
            "type": "quote",
            "event_id": "q1",
            "timestamp": TS,
            "sequence": 2,
            "symbol": "ES",
            "bid_price": 1.0,
            "ask_price": 1.1,
            "bid_size": 1.0,
            "ask_size": 1.0,
        },
        source="replay",
    )
    assert isinstance(event, Quote)


def test_snapshot_normalizes_optional_provider_timestamps():
    event = normalize_market_payload(
        {
            "type": "snapshot",
            "event_id": "account-1",
            "timestamp": TS.isoformat(),
            "sequence": 1,
            "equity": 100_000,
            "broker_timestamp": TS.isoformat(),
            "last_sync": TS.isoformat(),
        },
        source="replay",
    )

    assert isinstance(event, AccountSnapshot)
    assert event.timestamp == TS
    assert event.broker_timestamp == TS
    assert event.last_sync == TS


def test_snapshot_rejects_naive_optional_provider_timestamp():
    with pytest.raises(ConnectorError, match="timezone-aware"):
        normalize_market_payload(
            {
                "type": "snapshot",
                "event_id": "account-1",
                "timestamp": TS,
                "sequence": 1,
                "last_sync": "2024-01-15T09:30:00",
            },
            source="replay",
        )

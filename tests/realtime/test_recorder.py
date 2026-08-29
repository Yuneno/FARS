"""RT-3 JSONL recorder tests."""

from datetime import datetime, timezone

import pytest

from src.realtime.events import (
    AccountSnapshot,
    Bar,
    ExecutionReport,
    MarketTick,
    MarketTrade,
    OrderIntent,
    Quote,
    RiskDecision,
    Signal,
    SystemEvent,
)
from src.realtime.interfaces import EventRecorder
from src.realtime.recorder import (
    FileEventRecorder,
    RecorderError,
    reconstruct_events,
)

TS = datetime(2024, 3, 1, 14, 0, tzinfo=timezone.utc)


def _tick() -> MarketTick:
    return MarketTick(
        event_id="t1",
        source="replay",
        timestamp=TS,
        sequence=1,
        symbol="MNQ",
        price=1.25,
        volume=1.0,
        origin="replay",
    )


def test_roundtrip_market_and_decision_chain(tmp_path):
    path = tmp_path / "session.jsonl"
    recorder = FileEventRecorder(path)
    assert isinstance(recorder, EventRecorder)
    signal = Signal(
        event_id="s1",
        source="strategy",
        timestamp=TS,
        sequence=1,
        symbol="MNQ",
        action="LONG",
        origin="replay",
    )
    decision = RiskDecision(
        event_id="d1",
        source="risk",
        timestamp=TS,
        sequence=1,
        signal_id="s1",
        signal_source="strategy",
        approved=True,
        reason="OK",
        origin="replay",
    )
    intent = OrderIntent(
        event_id="o1",
        source="risk-gate",
        timestamp=TS,
        sequence=1,
        symbol="MNQ",
        action="LONG",
        risk_decision_id="d1",
        origin="replay",
    )
    fill = ExecutionReport(
        event_id="x1",
        source="paper",
        timestamp=TS,
        sequence=1,
        order_intent_id="o1",
        status="filled",
        origin="replay",
    )
    snap = AccountSnapshot(
        event_id="a1",
        source="replay",
        timestamp=TS,
        sequence=2,
        origin="replay",
        equity=25000.0,
        positions=[("MNQ", 1)],
        broker_timestamp=TS,
    )
    extras = [
        Quote(
            event_id="q1",
            source="replay",
            timestamp=TS,
            sequence=3,
            symbol="ES",
            bid_price=1.0,
            ask_price=1.1,
            bid_size=1.0,
            ask_size=1.0,
            origin="replay",
        ),
        Bar(
            event_id="b1",
            source="replay",
            timestamp=TS,
            sequence=4,
            symbol="ES",
            interval="1m",
            open=1.0,
            high=1.1,
            low=0.9,
            close=1.05,
            volume=10.0,
            origin="replay",
        ),
        MarketTrade(
            event_id="p1",
            source="replay",
            timestamp=TS,
            sequence=5,
            symbol="ES",
            price=1.05,
            size=2.0,
            origin="replay",
        ),
        SystemEvent(
            event_id="sys1",
            source="replay/system",
            timestamp=TS,
            sequence=1,
            kind="connector_disconnected",
            origin="replay",
        ),
    ]
    chain = (_tick(), signal, decision, intent, fill, snap, *extras)
    for event in chain:
        recorder.record(event)
    restored = reconstruct_events(path)
    assert len(restored) == len(chain)
    assert restored[0] == _tick()
    assert restored[1] == signal
    assert restored[2].approved is True
    assert restored[5].positions == (("MNQ", 1),)
    assert restored[5].broker_timestamp == TS
    assert restored[6] == extras[0]
    assert restored[9].kind == "connector_disconnected"


def test_rejects_non_canonical_and_malformed(tmp_path):
    path = tmp_path / "session.jsonl"
    recorder = FileEventRecorder(path)
    with pytest.raises(RecorderError, match="canonical"):
        recorder.record({"type": "tick"})
    recorder.record(_tick())
    path.write_text(path.read_text(encoding="utf-8") + "{not json}\n", encoding="utf-8")
    with pytest.raises(RecorderError, match="malformed"):
        reconstruct_events(path)


def test_does_not_alter_unrelated_files(tmp_path):
    other = tmp_path / "source.csv"
    other.write_text("a,b\n1,2\n", encoding="utf-8")
    recorder = FileEventRecorder(tmp_path / "journal.jsonl")
    recorder.record(_tick())
    assert other.read_text(encoding="utf-8") == "a,b\n1,2\n"

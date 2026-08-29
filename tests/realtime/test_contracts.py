"""RT-0 interface, clock, and ordering contract tests."""

from datetime import datetime, timedelta, timezone

import pytest

from src.realtime.clock import Clock, FrozenClock, SystemClock
from src.realtime.events import (
    EXEC_ACCEPTED,
    SIGNAL_LONG,
    CanonicalEvent,
    ExecutionReport,
    MarketTick,
    OrderIntent,
    RiskDecision,
    Signal,
    is_authorized,
)
from src.realtime.interfaces import (
    LIVE_EXECUTION_ENABLED,
    EventBus,
    EventRecorder,
    ExecutionAdapter,
    MarketDataConnector,
    RiskEngine,
    Strategy,
    require_authorized_intent,
    require_order_intent,
    require_risk_decision,
    require_signal,
)
from src.realtime.ordering import OrderingClass, SequenceTracker, is_out_of_order, requires_halt

TS = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


def _tick(event_id: str, sequence: int, source: str = "feed") -> MarketTick:
    return MarketTick(
        event_id=event_id,
        source=source,
        timestamp=TS,
        sequence=sequence,
        symbol="MNQ",
        price=1.0,
        volume=1.0,
    )


def _signal() -> Signal:
    return Signal(
        event_id="sig-1",
        source="strategy",
        timestamp=TS,
        sequence=1,
        symbol="MNQ",
        action=SIGNAL_LONG,
    )


class StubConnector:
    def __init__(self, event: CanonicalEvent) -> None:
        self._event = event

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def next_event(self) -> CanonicalEvent:
        return self._event


class MemoryBus:
    def __init__(self) -> None:
        self._subs: list = []
        self.published: list[CanonicalEvent] = []

    def subscribe(self, callback) -> None:
        self._subs.append(callback)

    def publish(self, event: CanonicalEvent) -> None:
        self.published.append(event)
        for callback in self._subs:
            callback(event)


class MemoryRecorder:
    def __init__(self) -> None:
        self.events: list[CanonicalEvent] = []

    def record(self, event: CanonicalEvent) -> None:
        self.events.append(event)


class StubStrategy:
    def on_event(self, event: CanonicalEvent) -> Signal | None:
        if isinstance(event, MarketTick):
            return _signal()
        return None


class StubRisk:
    def __init__(self, *, approved: bool) -> None:
        self.approved = approved

    def evaluate(self, signal: Signal) -> RiskDecision:
        return RiskDecision(
            event_id="rd-1",
            source="risk",
            timestamp=signal.timestamp,
            sequence=signal.sequence,
            signal_id=signal.event_id,
            signal_source=signal.source,
            approved=self.approved,
            reason="OK" if self.approved else "DENIED",
            origin=signal.origin,
        )


class StubExecution(ExecutionAdapter):
    def __init__(self) -> None:
        self.submitted: list[OrderIntent] = []

    def _execute_authorized(self, intent: OrderIntent) -> ExecutionReport:
        self.submitted.append(intent)
        return ExecutionReport(
            event_id="er-1",
            source="paper",
            timestamp=intent.timestamp,
            sequence=intent.sequence,
            order_intent_id=intent.event_id,
            status=EXEC_ACCEPTED,
        )


def test_frozen_clock_rejects_naive_and_is_deterministic():
    clock = FrozenClock(TS)
    assert clock.now() == TS
    later = TS + timedelta(seconds=5)
    clock.set(later)
    assert clock.now() == later
    with pytest.raises(ValueError, match="timezone-aware"):
        FrozenClock(datetime(2024, 6, 1, 12, 0))


def test_system_clock_is_timezone_aware():
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() is not None


def test_frozen_clock_satisfies_clock_protocol():
    clock: Clock = FrozenClock(TS)
    assert clock.now() == TS


def test_sequence_classifications_are_deterministic():
    tracker = SequenceTracker()
    assert tracker.classify(_tick("e101", 101)) is OrderingClass.ORDERED
    assert tracker.classify(_tick("e101", 101)) is OrderingClass.DUPLICATE
    assert tracker.classify(_tick("e101-b", 101)) is OrderingClass.CONFLICT
    assert tracker.classify(_tick("e103", 103)) is OrderingClass.SEQUENCE_GAP
    late = tracker.classify(_tick("e102", 102))
    assert late is OrderingClass.LATE
    assert late.value == "late"
    assert tracker.classify(_tick("e104", 104, source="other")) is OrderingClass.ORDERED


def test_gap_does_not_silently_accept_the_missing_sequence_as_ordered():
    tracker = SequenceTracker()
    tracker.classify(_tick("e101", 101))
    gap = tracker.classify(_tick("e103", 103))
    late = tracker.classify(_tick("e102", 102))
    assert gap is OrderingClass.SEQUENCE_GAP
    assert late is OrderingClass.LATE
    assert is_out_of_order(gap) is True
    assert is_out_of_order(late) is True
    assert is_out_of_order(OrderingClass.ORDERED) is False
    assert is_out_of_order(OrderingClass.DUPLICATE) is False
    assert requires_halt(gap) is False
    assert requires_halt(late) is False
    assert requires_halt(OrderingClass.CONFLICT) is True
    assert requires_halt(OrderingClass.DUPLICATE) is False
    assert requires_halt(OrderingClass.ORDERED) is False
    assert tracker.classify(_tick("e102", 102)) is OrderingClass.DUPLICATE
    assert tracker.classify(_tick("e102-b", 102)) is OrderingClass.CONFLICT
    assert tracker.classify(_tick("e104", 104)) is OrderingClass.ORDERED
    reused = tracker.classify(_tick("e101", 105))
    assert reused is OrderingClass.CONFLICT
    assert requires_halt(reused) is True
    mutated = MarketTick(
        event_id="e104",
        source="feed",
        timestamp=TS,
        sequence=104,
        symbol="MNQ",
        price=9.0,
        volume=1.0,
    )
    assert tracker.classify(mutated) is OrderingClass.CONFLICT


def test_bool_and_float_numerics_are_rejected():
    with pytest.raises(ValueError, match="price"):
        MarketTick(
            event_id="e1",
            source="feed",
            timestamp=TS,
            sequence=1,
            symbol="MNQ",
            price=True,  # type: ignore[arg-type]
            volume=1.0,
        )
    with pytest.raises(ValueError, match="sequence"):
        MarketTick(
            event_id="e1",
            source="feed",
            timestamp=TS,
            sequence=1.0,  # type: ignore[arg-type]
            symbol="MNQ",
            price=1.0,
            volume=1.0,
        )


def test_connector_emits_canonical_events_only():
    connector: MarketDataConnector = StubConnector(_tick("e1", 1))
    event = connector.next_event()
    assert isinstance(event, MarketTick)


def test_bus_and_recorder_round_trip_canonical_events():
    bus: EventBus = MemoryBus()
    recorder: EventRecorder = MemoryRecorder()
    bus.subscribe(recorder.record)
    event = _tick("e1", 1)
    bus.publish(event)
    assert recorder.events == [event]


def test_strategy_returns_signal_not_order():
    strategy: Strategy = StubStrategy()
    signal = require_signal(strategy.on_event(_tick("e1", 1)))
    assert isinstance(signal, Signal)
    with pytest.raises(TypeError, match="Signal"):
        require_signal(_tick("e1", 1))


def test_risk_returns_explicit_decision_and_execution_requires_intent():
    signal = _signal()
    risk: RiskEngine = StubRisk(approved=True)
    decision = require_risk_decision(risk.evaluate(signal))
    assert is_authorized(decision) is True
    execution: ExecutionAdapter = StubExecution()
    with pytest.raises(TypeError, match="OrderIntent"):
        execution.submit(signal, decision, signal)  # type: ignore[arg-type]
    intent = OrderIntent(
        event_id="oi-1",
        source="risk-gate",
        timestamp=TS,
        sequence=1,
        symbol="MNQ",
        action=SIGNAL_LONG,
        risk_decision_id=decision.event_id,
    )
    report = execution.submit(signal, decision, intent)
    assert report.status == EXEC_ACCEPTED
    denied = StubRisk(approved=False).evaluate(signal)
    with pytest.raises(ValueError, match="did not approve"):
        execution.submit(signal, denied, intent)
    mismatched = OrderIntent(
        event_id="oi-2",
        source="risk-gate",
        timestamp=TS,
        sequence=1,
        symbol="ES",
        action="SHORT",
        risk_decision_id=decision.event_id,
    )
    with pytest.raises(ValueError, match="symbol and action"):
        execution.submit(signal, decision, mismatched)


def test_denied_risk_is_not_authorization():
    decision = StubRisk(approved=False).evaluate(_signal())
    assert is_authorized(decision) is False
    assert LIVE_EXECUTION_ENABLED is False


def test_execution_submit_cannot_be_bypassed_by_mixin():
    with pytest.raises(TypeError, match="cannot be overridden"):

        class _Mixin:
            def submit(self, signal, decision, intent):  # noqa: ARG002
                return intent

        class _Evil(_Mixin, ExecutionAdapter):
            def _execute_authorized(self, intent):
                raise AssertionError("bypass")


def test_missing_risk_decision_cannot_be_implicit_true():
    assert is_authorized(None) is False
    assert bool(None) is False
    assert is_authorized(None) is not True

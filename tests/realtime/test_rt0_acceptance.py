"""RT-0 acceptance: every canonical type, edge cases, and a fail-closed pipeline."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from src.realtime.clock import FrozenClock
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
    identity_key,
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
from src.realtime.ordering import OrderingClass, SequenceTracker

TS = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
NAIVE = datetime(2024, 1, 15, 9, 30)


def _env(**overrides):
    values = {
        "event_id": "id-1",
        "source": "feed",
        "timestamp": TS,
        "sequence": 1,
    }
    values.update(overrides)
    return values


def make_tick(**overrides):
    values = {**_env(), "symbol": "MNQ", "price": 1.25, "volume": 0.0}
    values.update(overrides)
    return MarketTick(**values)


def make_quote(**overrides):
    values = {
        **_env(),
        "symbol": "MNQ",
        "bid_price": 1.0,
        "ask_price": 1.1,
        "bid_size": 0.0,
        "ask_size": 2.0,
    }
    values.update(overrides)
    return Quote(**values)


def make_bar(**overrides):
    values = {
        **_env(),
        "symbol": "MNQ",
        "interval": "1m",
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
        "volume": 4.0,
    }
    values.update(overrides)
    return Bar(**values)


def make_trade(**overrides):
    values = {**_env(), "symbol": "ES", "price": 5000.0, "size": 1.0}
    values.update(overrides)
    return MarketTrade(**values)


def make_snapshot(**overrides):
    values = {**_env(), "equity": 25000.0}
    values.update(overrides)
    return AccountSnapshot(**values)


def make_signal(**overrides):
    values = {**_env(), "symbol": "MNQ", "action": "LONG"}
    values.update(overrides)
    return Signal(**values)


def make_decision(**overrides):
    values = {
        **_env(),
        "approved": False,
        "reason": "DENIED",
        "signal_id": "sig-1",
        "signal_source": "feed",
    }
    values.update(overrides)
    return RiskDecision(**values)


def make_intent(**overrides):
    values = {
        **_env(),
        "symbol": "MNQ",
        "action": "FLAT",
        "risk_decision_id": "rd-1",
    }
    values.update(overrides)
    return OrderIntent(**values)


def make_report(**overrides):
    values = {**_env(), "order_intent_id": "oi-1", "status": "filled"}
    values.update(overrides)
    return ExecutionReport(**values)


def make_system(**overrides):
    values = {**_env(), "kind": "sequence_gap"}
    values.update(overrides)
    return SystemEvent(**values)


EVENT_FACTORIES = [
    make_tick,
    make_quote,
    make_bar,
    make_trade,
    make_snapshot,
    make_signal,
    make_decision,
    make_intent,
    make_report,
    make_system,
]


@pytest.mark.parametrize("factory", EVENT_FACTORIES)
def test_every_event_has_timezone_aware_timestamp(factory):
    event = factory()
    assert event.timestamp.tzinfo is not None
    assert event.timestamp.utcoffset() is not None


@pytest.mark.parametrize("factory", EVENT_FACTORIES)
def test_every_event_rejects_naive_timestamp(factory):
    with pytest.raises(ValueError, match="timezone-aware"):
        factory(timestamp=NAIVE)


@pytest.mark.parametrize("factory", EVENT_FACTORIES)
def test_every_event_rejects_missing_identity(factory):
    with pytest.raises(ValueError, match="event_id"):
        factory(event_id="")
    with pytest.raises(ValueError, match="source"):
        factory(source=" ")


@pytest.mark.parametrize("factory", EVENT_FACTORIES)
def test_every_event_is_frozen(factory):
    event = factory()
    with pytest.raises(AttributeError):
        event.event_id = "mutated"


@pytest.mark.parametrize("factory", EVENT_FACTORIES)
def test_replay_origin_uses_the_same_type(factory):
    live = factory(origin="live")
    replay = factory(origin="replay", event_id="id-2")
    assert type(live) is type(replay)
    assert identity_key(live)[0] == identity_key(replay)[0]


def test_quote_rejects_negative_sizes_not_crossed_quotes():
    with pytest.raises(ValueError, match="bid_size"):
        Quote(
            **_env(),
            symbol="MNQ",
            bid_price=2.0,
            ask_price=1.0,
            bid_size=-1.0,
            ask_size=1.0,
        )


def test_market_trade_rejects_zero_size():
    with pytest.raises(ValueError, match="size"):
        make_trade(size=0)


def test_flat_bar_is_valid():
    bar = Bar(
        **_env(),
        symbol="YM",
        interval="5m",
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        volume=0.0,
    )
    assert bar.high == bar.low


def test_bar_rejects_empty_interval():
    with pytest.raises(ValueError, match="interval"):
        Bar(
            **_env(),
            symbol="MNQ",
            interval="",
            open=1.0,
            high=1.0,
            low=1.0,
            close=1.0,
            volume=0.0,
        )


def test_snapshot_rejects_nonfinite_equity_and_naive_broker_time():
    with pytest.raises(ValueError, match="equity"):
        AccountSnapshot(**_env(), equity=float("inf"))
    with pytest.raises(ValueError, match="timezone-aware"):
        AccountSnapshot(**_env(), broker_timestamp=NAIVE)
    with pytest.raises(ValueError, match="positions"):
        AccountSnapshot(**_env(), positions={"MNQ": 1})
    with pytest.raises(ValueError, match="provider objects|mappings"):
        AccountSnapshot(**_env(), positions=[("MNQ", {"qty": 1})])


def test_order_intent_requires_risk_decision_id():
    with pytest.raises(ValueError, match="risk_decision_id"):
        OrderIntent(**_env(), symbol="MNQ", action="LONG", risk_decision_id="")


def test_execution_report_requires_intent_id():
    with pytest.raises(ValueError, match="order_intent_id"):
        ExecutionReport(**_env(), order_intent_id="", status="accepted")


def test_frozen_clock_does_not_follow_wall_time():
    clock = FrozenClock(TS)
    time.sleep(0.02)
    assert clock.now() == TS


def test_first_sequence_need_not_start_at_zero():
    tracker = SequenceTracker()
    assert tracker.classify(make_tick(event_id="a", sequence=50)) is OrderingClass.ORDERED
    assert tracker.classify(make_tick(event_id="b", sequence=51)) is OrderingClass.ORDERED
    assert tracker.classify(make_tick(event_id="c", sequence=51)) is OrderingClass.CONFLICT


class _Connector:
    def connect(self):
        return None

    def disconnect(self):
        return None

    def next_event(self):
        return make_tick()


class _Bus:
    def __init__(self):
        self._subs = []

    def subscribe(self, callback):
        self._subs.append(callback)

    def publish(self, event):
        for callback in self._subs:
            callback(event)


class _Recorder:
    def __init__(self):
        self.seen = []

    def record(self, event):
        self.seen.append(event)


class _Strategy:
    def on_event(self, event):
        if isinstance(event, MarketTick):
            return make_signal(event_id="sig-live", sequence=event.sequence)
        return None


class _Risk:
    def __init__(self, approved):
        self.approved = approved

    def evaluate(self, signal):
        return make_decision(
            event_id="rd-live",
            signal_id=signal.event_id,
            signal_source=signal.source,
            approved=self.approved,
            reason="OK" if self.approved else "UNKNOWN_CRITICAL_STATE",
            sequence=signal.sequence,
            origin=signal.origin,
        )


class _Execution(ExecutionAdapter):
    def __init__(self):
        self.submitted = []

    def _execute_authorized(self, intent):
        self.submitted.append(intent)
        return make_report(event_id="er-live", order_intent_id=intent.event_id)


def _run_pipeline(approved: bool) -> tuple[object, _Execution]:
    connector: MarketDataConnector = _Connector()
    bus: EventBus = _Bus()
    recorder: EventRecorder = _Recorder()
    strategy: Strategy = _Strategy()
    risk: RiskEngine = _Risk(approved)
    execution: ExecutionAdapter = _Execution()
    bus.subscribe(recorder.record)

    event = connector.next_event()
    bus.publish(event)
    signal = require_signal(strategy.on_event(event))
    decision = require_risk_decision(risk.evaluate(signal))
    intent = make_intent(
        event_id="oi-live",
        risk_decision_id=decision.event_id,
        sequence=decision.sequence,
        symbol=signal.symbol,
        action=signal.action,
    )
    if approved:
        execution.submit(signal, decision, intent)
    else:
        with pytest.raises(ValueError, match="did not approve"):
            execution.submit(signal, decision, intent)
    return decision, execution


def test_pipeline_deny_never_reaches_execution():
    decision, execution = _run_pipeline(approved=False)
    assert is_authorized(decision) is False
    assert execution.submitted == []
    assert LIVE_EXECUTION_ENABLED is False


def test_pipeline_approve_submits_order_intent_only():
    decision, execution = _run_pipeline(approved=True)
    assert is_authorized(decision) is True
    assert len(execution.submitted) == 1
    assert isinstance(execution.submitted[0], OrderIntent)


def test_protocol_stubs_match_runtime_checkable_interfaces():
    assert isinstance(_Connector(), MarketDataConnector)
    assert isinstance(_Bus(), EventBus)
    assert isinstance(_Recorder(), EventRecorder)
    assert isinstance(_Strategy(), Strategy)
    assert isinstance(_Risk(False), RiskEngine)
    assert isinstance(_Execution(), ExecutionAdapter)

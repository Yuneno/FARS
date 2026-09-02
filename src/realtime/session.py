"""RT-8 deterministic paper-session orchestration.

The session preserves the mandatory dependency direction:

    Connector -> EventBus -> Strategy -> Signal -> Risk -> RiskDecision
        -> OrderIntent -> Paper Execution -> ExecutionReport

Every input and generated canonical event traverses the same bus and recorder.
Denied decisions never create an ``OrderIntent``.  Any component failure stops
the session; it is never converted into approval or a silent dropped event.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from src.realtime.clock import Clock
from src.realtime.events import (
    CanonicalEvent,
    ExecutionReport,
    OrderIntent,
    RiskDecision,
    Signal,
    identity_key,
    is_authorized,
)
from src.realtime.interfaces import (
    AsyncEventBus,
    EventRecorder,
    ExecutionAdapter,
    MarketDataConnector,
    RiskEngine,
    Strategy,
    require_order_intent,
    require_authorized_intent,
    require_risk_decision,
    require_signal,
)


IntentFactory = Callable[[Signal, RiskDecision, int, Clock], OrderIntent]


class RealtimeSessionError(RuntimeError):
    """A component failed, returned the wrong contract, or exceeded a guard."""


@dataclass(frozen=True)
class RealtimeSessionMetrics:
    connector_events: int
    bus_events: int
    signals_generated: int
    risk_approvals: int
    risk_denials: int
    orders_submitted: int
    execution_reports: int


@dataclass(frozen=True)
class RealtimeSessionResult:
    metrics: RealtimeSessionMetrics
    decisions: tuple[RiskDecision, ...]
    intents: tuple[OrderIntent, ...]
    reports: tuple[ExecutionReport, ...]


def default_intent_factory(
    signal: Signal,
    decision: RiskDecision,
    sequence: int,
    clock: Clock,
) -> OrderIntent:
    """Create a minimal intent bound to one explicitly approved decision."""

    return OrderIntent(
        event_id=f"paper-intent-{sequence}",
        source="fars-paper-session",
        timestamp=clock.now(),
        sequence=sequence,
        symbol=signal.symbol,
        action=signal.action,
        risk_decision_id=decision.event_id,
        origin=signal.origin,
    )


class PaperRealtimeSession:
    """Run one finite connector stream through the complete paper pipeline."""

    def __init__(
        self,
        *,
        connector: MarketDataConnector,
        bus: AsyncEventBus,
        strategy: Strategy,
        risk: RiskEngine,
        execution: ExecutionAdapter,
        recorder: EventRecorder,
        clock: Clock,
        intent_factory: IntentFactory = default_intent_factory,
        max_generated_signals: int = 10_000,
    ) -> None:
        if not callable(getattr(connector, "connect", None)):
            raise TypeError("connector must implement MarketDataConnector")
        if not callable(getattr(bus, "publish", None)):
            raise TypeError("bus must implement AsyncEventBus")
        if not callable(getattr(strategy, "on_event", None)):
            raise TypeError("strategy must implement Strategy")
        if not callable(getattr(risk, "evaluate", None)):
            raise TypeError("risk must implement RiskEngine")
        if not isinstance(execution, ExecutionAdapter):
            raise TypeError("execution must implement ExecutionAdapter")
        if not callable(getattr(recorder, "record", None)):
            raise TypeError("recorder must implement EventRecorder")
        if not callable(getattr(clock, "now", None)):
            raise TypeError("clock must implement Clock")
        if not callable(intent_factory):
            raise TypeError("intent_factory must be callable")
        if (
            not isinstance(max_generated_signals, int)
            or isinstance(max_generated_signals, bool)
            or max_generated_signals <= 0
        ):
            raise ValueError("max_generated_signals must be a positive integer")
        self._connector = connector
        self._bus = bus
        self._strategy = strategy
        self._risk = risk
        self._execution = execution
        self._recorder = recorder
        self._clock = clock
        self._intent_factory = intent_factory
        self._max_generated_signals = max_generated_signals
        self._pending_signals: deque[Signal] = deque()
        self._processed_signal_ids: set[tuple[str, str]] = set()
        self._decisions: list[RiskDecision] = []
        self._intents: list[OrderIntent] = []
        self._reports: list[ExecutionReport] = []
        self._connector_events = 0
        self._bus_events = 0
        self._signals_generated = 0
        self._risk_approvals = 0
        self._risk_denials = 0
        self._ran = False

    async def run(self) -> RealtimeSessionResult:
        if self._ran:
            raise RealtimeSessionError("paper session instances are single-use")
        self._ran = True
        self._bus.subscribe(self._on_event)
        connected = False
        started = False
        primary_error: BaseException | None = None
        try:
            await self._bus.start()
            started = True
            self._connector.connect()
            connected = True
            while True:
                event = self._connector.next_event()
                if event is None:
                    break
                self._connector_events += 1
                await self._publish(event)
                await self._drain_signals()
        except BaseException as exc:
            primary_error = exc
        finally:
            if connected:
                try:
                    self._connector.disconnect()
                except BaseException as exc:
                    if primary_error is None:
                        primary_error = exc
            if started:
                try:
                    await self._bus.shutdown(drain=primary_error is None)
                except BaseException as exc:
                    if primary_error is None:
                        primary_error = exc
        if primary_error is not None:
            raise RealtimeSessionError("paper realtime session halted") from primary_error
        return RealtimeSessionResult(
            metrics=RealtimeSessionMetrics(
                connector_events=self._connector_events,
                bus_events=self._bus_events,
                signals_generated=self._signals_generated,
                risk_approvals=self._risk_approvals,
                risk_denials=self._risk_denials,
                orders_submitted=len(self._intents),
                execution_reports=len(self._reports),
            ),
            decisions=tuple(self._decisions),
            intents=tuple(self._intents),
            reports=tuple(self._reports),
        )

    def _on_event(self, event: CanonicalEvent) -> None:
        self._recorder.record(event)
        observe = getattr(self._risk, "observe", None)
        if callable(observe):
            observe(event)
        self._bus_events += 1
        proposed = self._strategy.on_event(event)
        if proposed is None:
            return
        signal = require_signal(proposed)
        self._signals_generated += 1
        if self._signals_generated > self._max_generated_signals:
            raise RealtimeSessionError("strategy exceeded max_generated_signals")
        self._pending_signals.append(signal)

    async def _publish(self, event: CanonicalEvent) -> None:
        await self._bus.publish(event)
        await self._bus.wait_idle()

    async def _drain_signals(self) -> None:
        while self._pending_signals:
            signal = self._pending_signals.popleft()
            signal_id = identity_key(signal)
            already_processed = signal_id in self._processed_signal_ids
            await self._publish(signal)
            if already_processed:
                continue
            self._processed_signal_ids.add(signal_id)
            decision = require_risk_decision(self._risk.evaluate(signal))
            if (decision.signal_source, decision.signal_id) != identity_key(signal):
                raise RealtimeSessionError(
                    "RiskDecision does not reference the evaluated Signal"
                )
            if decision.origin != signal.origin:
                raise RealtimeSessionError(
                    "RiskDecision origin does not match the evaluated Signal"
                )
            self._decisions.append(decision)
            await self._publish(decision)
            if not is_authorized(decision):
                self._risk_denials += 1
                continue
            self._risk_approvals += 1
            intent = require_order_intent(
                self._intent_factory(
                    signal,
                    decision,
                    len(self._intents) + 1,
                    self._clock,
                )
            )
            require_authorized_intent(signal, decision, intent)
            self._intents.append(intent)
            await self._publish(intent)
            report = self._execution.submit(signal, decision, intent)
            if not isinstance(report, ExecutionReport):
                raise TypeError(
                    "ExecutionAdapter must return ExecutionReport, "
                    f"got {type(report).__name__}"
                )
            if report.order_intent_id != intent.event_id:
                raise RealtimeSessionError(
                    "ExecutionReport does not reference the submitted OrderIntent"
                )
            if report.origin != intent.origin:
                raise RealtimeSessionError(
                    "ExecutionReport origin does not match the submitted OrderIntent"
                )
            self._reports.append(report)
            await self._publish(report)


__all__ = [
    "IntentFactory",
    "PaperRealtimeSession",
    "RealtimeSessionError",
    "RealtimeSessionMetrics",
    "RealtimeSessionResult",
    "default_intent_factory",
]

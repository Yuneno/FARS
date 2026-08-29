"""Minimal RT-0 interfaces. No provider payloads downstream.

Dependency direction (mandatory):

    Connector → CanonicalEvent → EventBus → Strategy → Signal
        → RiskEngine → RiskDecision → OrderIntent → ExecutionAdapter

Strategy MUST NOT talk to execution. Live execution stays locked.
"""

from __future__ import annotations

from abc import ABC, ABCMeta, abstractmethod
from collections.abc import Callable
from typing import Protocol, runtime_checkable

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

LIVE_EXECUTION_ENABLED = False


def require_signal(value: object) -> Signal:
    if not isinstance(value, Signal):
        raise TypeError(f"Strategy must return Signal, got {type(value).__name__}")
    return value


def require_risk_decision(value: object) -> RiskDecision:
    if not isinstance(value, RiskDecision):
        raise TypeError(
            f"RiskEngine must return RiskDecision, got {type(value).__name__}"
        )
    return value


def require_order_intent(value: object) -> OrderIntent:
    if not isinstance(value, OrderIntent):
        raise TypeError(
            "ExecutionAdapter accepts OrderIntent only, "
            f"got {type(value).__name__}"
        )
    return value


def require_authorized_intent(
    signal: object, decision: object, intent: object
) -> OrderIntent:
    """Executable risk veto. Denied or unbound intents cannot proceed."""
    proposed = require_signal(signal)
    checked = require_order_intent(intent)
    decided = require_risk_decision(decision)
    if (decided.signal_source, decided.signal_id) != identity_key(proposed):
        raise ValueError("RiskDecision must reference the Signal identity (source, event_id)")
    if proposed.origin != decided.origin or proposed.origin != checked.origin:
        raise ValueError("Signal, RiskDecision, and OrderIntent origin must match")
    if checked.risk_decision_id != decided.event_id:
        raise ValueError("OrderIntent.risk_decision_id must equal RiskDecision.event_id")
    if checked.symbol != proposed.symbol or checked.action != proposed.action:
        raise ValueError("OrderIntent must match the reviewed Signal symbol and action")
    if not is_authorized(decided):
        raise ValueError("RiskDecision did not approve; execution is denied")
    return checked


@runtime_checkable
class MarketDataConnector(Protocol):
    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def next_event(self) -> CanonicalEvent | None: ...


@runtime_checkable
class EventBus(Protocol):
    def publish(self, event: CanonicalEvent) -> None: ...

    def subscribe(self, callback: Callable[[CanonicalEvent], None]) -> None: ...


@runtime_checkable
class EventRecorder(Protocol):
    def record(self, event: CanonicalEvent) -> None: ...


@runtime_checkable
class Strategy(Protocol):
    def on_event(self, event: CanonicalEvent) -> Signal | None: ...


@runtime_checkable
class RiskEngine(Protocol):
    def evaluate(self, signal: Signal) -> RiskDecision: ...


class _SealedSubmitMeta(ABCMeta):
    def __new__(mcs, name, bases, namespace, **kwargs):
        cls = super().__new__(mcs, name, bases, namespace, **kwargs)
        if name == "ExecutionAdapter":
            return cls
        if cls.submit is not ExecutionAdapter.submit:
            raise TypeError("ExecutionAdapter.submit cannot be overridden")
        return cls


class ExecutionAdapter(ABC, metaclass=_SealedSubmitMeta):
    """Execution boundary. ``submit`` is the only public entry and enforces veto."""

    def submit(
        self, signal: Signal, decision: RiskDecision, intent: OrderIntent
    ) -> ExecutionReport:
        authorized = require_authorized_intent(signal, decision, intent)
        return self._execute_authorized(authorized)

    @abstractmethod
    def _execute_authorized(self, intent: OrderIntent) -> ExecutionReport:
        """Private hook. Callers must go through ``submit``."""


__all__ = [
    "LIVE_EXECUTION_ENABLED",
    "Clock",
    "EventBus",
    "EventRecorder",
    "ExecutionAdapter",
    "MarketDataConnector",
    "RiskEngine",
    "Strategy",
    "require_authorized_intent",
    "require_order_intent",
    "require_risk_decision",
    "require_signal",
]

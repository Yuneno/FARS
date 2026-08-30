"""RT-6 account-aware risk authorization.

Strategy-agnostic: ``evaluate`` sees a ``Signal``, never a strategy or broker.
Account-aware: authorization uses ``FundedAccountRules`` plus observed
``AccountSnapshot`` / ``SystemEvent`` state.

Unknown critical state denies. Never allows.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from src.realtime.clock import Clock
from src.realtime.events import (
    AccountSnapshot,
    CanonicalEvent,
    RiskDecision,
    Signal,
    SystemEvent,
    SYSTEM_CIRCUIT_BREAKER,
    SYSTEM_CONNECTOR_DISCONNECTED,
    SYSTEM_CONNECTOR_RECONNECTED,
    SYSTEM_HALTED,
    SYSTEM_RECONCILIATION_MISMATCH,
    SYSTEM_STALE_MARKET_DATA,
    identity_key,
)
from src.types import FundedAccountRules

REASON_UNKNOWN = "UNKNOWN_CRITICAL_STATE"
REASON_HALTED = "SYSTEM_HALTED"
REASON_STALE = "STALE_MARKET_DATA"
REASON_DISCONNECTED = "CONNECTOR_DISCONNECTED"
REASON_RECONCILE = "RECONCILIATION_MISMATCH"
REASON_CIRCUIT = "CIRCUIT_BREAKER"
REASON_DRAWDOWN = "DRAWDOWN_BUFFER_TOO_LOW"
REASON_DAILY_LOSS = "DAILY_LOSS_LIMIT"
REASON_APPROVED = "APPROVED"


def _utc_day(value: datetime) -> date:
    return value.astimezone(timezone.utc).date()


class AccountAwareRiskEngine:
    """Realtime Risk Engine v2. Implements the RT-0 ``RiskEngine`` protocol."""

    def __init__(
        self,
        rules: FundedAccountRules,
        clock: Clock,
        *,
        source: str = "fars-risk",
    ) -> None:
        if not isinstance(rules, FundedAccountRules):
            raise TypeError(
                f"rules must be FundedAccountRules, got {type(rules).__name__}"
            )
        if not isinstance(source, str) or source.strip() == "":
            raise ValueError(f"source must be a non-empty string, got {source!r}")
        now = clock.now()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock.now() must return a timezone-aware datetime")
        self._rules = rules
        self._clock = clock
        self._source = source
        self._seq = 0
        self._snapshot: AccountSnapshot | None = None
        self._start_of_day_equity: float | None = None
        self._start_of_day_date: date | None = None
        self._halted = False
        self._stale = False
        self._disconnected = False
        self._circuit = False
        self._mismatch = False
        self._clock_inconsistent = False

    def observe(self, event: CanonicalEvent) -> None:
        """Ingest account/system state. Market ticks and signals are ignored."""
        if isinstance(event, AccountSnapshot):
            self._ingest_snapshot(event)
            return
        if isinstance(event, SystemEvent):
            self._ingest_system(event)
            return
        if not isinstance(event, CanonicalEvent):
            raise TypeError(
                f"observe accepts canonical events, got {type(event).__name__}"
            )

    def evaluate(self, signal: Signal) -> RiskDecision:
        if not isinstance(signal, Signal):
            raise TypeError(f"RiskEngine.evaluate requires Signal, got {type(signal).__name__}")
        approved, reason = self._authorize(signal)
        self._seq += 1
        signal_source, signal_id = identity_key(signal)
        return RiskDecision(
            event_id=f"rd-{self._seq}",
            source=self._source,
            timestamp=self._clock.now(),
            sequence=self._seq,
            signal_id=signal_id,
            signal_source=signal_source,
            approved=approved,
            reason=reason,
            origin=signal.origin,
        )

    def _ingest_system(self, event: SystemEvent) -> None:
        if event.kind == SYSTEM_HALTED:
            self._halted = True
        elif event.kind == SYSTEM_CIRCUIT_BREAKER:
            self._circuit = True
        elif event.kind == SYSTEM_RECONCILIATION_MISMATCH:
            self._mismatch = True
        elif event.kind == SYSTEM_STALE_MARKET_DATA:
            self._stale = True
        elif event.kind == SYSTEM_CONNECTOR_DISCONNECTED:
            self._disconnected = True
            self._stale = True
        elif event.kind == SYSTEM_CONNECTOR_RECONNECTED:
            self._disconnected = False
            self._stale = True

    def _ingest_snapshot(self, snap: AccountSnapshot) -> None:
        previous = self._snapshot
        if previous is not None and snap.timestamp < previous.timestamp:
            self._clock_inconsistent = True
        day = _utc_day(snap.timestamp)
        if snap.equity is not None:
            if self._start_of_day_date is None:
                self._start_of_day_equity = snap.equity
                self._start_of_day_date = day
            elif day > self._start_of_day_date:
                if previous is not None and previous.equity is not None:
                    self._start_of_day_equity = previous.equity
                else:
                    self._start_of_day_equity = snap.equity
                self._start_of_day_date = day
            elif day < self._start_of_day_date:
                self._clock_inconsistent = True
            if not self._disconnected:
                self._stale = False
        self._snapshot = snap

    def _authorize(self, signal: Signal) -> tuple[bool, str]:
        if self._halted:
            return False, REASON_HALTED
        if self._circuit:
            return False, REASON_CIRCUIT
        if self._mismatch:
            return False, REASON_RECONCILE
        if self._disconnected:
            return False, REASON_DISCONNECTED
        if self._stale:
            return False, REASON_STALE
        if self._clock_inconsistent:
            return False, REASON_UNKNOWN

        snap = self._snapshot
        if snap is None or snap.equity is None:
            return False, REASON_UNKNOWN
        if snap.origin != signal.origin:
            return False, REASON_UNKNOWN
        if self._start_of_day_equity is None:
            return False, REASON_UNKNOWN

        if self._rules.drawdown_mode == "trailing":
            peak = snap.peak_equity
            if peak is None or snap.equity > peak:
                return False, REASON_UNKNOWN

        if self._drawdown_violated(snap):
            return False, REASON_DRAWDOWN
        if self._daily_loss_violated(snap):
            return False, REASON_DAILY_LOSS
        return True, REASON_APPROVED

    def _drawdown_violated(self, snap: AccountSnapshot) -> bool:
        equity = snap.equity
        if equity is None:
            return True
        if self._rules.drawdown_mode == "static":
            ref = self._rules.initial_balance
        else:
            peak = snap.peak_equity
            if peak is None:
                return True
            ref = peak
        threshold = ref - ref * self._rules.max_drawdown_pct
        return equity <= threshold

    def _daily_loss_violated(self, snap: AccountSnapshot) -> bool:
        equity = snap.equity
        sod = self._start_of_day_equity
        if equity is None or sod is None:
            return True
        if self._rules.daily_loss_base == "initial":
            threshold = sod - self._rules.initial_balance * self._rules.daily_loss_limit_pct
        else:
            threshold = sod - sod * self._rules.daily_loss_limit_pct
        return equity <= threshold

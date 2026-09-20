"""Session Circuit Breakers and Lifecycle Manager for Practice Accounts.

Implements §4-bis RT-9 declared limits:
1. Daily profit target (+$500) -> flatten once if open + halt for today (no se devuelve lo ganado).
2. Daily loss limit (-$200) -> flatten once + halt until next day.
3. Max risk per order ($200) -> enforced at adapter pre-check.
4. Total accumulated drawdown (-$1,000) -> flatten once + total shutdown.
5. Max trades per day (6) -> veto subsequent orders.

Fail-closed, state-machine tracking, recorded via SystemEvent into canonical event recorder.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any, Literal

from src.realtime.clock import Clock
from src.realtime.events import (
    SYSTEM_CIRCUIT_BREAKER,
    SYSTEM_HALTED,
    AccountSnapshot,
    CanonicalEvent,
    RiskDecision,
    SystemEvent,
)
from src.realtime.interfaces import EventRecorder
from src.realtime.practice_adapter import PracticeExecutionAdapter
from src.realtime.risk import (
    REASON_DAILY_LOSS,
    REASON_DAILY_PROFIT_TARGET,
    REASON_DRAWDOWN,
    REASON_MAX_TRADES,
)
from src.types import FundedAccountRules

STATE_ACTIVE = "ACTIVE"
STATE_HALTED_DAILY = "HALTED_DAILY"
STATE_TOTAL_SHUTDOWN = "TOTAL_SHUTDOWN"

SessionState = Literal["ACTIVE", "HALTED_DAILY", "TOTAL_SHUTDOWN"]


def _utc_day(dt: datetime) -> date:
    return dt.astimezone(timezone.utc).date()


class SessionCircuitBreaker:
    """Session-level risk guardian that enforces declared limits and operational halts."""

    def __init__(
        self,
        *,
        rules: FundedAccountRules,
        clock: Clock,
        adapter: PracticeExecutionAdapter,
        recorder: EventRecorder | None = None,
        source: str = "fars-circuit-breaker",
    ) -> None:
        self._rules = rules
        self._clock = clock
        self._adapter = adapter
        self._recorder = recorder
        self._source = source

        self._state: SessionState = STATE_ACTIVE
        self._current_date: date | None = None
        self._seq = 0
        self._daily_flattens_count = 0
        self._total_flattens_count = 0
        self._daily_halt_reason: str | None = None
        self._events_log: list[SystemEvent] = []

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state == STATE_ACTIVE

    @property
    def is_daily_halted(self) -> bool:
        return self._state == STATE_HALTED_DAILY

    @property
    def is_total_shutdown(self) -> bool:
        return self._state == STATE_TOTAL_SHUTDOWN

    @property
    def total_flattens_count(self) -> int:
        return self._total_flattens_count

    @property
    def events_log(self) -> tuple[SystemEvent, ...]:
        return tuple(self._events_log)

    def can_submit_order(self) -> tuple[bool, str]:
        """Check if trading is permitted by circuit breakers."""
        if self._state == STATE_TOTAL_SHUTDOWN:
            return False, "TOTAL_SHUTDOWN: accumulated drawdown limit breached"
        if self._state == STATE_HALTED_DAILY:
            return False, f"HALTED_DAILY: {self._daily_halt_reason or 'session limit reached'}"
        return True, "ACTIVE"

    def on_event(self, event: CanonicalEvent) -> None:
        """Observe stream events and trigger circuit breakers as appropriate."""
        if isinstance(event, AccountSnapshot):
            self._check_day_rollover(event.timestamp)
        elif hasattr(event, "timestamp") and isinstance(event.timestamp, datetime):
            self._check_day_rollover(event.timestamp)

        if isinstance(event, RiskDecision):
            self.on_risk_decision(event)

    def on_risk_decision(self, decision: RiskDecision) -> None:
        """Handle risk decisions and take operational action (flatten + halt)."""
        self._check_day_rollover(decision.timestamp)

        if self._state == STATE_TOTAL_SHUTDOWN:
            return

        reason = decision.reason

        # 1. Accumulated drawdown limit (-$1,000) -> Total Shutdown
        if reason == REASON_DRAWDOWN:
            self._trigger_total_shutdown(
                detail=f"DRAWDOWN_BUFFER_TOO_LOW (-${getattr(self._rules, 'max_drawdown_usd', 1000.0):.0f}): total shutdown initiated"
            )
            return

        # 2. Daily Loss Limit (-$200) -> Flatten + Halt for the day
        if reason == REASON_DAILY_LOSS:
            if self._state != STATE_HALTED_DAILY:
                self._trigger_daily_halt(
                    reason=REASON_DAILY_LOSS,
                    detail=f"DAILY_LOSS_LIMIT (-${getattr(self._rules, 'daily_loss_limit_usd', 200.0):.0f}): flatten position + halt until next day",
                )
            return

        # 3. Daily Profit Target reached (+$500) -> Flatten if open + Halt for today
        if reason == REASON_DAILY_PROFIT_TARGET:
            if self._state != STATE_HALTED_DAILY:
                self._trigger_daily_halt(
                    reason=REASON_DAILY_PROFIT_TARGET,
                    detail=f"DAILY_PROFIT_TARGET_REACHED (+${getattr(self._rules, 'daily_profit_target_usd', 500.0):.0f}): profit locked, flatten if open + halt for today",
                )
            return

    def _check_day_rollover(self, event_ts: datetime) -> None:
        event_date = _utc_day(event_ts)
        if self._current_date is None:
            self._current_date = event_date
            return

        if event_date > self._current_date:
            self._current_date = event_date
            self._daily_flattens_count = 0
            if self._state == STATE_HALTED_DAILY:
                self._state = STATE_ACTIVE
                self._daily_halt_reason = None
                self._record_system_event(
                    kind=SYSTEM_CIRCUIT_BREAKER,
                    detail=f"NEW_DAY_RESUMED: Rollover to {event_date.isoformat()}, circuit breaker reset to ACTIVE",
                )

    def _trigger_daily_halt(self, reason: str, detail: str) -> None:
        self._state = STATE_HALTED_DAILY
        self._daily_halt_reason = reason
        flattened = self._adapter.flatten()
        self._daily_flattens_count += 1
        self._total_flattens_count += 1

        flatten_detail = f"flattened={flattened}" if flattened else "no open position"
        full_detail = f"{detail} ({flatten_detail})"
        self._record_system_event(kind=SYSTEM_CIRCUIT_BREAKER, detail=full_detail)

    def _trigger_total_shutdown(self, detail: str) -> None:
        self._state = STATE_TOTAL_SHUTDOWN
        flattened = self._adapter.flatten()
        self._daily_flattens_count += 1
        self._total_flattens_count += 1

        flatten_detail = f"flattened={flattened}" if flattened else "no open position"
        full_detail = f"{detail} ({flatten_detail})"
        self._record_system_event(kind=SYSTEM_HALTED, detail=full_detail)

    def _record_system_event(self, kind: str, detail: str) -> SystemEvent:
        self._seq += 1
        event = SystemEvent(
            event_id=f"cb-sys-{self._seq}",
            source=self._source,
            timestamp=self._clock.now(),
            sequence=self._seq,
            kind=kind,
            origin="live",
            detail=detail,
        )
        self._events_log.append(event)
        if self._recorder is not None:
            self._recorder.record(event)
        return event

    def generate_summary(self) -> dict[str, Any]:
        return {
            "session_state": self._state,
            "current_utc_date": self._current_date.isoformat() if self._current_date else None,
            "daily_halt_reason": self._daily_halt_reason,
            "total_flattens_count": self._total_flattens_count,
            "events_emitted_count": len(self._events_log),
            "events": [
                {
                    "event_id": e.event_id,
                    "kind": e.kind,
                    "timestamp": e.timestamp.isoformat(),
                    "detail": e.detail,
                }
                for e in self._events_log
            ],
        }

"""Tests for Session Circuit Breakers (§4-bis RT-9) in FARS realtime engine.

Verifies:
1. Day touching -$200 daily loss -> flatten exactly once + halt until next UTC day.
2. Day touching +$500 profit target -> flatten if open + stop trading for today (lock gains).
3. Order with risk > $200 that fits at 1 micro -> reduced to 1 micro and filled.
4. Order with risk > $200 that exceeds $200 even at 1 micro -> vetoed (EXEC_REJECTED with MAX_RISK_PER_ORDER).
5. Accumulated loss reaching -$1,000 -> flatten + permanent TOTAL_SHUTDOWN.
6. UTC day rollover resets HALTED_DAILY to ACTIVE, but preserves TOTAL_SHUTDOWN.
7. Anti-overtrading (max 6 trades/day) enforced via MAX_TRADES_REACHED.
8. Account allowlist enforcement in PracticeExecutionAdapter.
9. LIVE_EXECUTION_ENABLED stays strictly False.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from src.realtime.clock import FrozenClock
from src.realtime.circuit_breaker import (
    SessionCircuitBreaker,
    STATE_ACTIVE,
    STATE_HALTED_DAILY,
    STATE_TOTAL_SHUTDOWN,
)
from src.realtime.events import (
    AccountSnapshot,
    EXEC_FILLED,
    EXEC_REJECTED,
    OrderIntent,
    RiskDecision,
    Signal,
    SystemEvent,
    SYSTEM_CIRCUIT_BREAKER,
    SYSTEM_HALTED,
    is_authorized,
)
from src.realtime.interfaces import LIVE_EXECUTION_ENABLED
from src.realtime.practice_adapter import PracticeExecutionAdapter
from src.realtime.risk import (
    AccountAwareRiskEngine,
    REASON_DAILY_LOSS,
    REASON_DAILY_PROFIT_TARGET,
    REASON_DRAWDOWN,
    REASON_MAX_RISK_PER_ORDER,
    REASON_MAX_TRADES,
)
from src.types import create_practice_rules

TS = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)


def test_live_execution_enabled_remains_false():
    assert LIVE_EXECUTION_ENABLED is False


def test_practice_session_day_touches_minus_200_flattens_and_halts():
    clock = FrozenClock(TS)
    rules = create_practice_rules(
        initial_balance=50_000.0,
        daily_loss_limit_usd=200.0,
        daily_profit_target_usd=500.0,
    )
    adapter = PracticeExecutionAdapter(clock, max_risk_dollars_per_order=200.0)
    risk = AccountAwareRiskEngine(rules, clock)
    cb = SessionCircuitBreaker(rules=rules, clock=clock, adapter=adapter)

    # Initial state: flat account with 50k
    snap_sod = AccountSnapshot("s-1", "acc", TS, 1, "live", equity=50_000.0, balance=50_000.0, trades_applied=0)
    risk.observe(snap_sod)
    cb.on_event(snap_sod)
    assert cb.is_active

    # Strategy signal approved, order sent (50 pts stop, 1 micro = $100 risk)
    sig1 = Signal("sig-1", "strat", TS, 1, "MNQ", "LONG", "live")
    dec1 = risk.evaluate(sig1)
    assert is_authorized(dec1) is True
    oi1 = OrderIntent("oi-1", "exec", TS, 1, "MNQ", "LONG", dec1.event_id, origin="live", entry_price=20000.0, stop_price=19950.0)
    rep1 = adapter.submit(sig1, dec1, oi1)
    assert rep1.status == EXEC_FILLED
    assert adapter.open_positions["MNQ"] == 1

    # Account suffers loss: equity drops to $49,800 (-$200)
    t_loss = TS + timedelta(minutes=20)
    clock.set(t_loss)
    snap_loss = AccountSnapshot("s-2", "acc", t_loss, 2, "live", equity=49_800.0, balance=50_000.0, trades_applied=1)
    risk.observe(snap_loss)
    cb.on_event(snap_loss)

    # Risk engine evaluates new signal -> denies due to daily loss
    sig2 = Signal("sig-2", "strat", t_loss, 2, "MNQ", "LONG", "live")
    dec2 = risk.evaluate(sig2)
    assert is_authorized(dec2) is False
    assert dec2.reason == REASON_DAILY_LOSS

    # Circuit breaker handles decision
    cb.on_risk_decision(dec2)

    # Assertions: flatten called once, position is 0, state is HALTED_DAILY
    assert cb.is_daily_halted is True
    assert adapter.open_positions["MNQ"] == 0
    assert adapter.flatten_count == 1
    can, reason = cb.can_submit_order()
    assert can is False
    assert "HALTED_DAILY" in reason

    # Verify audit event in cb log
    sys_events = cb.events_log
    assert len(sys_events) == 1
    assert sys_events[0].kind == SYSTEM_CIRCUIT_BREAKER
    assert "DAILY_LOSS_LIMIT" in sys_events[0].detail
    assert "flattened=['MNQ']" in sys_events[0].detail


def test_practice_session_day_touches_plus_500_stops_for_today_and_flattens():
    clock = FrozenClock(TS)
    rules = create_practice_rules(
        initial_balance=50_000.0,
        daily_profit_target_usd=500.0,
    )
    adapter = PracticeExecutionAdapter(clock, max_risk_dollars_per_order=200.0)
    risk = AccountAwareRiskEngine(rules, clock)
    cb = SessionCircuitBreaker(rules=rules, clock=clock, adapter=adapter)

    # Initial state
    snap_sod = AccountSnapshot("s-1", "acc", TS, 1, "live", equity=50_000.0, balance=50_000.0, trades_applied=0)
    risk.observe(snap_sod)
    cb.on_event(snap_sod)

    # Open position
    sig1 = Signal("sig-1", "strat", TS, 1, "MNQ", "LONG", "live")
    dec1 = risk.evaluate(sig1)
    oi1 = OrderIntent("oi-1", "exec", TS, 1, "MNQ", "LONG", dec1.event_id, origin="live", entry_price=20000.0, stop_price=19950.0)
    adapter.submit(sig1, dec1, oi1)
    assert adapter.open_positions["MNQ"] == 1

    # Market moves in favor: equity reaches $50,500 (+$500 target)
    t_profit = TS + timedelta(hours=1)
    clock.set(t_profit)
    snap_profit = AccountSnapshot("s-2", "acc", t_profit, 2, "live", equity=50_500.0, balance=50_500.0, trades_applied=1)
    risk.observe(snap_profit)
    cb.on_event(snap_profit)

    # Subsequent signal evaluation is denied with DAILY_PROFIT_TARGET_REACHED
    sig2 = Signal("sig-2", "strat", t_profit, 2, "MNQ", "LONG", "live")
    dec2 = risk.evaluate(sig2)
    assert is_authorized(dec2) is False
    assert dec2.reason == REASON_DAILY_PROFIT_TARGET

    cb.on_risk_decision(dec2)

    # Assertions: flatten position once, state is HALTED_DAILY (parada por hoy)
    assert cb.is_daily_halted is True
    assert adapter.open_positions["MNQ"] == 0
    assert adapter.flatten_count == 1
    can, reason = cb.can_submit_order()
    assert can is False
    assert "HALTED_DAILY" in reason

    # Verify audit event
    sys_events = cb.events_log
    assert len(sys_events) == 1
    assert sys_events[0].kind == SYSTEM_CIRCUIT_BREAKER
    assert "DAILY_PROFIT_TARGET_REACHED" in sys_events[0].detail


def test_order_precheck_reduces_size_to_1_micro_when_within_limit():
    clock = FrozenClock(TS)
    adapter = PracticeExecutionAdapter(clock, max_risk_dollars_per_order=200.0, default_dollars_per_point=2.0)

    # Risk: 50 pts * $2/pt * 3 micros = $300 > $200. 1 micro risk = 50 * 2 * 1 = $100 <= $200.
    sig = Signal("sig-1", "strat", TS, 1, "MNQ", "LONG", "live")
    dec = RiskDecision("rd-1", "risk", TS, 1, sig.event_id, sig.source, approved=True, reason="APPROVED", origin="live")
    oi = OrderIntent(
        "oi-1",
        "exec",
        TS,
        1,
        "MNQ",
        "LONG",
        dec.event_id,
        origin="live",
        size=3,
        entry_price=20000.0,
        stop_price=19950.0,
        dollars_per_point=2.0,
    )

    report = adapter.submit(sig, dec, oi)
    assert report.status == EXEC_FILLED
    assert "size=1" in report.reason
    assert "REDUCED_TO_1_MICRO" in report.reason
    # Verify open position reflects the reduced size of 1
    assert adapter.open_positions["MNQ"] == 1


def test_order_precheck_vetoes_when_1_micro_exceeds_max_risk():
    clock = FrozenClock(TS)
    adapter = PracticeExecutionAdapter(clock, max_risk_dollars_per_order=200.0, default_dollars_per_point=2.0)

    # Risk: 150 pts * $2/pt * 1 micro = $300 > $200. Even 1 micro exceeds $200.
    sig = Signal("sig-1", "strat", TS, 1, "MNQ", "LONG", "live")
    dec = RiskDecision("rd-1", "risk", TS, 1, sig.event_id, sig.source, approved=True, reason="APPROVED", origin="live")
    oi = OrderIntent(
        "oi-1",
        "exec",
        TS,
        1,
        "MNQ",
        "LONG",
        dec.event_id,
        origin="live",
        size=1,
        entry_price=20000.0,
        stop_price=19850.0,
        dollars_per_point=2.0,
    )

    report = adapter.submit(sig, dec, oi)
    assert report.status == EXEC_REJECTED
    assert REASON_MAX_RISK_PER_ORDER in report.reason
    assert "$300.00" in report.reason
    # Open position must stay 0
    assert adapter.open_positions.get("MNQ", 0) == 0


def test_practice_session_accumulated_loss_1000_triggers_total_shutdown():
    clock = FrozenClock(TS)
    rules = create_practice_rules(
        initial_balance=50_000.0,
        max_drawdown_usd=1000.0,
    )
    adapter = PracticeExecutionAdapter(clock)
    risk = AccountAwareRiskEngine(rules, clock)
    cb = SessionCircuitBreaker(rules=rules, clock=clock, adapter=adapter)

    # Initial state
    snap_sod = AccountSnapshot("s-1", "acc", TS, 1, "live", equity=50_000.0, balance=50_000.0, trades_applied=0)
    risk.observe(snap_sod)
    cb.on_event(snap_sod)

    # Position open
    sig = Signal("sig-1", "strat", TS, 1, "MNQ", "LONG", "live")
    dec = risk.evaluate(sig)
    oi = OrderIntent("oi-1", "exec", TS, 1, "MNQ", "LONG", dec.event_id, origin="live", entry_price=20000.0, stop_price=19950.0)
    adapter.submit(sig, dec, oi)
    assert adapter.open_positions["MNQ"] == 1

    # Equity hits $49,000 (-$1,000 max drawdown breach)
    t_dd = TS + timedelta(hours=3)
    clock.set(t_dd)
    snap_dd = AccountSnapshot("s-2", "acc", t_dd, 2, "live", equity=49_000.0, balance=49_000.0, trades_applied=1)
    risk.observe(snap_dd)
    cb.on_event(snap_dd)

    dec_dd = risk.evaluate(sig)
    assert is_authorized(dec_dd) is False
    assert dec_dd.reason == REASON_DRAWDOWN

    cb.on_risk_decision(dec_dd)

    # Assertions: flattened and in permanent TOTAL_SHUTDOWN
    assert cb.is_total_shutdown is True
    assert adapter.open_positions["MNQ"] == 0
    assert adapter.flatten_count == 1
    can, reason = cb.can_submit_order()
    assert can is False
    assert "TOTAL_SHUTDOWN" in reason

    # Rollover to next day: TOTAL_SHUTDOWN MUST NOT RESET!
    t_next_day = TS + timedelta(days=1)
    clock.set(t_next_day)
    snap_next = AccountSnapshot("s-3", "acc", t_next_day, 3, "live", equity=49_000.0, balance=49_000.0, trades_applied=1)
    cb.on_event(snap_next)
    assert cb.is_total_shutdown is True
    can_next, _ = cb.can_submit_order()
    assert can_next is False


def test_daily_halt_resets_to_active_on_utc_day_rollover():
    clock = FrozenClock(TS)
    rules = create_practice_rules(initial_balance=50_000.0, daily_loss_limit_usd=200.0)
    adapter = PracticeExecutionAdapter(clock)
    cb = SessionCircuitBreaker(rules=rules, clock=clock, adapter=adapter)

    # Day 1: Halting event
    snap_sod = AccountSnapshot("s-1", "acc", TS, 1, "live", equity=50_000.0, balance=50_000.0, trades_applied=0)
    cb.on_event(snap_sod)
    dec_loss = RiskDecision("rd-1", "risk", TS, 1, "sig-1", "strat", approved=False, reason=REASON_DAILY_LOSS, origin="live")
    cb.on_risk_decision(dec_loss)
    assert cb.is_daily_halted is True

    # Day 2: Rollover to next UTC day
    t_day2 = TS + timedelta(days=1)
    clock.set(t_day2)
    snap_day2 = AccountSnapshot("s-2", "acc", t_day2, 2, "live", equity=49_800.0, balance=49_800.0, trades_applied=0)
    cb.on_event(snap_day2)

    # Must be ACTIVE again
    assert cb.is_active is True
    can, reason = cb.can_submit_order()
    assert can is True
    assert reason == "ACTIVE"


def test_anti_overtrading_max_6_trades_per_day():
    clock = FrozenClock(TS)
    rules = create_practice_rules(max_trades=6)
    risk = AccountAwareRiskEngine(rules, clock)

    snap = AccountSnapshot("s-1", "acc", TS, 1, "live", equity=50_000.0, balance=50_000.0, trades_applied=6)
    risk.observe(snap)

    sig = Signal("sig-1", "strat", TS, 1, "MNQ", "LONG", "live")
    dec = risk.evaluate(sig)
    assert is_authorized(dec) is False
    assert dec.reason == REASON_MAX_TRADES


def test_allowlist_enforcement_in_practice_adapter():
    clock = FrozenClock(TS)
    adapter = PracticeExecutionAdapter(
        clock,
        account_name="PRACTICE_ILLEGAL",
        account_allowlist=("PRACTICE_ALLOWED_1", "PRACTICE_ALLOWED_2"),
    )

    sig = Signal("sig-1", "strat", TS, 1, "MNQ", "LONG", "live")
    dec = RiskDecision("rd-1", "risk", TS, 1, sig.event_id, sig.source, approved=True, reason="APPROVED", origin="live")
    oi = OrderIntent("oi-1", "exec", TS, 1, "MNQ", "LONG", dec.event_id, origin="live")

    report = adapter.submit(sig, dec, oi)
    assert report.status == EXEC_REJECTED
    assert "ACCOUNT_NOT_ALLOWLISTED" in report.reason

"""RT-6 AccountAwareRiskEngine: strategy-agnostic, fail-closed authorization."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.realtime.clock import FrozenClock
from src.realtime.events import (
    AccountSnapshot,
    MarketTick,
    Signal,
    SystemEvent,
    SYSTEM_CIRCUIT_BREAKER,
    SYSTEM_CONNECTOR_DISCONNECTED,
    SYSTEM_CONNECTOR_RECONNECTED,
    SYSTEM_HALTED,
    SYSTEM_RECONCILIATION_MISMATCH,
    SYSTEM_SEQUENCE_GAP,
    SYSTEM_STALE_MARKET_DATA,
    is_authorized,
)
from src.realtime.interfaces import RiskEngine, require_risk_decision
from src.realtime.risk import (
    REASON_APPROVED,
    REASON_CIRCUIT,
    REASON_DAILY_LOSS,
    REASON_DISCONNECTED,
    REASON_DRAWDOWN,
    REASON_HALTED,
    REASON_RECONCILE,
    REASON_STALE,
    REASON_UNKNOWN,
    AccountAwareRiskEngine,
)
from src.types import FundedAccountRules, Trade

TS = datetime(2024, 8, 1, 14, 0, tzinfo=timezone.utc)


def _rules(**kwargs) -> FundedAccountRules:
    return FundedAccountRules(
        initial_balance=kwargs.get("initial_balance", 100_000.0),
        profit_target_pct=kwargs.get("profit_target_pct", 0.10),
        max_drawdown_pct=kwargs.get("max_drawdown_pct", 0.10),
        daily_loss_limit_pct=kwargs.get("daily_loss_limit_pct", 0.05),
        risk_per_trade=kwargs.get("risk_per_trade", 0.01),
        daily_loss_base=kwargs.get("daily_loss_base", "initial"),
        drawdown_mode=kwargs.get("drawdown_mode", "static"),
        max_trades=kwargs.get("max_trades"),
    )


def _engine(rules=None, instant=TS) -> AccountAwareRiskEngine:
    return AccountAwareRiskEngine(rules or _rules(), FrozenClock(instant))


def _snapshot(**overrides) -> AccountSnapshot:
    values = {
        "event_id": "acct-1",
        "source": "acct",
        "timestamp": TS,
        "sequence": 1,
        "equity": 100_000.0,
        "peak_equity": 100_000.0,
        "origin": "live",
    }
    values.update(overrides)
    return AccountSnapshot(**values)


def _signal(**overrides) -> Signal:
    values = {
        "event_id": "sig-1",
        "source": "strat",
        "timestamp": TS,
        "sequence": 1,
        "symbol": "MNQ",
        "action": "LONG",
        "origin": "live",
    }
    values.update(overrides)
    return Signal(**values)


def _system(kind: str, **overrides) -> SystemEvent:
    values = {
        "event_id": f"sys-{kind}",
        "source": "feed/system",
        "timestamp": TS,
        "sequence": 1,
        "kind": kind,
        "origin": "live",
    }
    values.update(overrides)
    return SystemEvent(**values)


def test_engine_matches_risk_protocol():
    engine = _engine()
    assert isinstance(engine, RiskEngine)


def test_no_snapshot_is_unknown_deny():
    decision = _engine().evaluate(_signal())
    assert is_authorized(decision) is False
    assert decision.approved is False
    assert decision.reason == REASON_UNKNOWN
    assert decision.signal_id == "sig-1"
    assert decision.signal_source == "strat"
    assert decision.origin == "live"


def test_snapshot_without_equity_is_unknown_deny():
    engine = _engine()
    engine.observe(_snapshot(equity=None, peak_equity=None))
    decision = engine.evaluate(_signal())
    assert is_authorized(decision) is False
    assert decision.reason == REASON_UNKNOWN


def test_healthy_snapshot_approves_and_binds_signal_identity():
    engine = _engine()
    engine.observe(_snapshot())
    signal = _signal()
    decision = require_risk_decision(engine.evaluate(signal))
    assert is_authorized(decision) is True
    assert decision.approved is True
    assert decision.reason == REASON_APPROVED
    assert decision.signal_id == signal.event_id
    assert decision.signal_source == signal.source
    assert decision.timestamp == TS


def test_same_account_state_is_strategy_agnostic():
    engine = _engine()
    engine.observe(_snapshot())
    long_dec = engine.evaluate(_signal(event_id="a", source="alpha", action="LONG"))
    short_dec = engine.evaluate(_signal(event_id="b", source="beta", action="SHORT", sequence=2))
    assert long_dec.approved is True
    assert short_dec.approved is True
    assert long_dec.signal_source == "alpha"
    assert short_dec.signal_source == "beta"


def test_static_drawdown_at_limit_denies():
    engine = _engine()
    engine.observe(_snapshot(equity=90_000.0, peak_equity=100_000.0))
    decision = engine.evaluate(_signal())
    assert is_authorized(decision) is False
    assert decision.reason == REASON_DRAWDOWN


def test_static_drawdown_just_above_limit_allows():
    engine = _engine()
    engine.observe(_snapshot(equity=90_000.01, peak_equity=100_000.0))
    decision = engine.evaluate(_signal())
    assert is_authorized(decision) is True


def test_static_drawdown_ignores_peak_gains():
    """100k → 150k → 100k is 0% static DD."""
    engine = _engine()
    engine.observe(_snapshot(equity=100_000.0, peak_equity=150_000.0))
    decision = engine.evaluate(_signal())
    assert is_authorized(decision) is True


def test_trailing_drawdown_from_peak_denies():
    engine = _engine(_rules(drawdown_mode="trailing"))
    engine.observe(_snapshot(equity=135_000.0, peak_equity=150_000.0))
    decision = engine.evaluate(_signal())
    assert is_authorized(decision) is False
    assert decision.reason == REASON_DRAWDOWN


def test_trailing_without_peak_is_unknown_deny():
    engine = _engine(_rules(drawdown_mode="trailing"))
    engine.observe(_snapshot(equity=100_000.0, peak_equity=None))
    decision = engine.evaluate(_signal())
    assert is_authorized(decision) is False
    assert decision.reason == REASON_UNKNOWN


def test_trailing_peak_below_equity_is_unknown_deny():
    engine = _engine(_rules(drawdown_mode="trailing"))
    engine.observe(_snapshot(equity=120_000.0, peak_equity=110_000.0))
    decision = engine.evaluate(_signal())
    assert is_authorized(decision) is False
    assert decision.reason == REASON_UNKNOWN


def test_daily_loss_at_limit_denies():
    engine = _engine()
    engine.observe(_snapshot(equity=100_000.0))
    later = TS + timedelta(minutes=30)
    engine.observe(
        _snapshot(
            event_id="acct-2",
            timestamp=later,
            sequence=2,
            equity=95_000.0,
            peak_equity=100_000.0,
        )
    )
    decision = engine.evaluate(_signal(timestamp=later, sequence=2))
    assert is_authorized(decision) is False
    assert decision.reason == REASON_DAILY_LOSS


def test_daily_loss_resets_on_new_utc_day():
    engine = _engine()
    engine.observe(_snapshot(equity=100_000.0))
    next_day = TS + timedelta(days=1)
    engine.observe(
        _snapshot(
            event_id="acct-2",
            timestamp=next_day,
            sequence=2,
            equity=96_000.0,
            peak_equity=100_000.0,
        )
    )
    decision = engine.evaluate(_signal(timestamp=next_day, sequence=2))
    assert is_authorized(decision) is True


def test_offset_labels_do_not_reset_utc_daily_loss():
    """Same UTC day with mixed offsets must not look like a new local date."""
    ast = timezone(timedelta(hours=-4))
    engine = _engine()
    engine.observe(
        _snapshot(
            timestamp=datetime(2024, 8, 1, 21, 0, tzinfo=ast),
            equity=100_000.0,
        )
    )
    engine.observe(
        _snapshot(
            event_id="acct-2",
            timestamp=datetime(2024, 8, 1, 21, 20, tzinfo=ast),
            sequence=2,
            equity=95_000.0,
            peak_equity=100_000.0,
        )
    )
    engine.observe(
        _snapshot(
            event_id="acct-3",
            timestamp=datetime(2024, 8, 2, 1, 21, tzinfo=timezone.utc),
            sequence=3,
            equity=95_000.0,
            peak_equity=100_000.0,
        )
    )
    decision = engine.evaluate(_signal(sequence=3))
    assert is_authorized(decision) is False
    assert decision.reason == REASON_DAILY_LOSS


def test_daily_loss_eod_base_uses_start_of_day_equity():
    engine = _engine(_rules(daily_loss_base="eod", max_drawdown_pct=0.50))
    engine.observe(_snapshot(equity=80_000.0, peak_equity=100_000.0))
    later = TS + timedelta(minutes=10)
    engine.observe(
        _snapshot(
            event_id="acct-2",
            timestamp=later,
            sequence=2,
            equity=76_000.0,
            peak_equity=100_000.0,
        )
    )
    decision = engine.evaluate(_signal(timestamp=later, sequence=2))
    assert is_authorized(decision) is False
    assert decision.reason == REASON_DAILY_LOSS


def test_origin_mismatch_is_unknown_deny():
    engine = _engine()
    engine.observe(_snapshot(origin="replay"))
    decision = engine.evaluate(_signal(origin="live"))
    assert is_authorized(decision) is False
    assert decision.reason == REASON_UNKNOWN


def test_halt_latches_even_after_healthy_snapshot():
    engine = _engine()
    engine.observe(_snapshot())
    engine.observe(_system(SYSTEM_HALTED))
    engine.observe(_snapshot(event_id="acct-2", sequence=2, equity=100_000.0))
    decision = engine.evaluate(_signal())
    assert is_authorized(decision) is False
    assert decision.reason == REASON_HALTED


def test_circuit_breaker_and_mismatch_deny():
    engine = _engine()
    engine.observe(_snapshot())
    engine.observe(_system(SYSTEM_CIRCUIT_BREAKER))
    assert engine.evaluate(_signal()).reason == REASON_CIRCUIT

    engine = _engine()
    engine.observe(_snapshot())
    engine.observe(_system(SYSTEM_RECONCILIATION_MISMATCH))
    assert engine.evaluate(_signal()).reason == REASON_RECONCILE


def test_stale_denies_until_fresh_snapshot():
    engine = _engine()
    engine.observe(_snapshot())
    engine.observe(_system(SYSTEM_STALE_MARKET_DATA))
    assert engine.evaluate(_signal()).reason == REASON_STALE
    engine.observe(_snapshot(event_id="acct-2", sequence=2))
    assert is_authorized(engine.evaluate(_signal(event_id="sig-2", sequence=2))) is True


def test_disconnect_denies_until_resync_snapshot():
    engine = _engine()
    engine.observe(_snapshot())
    engine.observe(_system(SYSTEM_CONNECTOR_DISCONNECTED))
    assert engine.evaluate(_signal()).reason == REASON_DISCONNECTED
    engine.observe(_system(SYSTEM_CONNECTOR_RECONNECTED, event_id="sys-up", sequence=2))
    stale = engine.evaluate(_signal(event_id="sig-2", sequence=2))
    assert is_authorized(stale) is False
    assert stale.reason == REASON_STALE
    engine.observe(_snapshot(event_id="acct-2", sequence=3))
    assert is_authorized(engine.evaluate(_signal(event_id="sig-3", sequence=3))) is True


def test_snapshot_during_disconnect_does_not_count_as_resync():
    engine = _engine()
    engine.observe(_snapshot())
    engine.observe(_system(SYSTEM_CONNECTOR_DISCONNECTED))
    engine.observe(_snapshot(event_id="acct-mid", sequence=2, equity=100_000.0))
    engine.observe(_system(SYSTEM_CONNECTOR_RECONNECTED, event_id="sys-up", sequence=3))
    decision = engine.evaluate(_signal(event_id="sig-2", sequence=2))
    assert is_authorized(decision) is False
    assert decision.reason == REASON_STALE
    engine.observe(_snapshot(event_id="acct-3", sequence=4))
    assert is_authorized(engine.evaluate(_signal(event_id="sig-3", sequence=3))) is True


def test_incomplete_snapshot_does_not_clear_stale():
    engine = _engine()
    engine.observe(_snapshot())
    engine.observe(_system(SYSTEM_STALE_MARKET_DATA))
    engine.observe(_snapshot(event_id="acct-empty", sequence=2, equity=None, peak_equity=None))
    decision = engine.evaluate(_signal())
    assert is_authorized(decision) is False
    assert decision.reason == REASON_STALE


def test_zero_equity_is_drawdown_not_unknown():
    engine = _engine()
    engine.observe(_snapshot(equity=0.0, peak_equity=100_000.0))
    decision = engine.evaluate(_signal())
    assert is_authorized(decision) is False
    assert decision.reason == REASON_DRAWDOWN


def test_sequence_gap_does_not_deny_known_account():
    engine = _engine()
    engine.observe(_snapshot())
    engine.observe(_system(SYSTEM_SEQUENCE_GAP))
    assert is_authorized(engine.evaluate(_signal())) is True


def test_market_ticks_are_not_account_state():
    engine = _engine()
    engine.observe(MarketTick("t1", "feed", TS, 1, "MNQ", 1.0, 1.0))
    decision = engine.evaluate(_signal())
    assert decision.reason == REASON_UNKNOWN


def test_core_trade_is_not_observable_state():
    engine = _engine()
    with pytest.raises(TypeError, match="canonical events"):
        engine.observe(Trade(r_result=1.0))  # type: ignore[arg-type]


def test_non_signal_evaluate_raises():
    with pytest.raises(TypeError, match="Signal"):
        _engine().evaluate(_snapshot())  # type: ignore[arg-type]


def test_clock_used_for_decision_timestamp_not_signal_time():
    clock = FrozenClock(TS)
    engine = AccountAwareRiskEngine(_rules(), clock)
    engine.observe(_snapshot())
    later = TS + timedelta(seconds=5)
    clock.set(later)
    decision = engine.evaluate(_signal())
    assert decision.timestamp == later


def test_drawdown_dollar_boundary_does_not_miss_via_ratio():
    """Core pitfall: ratio DD can land 1 ULP under the limit."""
    rules = _rules(initial_balance=28778.7, max_drawdown_pct=0.12)
    engine = _engine(rules)
    threshold = 28778.7 - 28778.7 * 0.12
    engine.observe(_snapshot(equity=threshold, peak_equity=28778.7))
    decision = engine.evaluate(_signal())
    assert is_authorized(decision) is False
    assert decision.reason == REASON_DRAWDOWN


def test_snapshot_time_going_backwards_is_unknown_deny():
    engine = _engine()
    engine.observe(_snapshot(equity=100_000.0))
    earlier = TS - timedelta(days=1)
    engine.observe(
        _snapshot(
            event_id="acct-2",
            timestamp=earlier,
            sequence=2,
            equity=100_000.0,
            peak_equity=100_000.0,
        )
    )
    decision = engine.evaluate(_signal())
    assert is_authorized(decision) is False
    assert decision.reason == REASON_UNKNOWN


def test_intraday_timestamp_rewind_is_unknown_deny():
    engine = _engine()
    engine.observe(_snapshot(equity=94_000.0))
    earlier = TS - timedelta(minutes=5)
    engine.observe(
        _snapshot(
            event_id="acct-2",
            timestamp=earlier,
            sequence=2,
            equity=100_000.0,
            peak_equity=100_000.0,
        )
    )
    decision = engine.evaluate(_signal())
    assert is_authorized(decision) is False
    assert decision.reason == REASON_UNKNOWN


def test_denied_decision_cannot_reach_execution():
    from src.realtime.events import EXEC_ACCEPTED, ExecutionReport, OrderIntent
    from src.realtime.interfaces import ExecutionAdapter

    class _Sink(ExecutionAdapter):
        def __init__(self) -> None:
            self.got: list[OrderIntent] = []

        def _execute_authorized(self, intent: OrderIntent) -> ExecutionReport:
            self.got.append(intent)
            return ExecutionReport("er-1", "exec", TS, 1, intent.event_id, EXEC_ACCEPTED)

    engine = _engine()
    signal = _signal()
    decision = engine.evaluate(signal)
    intent = OrderIntent(
        "oi-1",
        "exec",
        TS,
        1,
        signal.symbol,
        signal.action,
        decision.event_id,
        origin=signal.origin,
    )
    sink = _Sink()
    with pytest.raises(ValueError, match="did not approve"):
        sink.submit(signal, decision, intent)
    assert sink.got == []

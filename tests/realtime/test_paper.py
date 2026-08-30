"""RT-7 paper execution: same adapter interface, explicit non-live fills."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.realtime.clock import FrozenClock
from src.realtime.events import (
    EXEC_FILLED,
    EXEC_PARTIAL,
    EXEC_REJECTED,
    AccountSnapshot,
    OrderIntent,
    RiskDecision,
    Signal,
    is_authorized,
)
from src.realtime.interfaces import LIVE_EXECUTION_ENABLED, ExecutionAdapter
from src.realtime.paper import (
    PAPER_FILL_PARTIAL,
    PAPER_FILL_REJECT,
    PaperAssumptions,
    PaperExecutionAdapter,
    default_paper_assumptions,
)
from src.realtime.risk import AccountAwareRiskEngine
from src.types import FundedAccountRules

TS = datetime(2024, 8, 1, 14, 0, tzinfo=timezone.utc)


def _rules() -> FundedAccountRules:
    return FundedAccountRules(
        initial_balance=100_000.0,
        profit_target_pct=0.10,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.05,
        risk_per_trade=0.01,
    )


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


def _intent(decision: RiskDecision, signal: Signal, **overrides) -> OrderIntent:
    values = {
        "event_id": "oi-1",
        "source": "exec",
        "timestamp": TS,
        "sequence": 1,
        "symbol": signal.symbol,
        "action": signal.action,
        "risk_decision_id": decision.event_id,
        "origin": signal.origin,
    }
    values.update(overrides)
    return OrderIntent(**values)


def _authorized(engine: AccountAwareRiskEngine | None = None):
    risk = engine or AccountAwareRiskEngine(_rules(), FrozenClock(TS))
    risk.observe(
        AccountSnapshot(
            "acct-1",
            "acct",
            TS,
            1,
            equity=100_000.0,
            peak_equity=100_000.0,
            origin="live",
        )
    )
    signal = _signal()
    decision = risk.evaluate(signal)
    assert is_authorized(decision) is True
    return risk, signal, decision, _intent(decision, signal)


def _paper(assumptions=None, instant=TS) -> PaperExecutionAdapter:
    return PaperExecutionAdapter(FrozenClock(instant), assumptions or default_paper_assumptions())


def test_paper_is_execution_adapter_and_live_stays_locked():
    paper = _paper()
    assert isinstance(paper, ExecutionAdapter)
    assert LIVE_EXECUTION_ENABLED is False


def test_paper_cannot_override_submit():
    with pytest.raises(TypeError, match="submit cannot be overridden"):

        class _Broken(PaperExecutionAdapter):
            def submit(self, signal, decision, intent):  # type: ignore[override]
                return self._execute_authorized(intent)


def test_authorized_paper_fill_is_labeled_not_live():
    _, signal, decision, intent = _authorized()
    report = _paper().submit(signal, decision, intent)
    assert report.status == EXEC_FILLED
    assert report.order_intent_id == intent.event_id
    assert report.origin == "live"
    assert report.reason.startswith("PAPER not_live_equivalent")
    assert "slippage=unmodeled" in report.reason
    assert "commissions=unmodeled" in report.reason
    assert "live_equivalent" not in report.reason.replace("not_live_equivalent", "")


def test_denied_risk_never_reaches_paper_fill():
    risk = AccountAwareRiskEngine(_rules(), FrozenClock(TS))
    signal = _signal()
    decision = risk.evaluate(signal)
    assert is_authorized(decision) is False
    paper = _paper()
    with pytest.raises(ValueError, match="did not approve"):
        paper.submit(signal, decision, _intent(decision, signal))


def test_latency_is_applied_to_fill_timestamp_not_clock():
    clock = FrozenClock(TS)
    paper = PaperExecutionAdapter(
        clock,
        PaperAssumptions(
            latency=timedelta(seconds=3),
            slippage="unmodeled: OrderIntent has no price",
            commissions="unmodeled: OrderIntent has no quantity",
            partial_fills="not_simulated: each intent is one report",
            rejections="none",
            fill_policy="full",
        ),
    )
    _, signal, decision, intent = _authorized()
    report = paper.submit(signal, decision, intent)
    assert report.timestamp == TS + timedelta(seconds=3)
    assert clock.now() == TS


def test_partial_and_reject_policies():
    _, signal, decision, intent = _authorized()
    partial = _paper(
        PaperAssumptions(
            latency=timedelta(0),
            slippage="unmodeled: OrderIntent has no price",
            commissions="unmodeled: OrderIntent has no quantity",
            partial_fills="assumed_partial: no size on intent",
            rejections="none",
            fill_policy=PAPER_FILL_PARTIAL,
        )
    ).submit(signal, decision, intent)
    assert partial.status == EXEC_PARTIAL

    rejected = _paper(
        PaperAssumptions(
            latency=timedelta(0),
            slippage="unmodeled: OrderIntent has no price",
            commissions="unmodeled: OrderIntent has no quantity",
            partial_fills="not_simulated: each intent is one report",
            rejections="all_intents",
            fill_policy=PAPER_FILL_REJECT,
        )
    ).submit(signal, decision, intent)
    assert rejected.status == EXEC_REJECTED


def test_duplicate_intent_is_idempotent():
    _, signal, decision, intent = _authorized()
    paper = _paper()
    first = paper.submit(signal, decision, intent)
    second = paper.submit(signal, decision, intent)
    assert second is first
    assert first.status == EXEC_FILLED


def test_empty_assumption_notes_are_rejected():
    with pytest.raises(ValueError, match="slippage"):
        PaperAssumptions(
            latency=timedelta(0),
            slippage="",
            commissions="x",
            partial_fills="x",
            rejections="x",
            fill_policy="full",
        )
    with pytest.raises(ValueError, match="latency"):
        PaperAssumptions(
            latency=timedelta(seconds=-1),
            slippage="x",
            commissions="x",
            partial_fills="x",
            rejections="x",
            fill_policy="full",
        )


def test_paper_refuses_if_live_flag_is_true(monkeypatch):
    import src.realtime.paper as paper_mod

    monkeypatch.setattr(paper_mod, "LIVE_EXECUTION_ENABLED", True)
    with pytest.raises(RuntimeError, match="live execution"):
        PaperExecutionAdapter(FrozenClock(TS), default_paper_assumptions())


def test_strategy_risk_paper_pipeline():
    from src.realtime.events import MarketTick
    from src.realtime.interfaces import Strategy, require_signal

    class _Strat:
        def on_event(self, event):
            if isinstance(event, MarketTick):
                return _signal(event_id="sig-tick", sequence=event.sequence)
            return None

    strategy: Strategy = _Strat()
    tick = MarketTick("t1", "feed", TS, 1, "MNQ", 1.0, 1.0)
    signal = require_signal(strategy.on_event(tick))
    risk = AccountAwareRiskEngine(_rules(), FrozenClock(TS))
    risk.observe(
        AccountSnapshot(
            "acct-1",
            "acct",
            TS,
            1,
            equity=100_000.0,
            peak_equity=100_000.0,
            origin="live",
        )
    )
    decision = risk.evaluate(signal)
    intent = _intent(decision, signal, event_id="oi-tick")
    report = _paper().submit(signal, decision, intent)
    assert report.status == EXEC_FILLED
    assert "PAPER not_live_equivalent" in report.reason

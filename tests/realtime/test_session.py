"""RT-8 complete Connector -> Bus -> Risk -> Paper -> Recorder session tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.realtime.bus import AsyncIOEventBus
from src.realtime.clock import FrozenClock
from src.realtime.connector import ReplayMarketConnector
from src.realtime.events import EXEC_FILLED, MarketTick, RiskDecision, Signal
from src.realtime.paper import PaperExecutionAdapter, default_paper_assumptions
from src.realtime.recorder import FileEventRecorder, reconstruct_events
from src.realtime.risk import AccountAwareRiskEngine, REASON_UNKNOWN
from src.realtime.session import PaperRealtimeSession, RealtimeSessionError
from src.types import FundedAccountRules


TS = datetime(2024, 9, 1, 14, tzinfo=timezone.utc)


def _rules() -> FundedAccountRules:
    return FundedAccountRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.05,
        risk_per_trade=0.01,
    )


class _TickStrategy:
    def __init__(self) -> None:
        self._sequence = 0

    def on_event(self, event):
        if not isinstance(event, MarketTick):
            return None
        self._sequence += 1
        return Signal(
            event_id=f"signal-{event.event_id}",
            source="test-strategy",
            timestamp=event.timestamp,
            sequence=self._sequence,
            symbol=event.symbol,
            action="LONG",
            origin=event.origin,
        )


def _payloads(*, include_snapshot=True):
    payloads = []
    if include_snapshot:
        payloads.append(
            {
                "type": "snapshot",
                "event_id": "account-1",
                "timestamp": TS.isoformat(),
                "sequence": 1,
                "equity": 100_000,
                "balance": 100_000,
                "peak_equity": 100_000,
                "last_sync": TS.isoformat(),
            }
        )
    payloads.append(
        {
            "type": "tick",
            "event_id": "tick-1",
            "timestamp": TS.isoformat(),
            "sequence": 2 if include_snapshot else 1,
            "symbol": "MNQ",
            "price": 20_000,
            "volume": 1,
        }
    )
    return payloads


def _session(tmp_path, *, include_snapshot=True, strategy=None, risk=None):
    clock = FrozenClock(TS)
    journal = tmp_path / "paper-session.jsonl"
    return (
        PaperRealtimeSession(
            connector=ReplayMarketConnector(
                _payloads(include_snapshot=include_snapshot),
                source="replay-feed",
                clock=clock,
            ),
            bus=AsyncIOEventBus(maxsize=16),
            strategy=strategy or _TickStrategy(),
            risk=risk or AccountAwareRiskEngine(_rules(), clock),
            execution=PaperExecutionAdapter(
                clock,
                default_paper_assumptions(),
            ),
            recorder=FileEventRecorder(journal),
            clock=clock,
        ),
        journal,
    )


def test_complete_session_records_every_pipeline_boundary(tmp_path):
    session, journal = _session(tmp_path)
    result = asyncio.run(session.run())

    assert result.metrics.connector_events == 2
    assert result.metrics.bus_events == 6
    assert result.metrics.signals_generated == 1
    assert result.metrics.risk_approvals == 1
    assert result.metrics.risk_denials == 0
    assert result.metrics.orders_submitted == 1
    assert result.metrics.execution_reports == 1
    assert result.reports[0].status == EXEC_FILLED
    assert "PAPER not_live_equivalent" in result.reports[0].reason
    recorded = reconstruct_events(journal)
    assert [type(event).__name__ for event in recorded] == [
        "AccountSnapshot",
        "MarketTick",
        "Signal",
        "RiskDecision",
        "OrderIntent",
        "ExecutionReport",
    ]


def test_unknown_account_state_denies_without_creating_an_order(tmp_path):
    session, journal = _session(tmp_path, include_snapshot=False)
    result = asyncio.run(session.run())

    assert result.metrics.risk_approvals == 0
    assert result.metrics.risk_denials == 1
    assert result.metrics.orders_submitted == 0
    assert result.metrics.execution_reports == 0
    assert result.decisions[0].reason == REASON_UNKNOWN
    assert [type(event).__name__ for event in reconstruct_events(journal)] == [
        "MarketTick",
        "Signal",
        "RiskDecision",
    ]


def test_strategy_contract_failure_halts_and_never_executes(tmp_path):
    class _BrokenStrategy:
        def on_event(self, event):
            return object() if isinstance(event, MarketTick) else None

    session, journal = _session(tmp_path, strategy=_BrokenStrategy())

    with pytest.raises(RealtimeSessionError, match="halted"):
        asyncio.run(session.run())
    assert [type(event).__name__ for event in reconstruct_events(journal)] == [
        "AccountSnapshot",
        "MarketTick",
    ]


def test_session_is_single_use(tmp_path):
    session, _journal = _session(tmp_path)
    asyncio.run(session.run())

    with pytest.raises(RealtimeSessionError, match="single-use"):
        asyncio.run(session.run())


def test_misbound_risk_decision_halts_before_intent_is_recorded(tmp_path):
    class _MisboundRisk:
        def observe(self, _event):
            return None

        def evaluate(self, signal):
            return RiskDecision(
                event_id="bad-risk",
                source="bad-risk",
                timestamp=signal.timestamp,
                sequence=1,
                signal_id="different-signal",
                signal_source=signal.source,
                approved=True,
                reason="incorrect approval",
                origin=signal.origin,
            )

    session, journal = _session(tmp_path, risk=_MisboundRisk())

    with pytest.raises(RealtimeSessionError, match="halted"):
        asyncio.run(session.run())
    assert [type(event).__name__ for event in reconstruct_events(journal)] == [
        "AccountSnapshot",
        "MarketTick",
        "Signal",
    ]

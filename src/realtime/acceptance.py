"""RT-8 deterministic validation and explicit acceptance result.

The harness exercises the realtime/replay/risk/paper pipeline without broker
access or live execution.  A PASS confirms only the RT-8 checks declared here;
it does not enable RT-9 and is not evidence of strategy profitability.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from src.realtime.bus import AsyncIOEventBus, BusError
from src.realtime.clock import FrozenClock
from src.realtime.connector import ReplayMarketConnector
from src.realtime.events import (
    EXEC_FILLED,
    EXEC_REJECTED,
    ORIGIN_LIVE,
    ORIGIN_REPLAY,
    SIGNAL_LONG,
    SYSTEM_CIRCUIT_BREAKER,
    SYSTEM_CONNECTOR_DISCONNECTED,
    SYSTEM_CONNECTOR_RECONNECTED,
    SYSTEM_RECONCILIATION_MISMATCH,
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
)
from src.realtime.interfaces import LIVE_EXECUTION_ENABLED
from src.realtime.paper import (
    PAPER_FILL_REJECT,
    PaperAssumptions,
    PaperExecutionAdapter,
    default_paper_assumptions,
)
from src.realtime.recorder import (
    FileEventRecorder,
    event_to_record,
    reconstruct_events,
    record_to_event,
)
from src.realtime.replay import ReplayEngine
from src.realtime.risk import (
    REASON_APPROVED,
    REASON_CIRCUIT,
    REASON_DISCONNECTED,
    REASON_RECONCILE,
    REASON_STALE,
    REASON_UNKNOWN,
    REASON_MAX_TRADES,
    AccountAwareRiskEngine,
)
from src.realtime.session import PaperRealtimeSession
from src.types import FundedAccountRules


RT8_SCHEMA_VERSION = "fars-rt8-acceptance-v1"
RT8_PASS = "PASS"
RT8_FAIL = "FAIL"

AcceptanceStatus = Literal["PASS", "FAIL"]

REQUIRED_CHECKS = (
    "event_correctness",
    "duplicate_safety",
    "ordering_behavior",
    "replay_reproducibility",
    "risk_enforcement",
    "account_state_consistency",
    "circuit_breakers",
    "reconnect_resync",
    "stale_data_behavior",
    "paper_trading_stability",
    "latency_error_behavior",
    "complete_pipeline",
)

_TS = datetime(2024, 10, 1, 14, tzinfo=timezone.utc)


@dataclass(frozen=True)
class RT8CheckResult:
    name: str
    passed: bool
    detail: str

    def __post_init__(self) -> None:
        if self.name not in REQUIRED_CHECKS:
            raise ValueError(f"unknown RT-8 check {self.name!r}")
        if not isinstance(self.passed, bool):
            raise ValueError("passed must be bool")
        if not isinstance(self.detail, str) or not self.detail.strip():
            raise ValueError("detail must be a non-empty string")


@dataclass(frozen=True)
class RT8AcceptanceResult:
    status: AcceptanceStatus
    checks: tuple[RT8CheckResult, ...]
    live_execution_enabled: bool
    limitations: tuple[str, ...]
    schema_version: str = RT8_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "checks", tuple(self.checks))
        object.__setattr__(self, "limitations", tuple(self.limitations))
        if self.schema_version != RT8_SCHEMA_VERSION:
            raise ValueError("unsupported RT-8 result schema")
        names = tuple(check.name for check in self.checks)
        if names != REQUIRED_CHECKS:
            raise ValueError("RT-8 result must contain every required check in order")
        expected = RT8_PASS if all(check.passed for check in self.checks) else RT8_FAIL
        if self.status != expected:
            raise ValueError("RT-8 status is inconsistent with its checks")
        if self.live_execution_enabled:
            raise ValueError("RT-8 acceptance must not enable live execution")

    @property
    def failures(self) -> tuple[RT8CheckResult, ...]:
        return tuple(check for check in self.checks if not check.passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "live_execution_enabled": self.live_execution_enabled,
            "checks": [asdict(check) for check in self.checks],
            "limitations": list(self.limitations),
        }


def _rules() -> FundedAccountRules:
    return FundedAccountRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.05,
        risk_per_trade=0.01,
    )


def _snapshot(
    *,
    event_id: str = "account-1",
    sequence: int = 1,
    timestamp: datetime = _TS,
    origin: str = ORIGIN_LIVE,
    equity: float = 100_000,
    peak_equity: float = 100_000,
    trades_applied: int = 0,
) -> AccountSnapshot:
    return AccountSnapshot(
        event_id=event_id,
        source="account",
        timestamp=timestamp,
        sequence=sequence,
        origin=origin,
        balance=equity,
        equity=equity,
        peak_equity=peak_equity,
        broker_timestamp=timestamp,
        last_sync=timestamp,
        trades_applied=trades_applied,
    )


def _signal(
    *,
    event_id: str = "signal-1",
    sequence: int = 1,
    timestamp: datetime = _TS,
    origin: str = ORIGIN_LIVE,
) -> Signal:
    return Signal(
        event_id=event_id,
        source="strategy",
        timestamp=timestamp,
        sequence=sequence,
        symbol="MNQ",
        action=SIGNAL_LONG,
        origin=origin,
    )


def _system(kind: str, sequence: int) -> SystemEvent:
    return SystemEvent(
        event_id=f"system-{sequence}",
        source="system",
        timestamp=_TS,
        sequence=sequence,
        kind=kind,
        origin=ORIGIN_LIVE,
    )


def _approved_bundle(
    *,
    clock: FrozenClock | None = None,
) -> tuple[FrozenClock, Signal, RiskDecision, OrderIntent]:
    active_clock = clock or FrozenClock(_TS)
    risk = AccountAwareRiskEngine(_rules(), active_clock)
    risk.observe(_snapshot())
    signal = _signal()
    decision = risk.evaluate(signal)
    if not decision.approved:
        raise AssertionError("acceptance fixture expected explicit risk approval")
    intent = OrderIntent(
        event_id="intent-1",
        source="paper-session",
        timestamp=_TS,
        sequence=1,
        symbol=signal.symbol,
        action=signal.action,
        risk_decision_id=decision.event_id,
        origin=signal.origin,
    )
    return active_clock, signal, decision, intent


def _event_correctness(_root: Path) -> str:
    events = (
        MarketTick("tick", "feed", _TS, 1, "MNQ", 20_000, 1, ORIGIN_REPLAY),
        Quote("quote", "feed", _TS, 2, "MNQ", 19_999, 20_001, 1, 1, ORIGIN_REPLAY),
        Bar(
            "bar",
            "feed",
            _TS,
            3,
            "MNQ",
            "1m",
            20_000,
            20_010,
            19_990,
            20_005,
            10,
            ORIGIN_REPLAY,
        ),
        MarketTrade("trade", "feed", _TS, 4, "MNQ", 20_000, 1, ORIGIN_REPLAY),
        _snapshot(origin=ORIGIN_REPLAY),
        _signal(origin=ORIGIN_REPLAY),
        RiskDecision(
            "risk-1",
            "risk",
            _TS,
            1,
            "signal-1",
            "strategy",
            True,
            "APPROVED",
            ORIGIN_REPLAY,
        ),
        OrderIntent(
            "intent-1",
            "paper",
            _TS,
            1,
            "MNQ",
            SIGNAL_LONG,
            "risk-1",
            ORIGIN_REPLAY,
        ),
        ExecutionReport(
            "report-1",
            "paper",
            _TS,
            1,
            "intent-1",
            EXEC_FILLED,
            ORIGIN_REPLAY,
            "PAPER not_live_equivalent",
        ),
        SystemEvent(
            "system-1",
            "system",
            _TS,
            1,
            SYSTEM_CIRCUIT_BREAKER,
            ORIGIN_REPLAY,
        ),
    )
    reconstructed = tuple(record_to_event(event_to_record(event)) for event in events)
    if reconstructed != events:
        raise AssertionError("canonical record round-trip changed an event")
    return f"{len(events)} canonical event types round-trip exactly"


async def _duplicate_scenario(root: Path) -> str:
    bus = AsyncIOEventBus(maxsize=4)
    seen: list[MarketTick] = []
    bus.subscribe(lambda event: seen.append(event))
    await bus.start()
    tick = MarketTick("tick-1", "feed", _TS, 1, "MNQ", 20_000, 1)
    await bus.publish(tick)
    await bus.publish(tick)
    await bus.shutdown()
    if seen != [tick] or bus.duplicates != 1:
        raise AssertionError("duplicate event was delivered or not counted")

    class DuplicateSignalStrategy:
        def on_event(self, event):
            if not isinstance(event, MarketTick):
                return None
            return Signal(
                event_id="same-signal",
                source="duplicate-strategy",
                timestamp=event.timestamp,
                sequence=1,
                symbol=event.symbol,
                action=SIGNAL_LONG,
                origin=event.origin,
            )

    clock = FrozenClock(_TS)
    session_bus = AsyncIOEventBus(maxsize=16)
    journal = root / "duplicate-pipeline.jsonl"
    payloads = (
        {
            "type": "snapshot",
            "event_id": "account-duplicate-check",
            "timestamp": _TS,
            "sequence": 1,
            "equity": 100_000,
            "peak_equity": 100_000,
            "last_sync": _TS,
        },
        {
            "type": "tick",
            "event_id": "duplicate-check-tick-1",
            "timestamp": _TS,
            "sequence": 2,
            "symbol": "MNQ",
            "price": 20_000,
            "volume": 1,
        },
        {
            "type": "tick",
            "event_id": "duplicate-check-tick-2",
            "timestamp": _TS,
            "sequence": 3,
            "symbol": "MNQ",
            "price": 20_001,
            "volume": 1,
        },
    )
    session = PaperRealtimeSession(
        connector=ReplayMarketConnector(payloads, source="duplicate-feed", clock=clock),
        bus=session_bus,
        strategy=DuplicateSignalStrategy(),
        risk=AccountAwareRiskEngine(_rules(), clock),
        execution=PaperExecutionAdapter(clock, default_paper_assumptions()),
        recorder=FileEventRecorder(journal),
        clock=clock,
    )
    result = await session.run()
    recorded = reconstruct_events(journal)
    if session_bus.duplicates != 1:
        raise AssertionError("duplicate generated signal was not counted by the bus")
    if result.metrics.orders_submitted != 1 or result.metrics.execution_reports != 1:
        raise AssertionError("duplicate generated signal created duplicate execution")
    if sum(isinstance(event, Signal) for event in recorded) != 1:
        raise AssertionError("duplicate generated signal was recorded more than once")
    return "duplicate event and generated signal each produced one downstream effect"


def _duplicate_safety(root: Path) -> str:
    return asyncio.run(_duplicate_scenario(root))


async def _ordering_scenario() -> str:
    bus = AsyncIOEventBus(maxsize=8)
    seen: list[int] = []
    bus.subscribe(lambda event: seen.append(event.sequence))
    await bus.start()
    for event_id, sequence in (("e1", 1), ("e3", 3), ("e2", 2)):
        await bus.publish(
            MarketTick(event_id, "feed", _TS, sequence, "MNQ", 20_000, 1)
        )
    await bus.shutdown()
    if seen != [1, 3, 2] or bus.gaps != 1 or bus.late != 1:
        raise AssertionError("gap/late behavior was reordered or misclassified")

    conflict_bus = AsyncIOEventBus(maxsize=4)
    await conflict_bus.start()
    await conflict_bus.publish(MarketTick("a", "feed", _TS, 1, "MNQ", 1, 1))
    try:
        await conflict_bus.publish(MarketTick("b", "feed", _TS, 1, "MNQ", 1, 1))
    except BusError:
        pass
    else:
        raise AssertionError("sequence identity conflict did not halt publishing")
    await conflict_bus.shutdown()
    if conflict_bus.conflicts != 1:
        raise AssertionError("sequence identity conflict was not counted")
    return "gap and late events disclosed; identity conflict halted the bus"


def _ordering_behavior(_root: Path) -> str:
    return asyncio.run(_ordering_scenario())


async def _replay_trace(path: Path) -> tuple:
    clock = FrozenClock(_TS)
    bus = AsyncIOEventBus(maxsize=4)
    trace: list[tuple[object, datetime]] = []
    bus.subscribe(lambda event: trace.append((event, clock.now())))
    await bus.start()
    played = await ReplayEngine(path, bus=bus, clock=clock).play()
    await bus.shutdown()
    return played, tuple(trace)


def _replay_reproducibility(root: Path) -> str:
    journal = root / "replay.jsonl"
    recorder = FileEventRecorder(journal)
    recorder.record(MarketTick("e1", "feed", _TS, 1, "MNQ", 1, 1))
    recorder.record(
        MarketTick("e2", "feed", _TS + timedelta(seconds=2), 2, "MNQ", 2, 1)
    )
    first = asyncio.run(_replay_trace(journal))
    second = asyncio.run(_replay_trace(journal))
    if first != second or first[0] != 2:
        raise AssertionError("fixed journal replay was not reproducible")
    return "two replay runs produced identical events and FrozenClock states"


def _risk_enforcement(_root: Path) -> str:
    clock = FrozenClock(_TS)
    risk = AccountAwareRiskEngine(_rules(), clock)
    denied = risk.evaluate(_signal())
    if denied.approved or denied.reason != REASON_UNKNOWN:
        raise AssertionError("unknown account state did not deny")
    risk.observe(_snapshot())
    approved = risk.evaluate(_signal(event_id="signal-2", sequence=2))
    if not approved.approved or approved.reason != REASON_APPROVED:
        raise AssertionError("complete safe account state was not approved")
    limited_rules = FundedAccountRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.05,
        risk_per_trade=0.01,
        max_trades=2,
    )
    limited = AccountAwareRiskEngine(limited_rules, clock)
    limited.observe(_snapshot(event_id="limited", trades_applied=2))
    maxed = limited.evaluate(_signal(event_id="signal-limited"))
    if maxed.approved or maxed.reason != REASON_MAX_TRADES:
        raise AssertionError("explicit max-trades state did not deny")
    limited.observe(
        _snapshot(event_id="limited-missing", sequence=2, trades_applied=None)
    )
    missing = limited.evaluate(_signal(event_id="signal-missing", sequence=2))
    limited.observe(
        _snapshot(event_id="limited-regressed", sequence=3, trades_applied=1)
    )
    regressed = limited.evaluate(_signal(event_id="signal-regressed", sequence=3))
    if missing.reason != REASON_UNKNOWN or regressed.reason != REASON_UNKNOWN:
        raise AssertionError("missing count hid a later max-trades regression")
    return "unknown, max-trades, and count-regression states denied; safe state approved"


def _account_state_consistency(_root: Path) -> str:
    risk = AccountAwareRiskEngine(_rules(), FrozenClock(_TS))
    risk.observe(_snapshot())
    risk.observe(_system(SYSTEM_RECONCILIATION_MISMATCH, 1))
    mismatch = risk.evaluate(_signal())
    risk.observe(_snapshot(event_id="account-2", sequence=2))
    latched = risk.evaluate(_signal(event_id="signal-2", sequence=2))
    if mismatch.reason != REASON_RECONCILE or latched.reason != REASON_RECONCILE:
        raise AssertionError("reconciliation mismatch did not latch fail-closed")
    return "reconciliation mismatch denied and remained latched after a snapshot"


def _circuit_breakers(_root: Path) -> str:
    risk = AccountAwareRiskEngine(_rules(), FrozenClock(_TS))
    risk.observe(_snapshot())
    risk.observe(_system(SYSTEM_CIRCUIT_BREAKER, 1))
    decision = risk.evaluate(_signal())
    if decision.approved or decision.reason != REASON_CIRCUIT:
        raise AssertionError("circuit breaker did not independently veto")
    return "circuit breaker latched and denied authorization"


def _reconnect_resync(_root: Path) -> str:
    risk = AccountAwareRiskEngine(_rules(), FrozenClock(_TS))
    risk.observe(_snapshot())
    if not risk.evaluate(_signal()).approved:
        raise AssertionError("safe baseline was not approved")
    risk.observe(_system(SYSTEM_CONNECTOR_DISCONNECTED, 1))
    disconnected = risk.evaluate(_signal(event_id="signal-2", sequence=2))
    risk.observe(_system(SYSTEM_CONNECTOR_RECONNECTED, 2))
    stale = risk.evaluate(_signal(event_id="signal-3", sequence=3))
    risk.observe(_snapshot(event_id="account-2", sequence=2))
    resynced = risk.evaluate(_signal(event_id="signal-4", sequence=4))
    if disconnected.reason != REASON_DISCONNECTED:
        raise AssertionError("disconnect did not deny")
    if stale.reason != REASON_STALE:
        raise AssertionError("reconnect incorrectly cleared stale state")
    if not resynced.approved:
        raise AssertionError("fresh post-reconnect snapshot did not resync")
    return "disconnect denied; reconnect stayed stale until a fresh snapshot"


def _stale_data_behavior(_root: Path) -> str:
    clock = FrozenClock(_TS + timedelta(seconds=30))
    connector = ReplayMarketConnector(
        [
            {
                "type": "tick",
                "event_id": "old-tick",
                "timestamp": _TS,
                "sequence": 1,
                "symbol": "MNQ",
                "price": 20_000,
                "volume": 1,
            }
        ],
        source="feed",
        clock=clock,
        stale_after=timedelta(seconds=5),
    )
    connector.connect()
    event = connector.next_event()
    connector.disconnect()
    if not isinstance(event, SystemEvent):
        raise AssertionError("stale market payload was emitted as market data")
    risk = AccountAwareRiskEngine(_rules(), clock)
    risk.observe(_snapshot(timestamp=clock.now()))
    risk.observe(event)
    decision = risk.evaluate(_signal(timestamp=clock.now()))
    if decision.reason != REASON_STALE:
        raise AssertionError("stale system event did not deny risk")
    return "stale payload became a system event and independently denied risk"


def _paper_trading_stability(_root: Path) -> str:
    clock, signal, decision, intent = _approved_bundle()
    paper = PaperExecutionAdapter(clock, default_paper_assumptions())
    first = paper.submit(signal, decision, intent)
    repeats = tuple(paper.submit(signal, decision, intent) for _ in range(20))
    if any(report is not first for report in repeats):
        raise AssertionError("duplicate paper intent was not idempotent")
    if first.status != EXEC_FILLED or "not_live_equivalent" not in first.reason:
        raise AssertionError("paper fill lacked explicit non-live assumptions")
    return "20 duplicate submits returned one explicitly non-live paper report"


async def _subscriber_failure() -> bool:
    bus = AsyncIOEventBus(maxsize=2)

    def fail(_event):
        raise RuntimeError("acceptance subscriber failure")

    bus.subscribe(fail)
    await bus.start()
    await bus.publish(MarketTick("tick", "feed", _TS, 1, "MNQ", 1, 1))
    try:
        await bus.wait_idle()
    except BusError:
        try:
            await bus.shutdown()
        except BusError:
            pass
        return True
    return False


def _latency_error_behavior(_root: Path) -> str:
    clock, signal, decision, intent = _approved_bundle()
    assumptions = PaperAssumptions(
        latency=timedelta(milliseconds=250),
        slippage="unmodeled: OrderIntent has no price",
        commissions="unmodeled: OrderIntent has no quantity",
        partial_fills="not_simulated: each intent is one report",
        rejections="all_intents",
        fill_policy=PAPER_FILL_REJECT,
    )
    report = PaperExecutionAdapter(clock, assumptions).submit(signal, decision, intent)
    if report.status != EXEC_REJECTED:
        raise AssertionError("configured paper rejection did not reject")
    if report.timestamp != _TS + timedelta(milliseconds=250):
        raise AssertionError("configured paper latency was not applied exactly")
    if not asyncio.run(_subscriber_failure()):
        raise AssertionError("subscriber failure did not propagate through the bus")
    return "modeled latency/rejection were exact; subscriber error halted the bus"


class _AcceptanceStrategy:
    def on_event(self, event):
        if not isinstance(event, MarketTick):
            return None
        return Signal(
            event_id=f"signal-{event.event_id}",
            source="acceptance-strategy",
            timestamp=event.timestamp,
            sequence=event.sequence,
            symbol=event.symbol,
            action=SIGNAL_LONG,
            origin=event.origin,
        )


def _complete_pipeline(root: Path) -> str:
    clock = FrozenClock(_TS)
    journal = root / "paper-pipeline.jsonl"
    payloads = (
        {
            "type": "snapshot",
            "event_id": "account-1",
            "timestamp": _TS,
            "sequence": 1,
            "equity": 100_000,
            "balance": 100_000,
            "peak_equity": 100_000,
            "last_sync": _TS,
        },
        {
            "type": "tick",
            "event_id": "tick-1",
            "timestamp": _TS,
            "sequence": 2,
            "symbol": "MNQ",
            "price": 20_000,
            "volume": 1,
        },
    )
    session = PaperRealtimeSession(
        connector=ReplayMarketConnector(payloads, source="acceptance-feed", clock=clock),
        bus=AsyncIOEventBus(maxsize=16),
        strategy=_AcceptanceStrategy(),
        risk=AccountAwareRiskEngine(_rules(), clock),
        execution=PaperExecutionAdapter(clock, default_paper_assumptions()),
        recorder=FileEventRecorder(journal),
        clock=clock,
    )
    result = asyncio.run(session.run())
    recorded = reconstruct_events(journal)
    expected_types = (
        AccountSnapshot,
        MarketTick,
        Signal,
        RiskDecision,
        OrderIntent,
        ExecutionReport,
    )
    if tuple(type(event) for event in recorded) != expected_types:
        raise AssertionError("complete pipeline journal missed or reordered a boundary")
    if result.metrics.orders_submitted != 1 or result.metrics.execution_reports != 1:
        raise AssertionError("complete pipeline did not produce exactly one paper report")
    return "connector-to-paper session recorded all six canonical boundaries in order"


_CHECKS: tuple[tuple[str, Callable[[Path], str]], ...] = (
    ("event_correctness", _event_correctness),
    ("duplicate_safety", _duplicate_safety),
    ("ordering_behavior", _ordering_behavior),
    ("replay_reproducibility", _replay_reproducibility),
    ("risk_enforcement", _risk_enforcement),
    ("account_state_consistency", _account_state_consistency),
    ("circuit_breakers", _circuit_breakers),
    ("reconnect_resync", _reconnect_resync),
    ("stale_data_behavior", _stale_data_behavior),
    ("paper_trading_stability", _paper_trading_stability),
    ("latency_error_behavior", _latency_error_behavior),
    ("complete_pipeline", _complete_pipeline),
)


def run_rt8_acceptance(*, artifact_root: str | Path | None = None) -> RT8AcceptanceResult:
    """Run every deterministic RT-8 check and return explicit PASS or FAIL."""

    parent = None if artifact_root is None else Path(artifact_root)
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True)
    checks: list[RT8CheckResult] = []
    with tempfile.TemporaryDirectory(prefix="fars-rt8-", dir=parent) as directory:
        root = Path(directory)
        for name, check in _CHECKS:
            try:
                detail = check(root)
            except BaseException as exc:
                detail = f"{type(exc).__name__}: {exc}"
                checks.append(RT8CheckResult(name, False, detail))
            else:
                checks.append(RT8CheckResult(name, True, detail))
    status: AcceptanceStatus = (
        RT8_PASS if all(check.passed for check in checks) else RT8_FAIL
    )
    return RT8AcceptanceResult(
        status=status,
        checks=tuple(checks),
        live_execution_enabled=LIVE_EXECUTION_ENABLED,
        limitations=(
            "RT-8 uses replay and deterministic paper execution, not a live broker",
            "paper fills do not establish live fill, slippage, commission, or latency behavior",
            "acceptance validates operational contracts, not strategy profitability",
            "RT-9 still requires a confirmed broker contract and explicit live configuration",
        ),
    )


def write_rt8_acceptance_report(
    result: RT8AcceptanceResult,
    path: str | Path,
) -> None:
    """Write one structured, deterministic JSON acceptance artifact."""

    if not isinstance(result, RT8AcceptanceResult):
        raise TypeError("result must be RT8AcceptanceResult")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "REQUIRED_CHECKS",
    "RT8AcceptanceResult",
    "RT8CheckResult",
    "RT8_FAIL",
    "RT8_PASS",
    "RT8_SCHEMA_VERSION",
    "run_rt8_acceptance",
    "write_rt8_acceptance_report",
]

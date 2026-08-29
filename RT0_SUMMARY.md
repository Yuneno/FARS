# RT-0 summary (FARS-Hermes)

Phase: FARS Realtime contracts only.
Branch: FARS-Hermes
Spec: FARS_REALTIME_SPEC.md (RT-0). Does not replace FARS Core or FARS 1.2.

## Why

Realtime needs its own event world. Core `Trade.r_result` is a finished analytical trade. Ticks, quotes, bars, and exchange prints must not be forced into that type.

## What was added

src/realtime/
- events.py — frozen canonical events
- ordering.py — duplicate / late / gap / conflict classification
- clock.py — FrozenClock / SystemClock (replay must not use wall clock)
- interfaces.py — connector, bus, recorder, strategy, risk, sealed execution adapter
- README.md — short module map

tests/realtime/ — contract, event, and pipeline tests

pyproject.toml — package `src.realtime`

## Event set

MarketTick, Quote, Bar, MarketTrade, AccountSnapshot, Signal, RiskDecision, OrderIntent, ExecutionReport, SystemEvent.

Shared envelope: event_id, source, timestamp, sequence, origin (live|replay).

Timestamp policy: `timestamp` is source event time, not local receipt time. AccountSnapshot.broker_timestamp and last_sync are different clocks and are never treated as the same instant.

## Invariants

- Events are frozen. Invalid/naive timestamps, empty ids, bool-as-int, non-finite prices are rejected.
- MarketTrade is not src.types.Trade.
- Live and replay share the same types; origin is provenance.
- SequenceTracker does not reorder. requires_halt is true for late, gap, and conflict. Identical retries are duplicates and do not halt.
- Strategy returns Signal. Risk returns an explicit RiskDecision. Absence of approval is deny.
- ExecutionAdapter.submit(signal, decision, intent) is the veto. Subclasses cannot override submit. Hook is _execute_authorized.
- Authorization binds Signal identity (source, event_id), origin, symbol, and action. approved must be the bool True.

## Out of scope

No Tradovate/TradeSea connector, no asyncio bus, no recorder storage, no replay engine, no paper/live trading, no Risk Engine v2, no Core rewrite. LIVE_EXECUTION_ENABLED = False.

## Tests

tests/realtime/ (93 tests at last run). Existing FARS deterministic suite still green.

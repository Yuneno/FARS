# RT-6 summary (FARS-Hermes)

Phase: Risk Engine v2
Spec: FARS_REALTIME_SPEC.md §2.8-2.9, §5.7, §7, §30

## What landed

src/realtime/risk.py — AccountAwareRiskEngine
- Implements RT-0 RiskEngine: evaluate(Signal) -> RiskDecision
- Account state arrives via observe(AccountSnapshot | SystemEvent)
- Strategy-agnostic: ticks/signals are not account state; Core Trade is rejected
- Fail-closed: no snapshot, missing equity, origin mismatch, trailing without peak, clock going backwards → UNKNOWN_CRITICAL_STATE
- Halt / circuit breaker / reconciliation mismatch latch deny
- Stale denies until a snapshot with equity while connected; reconnect also marks stale (snapshot in the gap is not a resync)
- Intra-day timestamp rewind latches UNKNOWN
- Sequence gap does not deny (account state still known)
- Static/trailing drawdown and daily-loss (initial/eod) use Core dollar thresholds, not ratios
- Zero equity is DRAWDOWN, not UNKNOWN
- Start-of-day equity is the first snapshot of a UTC day (previous close on day change); day is UTC so mixed offsets cannot reset daily loss
- LIVE_EXECUTION_ENABLED stays False. No paper/live orders.

## Tests

tests/realtime/test_risk_engine.py
163 realtime tests passed.
755 deterministic tests passed (`-m "not statistical"`).

## Out of scope (later)

- max_trades was deferred here, then resolved in RT-8 with an explicit,
  monotonic `AccountSnapshot.trades_applied` field. It is never inferred from
  order or fill events; missing count fails closed when the rule is configured.
- position / working-order limits
- paper execution (RT-7)

# RT-6 review (Hermes self-review)

Spec: FARS_REALTIME_SPEC.md §30 + architectural rules §2, §5.7, §7.
Diff reviewed: src/realtime/risk.py, tests/realtime/test_risk_engine.py, src/realtime/__init__.py.
Tests: pytest tests/realtime (163 passed); pytest tests/ -m "not statistical" (755 passed, 10 deselected).

## Verdict

REVIEW PASSED

## Checks

- Risk is independent of Strategy and Execution. evaluate() only consumes Signal.
- Absence of approval is deny. Missing snapshot/equity never allows.
- approved is constructed as bool only (existing RiskDecision contract).
- AccountSnapshot is not Core AccountState; MarketTrade/ticks are not Core Trade.
- Drawdown/daily-loss boundaries match Core dollar-space checks (equity <= threshold).
- Live execution remains locked.
- No spec rewrite, no new deps, no broker.
- Follow-up review: reconnect without resync was an allow hole (fixed: disconnect marks stale; reconnect also marks stale). Snapshot during disconnect is not a resync. Incomplete snapshot no longer clears stale. Intra-day timestamp rewind latches UNKNOWN. Zero equity is DRAWDOWN, not UNKNOWN. Deny cannot reach ExecutionAdapter.submit.

## WARNING

1. max_trades is not enforced. AccountSnapshot has no trades_applied. Inventing a count from ExecutionReport would leak into RT-7. Fail-closed would deny every signal when max_trades is set, so RT-6 leaves that rule unused until paper/live can observe fills.
2. FLAT is denied at a drawdown/daily-loss limit the same as LONG/SHORT. The spec does not define a flatten exception. Reducing risk through a denied FLAT is a later product decision, not an RT-6 hole in the veto.

## SUGGESTION

Start-of-day equity is inferred from snapshot UTC dates because the snapshot contract has no SOD field. First snapshot of a session treats equity as SOD (daily loss 0 that instant). Documented in RT6_SUMMARY.md.

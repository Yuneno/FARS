# RT-7 summary (FARS-Hermes)

Phase: Paper Trading
Spec: FARS_REALTIME_SPEC.md §20 and §31

## What landed

src/realtime/paper.py
- PaperExecutionAdapter is an ExecutionAdapter (same submit veto as future live)
- PaperAssumptions are required and non-empty (no silent zero-cost fill)
- Default model labels slippage/commissions/partials as unmodeled (OrderIntent has no price/qty)
- Fill timestamp = clock.now() + latency; clock itself is not advanced
- fill_policy: full / partial / reject → ExecutionReport status
- Duplicate intent identity is idempotent
- Adapter refuses to construct if LIVE_EXECUTION_ENABLED is not False
- Every report.reason starts with PAPER not_live_equivalent

## Tests

tests/realtime/test_paper.py
173 realtime tests passed.
765 deterministic tests passed (`-m "not statistical"`).

## Out of scope

- LiveExecutionAdapter (RT-9, locked)
- Price/qty on OrderIntent
- Broker connectivity

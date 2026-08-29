# RT-5 summary (FARS-Hermes)

Phase: Realtime FARS Adapter
Spec: FARS_REALTIME_SPEC.md §29 and §34

## What landed

src/realtime/adapter.py
- Canonical realtime events cannot become Core Trade
- Filled ExecutionReport is still incomplete (no r_result) and is rejected
- require_core_trades() accepts only Core Trade with finite r_result
- run_core_metrics() calls compute_metrics only after that gate
- Empty input fails closed (no fake 0-trade metrics)

## Tests

tests/realtime/test_adapter.py
126 realtime tests passed.

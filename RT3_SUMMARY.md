# RT-3 summary (FARS-Hermes)

Phase: Recorder + Storage
Branch: FARS-Hermes
Spec: FARS_REALTIME_SPEC.md §17 and §27

## Storage choice

Spec prefers Parquet + DuckDB. Those packages are not in FARS requirements
and were not installed. RT-3 uses append-only JSONL (stdlib) with schema
`fars-rt3-jsonl-v1`. Same provenance; DuckDB can ingest this later if you
want that backend.

## What landed

src/realtime/recorder.py
- FileEventRecorder.record(event) appends one canonical event
- reconstruct_events(path) rebuilds the session
- non-canonical objects and malformed lines raise RecorderError
- does not write into source CSVs

Persists market events and the decision chain (Signal, RiskDecision,
OrderIntent, ExecutionReport, AccountSnapshot, SystemEvent).

## Tests

tests/realtime/test_recorder.py

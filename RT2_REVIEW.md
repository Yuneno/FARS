# RT-2 review (Hermes) — re-check

REVIEW PASSED

Re-read bus.py, tests, spec §9 and §26. Not Codex.

## Verdict

RT-2 concerns are covered:

- bounded queue (maxsize >= 1)
- backpressure: block or error, never silent drop
- duplicates: counted, not re-delivered
- ordering: gaps delivered, not reordered; diagnostics counted
- shutdown: drain or cancel; failed subscriber surfaces as BusError
- error propagation: dispatcher fail-closed; later publish refused
- normalization boundary: non-canonical objects rejected at publish
- no Kafka/Redis/NATS; no new dependencies

106 realtime tests passed.

## Not a fail

- overflow=block is implemented, less tested than overflow=error
- same SequenceTracker for all types on one source (callers must not mix streams)
- bus instance is not restartable after shutdown (create a new one)
- async publish can return before the subscriber runs; failure shows on next publish/shutdown

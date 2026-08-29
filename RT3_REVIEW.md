# RT-3 review (Hermes)

REVIEW PASSED

Self-review against §17 and §27. Not Codex.

## Checks

- Canonical events only; provider payloads rejected
- Roundtrip reconstructs market + decision-chain events
- Timezone-aware timestamps survive JSON
- AccountSnapshot positions freeze back to tuples
- Malformed JSON fails closed
- Unrelated files are not modified
- No new Python dependencies

## Storage note

Parquet/DuckDB not implemented (not in requirements). JSONL meets
reconstruct + provenance. Swap later if you approve those deps.

## Tests

tests/realtime green after recorder tests.

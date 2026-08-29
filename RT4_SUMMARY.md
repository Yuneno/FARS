# RT-4 summary (FARS-Hermes)

Phase: Replay Engine
Spec: FARS_REALTIME_SPEC.md §10 and §28

## What landed

src/realtime/replay.py — ReplayEngine
- Reads RT-3 JSONL via reconstruct_events
- Publishes onto the same AsyncIOEventBus as live
- FrozenClock only (wall clock rejected)
- replay_session() drains the bus before the first clock.set
- wait_idle after each event so subscribers see recorded time
- Foreign publishers are rejected while replay owns the bus
- Recorded origin is preserved (live stays live)
- Strategy/Risk unchanged

## Tests

tests/realtime/test_replay.py
131 realtime tests passed.

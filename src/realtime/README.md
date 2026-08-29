# FARS Realtime — RT-0

Contracts only. Not a live engine. Does not rewrite FARS Core.

## What landed

- Canonical frozen events in `events.py` (ticks, quotes, bars, market prints, snapshots, signals, risk, intents, fills, system).
- `MarketTrade` is not Core `Trade`.
- `timestamp` = source event time (live provider time or replay record). Not receipt time. Snapshot also has distinct `broker_timestamp` / `last_sync`.
- Duplicate/order classes in `ordering.py`: ordered, duplicate, late, gap, conflict. `requires_halt` on late/gap/conflict.
- Clock in `clock.py` so replay/tests are not wall-clock.
- Interfaces in `interfaces.py`. Execution is an ABC: `submit(signal, decision, intent)` is the veto. Subclasses cannot override `submit`. Hook is `_execute_authorized`.

## Risk boundary

`require_authorized_intent` binds:

- Signal identity `(source, event_id)` via `RiskDecision.signal_source` + `signal_id`
- matching origin across signal, decision, intent
- matching symbol/action
- `approved is True` (fail-closed; missing decision is deny)

## Tests

`tests/realtime/` — contracts, events, RT-0 acceptance including a deny/approve pipeline.

## Out of scope (later phases)

Asyncio bus, recorder/storage, replay engine, paper/live, Risk Engine v2, live broker APIs.
Live execution stays locked (`LIVE_EXECUTION_ENABLED = False`).

RT-1 replay connector: `connector.py` (see `RT1_SUMMARY.md`).
RT-2 bus: `bus.py` (see `RT2_SUMMARY.md`).
RT-3 recorder: `recorder.py` (see `RT3_SUMMARY.md`).
RT-4 replay: `replay.py` (see `RT4_SUMMARY.md`).
RT-5 adapter: `adapter.py` (see `RT5_SUMMARY.md`). Core metrics run only on completed `Trade` values.

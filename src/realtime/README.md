# FARS Realtime — RT-0 through RT-8

Validated replay/risk/paper pipeline. Not a live engine. Does not rewrite FARS
Core.

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

`tests/realtime/` — contracts, component tests, complete paper sessions, and
the explicit RT-8 acceptance harness.

## ProjectX / TopstepX (read-only RT-1)

`src/realtime/connectors/projectx.py` authenticates, searches contracts, pulls
historical bars, and reads positions/fills. It does **not** replace
`ReplayMarketConnector`. Fills are not Core `Trade`. Market Hub listen
(`src/realtime/listen.py`, `fars-projectx-listen`) journals MNQ quotes/prints
only. User Hub is not implemented. This is not RT-8 PASS for ProjectX and not
RT-9. See `PROJECTX_CONNECTOR.md`.

## Out of scope (later phases)

User Hub, strategy, RT-9, and live orders.
Live execution stays locked (`LIVE_EXECUTION_ENABLED = False`).

RT-1 replay connector: `connector.py` (see `RT1_SUMMARY.md`).
RT-2 bus: `bus.py` (see `RT2_SUMMARY.md`).
RT-3 recorder: `recorder.py` (see `RT3_SUMMARY.md`).
RT-4 replay: `replay.py` (see `RT4_SUMMARY.md`).
RT-5 adapter: `adapter.py` (see `RT5_SUMMARY.md`). Core metrics run only on completed `Trade` values.
RT-6 risk: `risk.py` — `AccountAwareRiskEngine` (see `RT6_SUMMARY.md`). Account-aware veto; unknown state denies.
RT-7 paper: `paper.py` — `PaperExecutionAdapter` (see `RT7_SUMMARY.md`). Simulated fills, not live-equivalent.
RT-8 validation: `session.py` + `acceptance.py` (see `RT8_SUMMARY.md` and
`RT8_ACCEPTANCE.json`). The deterministic acceptance result is `PASS`; live
execution remains disabled and no broker behavior was inferred from paper.

# RT-0..RT-4 self-review (finance-critical)

Verdict: PASS with residual notes. Not live-ready (RT-5..RT-9 not assigned).
118 `tests/realtime` passed (`python -m pytest -p no:debugging tests/realtime -q`).
No commit.

## What was wrong and is now closed

1. Concurrent same identity: reserved under lock; waiters share the owner Future.
   Owner cancel before enqueue: waiter takes over. Test: `test_pending_duplicate_survives_owner_cancel`.
2. Cancel after enqueue, before commit: event stays queued, identity is committed,
   waiters see success, no second put. Test: `test_cancel_after_enqueue_does_not_redeliver`.
3. Subscriber failure: dispatcher stores the error; `wait_idle()` and `shutdown()` raise `BusError`.
   Replay cannot return success after a failed handler. Test: `test_replay_fails_if_subscriber_fails`.
4. `wait_idle()` no longer hangs if the dispatcher dies with items still in the queue
   (join raced against the dispatcher task). Test: `test_wait_idle_raises_if_dispatcher_dies_with_queued_events`.
5. Journal durability: `FileEventRecorder.record` now `flush()` + `os.fsync`.

## Spec checks

- Canonical-only publish. Duplicates counted, not re-delivered.
- LATE/GAP delivered as-is; CONFLICT halts. No silent drop on overflow.
- Replay requires FrozenClock; `wait_idle()` after each publish.
- Execution still locked (`LIVE_EXECUTION_ENABLED = False`).
- Identity is `(source, event_id)`. System events use `{source}/system`.

## Residual (not CRITICAL for RT-4)

- Stale connector path emits `SYSTEM_STALE_MARKET_DATA` instead of the market event (event is not also forwarded). Fail-closed for trading on stale data; original print is not journaled unless something else recorded it.
- `received` can increment on publishes that later raise (not running / conflict). Counters are diagnostics, not the veto.
- JSONL is the journal; Parquet/DuckDB still not in deps.
- Live broker and paper/live execution are not in this phase. Do not enable live until RT-8.

## Efficiency

- One lock per publish reservation; `put`/`put_nowait` outside the lock when blocking.
- `put_nowait` first avoids an extra await when the queue has room.
- fsync is extra syscall per record: correct for crash durability, not a hot-path issue at replay volumes.

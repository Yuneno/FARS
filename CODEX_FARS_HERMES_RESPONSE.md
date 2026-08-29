# Codex review of FARS-Hermes — Hermes verification

Codex was right on two CRITICALS. Wrong to treat every gap/late as halt.

## CRITICAL 1 — silent drop after backpressure
VALID. Tracker committed before queue.put. Fixed: classify(commit=False) until enqueue succeeds. Retry of e3 now delivers.

## CRITICAL 2 — conflict vs halt
PARTLY VALID. Same identity + different payload is now CONFLICT, not duplicate. Bus halts on CONFLICT.
REJECTED: halt on every gap/late. That would kill replay of non-contiguous sequences. Gaps/late stay diagnostics (`requires_halt` only for CONFLICT).

## CRITICAL 3 — EventBus protocol
VALID. Added `AsyncEventBus`. AsyncIOEventBus is async; the sync EventBus protocol remains for RT-0 MemoryBus.

## WARNINGS
- Raw+normalized journal: still canonical-only on purpose (no provider leak). Stale events now keep original event_id in SystemEvent.detail.
- OrderIntent identity tuple: still open.
- AccountSnapshot float: still RT-0; 11A uses Decimal separately.
- Spec RT-1–RT-3 status lines updated (you authorized those phases).

Realtime tests: 109 passed after the fixes.

# RT-2 summary (FARS-Hermes)

Phase: Event Bus + Normalization
Branch: FARS-Hermes
Spec: FARS_REALTIME_SPEC.md §9 and §26

## What landed

src/realtime/bus.py — AsyncIOEventBus
- bounded asyncio.Queue (maxsize required, default 256)
- overflow=block (await) or overflow=error (raise). No silent drops.
- publish accepts canonical events only (normalization boundary)
- duplicates detected via SequenceTracker; not re-delivered
- late/gap/conflict delivered as-is (no reorder); counted
- start() required before publish; shutdown drains or cancels
- subscriber exceptions fail-closed (BusError)

RT-0 sync EventBus protocol is unchanged.

## Tests

tests/realtime/test_bus.py (asyncio.run, no extra plugin)

## Not in RT-2

Kafka, Redis, NATS, unbounded queues, live broker adapters.

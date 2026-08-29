# RT-1 review (Hermes)

REVIEW PASSED

Self-review against FARS_REALTIME_SPEC.md §8 and §25. Not Codex.

## Checks

- Lifecycle: connect / disconnect / next_event-before-connect
- Normalize only canonical-shaped mappings; nested/provider objects rejected
- Source/origin stamped by connector, not trusted from payload
- Reconnect restarts stream and emits connector_reconnected
- Stale prints become SystemEvent, not MarketTick
- Invalid prices/types raise ConnectorError (no partial event)
- Replay/historical adapter only; no live broker invented
- Core untouched

## Finding fixed in this review

System lifecycle events shared the market `source` and a separate sequence counter. That would look like a sequence conflict on the same stream. Lifecycle events now use `{source}/system`.

## Tests

tests/realtime: 101 passed after the fix.

## Remaining (not RT-1 blockers)

Live Tradovate/TradeSea still needs confirmed API access. Async bus is RT-2.

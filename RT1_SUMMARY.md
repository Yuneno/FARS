# RT-1 summary (FARS-Hermes)

Phase: Market Data Connector
Branch: FARS-Hermes
Spec: FARS_REALTIME_SPEC.md §25 and §8

## Provider choice

RT-1 says the first provider must be confirmed from actual API/data access.
Tradovate/TradeSea/IB are not implemented. The first adapter is the
historical/replay source listed in §8: ReplayMarketConnector.

A live broker adapter waits until Ricardo confirms access and a sample payload.

## What landed

src/realtime/connector.py
- normalize_market_payload: canonical-shaped mappings only; nested/provider objects rejected
- ReplayMarketConnector: connect / disconnect / next_event
- ConnectorError on bad lifecycle or bad payload
- source and origin are set by the connector, not trusted from the payload
- stale_after + Clock: stale prints become SystemEvent stale_market_data, not ticks
- reconnect restarts the payload stream and emits connector_reconnected

MarketDataConnector protocol now includes connect and disconnect.

## Tests

tests/realtime/test_connector.py
Full realtime suite and FARS deterministic suite still green at last run.

## Not in RT-1

Live sockets, auth, Tradovate schema, asyncio bus (RT-2), storage (RT-3).

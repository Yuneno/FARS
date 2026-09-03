# FARS ProjectX / TopstepX read-only connector

This is an RT-1 historical/account reader. It is not RT-8 acceptance, not RT-9,
and not live execution.

`LIVE_EXECUTION_ENABLED` stays `False`. The client cannot submit, modify,
cancel, or close orders.

## What it does

1. Authenticate with the official API-key endpoint.
2. Keep the session token in memory only; rotate it from `validate`.
3. Load username/API key from process env or a local `.env` without shell exec.
4. Select the intended account by exact name.
5. Search contracts (MNQ/NQ) and retrieve historical OHLCV bars.
6. Read open positions and raw ProjectX fills.

Provider fills are `ProjectXFill`. They are not Core `Trade` and not
`MarketTrade`. A null P&L is a half turn. A reviewed round-trip aggregator is
required before those records can feed Core analytics.

Canonical mapping:

- `ProjectXBar` → RT `Bar` via `canonical_bars`
- `ProjectXAccount` + positions → RT `AccountSnapshot` via
  `canonical_account_snapshot` (`equity` and `trades_applied` stay unknown)
- `ProjectXHistoricalBarConnector` implements `MarketDataConnector`

## Setup

```bash
cp .env.example .env
```

Put the TopstepX username and ProjectX API key in `.env`. If several active
accounts exist, copy the exact account name into `FARS_PROJECTX_ACCOUNT_NAME`.

Do not share `.env`, attach it to a chat, or commit it.

## Commands

```bash
fars-projectx doctor
fars-projectx contracts MNQ
fars-projectx bars CONTRACT_ID \
  --start 2026-09-02T13:00:00Z \
  --end 2026-09-02T14:00:00Z \
  --unit 2 --unit-number 1 --limit 60
```

Bar units: 1 second, 2 minute, 3 hour, 4 day, 5 week, 6 month. Max 20,000 bars
per request. `--live` selects the provider live market-data catalog, not order
routing.

## Out of scope here

SignalR hubs, strategy, RT-9, live orders, and claiming ProjectX passed RT-8.

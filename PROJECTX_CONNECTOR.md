# FARS ProjectX / TopstepX read-only connector

This is an RT-1 historical/account reader plus a read-only Market Hub
capture. It is not RT-8 acceptance, not RT-9, and not live execution.

`LIVE_EXECUTION_ENABLED` stays `False`. The client cannot submit, modify,
cancel, or close orders. User Hub is not implemented.

## What it does

1. Authenticate with the official API-key endpoint.
2. Keep the session token in memory only; rotate it from `validate`.
3. Load username/API key from process env or a local `.env` without shell exec.
4. Select the intended account by exact name.
5. Search contracts (MNQ/NQ) and retrieve historical OHLCV bars.
6. Read open positions and raw ProjectX fills.
7. Capture Market Hub quotes/prints into the RT JSONL journal (default MNQ;
   unique active token only).

Provider fills are `ProjectXFill`. They are not Core `Trade` and not
`MarketTrade`. A null P&L is a half turn. A reviewed round-trip aggregator is
required before those records can feed Core analytics.

Canonical mapping:

- `ProjectXBar` → RT `Bar` via `canonical_bars`
- `ProjectXAccount` + positions → RT `AccountSnapshot` via
  `canonical_account_snapshot` (`equity` and `trades_applied` stay unknown)
- `ProjectXHistoricalBarConnector` implements `MarketDataConnector`
- Market Hub quotes/prints → RT `Quote` / `MarketTrade` / `MarketTick`
  (`origin=live`). Incomplete payloads are skipped, never invented.

Unknown snapshot equity is fail-closed for RT-6. Do not copy `balance`,
mark positions from bars, or sum fill PnL.

## Setup

```bash
cp .env.example .env
```

Put the TopstepX username and ProjectX API key in `.env`. If several active
accounts exist, copy the exact account name into `FARS_PROJECTX_ACCOUNT_NAME`.

Do not share `.env`, attach it to a chat, or commit it.

Market Hub listen uses the `websocket` package if it is already on the
machine. It is not a FARS dependency and is not installed by this project.

## Commands

```bash
fars-projectx doctor
fars-projectx contracts MNQ
fars-projectx bars CONTRACT_ID \
  --start 2026-09-02T13:00:00Z \
  --end 2026-09-02T14:00:00Z \
  --unit 2 --unit-number 1 --limit 60
fars-projectx-listen \
  --hours 2 \
  --journal tmp/rt-listen/journal.jsonl \
  --meta tmp/rt-listen/meta.json \
  --report tmp/rt-listen/report.json
```

Bar units: 1 second, 2 minute, 3 hour, 4 day, 5 week, 6 month. Max 20,000 bars
per request. `--live` selects the provider live market-data catalog, not order
routing.

`fars-projectx-listen` stays `READ_ONLY`, does not open User Hub, and does not
claim ProjectX passed RT-8. Default `--symbol` is MNQ. Other roots are accepted
only when Contract/search returns exactly one active token match (dotted
components, not substrings: NQ cannot bind MNQ). Pass `--hours` or `--seconds`,
not both.

## Out of scope here

User Hub, strategy, RT-9, live orders, and claiming ProjectX passed RT-8.

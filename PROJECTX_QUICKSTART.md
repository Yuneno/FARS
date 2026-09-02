# FARS + ProjectX quick start

## What this milestone does

RT-1 creates the first safe bridge between FARS and TopstepX/ProjectX:

1. Authenticate with the official API-key endpoint.
2. Keep the 24-hour session token in memory only.
3. Find active accounts through the API instead of hard-coding the dashboard
   `SUB ID`.
4. Select the intended account exactly by name when more than one exists.
5. Search futures contracts and retrieve historical OHLCV bars.
6. Read open positions and raw ProjectX trade/fill records.

The bridge is deliberately read-only. It cannot submit, modify, cancel, or
close an order.

## One-time setup

Install FARS from the repository root:

```bash
python -m pip install -e ".[dev]"
```

### macOS or Linux

```bash
cp .env.example .env
```

### Windows PowerShell

```powershell
Copy-Item .env.example .env
```

Open `.env` and enter the TopstepX username and ProjectX API key. If the profile
contains multiple active accounts, copy the exact intended account name from
the ProjectX dashboard into `FARS_PROJECTX_ACCOUNT_NAME`.

Do not share `.env`, attach it to a chat, or commit it. FARS ignores the file in
Git and never prints either the API key or session token.

## Connection test

```bash
fars-projectx doctor
```

Expected shape:

```text
connection: ok
mode: READ_ONLY
execution_allowed: False
active_account_count: 1
selected_account: {...}
open_positions: 0
```

This performs authenticated read-only requests. It does not place a test order.

## Market-data test

Find the active Micro E-mini Nasdaq contract:

```bash
fars-projectx contracts MNQ
```

Copy the active `CON...` identifier from that output, then retrieve bars:

```bash
fars-projectx bars CONTRACT_ID \
  --start 2026-09-02T13:00:00Z \
  --end 2026-09-02T14:00:00Z \
  --unit 2 --unit-number 1 --limit 60
```

ProjectX bar units are: 1 second, 2 minute, 3 hour, 4 day, 5 week, and 6 month.
The connector enforces the provider maximum of 20,000 bars per request and uses
simulated market data unless `--live` is supplied explicitly.

## What is still required before autonomous trading

FARS 1.2 estimates risk and account-passing probabilities; it is not currently
a BUY/SELL strategy. The next approved milestones are:

1. RT-2: consume and normalize the ProjectX SignalR market and user hubs.
2. Strategy adapter: implement the mentor strategy as deterministic signals.
3. Risk gate: convert an approved signal into a bounded `OrderIntent`, failing
   closed on stale data, account uncertainty, duplicates, or rule violations.
4. Paper execution and reconciliation.
5. Independent validation before any live/Challenge execution method exists.

Raw ProjectX trades are fills. A record with null P&L is a half turn, so the
connector does not mislabel it as a completed FARS round trip. A reviewed fill
aggregator is required before those records feed Phase 11A account statistics.

# FARS LAB — Codex + OmniRoute Decisions

## D1: Audit trail uses error_indices set (not idx < len(errors))
- Previous: `idx < len(result.errors) and any(e.record_index == idx ...)` 
- Problem: only matched errors with record_index < len(errors)
- Fix: `any(e.record_index == idx for e in result.errors)` via set lookup

## D2: Trade-to-row mapping uses sequential counter
- Previous: broken loop that always assigned last trade to all rows
- Fix: trade_idx counter incremented only for non-erroled, non-dedup rows

## D3: CLI import subcommand handles errors independently
- Previous: main() tried to load all CSVs through load_trade_csv first
- Fix: import command parsed before general CSV loading path

## D4: MAX_WORKERS=6 enforced at run_parallel level
- Per PACK requirement: maximum six processes with spawn context

## D5: Temporal purge by trade entry/exit intervals
- Per P5 PACK: remove training bars within trades overlapping test window
- Conservative boundary: entire trade interval purged if any overlap

## D6: Trailing drawdown types
- static: HWM only increases (standard trailing)
- eod: HWM locked at session close
- intraday: trailing HWM resets at session start (tighter)

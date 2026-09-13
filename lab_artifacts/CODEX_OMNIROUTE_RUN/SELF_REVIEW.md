# FARS LAB — Self Review (Codex + OmniRoute)

## Status: PARTIAL_BLOCKED

## Base and Final
- Base: 4727b9e (P1-B1 closure)
- Final HEAD: 2e45022 + c46132b + d6e3ac7 + 0063acc
- Branch: main (8 commits ahead of origin)
- Working tree: clean (untracked temp scripts excluded)

## Criteria Review

### A0 — Isolation verified
- [x] Workspace restricted to E:\FARS-LAB
- [x] No production/broker/router changes
- [x] No credentials or .env files accessed
- [x] Baseline HEAD preserved

### A1 — P1 Adapter + Import Pipeline
- [x] Boolean rejection: pnl_net=True -> INVALID_PNL_NET_TYPE
- [x] Side validation: Decimal("1.5") -> SIDE_NOT_INTEGER
- [x] Symbol validation: config.symbol checked against VALID_SYMBOLS
- [x] Timestamp comma: rejected with INVALID_TIMESTAMP_FORMAT
- [x] deduplicated at end with default 0
- [x] symbol_used computed on accepted trades, None for mixed batches
- [x] source_dataset only in metadata when normalised_outcome is None
- [x] CLI import subcommand with --symbol, --accept-partial, --format
- [x] Two-dataset same-asset independent pipelines
- [x] Audit per row with error/warning status
- [x] Reimport deduplication with existing_hashes
- [x] Full pipeline: CSV -> convert -> audit -> persist -> ingest -> metrics -> bootstrap

### A2 — P2 Parallel Runner
- [x] MAX_WORKERS=6 enforced
- [x] job_id from semantic identity (no PID/timings)
- [x] derive_job_seed deterministic
- [x] Worker validation (strategy, profile)
- [x] Warmup and range filtering
- [x] Sequential fallback on PermissionError
- [ ] Real spawn test blocked by sandbox (2 skipped)

### A3 — P3 Fill Profiles
- [x] LEGACY and GAP_AWARE profiles
- [x] CostConfig with tick_size/tick_value/commission/slippage
- [x] SlippageReport computed
- [ ] run_with_profile standalone only; full executor integration not demonstrated

### A4 — P4 Detectors
- [x] CRT, FVG, CISD, sweeps implemented
- [x] DiagnosticEvent with SHA-256 event_id
- [x] JITA comparison documented (divergences explicit)
- [ ] Deep JITA semantic comparison pending

### A5 — P5 Hypothesis Registry
- [x] HypothesisRegistry with multiplicity tracking
- [x] WalkForwardPlan with expanding windows
- [x] purge_train_by_trade_intervals (entry/exit interval overlap)
- [x] p-value validation (NaN rejected, range [0,1])

### A6 — P6 Account Simulator
- [x] trailing_type: static/eod/intraday verified
- [x] daily_loss_limit with session reset
- [x] tick_value/tick_size units correct
- [ ] NY DST-aware session handling (date-based only, not clock-based)

### A7 — P7 Cross-Validation
- [x] Full flow demonstrated with synthetic fixtures
- [x] All 288 tests pass
- [x] Baseline P0 unaltered
- [ ] Benchmark BLOCKED: requires real MNQ/YM historical data

## Risks for Reviewer
1. CRT/CISD detectors use simplified lookback vs JITA swing structure
2. No real market data for representative benchmark
3. Spawn tests skipped in sandbox environment
4. NY session reset uses date string, not clock-time DST
5. Juanca strategy comparison deferred (registered, not implemented)

## Commands to Reproduce
```
cd E:\FARS-LAB\FARS
E:\FARS-LAB\.venv-fars\Scripts\python.exe -m pytest tests/test_tsfm_adapter.py tests/test_parallel.py tests/test_fills.py tests/test_detectors.py tests/test_hypothesis.py tests/test_account_sim.py tests/test_importer.py -v
```

## Model Run
- Model: session default (Codex CLI)
- Provider: OmniRoute
- Duration: continuous
- Human interventions: none after initial prompt

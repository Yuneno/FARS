# P7 Benchmark Verification Report
Date: 2026-09-13 | Verifier: Codex CLI | Branch: main (94ee2b6)

## 1. Strategy Executed

- **Class**: BreakoutStrategy (src/backtest/strategy.py)
- **Type**: Donchian channel breakout - PROVISIONAL placeholder
- **NOT SMC-FVG/EMAS/CRT** (Juanca strategies): exist on branch 50d0efc (feature/port-smcfvg-emas), NEVER merged to main
- **lookback**: 20 bars (default)
- **stop_atr_mult**: 1.5 | **target_atr_mult**: 3.0 | **min_atr**: 0.25
- **Source**: _run_single_job in src/parallel.py:169: run_backtest(bars, BreakoutStrategy(), config)

## 2. BacktestConfig Applied

- initial_balance=50000, risk_per_trade=0.01 (from rules_dict)
- **dollar_per_point=2.0 (MNQ default, WRONG for MYM which should be 0.5)**
- **tick_size=0.25 (MNQ default, WRONG for MYM which should be 1.0)**
- commission_per_side=0.62, slippage_points=0.25 (PROVISIONAL)
- Source: parallel.py:167 - only initial_balance and risk_per_trade from rules_dict

## 3. MNQ Canonical Cutoff Violation

- MNQ H4: 23687 bars, range 2010-06-07 to 2026-09-03, pre-2019 = 11400 bars (48.1%)
- MNQ D1: 5040 bars, range 2010-06-07 to 2026-09-03, pre-2019 = 2649 bars (52.6%)
- Canonical spec (docs/refactor/canonical-dataset.md): MNQ cutoff = 2019-05-06
- Benchmark applied NO range filter, ran full 2010-2026 range

## 4. YM/MYM Data Provenance

- Raw source: _meta/raw/YM_2010-2026.dbn.zst (E-mini Dow, full-size contract)
- Current CSV: databento/MYM_*.csv - symbol=MYM, same OHLCV structure
- Old snapshot: _viejo_2026-07/YM_*.csv - symbol=YM, snapshot through 2026-07-08
- Price divergence: first 5 bars identical, diverges at bar 6 onward
- Bar count: MYM_H4=23693, _viejo/YM_H4=25524
- YM data IS valid and usable; MYM launched May 2019, YM (full-size) existed since 2002
- MYM cannot substitute YM for pre-2019 analysis

## 5. Execution Metadata

- Commit: 94ee2b6 (HEAD of main)
- Benchmark script: NOT COMMITTED (ran inline in prior session, only JSON artifacts saved)
- Commands: run_parallel() from src/parallel.py with pre-loaded CSV bars as dicts
- Workers w1: effective=1, wall=0.275s, 4 jobs, 0 errors
- Workers w2: effective=1 (fallback), wall=0.281s
- Workers w6: effective=1 (fallback), wall=0.257s
- Determinism: All 4 jobs DETERMINISTIC across 3 runs
- master_seed=42, warmup_bars=20
- Test suite: 288 passed, 2 skipped (spawn), 0 failed

## 6. Results (benchmark_final.json - INVALID)

| Job      | Symbol | TF | Bars   | Trades | Net PnL    | WinRate | $/pt Used | Correct | Validity          |
|----------|--------|----|--------|--------|------------|---------|-----------|---------|-------------------|
| MNQ_H4   | MNQ    | H4 | 23687  | 1262   | +1701.18   | 34.87%  | 2.0       | 2.0     | Non-canonical     |
| MNQ_D1   | MNQ    | D1 | 5040   | 278    | -4627.42   | 33.45%  | 2.0       | 2.0     | Non-canonical     |
| MYM_H4   | MYM    | H4 | 23693  | 1215   | -10551.16  | 33.50%  | 2.0       | 0.50    | INVALID ($/pt wrong) |
| MYM_D1   | MYM    | D1 | 5045   | 263    | +1133.46   | 33.08%  | 2.0       | 0.50    | INVALID ($/pt wrong) |

## 7. P7 Status Verdict

**P7: PARTIALLY COMPLETE - BLOCKED for valid benchmark.**

Works:
- E2E pipeline runs with real CSV data from databento.zip
- Parallel runner correctly falls back to sequential in sandbox
- Determinism verified across 3 runs
- Cross-market data loading works for MNQ and MYM

Blocks:
1. Strategy is BreakoutStrategy (placeholder) - not SMC-FVG/EMAS from kai (branch 50d0efc never merged)
2. MYM dollar_per_point bug - all MYM PnL values inflated 4x
3. MNQ pre-2019 synthetic data - 48-53% of bars are synthetic
4. No benchmark script committed - results not reproducible

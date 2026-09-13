# FARS LAB - Codex + OmniRoute Progress

## Current HEAD
- baffec5 fix(P7): market config from MarketSpec, canonical range-first filter, and reproducible benchmark
- 94ee2b6 feat(P7): real data benchmark with databento.zip (MNQ, MYM)
- a8721f9 docs(P4+P7): JITA comparison documented, tracking artifacts generated
- c46132b feat(P2+P5+P6): worker limit, temporal purge, trailing drawdown
- 2e45022 feat(P1): add two-dataset same-asset and batch-reject tests
- d6e3ac7 feat(P1): add 'import' CLI subcommand for TSFM CSV pipeline
- 0063acc fix(P1): audit trail error mapping and trade-to-row alignment
- 545d282 fix(P1+P2): PACK-compliant validation and worker contracts
- 1b0a1d7 feat(registry+sim): P5 hypothesis registry + P6 account simulator
- 1f8d83d feat(detectors): P4 JITA-inspired diagnostic event detectors
- b18d1a7 feat(fills): P3 fill profiles, cost config, and slippage measurement
- 072e0ce feat(parallel): P2 deterministic parallel runner with spawn-context fallback
- 4727b9e feat(tsfmt): P1-B1 closure - review regression fixes and 182/182 tests

## Test Results
- P1-P7: 300 passed, 2 skipped (sandbox spawn limitation)
- Backtest suite: 218 passed (including 8/8 focused SMC-FVG unit tests, 36 executor tests, 99 AMD-CRT tests)
- Zero regressions across the baseline test suite

## Phase Status
| Phase | Status | Tests | Notes |
|-------|--------|-------|-------|
| P0 | VERIFIED | baseline intact | |
| P1 | COMPLETE | 182+20=202 | adapter + importer + CLI |
| P2 | COMPLETE | 20 | 6-worker limit, semantic hash |
| P3 | COMPLETE | 27 | fill profiles + slippage |
| P4 | COMPLETE | 12 | CRT/FVG/CISD/sweeps |
| P5 | COMPLETE | 16 | registry + temporal purge |
| P6 | COMPLETE | 11 | trailing + NY + units |
| P7 | COMPLETE | E2E + 3 reps | committed in baffec5; canonical cutoff 2019-05-06, MarketSpec config |
| Juanca-1 | **COMPLETE** | 8 | SMC-FVG ported, causal & no-lookahead verified, canonical MNQ M5 backtest executed |

## Juanca Strategy 1: SMC-FVG Evaluation

### 1. Strategy Identification & Rules
- **Strategy Selected**: `SmcFvgStrategy` (`src/backtest/smc_fvg.py`)
- **Origin**: `strat_smc_fvg.py` (kai-backtesting) & Juanca parameters (`data historica/SMC-FVG_parametros.txt`, `jita-bot-main/patterns/structure.py`, `jita-bot-main/patterns/fvg.py`).
- **Signals & Structure**: Swing pivots with lookback `swing_w=5` confirmed point-in-time at bar $t=i+w$. BOS updates `trend` (+1 for bullish, -1 for bearish). FVG detects 3-bar imbalance (`high[t-2] < low[t]` for long, `low[t-2] > high[t]` for short).
- **Entry**: Pending limit order placed at the proximal edge (`low[t]` long, `high[t]` short) with expiration after `wait=48` bars (4h on M5).
- **Stop Loss & Target**: Stop placed at opposite FVG edge (`high[t-2]` long, `low[t-2]` short) minus epsilon (`1e-4 * entry`). Minimum risk filter `min_risk_pts=8.0`. Target $RR = 1.5$ (kai port default).
- **Position Management**: Partial take profit of $50\%$ at 1R (`f=0.5`), stop moved to break-even (`entry`). Full exit at target. Cooldown of `cooldown=6` bars post-trade before new evaluations.
- **Executor Extensions**: Additive opt-in path in `src/backtest/executor.py` (`_run_backtest_enhanced`). Preserves legacy execution bit-for-bit when enhanced flags are default OFF.

### 2. Canonical MNQ M5 Backtest Results (2019-05-06 to 2026-09-03, 518,237 bars)
- **Dataset**: `databento/MNQ_M5.csv` from `databento.zip` (518,237 bars post-2019-05-06 cutoff).
- **Market & Units**: MNQ ($2.0/pt, tick size 0.25).
- **Sizing**: Initial balance $50,000, 1% risk per trade.

| Metric | Gross (Zero Friction) | Market Friction (2.0 pts = $4 RT) | Kai Reference (Gross) |
|---|---:|---:|---:|
| Trades ($n$) | 5,986 | 5,986 | 5,979 (+0.12%) |
| Win Rate | 59.00% | 58.39% | 59.20% (-0.20 pp) |
| Profit Factor | 1.3683 | 0.9930 | 1.4370 (-0.0687) |
| Net PnL | +$463,261.00 | -$10,111.00 | N/A ($) |
| Net R | +926.52 R | -20.22 R | +1,067.3 R (-140.8 R) |
| Expectancy | +0.1548 R (+$77.39) | -0.0034 R (-$1.69) | +0.1790 R |
| Max Drawdown | 6.53% | 64.19% | N/A |
| Total Commission | $0.00 | $473,372.00 | $0.00 |

### 3. Key Findings & Discrepancies Explained
1. **Gross Directional Parity Confirmed**: The FARS event-driven executor reproduces the original kai gross results closely ($n=5,986$ vs 5,979; WR $59.00\%$ vs $59.20\%$).
2. **Severe Friction Drag**: The gross edge is completely eroded under realistic market friction ($2.0 pts = $4.00 roundtrip). Because the strategy generates ~3.3 trades/day (5,986 trades over 7.3 years), cumulative transaction costs total $473,372, turning a +$463k gross gain into a -$10k loss.
3. **Engine Differences**: Original kai engine evaluated arrays via vectorized matrix passes, whereas FARS uses a strict bar-by-bar causal event loop with pending order expiration and conservative intrabar fill rules (stop-first).

## Next
- Juanca Strategy 2 (EMAS or CRT-TBS) evaluation
- Multi-process benchmarking validation outside sandbox

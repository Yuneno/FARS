# FARS LAB - Codex + OmniRoute Progress

## Current HEAD
- 94ee2b6 feat(P7): real data benchmark with databento.zip (MNQ, MYM) **[P7 BENCHMARK CORRECTED]**
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
- 288 passed, 2 skipped (sandbox spawn limitation)
- All P1-P6 phase tests green
- P7 E2E flow demonstrated with corrected real-data benchmark (MNQ only, canonical cutoff, 3 reps)

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
| P7 | **PARTIAL** | E2E | corrected benchmark valid; non-provisional strategy still pending |

## P7 Benchmark Bugs (verified 2026-09-13)
1. **BreakoutStrategy placeholder** (not SMC-FVG/EMAS): SMC-FVG/EMAS exist on branch 50d0efc, which is reachable from main, but the corrected infrastructure benchmark still uses the provisional strategy
2. **MYM dollar_per_point=2.0** (should be 0.5): fixed in `src/parallel.py`; MYM remains excluded pending provenance clarification
3. **MNQ pre-2019 synthetic data**: fixed by canonical cutoff 2019-05-06 in `benchmark_p7.py`
4. **No benchmark script committed**: reproducible script exists as an untracked file with the saved JSON evidence; not yet committed

## Blocked
- P7 valid non-provisional benchmark: requires SMC-FVG/EMAS strategy execution + MYM provenance clarification
- Juanca strategy comparison: deferred (strategies not on main)

## Next
- Commit benchmark script and focused P7 tests for reproducibility
- Validate multiprocess execution outside the sandbox (current benchmark fell back to sequential)
- Clarify MYM provenance and friction scenario before including MYM
- Run non-provisional SMC-FVG/EMAS strategy benchmark

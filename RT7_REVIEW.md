# RT-7 review (Hermes self-review)

Spec: FARS_REALTIME_SPEC.md §20, §31.
Diff reviewed: src/realtime/paper.py, tests/realtime/test_paper.py, src/realtime/__init__.py.
Tests: pytest tests/realtime (173 passed); pytest tests/ -m "not statistical" (765 passed, 10 deselected).

## Verdict

REVIEW PASSED

## Checks

- Paper and future live share ExecutionAdapter.submit. submit cannot be overridden.
- Denied RiskDecision never reaches a paper fill.
- Assumptions for fills, latency, slippage, commissions, partial fills, and rejections are explicit on the report.
- Paper does not claim live equivalence.
- LIVE_EXECUTION_ENABLED remains False. Adapter construction fails if that flag is true.
- No broker, no Core Trade conversion, no spec rewrite.

## WARNING

1. OrderIntent still has no price or quantity, so slippage and commissions cannot be applied as numbers. Defaults are labeled unmodeled, not zero.
2. There is no long-running paper session loop (bus + replay + strategy + risk + paper as one process). RT-7 lands the execution adapter and a strategy→risk→paper test. A session runner can wait for RT-8 if needed.

## SUGGESTION

Keep fill_policy as a stated model, not a hidden RNG. Random partials would break replay determinism.

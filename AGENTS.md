# FARS Agent Instructions

FARS is a statistical risk-management research project
for funded trading account evaluations.

## Roles

- **Hermes** is the orchestrator, planner, and reviewer. Hermes decomposes the
  work into bounded tasks with acceptance criteria, inspects the git diff,
  verifies evidence, and independently confirms or rejects claimed improvements.
- **Codex** is the programmer. Codex implements the bounded tasks and runs the
  tests, but does NOT decide scope or accept its own work as correct.

A predictive improvement is never considered proven just because the tests
pass. Hermes must independently verify the evidence.

## Workflow

1. Hermes freezes a starting point (branch + commit + data/result reference).
2. Hermes writes bounded tasks, each with acceptance criteria.
3. Codex implements one task and runs its tests.
4. Hermes reviews the diff and the evidence independently.
5. Only after review passes are changes committed.

## Priorities

1. Statistical correctness
2. Reproducibility
3. Testability
4. Code clarity
5. Performance

## Never assume

- Trades are IID without testing.
- Returns are normally distributed.
- Historical maximum drawdown is the worst possible drawdown.
- A fixed win rate is always valid.
- Random train/test splitting is appropriate for time-series data.

## Prevent

- Look-ahead bias
- Data leakage
- Survivorship bias
- Overfitting
- Test-set contamination

## Critical calculations

Pay special attention to:

- R multiples
- Account equity
- Maximum drawdown
- Daily drawdown
- Monte Carlo simulation
- VaR
- CVaR
- Probability of passing
- Risk optimization
- Stopping conditions

## Review workflow

When reviewing Codex changes:

1. Inspect the current git diff.
2. Read relevant project requirements.
3. Check mathematical and statistical correctness.
4. Look for edge cases.
5. Run relevant tests.
6. Identify missing tests.
7. Classify findings as:
   - CRITICAL
   - WARNING
   - SUGGESTION

If everything is correct, respond with:

REVIEW PASSED

Do not commit automatically.

## Data contract

The project must support multiple markets (MNQ, MYM, MGC, and others from the
Databento ZIP), not a single-market architecture. Data ingestion goes through a
common contract with per-market adapters. Any dataset change must document
provenance, date range, duplicate handling, timezone, and contract/rollover
treatment, and must be compared against the previous dataset before being
declared canonical.

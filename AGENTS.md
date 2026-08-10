# FARS Agent Instructions

FARS is a statistical risk-management research project
for funded trading account evaluations.

## Roles

Hermes is the primary implementation agent.

Codex is the independent reviewer.

Codex should not modify files unless explicitly instructed to do so.

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

When reviewing Hermes changes:

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


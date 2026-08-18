# FARS — Funded Account Risk System

Statistical risk-management research for funded trading account evaluations.

## Objective

Estimate which risk-per-trade level maximizes the probability of
reaching a profit target before violating account risk constraints.

```
r* = argmax_r P(PASS | r)
```

FARS v0.1 is NOT a BUY/SELL prediction bot. It is a Monte Carlo
simulation engine for statistical risk analysis.

## Development

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
python main.py
```

## Phases

See `FARS_SPEC.md` for the full specification and development phases.

Phase 7 (current): interpretable Matplotlib diagnostics for pass probability,
drawdown tail risk, final equity, losing streaks, and terminal outcomes across
candidate risk levels.

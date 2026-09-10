# FARS — Funded Account Risk System

## 1. Objective

FARS is a statistical risk-management research project for funded trading account evaluations.

The primary objective is to estimate which risk-per-trade level maximizes the probability of reaching a profit target before violating account risk constraints.

Conceptually:

P(PASS | risk_per_trade)

and:

r* = argmax_r P(PASS | r)

FARS is NOT initially a BUY/SELL prediction bot: it does not predict market
direction or generate trade signals on its own.

Declared end goal (NOT yet implemented): FARS will eventually feed an autonomous
execution bot — supplying risk metrics, bootstrap results, funded-account rules,
and risk-engine decisions so the bot can place trades without a human in the
loop at execution time. This goal is explicitly not implemented, not tested, and
not safe to deploy today. See `AUTONOMY_ROADMAP.md`.

The first versions should focus on:

- statistical risk analysis
- Monte Carlo simulation
- funded-account rule modeling
- drawdown analysis
- probability of passing
- risk optimization
- reproducibility
- robust testing

## 2. Data Philosophy

Eventually, the project may use approximately 16 years of historical data.

Do not assume the structure of the real dataset before inspecting it.

Possible data types may include:

- OHLCV
- historical trades
- strategy signals
- multiple assets
- multiple timeframes

Real data must first go through a data audit.

Until real data is available, synthetic trade data may be used for development and testing.

## 3. Synthetic Data

The synthetic data generator should support configurable parameters such as:

- win_rate
- average_win_R
- average_loss_R
- std_win_R
- std_loss_R
- number_of_trades
- trades_per_day
- random_seed

Trade outcomes should be represented primarily in R-multiples.

Examples:

+2R
-1R
+0.7R
-0.5R

Synthetic data is for development, testing, and stress scenarios.

It must not be treated as proof that a trading strategy works.

## 4. Funded Account Rules

The funded account configuration should support:

- initial_balance
- profit_target_pct
- max_drawdown_pct
- daily_loss_limit_pct
- max_trades
- risk_per_trade

Do not assume every prop firm calculates drawdown in the same way.

The architecture should eventually support:

- static drawdown
- trailing drawdown
- equity-based drawdown
- balance-based drawdown
- daily loss rules

FARS v0.1 may begin with:

- static maximum drawdown
- daily loss limt

The implementation must keep these rule types modular so that future account-rule models can be added without rewriting the simulation engine.

## 5. Trade Representation

Each trade should be represented in a way that separates strategy performance from account size.

The main outcome variable should be:

R_result

Where:

- +1R means a gain equal to the amount initially risked
- -1R means a full loss of the initial risk
- +2R means a gain equal to twice the initial risk
- -0.5R means a partial loss equal to half the initial risk

A minimal trade record should eventually support:

- trade_id
- timestamp
- date
- asset
- direction
- entry_price
- stop_price
- exit_price
- R_result
- strategy_or_setup
- optional metadata

For FARS v0.1, only the fields required by the risk engine should be mandatory.

## 6. Core Statistical Metrics

Before running account simulations, FARS should calculate:

- number_of_trades
- win_rate
- average_win_R
- average_loss_R
- expectancy_R
- standard_deviation_R
- skewness
- kurtosis
- historical_max_drawdown_R
- maximum_losing_streak
- losing_streak_distribution

Expectancy should be interpreted as the expected R outcome per trade.

Do not use win rate alone to judge strategy quality.

Two strategies with the same win rate may have very different expectancy and drawdown behavior.

## 7. Monte Carlo Simulation

Monte Carlo simulation will be the core of FARS v0.1.

For each candidate risk_per_trade:

1. Generate or resample possible trade sequences.
2. Start from the configured initial balance.
3. Apply each trade result in R.
4. Update account equity.
5. Check all funded-account constraints after every trade.
6. Stop the simulation immediately when a terminal condition occurs.

Terminal conditions include:

- profit target reached
- maximum drawdown violated
- daily loss limit violated
- maximum configured number of trades reached

The number of simulations must be configurable.

Suggested development value:

10,000 simulations

Suggested final-analysis value:

100,000 or more, depending on runtime and convergence.

## 8. Candidate Risk Levels

FARS should test a configurable range of risk-per-trade values.

Initial suggested range:

- minimum risk: 0.10%
- maximum risk: 2.00%
- step size: 0.05%

For every candidate risk level, calculate:

- probability_pass
- probability_fail
- failure_probability_max_drawdown
- failure_probability_daily_loss
- timeout_probability
- median_trades_to_pass
- mean_trades_to_pass
- median_max_drawdown
- P95_max_drawdown
- P99_max_drawdown

The initial optimization target is:

r* = argmax P(PASS | r)

However, do not treat very small differences in estimated pass probability as meaningful without considering simulation uncertainty.

## 9. Risk-Adjusted Optimization

FARS should later support a configurable Funded Risk Efficiency Score (FRES).

Conceptually:

FRES(r) =
P_pass
- lambda * P_fail
- gamma * DD_P95
- delta * CVaR

The weights lambda, gamma, and delta must NOT be treated as universal constants.

They must be configurable and documented.

The purpose of FRES is to avoid selecting a risk level that produces only a tiny improvement in pass probability while substantially increasing drawdown or tail risk.

## 10. Tail Risk

FARS should support risk metrics including:

- Value at Risk (VaR)
- Conditional Value at Risk (CVaR)
- Expected Shortfall

Special attention should be given to the worst 5% of simulated outcomes.

Do not automatically assume normally distributed trade outcomes.

When sufficient real data is available, the empirical distribution should be the default reference.

Future comparisons may include:

- empirical distribution
- Normal distribution
- Student-t distribution

## 11. Real Historical Data

When real historical data becomes available, FARS must not automatically replace it with a theoretical distribution.

The default approach should prioritize empirical data.

Before resampling historical trades, test whether the data shows:

- autocorrelation
- volatility clustering
- temporal dependence
- regime changes
- abnormal losing streak behavior
- structural changes across time

If the trades appear approximately independent, standard bootstrap may be considered.

If significant temporal dependence exists, consider:

- block bootstrap
- regime-aware resampling
- Markov-chain approaches

Do not implement more complex methods unless the data justifies them.

## 12. Temporal Validation

Time-series data must not be randomly split as if observations were IID.

Use chronological separation.

Conceptually:

older data -> development
middle period -> validation
most recent period -> final out-of-sample test

The final test period should remain untouched until the methodology and parameters are defined.

Future versions may support:

- walk-forward validation
- rolling validation windows
- expanding windows

## 13. Bias Prevention

FARS must explicitly protect against:

- look-ahead bias
- data leakage
- survivorship bias
- overfitting
- test-set contamination
- parameter tuning on final test data

Any implementation that risks introducing these problems should be flagged before being accepted.

Statistical correctness is more important than producing impressive backtest results.

## 14. Project Architecture

Suggested initial structure:

FARS/
├── AGENTS.md
├── FARS_SPEC.md
├── README.md
├── requirements.txt
├── data/
│   ├── raw/
│   ├── processed/
│   └── synthetic/
├── notebooks/
│   └── fars_exploration.ipynb
├── src/
│   ├── config.py
│   ├── data_loader.py
│   ├── synthetic_data.py
│   ├── metrics.py
│   ├── drawdown.py
│   ├── simulation.py
│   ├── monte_carlo.py
│   ├── risk_metrics.py
│   ├── optimization.py
│   └── visualization.py
├── tests/
└── main.py

The architecture may be changed if there is a clear technical reason.

Avoid unnecessary abstractions and overengineering.

## 15. Initial Technology Stack

Use initially:

- Python
- NumPy
- pandas
- SciPy
- Matplotlib
- pytest

Optional later:

- Plotly
- Polars
- DuckDB

Do not implement machine learning models in FARS v0.1.

Do not implement:

- neural networks
- reinforcement learning
- LSTM
- transformers
- automated BUY/SELL prediction

The initial project is a statistical risk-management system.

## 16. Testing Requirements

Testing is mandatory.

At minimum, include tests for:

1. A strongly negative-expectancy strategy should have a substantially lower probability of passing than a comparable positive-expectancy strategy.

2. Excessively high risk-per-trade should increase the probability of violating account constraints.

3. The same random seed must produce reproducible simulation results.

4. A -1R trade with 1% risk should produce approximately -1% account impact before additional effects.

5. A +2R trade with 0.5% risk should produce approximately +1% account impact.

6. The simulation must stop immediately when the profit target is reached.

7. The simulation must stop immediately when maximum drawdown is violated.

8. Daily loss must reset correctly at the beginning of a new trading day.

9. Maximum drawdown calculations must correctly update after a new equity peak.

10. Edge cases must be added whenever bugs or ambiguous behavior are discovered.

All tests should be run before a phase is considered complete.

## 17. Visualization Requirements

FARS should generate clear visual diagnostics.

At minimum, include:

1. Risk vs Probability of Passing

X-axis:
risk_per_trade

Y-axis:
probability_pass

Highlight the estimated optimal risk.

2. Risk vs Drawdown

X-axis:
risk_per_trade

Y-axis:
P95 maximum drawdown

3. Distribution of final account outcomes.

4. Distribution of maximum drawdowns.

5. Distribution of losing streaks.

6. Comparison of:

- probability_pass
- probability_fail
- timeout_probability

across candidate risk levels.

Visualizations should prioritize interpretability over decoration.

## 18. Development Workflow

FARS will use two AI agents with different roles.

Hermes:
Primary implementation agent.

Codex:
Independent reviewer.

Expected workflow:

1. Hermes reads FARS_SPEC.md.
2. Hermes implements one clearly scoped task.
3. Hermes adds or updates tests.
4. Hermes runs the test suite.
5. Hermes does NOT commit automatically.
6. Codex reviews the uncommitted git diff.
7. Codex checks:
   - implementation correctness
   - mathematical correctness
   - statistical validity
   - edge cases
   - test coverage
8. Codex returns:
   - REVIEW PASSED
   or
   - REVIEW FAILED
9. If failed, findings should be classified as:
   - CRITICAL
   - WARNING
   - SUGGESTION
10. Hermes independently verifies Codex findings before applying fixes.
11. Codex reviews again.
12. Only after review passes and tests pass should changes be committed.

Neither agent should blindly trust the other.

## 19. Development Phases

Do not build the entire project at once.

Phase 1:
Project structure, configuration, and synthetic data.

Phase 2:
Core statistical metrics.

Phase 3:
Single funded-account simulation.

Phase 4:
Simulation validation and edge-case testing.

Phase 5:
Monte Carlo engine.

Phase 6:
Risk-per-trade optimization.

Phase 7:
Visualizations.

Phase 8:
Historical-data ingestion and audit.

Phase 9:
Advanced statistical validation.

Do not advance to the next phase while critical tests are failing.

## 20. Future Research

Do not implement these yet.

Potential future versions:

FARS v0.2:
Bayesian uncertainty estimation.

FARS v0.3:
Block bootstrap.

FARS v0.4:
Markov models for trade dependence.

FARS v0.5:
Market regime detection.

FARS v0.6:
Dynamic position sizing.

Possible future formulation:

risk_t = f(
    current_equity,
    remaining_drawdown,
    remaining_target,
    strategy_edge,
    volatility,
    market_regime
)

This may later be studied using:

- dynamic programming
- stochastic control
- Bayesian optimization

These methods should only be added if simpler methods are insufficient.

## 21. Agent Behavior

The purpose of FARS is research and risk analysis.

Agents must not present simulated results as guaranteed future performance.

Whenever a statistical assumption is introduced, document:

1. what the assumption is,
2. why it is being used,
3. when it may fail,
4. what a more robust future alternative would be.

If an implementation choice could materially distort estimated risk or probability of passing, flag it explicitly.

Prefer simple, testable methods before introducing more complex statistical models.

## 22. Initial Hermes Task

When Hermes begins development:

DO NOT immediately implement the full project.

First:

1. Read AGENTS.md.
2. Read FARS_SPEC.md completely.
3. Inspect the current repository.
4. Do not create or modify project files yet.
5. Review the proposed statistical methodology.
6. Identify mathematical, statistical, or architectural weaknesses.
7. Recommend corrections where necessary.
8. Propose the final architecture for FARS v0.1.
9. Define how Trade objects should be represented.
10. Define how FundedAccountRules should be represented.
11. Define the synthetic data generator interface.
12. Define the first unit tests.

Wait for user approval before beginning Phase 1 implementation.

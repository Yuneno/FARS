# FARS — Funded Account Risk System

Statistical risk-management research for funded trading account evaluations.

## Objective

Estimate which risk-per-trade level maximizes the probability of
reaching a profit target before violating account risk constraints.

```
r* = argmax_r P(PASS | r)
```

### What FARS is today

FARS 1.2 is a statistical risk-analysis system for funded-account research,
plus a fail-closed realtime risk engine and paper-trading pipeline (RT-0
through RT-8). It analyzes historical and simulated trade outcomes, quantifies
uncertainty (Monte Carlo, bootstrap), models funded-account rules, and — in its
realtime layer — authorizes or vetoes trade intents against account state with
unknown-state-deny semantics. Live execution remains disabled
(`LIVE_EXECUTION_ENABLED = False`).

### Declared end goal (NOT yet implemented)

The medium/long-term goal is for FARS to feed an autonomous execution bot: the
bot consumes FARS's risk metrics, bootstrap results, funded-account rules, and
risk-engine decisions to place trades without a human in the loop at execution
time.

This end goal is **explicitly not implemented, not tested, and not safe to
deploy today**. It changes the security, testing, and audit requirements from
"analysis system" to "decision component of an autonomous execution system".
See `AUTONOMY_ROADMAP.md` for the full gap analysis and the gating items that
must be satisfied before real execution autonomy is considered.

`FARS 1.2` names the current research specification and development line. The
Python distribution remains version `0.1.0` while the software is an
experimental prototype; these version identifiers describe specification scope
and package maturity, respectively.

The current version is an experimental research prototype. Reported confidence
intervals quantify Monte Carlo error conditional on the configured input model;
they do not include strategy-estimation or model uncertainty.

## Development

```bash
pip install -e ".[dev]"
python -m pytest -p no:debugging tests/ -v
python main.py
```

The `-p no:debugging` option works around a known crash in the debugging plugin
of some Anaconda/Python 3.13 environments; it is not required in a clean virtual
environment when normal pytest startup works.

The independently reviewed Phase 10A acceptance stack is reproducible with
Python 3.12.13 and the hash-locked dependency file:

```bash
python3.12 -m venv .venv-acceptance
.venv-acceptance/bin/python -m pip install --require-hashes \
    -r requirements-acceptance.lock
.venv-acceptance/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv-acceptance/bin/python -m pytest -m "not statistical"
.venv-acceptance/bin/python -m pytest -m statistical
```

`requirements-acceptance.in` records the reviewed scientific versions and the
packaging toolchain; `requirements-acceptance.lock` fixes their complete
transitive resolution and distribution hashes. The deterministic CI runs on
every push and pull request. The much slower statistical acceptance suite has a
separate manually triggered workflow, so a green fast job is not presented as
statistical acceptance.

## Command-line interface (Phase 10B)

Installing with `pip install -e .` provides the `fars` entry point:

```bash
# Audit a trade CSV: row counts, issues, capabilities, provenance
fars audit trades.csv --outcomes-finalized

# Core descriptive metrics (only if the audit leaves core_metrics available)
fars metrics trades.csv --outcomes-finalized --format json

# Phase 10A uncertainty analysis (requires timestamped data and an explicit seed)
fars bootstrap trades.csv --outcomes-finalized --seed 42 \
    --replicates 2000 --format json

# Map external column names to canonical fields
fars metrics trades.csv --outcomes-finalized \
    --map ResultR=r_result --map ClosedAt=timestamp \
    --timezone America/New_York --delimiter "," --encoding utf-8-sig
```

Exit codes: 0 success, 1 unexpected internal error, 2 invalid CLI arguments,
3 structurally invalid file/encoding/mapping/CSV, 4 audit completed but the
data block the requested analysis. Without installation, the same commands are
available as `python -m src.cli ...` from the project root.

`fars bootstrap` requires both `core_metrics` and `temporal_analysis`. Its IID
intervals remain conditional on approximate IID; circular-block intervals are
explicitly exploratory and assume plausible stationary short-memory
dependence. A fixed seed provides reproducibility, not statistical validity.
Bootstrap JSON reports finite floating-point values to 15 significant decimal
digits. This removes non-semantic final-bit noise from the numerical stack so
repeated runs under the locked acceptance environment are byte-identical. The
top-level bootstrap `schema_version` (`fars-1.2-bootstrap-result-v1`) versions
the output structure independently from the statistical `algorithm_version`
(`fars-1.2-phase10a-v6`).

## Historical CSV ingestion (Phase 8A)

```python
from src import load_trade_csv

dataset = load_trade_csv(
    "trades.csv",
    outcomes_finalized=True,
    column_mapping={"ResultR": "r_result", "ClosedAt": "timestamp"},
    analysis_timezone="America/New_York",
)
dataset.require_capability("core_metrics")
trades = dataset.trades
```

Column mappings run from source names to canonical names. FARS preserves
unmapped columns as per-trade metadata, never silently sorts the file, and
reports separate capability statuses for basic metrics, temporal analysis, and
daily-rule simulation. `outcomes_finalized=True` is an explicit attestation that
all supplied R outcomes represent closed, final trades; FARS does not infer this
from strategy-specific status columns.

## Monetary account-trade ingestion (Phase 11A)

Phase 11A keeps monetary account history separate from the Core R-based
`Trade`. Monetary amounts use `Decimal`, currency is mandatory, and callers
must explicitly attest that each row is an already completed round trip:

```python
from decimal import Decimal
from src import load_account_trade_csv

dataset = load_account_trade_csv(
    "account_trades.csv",
    row_semantics="completed_round_trip",
    currency_tolerance=Decimal("0.01"),
)
dataset.require_capability("account_pnl")
```

The adapter preserves unknown provider fields and records both raw-row and
normalized-record SHA-256 fingerprints. It reports separate capabilities for
account P&L, cost reconciliation, R analysis, closed-trade replay, and intraday
rule replay. Headers matching password, token, secret, credential, authorization,
cookie, OAuth, or private/API/access-key families are refused rather than copied
into metadata. Missing optional values remain missing. R is derived only from a
cost-reconciled `net_pnl` and a strictly positive `initial_risk_amount`, with
formula, source fields, and method version recorded. Fill aggregation, partial
fills, position reversals, provider-specific semantics, and intraday replay
from closed trades remain unsupported.

## Generic funded-account rules (Phase 11B)

Phase 11B adds an immutable monetary rule profile and a separate mutable replay
state without changing the legacy percentage engine. Exact rule boundaries use
`Decimal`, simultaneous violations are fully disclosed in deterministic order,
and missing event coverage blocks passing instead of being approximated:

```python
from datetime import datetime, timezone
from src import (
    CAP_CLOSED_TRADE_EVENTS,
    FundedAccountStateV2,
    rapid_25k_profile,
)

profile = rapid_25k_profile()
assert not profile.enabled

state = FundedAccountStateV2(
    profile,
    opened_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
    data_capabilities=frozenset({CAP_CLOSED_TRADE_EVENTS}),
)
```

The generic engine represents absolute or percentage targets and loss limits,
static or trailing drawdown, independent update and monitoring cadences,
threshold ceilings, optional daily limits, soft pauses, consistency blocks,
minimum days, position size, sessions, forced close, news metadata, and
inactivity. The Rapid 25K reference profile preserves the known 2026-08-27
values but remains provisional and disabled until its five blocking rule
questions are resolved.

## Probabilistic funded-account paths (Phase 11C)

Phase 11C combines an audited R-multiple dataset, its matching Phase 10A
eligibility result, an enabled Phase 11B rule profile, and explicit monetary
risk and cost assumptions. It refuses unsupported or structurally unstable
data instead of falling back to IID:

```python
from datetime import datetime, timezone
from decimal import Decimal
from src import (
    CostAssumptions,
    PathSimulationConfig,
    RiskSizingPolicy,
    run_probabilistic_paths,
)

result = run_probabilistic_paths(
    dataset,
    bootstrap_result,
    verified_profile,
    RiskSizingPolicy("fixed_amount", Decimal("100"), "USD"),
    CostAssumptions("USD", commission_per_trade=Decimal("4.50")),
    PathSimulationConfig(
        n_simulations=10_000,
        max_trades=100,
        seed=20260830,
        start_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        trades_per_day=3,
    ),
)
```

IID paths require `iid_eligible`. Dependence routes use the Phase 10A
expectancy-influence block length and are labeled exploratory CBB. The result
reports pass, breach, and censoring probabilities with Monte Carlo intervals;
conditional trades/days to pass; drawdown, loss-budget, streak, and best-day
distributions; assumptions; limitations; and complete dataset/profile/RNG
provenance. R-only paths support exact closed-trade rules and refuse profiles
that require intraday, EOD, position-size, news, or open-position event streams.
Risk sensitivity always returns the full predeclared curve and disallows a
multi-level tuning grid on validation or final OOS data.

## Current modeling assumptions

- Synthetic outcomes are IID and use a Bernoulli/lognormal mixture.
- `risk_per_trade` is fixed-dollar sizing based on the initial balance.
- Historical peak-to-trough drawdown and rule-defined drawdown are stored
  separately. FRES penalizes historical peak-to-trough tail risk; rule-defined
  drawdown separately reports funded-rule budget consumption.
- Wilson and bootstrap intervals are conditional on the selected model.
- Phase 8A accepts strategy-agnostic historical-trade CSV files through an
  explicit canonical mapping and reports which analyses the available fields
  can safely support.
- Phase 10A uses rank-based dependence diagnostics to select conditional IID
  bootstrap intervals or exploratory circular-block intervals. It does not
  certify IID or stationarity and does not estimate future extremes.
- Phase 11A accepts only explicitly completed monetary round trips. Supplied and
  derived R values are not mixed automatically, and multiple currencies are not
  aggregated without an approved conversion contract.
- Phase 11B rule replay requires explicit event-coverage capabilities. A
  missing intraday, end-of-day, position, news, or opening-time stream cannot
  produce an exact pass result.
- Phase 11C probabilities are conditional model estimates, not promises.
  Empirical IID/CBB resampling cannot generate unseen tail outcomes; stress
  testing remains a separate Phase 13 responsibility.

## Phases

See `FARS_SPEC.md` for the full specification and development phases.

Phases 1–7, the Phase 8A ingestion/audit contract, the Phase 9A CLI
(`fars audit` / `fars metrics`), the independently confirmed Phase 10A
bootstrap framework, and its Phase 10B CLI exposure are implemented: synthetic
generation, descriptive metrics, account simulation, Monte Carlo, risk
optimization/FRES, visual diagnostics, audited CSV adaptation, dependence
screening, and uncertainty intervals for expectancy, win rate, and standard
deviation. Phase 11A monetary account records, the Phase 11B generic funded-rule
engine, and Phase 11C probabilistic account paths are implemented and pending
independent review. Provider-specific prospective validation, drawdown/extreme
stress analysis, and advanced temporal validation remain future work.

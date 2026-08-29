# FARS — Funded Account Risk System

Statistical risk-management research for funded trading account evaluations.

## Objective

Estimate which risk-per-trade level maximizes the probability of
reaching a profit target before violating account risk constraints.

```
r* = argmax_r P(PASS | r)
```

FARS 1.2 is NOT a BUY/SELL prediction bot. It is a statistical risk-analysis
system for funded-account research.

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

## Phases

See `FARS_SPEC.md` for the full specification and development phases.

Phases 1–7, the Phase 8A ingestion/audit contract, the Phase 9A CLI
(`fars audit` / `fars metrics`), the independently confirmed Phase 10A
bootstrap framework, and its Phase 10B CLI exposure are implemented: synthetic
generation, descriptive metrics, account simulation, Monte Carlo, risk
optimization/FRES, visual diagnostics, audited CSV adaptation, dependence
screening, and uncertainty intervals for expectancy, win rate, and standard
deviation. Monetary account records, funded-rule v2 replay, probabilistic
account paths, drawdown/extreme stress analysis, and advanced temporal
validation remain future work.

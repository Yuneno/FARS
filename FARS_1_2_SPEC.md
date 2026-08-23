# FARS 1.2 Specification

## 0. Status

This document defines the requirements for FARS 1.2.

FARS 1.2 extends the existing FARS Core. It does not replace or rewrite the Core unless a change is explicitly justified and approved.

Unless explicitly overridden by this specification, the requirements, invariants, statistical safeguards, and principles defined in `FARS_SPEC.md` remain applicable.

This specification is a living design document while FARS 1.2 is under development. Requirements must not be invented or silently expanded by implementation agents. Ambiguous or new requirements must be discussed before implementation.

---

## 1. Purpose

FARS 1.2 extends FARS from a Core focused primarily on funded-account risk simulation and Monte Carlo analysis into a system capable of ingesting, validating, adapting, and statistically analyzing historical trade data supplied by external strategies.

FARS remains a quantitative/statistical risk-analysis system.

FARS is NOT:

- a market prediction system;
- a BUY/SELL signal generator;
- a guarantee of future profitability;
- a strategy-specific backtesting engine;
- a system designed to optimize specifically for the initial validation datasets.

Historical results must be treated as observations from the past, not as guarantees of future performance.

---

## 2. Core Compatibility

FARS 1.2 should extend the existing Core rather than rewrite it unnecessarily.

The existing Core remains the authoritative implementation for functionality already defined and validated unless a concrete defect, incompatibility, or approved architectural requirement requires modification.

New FARS 1.2 components should interact with the Core through small, well-defined interfaces whenever reasonably possible.

Backward compatibility should be preserved when it does not compromise statistical correctness or create unnecessary architectural complexity.

Any change that materially alters an existing Core invariant, statistical calculation, account rule, simulation behavior, or public interface must be explicitly identified and justified before implementation.

---

## 3. Initial Objectives

FARS 1.2 will initially focus on the following capabilities:

1. Define a standard external trade-data input interface.
2. Allow external users to provide historical trades from their own strategies.
3. Validate and adapt external trade data into a canonical representation usable by FARS.
4. Provide a simple command-line interface (CLI) for executing analyses.
5. Incorporate statistical Bootstrap methods as a formal part of FARS.
6. Evaluate whether IID Bootstrap, Block Bootstrap, or another justified resampling method is appropriate based on observed temporal dependence.
7. Use resampling methods to estimate distributions, uncertainty, confidence intervals, drawdowns, streaks, extremes, and probabilistic trade-related limits where statistically justified.
8. Validate FARS using real external historical datasets.
9. Support temporal validation methods such as out-of-sample evaluation, walk-forward validation, and stress testing where appropriate.
10. Explicitly protect against data leakage, look-ahead bias, overfitting, and final-test contamination.

These objectives define the intended scope. Detailed requirements for each capability will be added only after the corresponding design decisions are reviewed.

---

## 4. Initial Validation Data

The first external validation datasets currently available contain historical trades from:

- SMC-FVG (Fair Value Gap)
- SMC-OB (Order Block)

The datasets include trades across instruments including approximately:

- MNQ
- YM
- ES
- GC

and cover historical periods approximately between 2010 and 2026.

These datasets are validation cases only.

FARS 1.2 MUST remain strategy-agnostic and MUST NOT encode assumptions, required fields, transformations, statistical choices, or business logic solely because they fit SMC-FVG or SMC-OB.

The datasets may contain fields that are incomplete, unknown, undocumented, or not required by FARS. Unknown columns must not automatically cause otherwise usable trade records to be discarded.

The available backtests may not include all real execution costs, including commissions and slippage. FARS must not silently assume that reported historical results are net of all execution costs.

---

## 5. Proposed FARS 1.2 Architecture

The initial conceptual pipeline is:

```text
External Trade Data
        |
        v
Input Layer
        |
        v
Schema Mapping / Adapter
        |
        v
Validation Layer
        |
        v
Canonical Trade Dataset
        |
        v
Existing FARS Core
        |
        +--> Core Metrics / Simulation / Risk Analysis
        |
        +--> Bootstrap / Statistical Extensions
        |
        +--> Temporal Validation / OOS / Stress Testing
        |
        v
CLI / Analysis Output
```

This diagram is conceptual, not a requirement for specific Python modules or class names.

The architecture should favor small interfaces and separation of concerns over unnecessary abstractions.

---

## 6. External Data Interface

### 6.1 Design Principle

The external data interface must be strategy-agnostic.

FARS should accept historical trade records without requiring users to organize their data around SMC-FVG, SMC-OB, or any other specific strategy methodology.

External column names may differ from FARS canonical field names. A mapping/adaptation mechanism should translate supported external schemas into the canonical representation.

### 6.2 Minimum Required Information

The Phase 8A canonical trade contract is defined in section 7. Later analyses
may add field requirements only through an approved specification update.

FARS 1.2 should require only the information statistically necessary for a requested analysis. It should not reject an entire dataset merely because optional information is missing.

A dataset may therefore support some analyses while being insufficient for others.

FARS should eventually report which analysis capabilities are available or unavailable based on the fields present in the dataset.

### 6.3 Unknown and Optional Fields

Unknown external fields should be preserved or ignored safely where possible rather than treated as fatal errors.

Missing optional fields must not invalidate otherwise usable trade records.

Rows must not be deleted solely because unrelated or unknown columns contain missing values.

Phase 8A preserves unknown values per accepted record as defined in section 7.4.
Serialization or long-term storage of that metadata remains outside this phase.

---

## 7. Canonical Trade Schema

**STATUS: PHASE 8A CONTRACT APPROVED**

### 7.1 Scope

Phase 8A defines the minimum canonical contract needed to ingest closed-trade
CSV files safely. It does not yet define gross/net PnL or execution-cost
semantics. Columns related to those unresolved concepts may be preserved as
external metadata, but FARS must not interpret them until their contract is
approved.

### 7.2 Canonical Fields

`r_result` is the only field required for basic descriptive trade metrics. It
must be a finite real number expressed in R-multiples. Zero is valid. FARS must
not derive R from price or PnL fields in Phase 8A.

The following fields are optional for basic metrics:

- `trade_id`: non-empty string when supplied;
- `timestamp`: ISO-8601 timestamp;
- `asset`: string;
- `direction`: `long` or `short` after case normalization;
- `entry_price`, `stop_price`, and `exit_price`: finite real numbers;
- `strategy`: string.

`date` is derived from `timestamp` in an explicitly configured analysis
timezone. It is not an independent Phase 8A external input field.

Missing optional values do not invalidate a row for basic metrics. A malformed
optional value is omitted from its canonical field, preserved in audit
metadata, and reported as a warning.

### 7.3 Timestamps and Ordering

Basic metrics do not require timestamps. Temporal analysis requires every
accepted row to have a valid timezone-aware ISO-8601 timestamp. Naive
timestamps must not be assigned an implicit timezone.

Daily-rule simulation additionally requires an explicit IANA analysis timezone.
FARS derives each trade's `date` only after converting the timestamp into that
timezone.

Input order is authoritative. FARS must validate non-decreasing chronological
order and must not silently sort rows. Equal timestamps are allowed and retain
input order.

### 7.4 Identifiers, Duplicates, and Unknown Fields

`trade_id` is optional. Repeated non-empty IDs are ambiguous duplicates and
must be reported as errors that block automatic analysis. When IDs are absent,
FARS must not infer duplicate trades solely from equal outcomes or timestamps.

Unknown columns and their raw values must be preserved per accepted record.
Missing values in unknown columns must not cause row rejection.

Rows representing open or incomplete trades are usable only when they already
contain a valid, finalized `r_result`. Phase 8A does not infer an outcome from
open positions, prices, gross/net PnL, commissions, or slippage.

Because Phase 8A has no canonical position-status field, callers must explicitly
attest that all supplied outcomes are closed and final. The CSV loader must
require `outcomes_finalized=True`; omission or any other value is an error.

---

## 8. Data Validation and Audit

**STATUS: PHASE 8A CONTRACT APPROVED**

Phase 8A provides a CSV loader with an explicit source-column to canonical-field
mapping. Unmapped canonical names use exact identity matching. Mapping must be
one-to-one; missing mapped source columns, duplicate headers, unsupported
canonical targets, and target collisions are structural errors.

Validation produces a durable audit result containing:

- total, accepted, and rejected row counts;
- issues with severity, stable code, message, optional source row, and field;
- explicit availability and unavailability reasons for `core_metrics`,
  `temporal_analysis`, and `daily_rule_simulation`.

The canonical dataset must also retain reproducible ingestion provenance:

- canonical schema version;
- absolute source path, source byte size, and SHA-256 digest;
- effective source-to-canonical column mapping;
- final-outcome attestation;
- analysis timezone, delimiter, and encoding.

FARS must fail the load if the source fingerprint changes during ingestion.

Severity meanings are:

- `ERROR`: required data is unusable or duplicate identity makes automatic
  analysis unsafe;
- `WARNING`: optional or capability-specific data is unusable, while a narrower
  analysis may remain valid;
- `INFO`: non-failing audit context.

A row with missing, malformed, Boolean, NaN, or infinite `r_result` is rejected
and audited. Other usable rows remain available for inspection, but
`core_metrics` must be marked unavailable until the rejected-row decision is
reviewed; FARS must not silently analyze a selectively reduced sample.

Rows must not be rejected because unrelated optional or unknown fields are
missing. The loader must not mutate source files, reorder rows, fill missing
outcomes, assume a timezone, or deduplicate automatically.

---

## 9. Command-Line Interface

**STATUS: PHASE 9A CONTRACT APPROVED**

Phase 9A provides a minimal CLI that safely exposes only capabilities already
validated by FARS. It introduces no new statistical methods. The CLI must reuse
`load_trade_csv()` and `compute_metrics()` without duplicating their
validation logic, and must never modify the source CSV.

### 9.1 Commands

```text
fars audit   trades.csv --outcomes-finalized [options]
fars metrics trades.csv --outcomes-finalized [options]
```

Shared options:

- `--map Source=canonical` (repeatable), e.g. `--map ResultR=r_result`;
- `--timezone IANA_NAME` (analysis timezone);
- `--delimiter CHAR` (default `,`);
- `--encoding NAME` (default `utf-8-sig`);
- `--format text|json` (default `text`).

`--outcomes-finalized` is the strict attestation required by section 7.4.

### 9.2 `fars audit`

Loads the CSV and reports: total/accepted/rejected row counts; issues with
severity; capability availability with reasons; ingestion provenance
(SHA-256, size, path, schema version, mapping, timezone, delimiter, encoding).
It computes no statistics.

### 9.3 `fars metrics`

Runs the same audit first. Only if `core_metrics` is available it computes:
trade count, win rate, mean win/loss in R, expectancy, standard deviation,
skewness, kurtosis, historical max drawdown in R, and max losing streak.

It must not run Bootstrap, Monte Carlo on historical data, or optimization.
Phase 10A now defines a reviewed Bootstrap method, but exposing it requires a
separate CLI contract; Phase 9A remains intentionally limited to audit and
descriptive metrics.

### 9.4 Output

- `text`: human-readable; `json`: stable machine-readable schema.
- Statistically undefined values (e.g. standard deviation of one trade) are
  `null` in JSON, never NaN.
- Normal results go to stdout; errors go to stderr.

### 9.5 Exit Codes

- `0`: operation completed successfully.
- `1`: unexpected internal error.
- `2`: invalid CLI arguments.
- `3`: file, encoding, mapping, or CSV structurally invalid.
- `4`: audit completed, but the data block the requested analysis.

Optional warnings do not fail the command while the requested capability
remains available.

### 9.6 Phase 9A Limits

Not implemented in this phase: configuration files, IID or block Bootstrap,
optimization on historical trades, OOS or walk-forward validation, stress
testing, commission/slippage modeling, and deriving `r_result` from prices or
PnL.

---

## 10. Bootstrap Framework

**STATUS: PHASE 10A v4 IMPLEMENTED; v5 INDEPENDENTLY REJECTED; v6
INDEPENDENTLY REVIEWED, CONFIRMED, AND IMPLEMENTED** (see
`FARS_1_2_PHASE_10_BOOTSTRAP_DRAFT.md`, section 0). V4 acceptance exposed two
infeasible criteria. V5 corrected them but failed independent IID-size
validation for a skewed LogNormal marginal. V6 replaces raw-score portmanteau
inputs with marginal-normal rank scores and passed all 10 criteria on its newly
predeclared independent confirmation root; generators, sample sizes,
thresholds, and simulation counts remained unchanged.

Bootstrap will become a formal statistical component of FARS 1.2.

FARS MUST NOT assume historical trades are IID without testing or justification.

The Bootstrap design must distinguish between the statistical question being estimated and the resampling method used to estimate it.

Candidate methods may include:

- IID Bootstrap;
- Block Bootstrap variants;
- other resampling approaches when justified by the observed dependence structure.

Method selection must be driven by statistical evidence and documented assumptions rather than convenience.

Detailed requirements for diagnostics, method selection, confidence intervals, reproducibility, block-length selection, uncertainty estimation, and failure modes will be specified before implementation.

---

## 11. Probabilistic Risk and Trade Limits

**STATUS: DESIGN PENDING**

FARS may use Bootstrap and related statistical methods to estimate probabilistic distributions and bounds for quantities such as:

- drawdowns;
- losing and winning streaks;
- extreme outcomes;
- trade-count requirements;
- uncertainty around statistical metrics;
- other risk limits supported by the available data.

These limits must be probabilistic and assumption-aware. They must not be presented as guaranteed maximum or minimum future outcomes.

The exact meaning of any "trade limit" must be formally defined before implementation.

---

## 12. Temporal Validation and Out-of-Sample Evaluation

**STATUS: DESIGN PENDING**

FARS 1.2 will preserve chronological structure when performing temporal validation.

Random train/test splitting must not be used for time-dependent trade data merely for convenience.

Candidate validation methods include:

- chronological holdout;
- out-of-sample evaluation;
- walk-forward validation;
- rolling windows;
- expanding windows.

The methodology must prevent leakage from validation or final-test periods into calibration decisions.

---

## 13. Stress Testing

**STATUS: DESIGN PENDING**

Stress testing may be added when it answers a clearly defined risk question.

Stress scenarios must not be chosen solely to improve apparent strategy performance.

The relationship between historical resampling, synthetic perturbations, execution-cost assumptions, and stress scenarios will be specified before implementation.

---

## 14. Reproducibility

All stochastic FARS 1.2 methods must support reproducible execution through explicit random seeds where technically applicable.

Seed behavior must be testable and documented.

A fixed seed provides reproducibility, not statistical validity. Statistical conclusions must not depend materially on selecting a favorable seed.

---

## 15. Statistical Safeguards

FARS 1.2 inherits all statistical safeguards defined in `FARS_SPEC.md` and `AGENTS.md`.

In particular:

- do not assume trades are IID without testing;
- do not assume normally distributed outcomes;
- do not equate historical maximum drawdown with the worst possible future drawdown;
- do not use random time-series splits without justification;
- prevent look-ahead bias;
- prevent data leakage;
- prevent survivorship bias where relevant;
- prevent overfitting;
- prevent final-test contamination;
- distinguish calibration, validation, and final OOS evaluation;
- document statistical assumptions and known limitations.

Statistical correctness takes priority over producing favorable results.

---

## 16. Testing Requirements

FARS 1.2 additions must include tests appropriate to their statistical and software behavior.

Passing unit tests alone is not sufficient evidence that a statistical implementation is valid.

Tests should include, where applicable:

- deterministic correctness tests;
- malformed input and schema edge cases;
- missing-data behavior;
- reproducibility tests;
- temporal-ordering tests;
- leakage-prevention tests;
- statistical sanity checks;
- resampling invariants;
- tests for discovered bugs and ambiguous cases.

Specific acceptance tests will be defined alongside each finalized requirement.

---

## 17. Non-Goals

Unless explicitly added through a future approved specification change, FARS 1.2 is not intended to:

- predict future market direction;
- generate discretionary BUY/SELL signals;
- guarantee profitability;
- prove that a strategy has a persistent future edge;
- optimize specifically for SMC-FVG or SMC-OB;
- hide uncertainty behind a single deterministic risk number;
- treat backtest results as equivalent to live execution results;
- add complex statistical methods when simpler justified methods are adequate.

---

## 18. Agent and Development Workflow

Agents working on FARS 1.2 must read, at minimum:

1. `AGENTS.md`
2. `FARS_SPEC.md`
3. `FARS_1_2_SPEC.md`
4. the relevant current implementation and tests

The normal development workflow remains:

```text
Implementation
    -> tests
    -> independent Codex review
    -> verify valid findings
    -> corrections
    -> tests
    -> new review
    -> REVIEW PASSED
    -> commit / merge / push only when explicitly requested
```

When the same model family acts as both implementer and reviewer, separate sessions should be used where practical to reduce review bias.

Implementation agents must not silently convert design notes, dataset quirks, or speculative future ideas into requirements.

---

## 19. Future Extensions

Possible future extensions may include external contextual metadata such as market events or economic-news classifications when a statistically defensible research question is defined.

Such metadata must remain optional and must not become a requirement of the canonical trade interface unless explicitly approved in a future specification revision.

No future extension listed here is an implementation requirement.

---

## 20. Open Design Decisions

The following items must be resolved before their respective components are implemented:

The Phase 8A versions of the canonical trade schema, capability requirements,
column mapping, missing/duplicate policy, timestamp policy, and audit format are
resolved in sections 7 and 8. The following decisions remain open:

1. Gross versus net PnL representation.
2. Commission and slippage handling.
3. CLI configuration files (Phase 9A commands and options are resolved in section 9).
4. Temporal-dependence diagnostics.
5. Criteria for IID versus dependent Bootstrap methods.
6. Block Bootstrap variant and block-length methodology when required.
7. Bootstrap interval methodology.
8. Formal definition of probabilistic trade limits.
9. OOS and walk-forward protocols.
10. Stress-testing methodology.
11. Treatment of optional external contextual metadata.

These are intentionally unresolved. They must not be guessed by implementation agents.

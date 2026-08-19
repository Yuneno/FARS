# FARS 1.2 Specification

## 0. Status

**Specification status:** REVIEW DRAFT  
**Intended next action:** Independent Codex review before implementation.

This document defines the requirements and open design decisions for FARS 1.2.

FARS 1.2 extends the existing FARS Core. It does not replace or rewrite the Core unless a change is explicitly justified and approved.

This specification is a living design document while FARS 1.2 is under development. Requirements must not be invented, silently expanded, or inferred from quirks in the validation datasets.

Sections marked **DESIGN PENDING** are intentionally unresolved and MUST NOT be implemented by guessing their final behavior.

### 0.1 Normative Language

The following terms are used intentionally:

- **MUST / MUST NOT**: mandatory requirement.
- **SHOULD / SHOULD NOT**: preferred behavior unless a justified reason exists.
- **MAY**: optional behavior.
- **DESIGN PENDING**: unresolved; implementation must wait for approval.
- **DEFERRED**: intentionally outside the current implementation scope.

### 0.2 Specification Precedence

For FARS 1.2 work, use the following precedence:

1. `FARS_1_2_SPEC.md` for approved FARS 1.2 requirements.
2. `FARS_SPEC.md` for inherited Core requirements and invariants.
3. `AGENTS.md` for agent behavior and review workflow.
4. Current code and tests as evidence of the existing implementation.

If these sources appear to conflict, the conflict MUST be reported before changing behavior.

Historical references to early FARS version numbers in the original Core documents describe the development lineage of the current Core and do not redefine the FARS 1.2 version identifier.

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

## 2. Core Compatibility and Integration Boundary

FARS 1.2 SHOULD extend the existing Core rather than rewrite it unnecessarily.

The existing Core remains authoritative for functionality already defined and validated unless a concrete defect, incompatibility, or approved architectural requirement requires modification.

New FARS 1.2 components SHOULD interact with the Core through small, well-defined interfaces.

### 2.1 Data Boundaries

FARS 1.2 MUST distinguish between:

```text
External Record
      ↓
Canonical Record
      ↓
Core Trade
```

These are not automatically the same object.

- **External Record**: data exactly as provided by the source.
- **Canonical Record**: normalized and validated representation used by the FARS 1.2 data layer.
- **Core Trade**: the existing compact representation consumed by the validated FARS Core.

The canonical schema MUST NOT replace or expand the existing Core `Trade` merely for convenience.

A dedicated adapter SHOULD convert canonical records into Core `Trade` objects only when the fields required by the requested Core analysis are available.

Any change that materially alters an existing Core invariant, statistical calculation, account rule, simulation behavior, or public interface MUST be explicitly identified and justified before implementation.

---

## 3. Initial Objectives

FARS 1.2 will initially focus on:

1. A standard external trade-data input interface.
2. Validation and normalization of external historical trade data.
3. A canonical, strategy-agnostic representation.
4. Capability-aware analysis when some fields are unavailable.
5. A simple CLI for executing analyses.
6. Formal use of statistical resampling methods.
7. Evaluation of temporal dependence before choosing a resampling approach.
8. Probabilistic analysis of drawdowns, streaks, uncertainty, and other risk quantities where statistically justified.
9. Validation using real external historical datasets.
10. Chronological OOS, walk-forward, and stress-testing methods where appropriate.
11. Explicit protection against leakage, look-ahead bias, overfitting, and final-test contamination.
12. Reproducible analysis with auditable configuration and provenance.

Detailed requirements must be approved before implementation.

---

## 4. Initial Validation Data

The first available validation datasets contain historical trades from:

- SMC-FVG (Fair Value Gap)
- SMC-OB (Order Block)

They include instruments such as:

- MNQ
- YM
- ES
- GC

and cover periods approximately between 2010 and 2026.

These datasets are validation cases only.

FARS 1.2 MUST remain strategy-agnostic and MUST NOT encode assumptions or business logic solely because they fit these datasets.

Unknown, incomplete, or undocumented columns MUST NOT automatically cause otherwise usable records to be discarded.

The available backtests may not include all real execution costs, including commissions and slippage. FARS MUST NOT silently assume that historical results are net of all execution costs.

FARS also MUST NOT claim that imported backtests are free from upstream look-ahead bias, survivorship bias, overfitting, or other methodological problems unless sufficient information exists to support that conclusion.

---

## 5. Conceptual Architecture

```text
External Data
     ↓
Ingestion
     ↓
Schema Mapping
     ↓
Validation / Audit
     ↓
Canonical Dataset
     ├── Capability Assessment
     ├── Statistical Diagnostics
     ├── Resampling / Bootstrap
     │       ↓
     │   Resampled Sequences
     │       ↓
     ├── Core Adapter ───────→ Existing FARS Core
     ├── Temporal Validation
     └── Stress Testing
             ↓
       Analysis Results
             ↓
       Run Manifest / Reports

CLI ─────────→ Analysis Orchestrator
```

This is conceptual and does not require specific Python module or class names.

The CLI is an interface to the system, not the final stage of the statistical pipeline.

---

## 6. External Data Interface

### 6.1 Strategy-Agnostic Input

External column names may differ from FARS canonical names.

A mapping/adaptation mechanism SHOULD translate supported external schemas into the canonical representation.

### 6.2 Capability-Aware Analysis

FARS SHOULD require only the information necessary for the requested analysis.

A dataset MAY support some analyses while being insufficient for others.

Example:

```text
Available:
- descriptive PnL analysis
- chronological streak analysis

Unavailable:
- R-based Core simulation

Reason:
- R_result cannot be derived from available fields
```

FARS MUST NOT fabricate missing information to unlock an analysis.

### 6.3 Unknown and Optional Fields

Unknown external fields SHOULD be preserved or ignored safely where possible.

Missing optional fields MUST NOT invalidate otherwise usable records.

Rows MUST NOT be deleted solely because unrelated or unknown columns contain missing values.

### 6.4 Provenance

Material transformations SHOULD be traceable.

When FARS derives a value such as `r_result`, normalized time, or net PnL, the system SHOULD preserve enough provenance to identify the source fields and transformation used.

---

## 7. Canonical Trade Schema

**STATUS: DESIGN PENDING**

The canonical schema will define:

- canonical field names;
- required versus optional fields;
- data types;
- identifiers;
- outcome representation;
- R-multiples;
- gross and net PnL;
- commissions and slippage;
- instrument and strategy metadata;
- timestamps and timezone information;
- trading/session date;
- missing values;
- duplicates;
- open or incomplete positions;
- chronological ordering;
- preservation of external metadata;
- provenance of derived fields.

### 7.1 Meaning of One Record

Before implementation, FARS MUST define what one canonical record represents.

External data may describe:

- an individual execution/fill;
- an order;
- a partial close;
- a complete closed trade / round-trip;
- another source-specific structure.

FARS MUST NOT silently treat these concepts as equivalent.

If fill-level data is supported, the rules for consolidating fills into an analyzable trade MUST be defined before implementation.

### 7.2 PnL Is Not Automatically R

FARS MUST NOT interpret monetary PnL, points, ticks, or a WIN/LOSS label as `r_result` unless a valid and documented transformation exists.

If the initial risk required to calculate R cannot be established, R-based analyses MUST be marked unavailable rather than using an invented value.

### 7.3 Time and Timezone Policy

Canonical timestamps SHOULD use timezone-aware ISO-8601 values and SHOULD be normalized internally to UTC.

FARS MUST preserve enough source timezone information to reconstruct the original timing when available.

UTC normalization MUST NOT be used as a substitute for defining the relevant trading/session day.

Daily-loss logic, chronological validation, OOS partitioning, and session boundaries MUST use an explicitly defined time policy.

Equal timestamps, overlapping trades, and concurrent positions MUST NOT be ordered arbitrarily when ordering could change an analysis result.

### 7.4 Optional Intra-Trade Information

Fields such as the following MAY be supported:

- holding duration;
- Maximum Adverse Excursion (MAE);
- Maximum Favorable Excursion (MFE).

These fields MUST NOT be mandatory for basic ingestion.

Analyses that require intra-trade path information MUST be marked unavailable when the required information is absent.

---

## 8. Data Validation and Audit

**STATUS: DESIGN PENDING**

The validation layer will determine whether external data is structurally and statistically usable.

It will define:

- errors;
- warnings;
- accepted records;
- rejected records;
- suspicious records;
- capability limitations;
- audit summaries.

### 8.1 Outliers

Extreme or suspicious observations MUST NOT be removed, winsorized, clipped, or corrected automatically merely because they are unusual.

They SHOULD be flagged for review.

Removal or correction requires evidence that the observation is erroneous or an explicitly approved analysis transformation.

### 8.2 Sample Adequacy

FARS SHOULD evaluate whether the available sample is adequate for the requested statistical analysis.

The specification MUST NOT assume a universal cutoff such as `N < 50`.

Different analyses may require different amounts and structures of data.

FARS MAY warn or block a specific analysis when the available evidence is insufficient, but the rule and justification must be documented.

---

## 9. Analysis Units and Concurrent Exposure

**STATUS: DESIGN PENDING**

FARS MUST NOT automatically assume that every trade in one file belongs to one homogeneous statistical population.

Potential analysis units include:

- strategy;
- instrument;
- strategy × instrument;
- portfolio;
- user-defined groups.

Pooling across strategies or instruments MUST be an explicit analysis decision.

When positions can overlap across instruments or strategies, FARS must consider whether individual-trade resampling would destroy important cross-position dependence.

Before portfolio-level dependent resampling is implemented, FARS must define whether the analysis operates primarily in:

- **trade-space**, using ordered trade events; or
- **time-space**, using returns/exposure aggregated by defined time intervals.

This decision is **DESIGN PENDING**.

---

## 10. Bootstrap and Resampling Framework

**STATUS: DESIGN PENDING**

FARS MUST NOT assume historical trades are IID merely because a dependence test fails to reject independence.

The design must determine whether IID/exchangeable resampling is sufficiently defensible for the intended analysis.

No single statistical test may authorize IID Bootstrap by itself.

### 10.1 Distinct Bootstrap Uses

FARS 1.2 MUST distinguish between at least these purposes:

1. **Trade-sequence resampling**  
   Generate plausible sequences from historical observations for path-dependent risk analysis.

2. **Bootstrap inference**  
   Estimate sampling uncertainty or confidence intervals for a statistic calculated from observed data.

3. **Bootstrap of simulation outputs**  
   Estimate uncertainty in statistics derived from Monte Carlo or simulated paths when appropriate.

Existing Core behavior MUST be inspected and reused where appropriate rather than duplicated under new names.

### 10.2 Candidate Resampling Methods

Candidate methods may include:

- IID Bootstrap;
- Block Bootstrap variants;
- other methods justified by the observed dependence structure and research question.

The selected method, assumptions, limitations, and important parameters MUST be recorded.

Block length or equivalent dependence parameters MUST NOT be chosen arbitrarily.

---

## 11. Probabilistic Risk Bounds and Extremes

**STATUS: DESIGN PENDING**

FARS may estimate distributions or probabilistic bounds for:

- drawdowns;
- losing/winning streaks;
- finite-horizon worst observed/resampled outcomes;
- trade-count requirements;
- uncertainty around metrics;
- other justified risk quantities.

Ordinary empirical Bootstrap MUST NOT be presented as estimating the absolute worst possible future trade.

Because empirical resampling draws from observed values, its conclusions about extremes are conditional on the observed sample and selected resampling model.

Any reported risk bound MUST define:

- the quantity being estimated;
- the horizon, if applicable;
- the resampling/model assumptions;
- the probability or confidence level;
- important limitations.

Methods intended to extrapolate beyond observed extremes are **DEFERRED** unless separately researched and approved.

---

## 12. Statistical Interpretation of Uncertainty

FARS SHOULD distinguish between:

1. **Process/path variability**  
   Different sequences that could arise under the selected model.

2. **Estimation uncertainty**  
   Uncertainty because only a finite historical sample is available.

3. **Model uncertainty**  
   Uncertainty about whether the chosen statistical model or resampling method adequately represents the data.

A percentile of simulated outcomes MUST NOT be described as though it were automatically a confidence interval.

---

## 13. Temporal Validation and OOS

**STATUS: DESIGN PENDING**

FARS will preserve chronological structure for temporal validation.

Random train/test splitting MUST NOT be used for time-dependent trade data merely for convenience.

Candidate methods include:

- chronological holdout;
- out-of-sample evaluation;
- walk-forward validation;
- rolling windows;
- expanding windows.

The methodology MUST prevent leakage from validation or final-test periods into calibration decisions.

The final OOS period SHOULD remain untouched until methodology and tunable parameters are fixed.

---

## 14. Stress Testing

**STATUS: DESIGN PENDING**

Stress testing may be added when it answers a clearly defined risk question.

Stress scenarios MUST NOT be chosen solely to improve apparent strategy performance.

The relationship between:

- historical resampling;
- synthetic perturbations;
- execution-cost assumptions;
- dependence assumptions;
- stress scenarios

must be explicitly defined before implementation.

---

## 15. Reproducibility and Run Manifest

All stochastic FARS 1.2 methods MUST support reproducible execution through explicit random seeds where applicable.

A fixed seed provides reproducibility, not statistical validity.

Each completed analysis SHOULD be capable of producing a run manifest containing, when applicable:

- FARS version;
- specification revision;
- input dataset identifier or hash;
- mapping configuration;
- accepted/rejected record counts;
- selected analysis subset;
- random seed;
- resampling method;
- resampling parameters;
- number of resamples/simulations;
- confidence level;
- analysis horizon;
- execution-cost assumptions;
- relevant diagnostic decisions;
- warnings and limitations.

The exact manifest format remains **DESIGN PENDING**.

---

## 16. Statistical Safeguards

FARS 1.2 inherits the statistical safeguards in `FARS_SPEC.md` and `AGENTS.md`.

In particular:

- do not assume IID without justification;
- do not assume normality;
- do not equate historical maximum drawdown with the worst possible future drawdown;
- do not randomly split time-dependent data without justification;
- prevent FARS-created look-ahead bias;
- prevent FARS-created data leakage;
- prevent overfitting;
- prevent final-test contamination;
- separate calibration, validation, and final OOS evaluation;
- document assumptions and limitations.

FARS cannot automatically guarantee that imported strategy histories were generated without upstream bias.

Statistical correctness takes priority over favorable-looking results.

---

## 17. Testing Requirements

FARS 1.2 additions MUST include tests appropriate to their software and statistical behavior.

Passing unit tests alone is not sufficient evidence that a statistical implementation is valid.

Tests SHOULD include, where applicable:

- deterministic correctness;
- malformed input and schema edge cases;
- missing-data behavior;
- reproducibility;
- chronological ordering;
- timezone/session behavior;
- leakage prevention;
- statistical sanity checks;
- resampling invariants;
- capability gating;
- provenance;
- concurrent-position edge cases;
- discovered bugs and ambiguous behavior.

Specific acceptance tests will be defined with each finalized requirement.

---

## 18. Non-Goals

Unless explicitly approved later, FARS 1.2 is not intended to:

- predict future market direction;
- generate discretionary BUY/SELL signals;
- guarantee profitability;
- prove that a strategy has a persistent future edge;
- certify that an imported backtest is free of upstream methodological bias;
- optimize specifically for SMC-FVG or SMC-OB;
- invent missing trade risk or execution costs;
- hide uncertainty behind a single deterministic risk number;
- treat backtest results as equivalent to live execution;
- extrapolate absolute future extremes from ordinary empirical Bootstrap;
- add complex methods when simpler justified methods are adequate.

---

## 19. Agent and Development Workflow

Agents working on FARS 1.2 MUST read:

1. `AGENTS.md`
2. `FARS_SPEC.md`
3. `FARS_1_2_SPEC.md`
4. relevant current implementation and tests

Normal workflow:

```text
Implementation
    → tests
    → independent Codex review
    → verify valid findings
    → corrections
    → tests
    → new review
    → REVIEW PASSED
    → commit / merge / push only when explicitly requested
```

When the same model family implements and reviews, separate sessions SHOULD be used where practical.

Implementation agents MUST NOT turn design notes, dataset quirks, or speculative future ideas into requirements.

For the current REVIEW DRAFT, Codex SHOULD review the specification for contradictions, missing requirements, statistical weaknesses, Core incompatibilities, and unnecessary complexity before implementation begins.

---

## 20. Future Extensions

Possible future extensions may include contextual information such as economic events or news classifications when a statistically defensible research question is defined.

Such metadata must remain optional unless explicitly approved.

No item in this section is an implementation requirement.

---

## 21. Open Design Decisions and Resolution Order

Open decisions are grouped by dependency so that advanced statistical work does not block basic data architecture.

### Phase A — Ingestion and Data Contract

Resolve first:

1. Canonical trade schema.
2. Meaning of one canonical record.
3. Fill/order/round-trip consolidation policy.
4. Minimum data required for each analysis capability.
5. External column mapping configuration.
6. Missing-value and duplicate policies.
7. Gross versus net PnL.
8. Commission and slippage handling.
9. Timestamp, timezone, and trading-session policy.
10. Provenance requirements.
11. Dataset audit output.
12. Analysis-unit rules.

### Phase B — Statistical Engine Extensions

Resolve after Phase A:

1. Temporal-dependence diagnostics.
2. Criteria for defensible IID resampling.
3. Trade-space versus time-space treatment where concurrency matters.
4. Block Bootstrap variant when required.
5. Block-length methodology.
6. Bootstrap interval methodology.
7. Sample-adequacy/gating rules by analysis.
8. Formal definitions of probabilistic risk bounds.
9. Treatment of process, estimation, and model uncertainty.

### Phase C — Validation and Interface

Resolve after the required parts of A and B:

1. OOS protocol.
2. Walk-forward protocol.
3. Stress-testing methodology.
4. CLI commands and configuration.
5. Run manifest format.
6. Reporting behavior.
7. Optional external contextual metadata.

These items are intentionally unresolved until discussed and approved.

---

## 22. Review Gate Before Implementation

FARS 1.2 implementation SHOULD NOT begin until the review of this specification determines that:

1. Core compatibility boundaries are clear.
2. Phase A contains enough detail to implement the first scoped component.
3. No unresolved statistical assumption is being silently treated as fact.
4. Acceptance criteria exist for the component being implemented.
5. Any remaining `DESIGN PENDING` section outside that component can remain unresolved without affecting correctness.

Codex should return concrete findings classified according to `AGENTS.md`.

A passing specification review does not approve every future section for implementation. Each unresolved component still requires its own finalized requirements.

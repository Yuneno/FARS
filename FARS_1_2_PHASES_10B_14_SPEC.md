# FARS 1.2 — Specification for Phases 10B–14

## 0. Status and authority

**Status:** Approved formal specification (2026-08-28).

**Baseline reviewed:** `main` at commit
`0be1fc635125823ef3c50016f727a381d3ff8a5f` (2026-08-23).

This document extends `FARS_1_2_SPEC.md`. It does not replace `FARS_SPEC.md`,
`FARS_1_2_SPEC.md`, or `AGENTS.md`. Existing requirements and safeguards remain
binding unless this document explicitly resolves a previously open design
decision.

This roadmap preserves the existing numbering:

- Phase 10B finishes and exposes the reviewed Phase 10A work.
- Phase 11 is expanded into subphases 11A–11D for real-account data,
  funded-account rules, probabilistic limits, and the first provider adapter.
- Phase 12 remains temporal/OOS validation.
- Phase 13 remains stress testing.
- Phase 14 adds operational reporting without trade execution.

Implementation agents MUST NOT invent additional requirements. A rule that is
ambiguous, provider-dependent, or unsupported by available data must remain
explicitly unresolved rather than guessed.

---

## 1. Purpose

The next development cycle will connect the existing FARS statistical Core to
real funded-account evaluation data while preserving strategy independence.

The first concrete validation profile is the user's MyFundedFutures Rapid 25K
evaluation on Tradovate/TradingView. This is a validation case, not the system's
architecture.

FARS remains a risk-analysis system. It MUST NOT:

- predict market direction;
- generate BUY/SELL signals;
- place, modify, or cancel orders;
- guarantee that an evaluation will pass;
- treat simulated or historical performance as live-market evidence;
- optimize rules specifically to make Rapid 25K results look favorable.

---

## 2. Current baseline and stabilization gate

The reviewed commit already contains:

- FARS Core phases 1–7;
- Phase 8A CSV ingestion and audit;
- Phase 9A CLI commands `fars audit` and `fars metrics`;
- Phase 10A v6 IID/Circular Block Bootstrap implementation;
- deterministic and statistical test suites.

The following limitations are confirmed at the baseline:

1. Phase 10A is not exposed through the CLI.
2. Historical ingestion requires a finalized `r_result`; it does not define
   monetary P&L, commissions, fees, slippage, or Tradovate semantics.
3. `FundedAccountRules` represents percentage targets, percentage drawdowns,
   and a mandatory daily loss limit. This cannot faithfully encode Rapid 25K.
4. `AccountState` updates from closed-trade R outcomes. It cannot enforce an
   intraday equity rule without intraday equity or position events.
5. Five deterministic tests fail on the reviewed baseline because CSV
   delimiters `"`, newline, and carriage return are accepted despite the test
   contract rejecting them.
6. Dependency ranges are broad for NumPy, SciPy, pandas, and matplotlib, while
   Phase 10A reproducibility is version-dependent.
7. No GitHub Actions workflow is present to enforce tests automatically.
8. `FARS_1_2_SPEC.md` still lists several Phase 10 design decisions as open even
   though Phase 10A v6 resolves them.

No later phase may be declared complete until the Phase 10B stabilization gate
passes.

---

## 3. Cross-phase invariants

### 3.1 Backward compatibility

- The existing `Trade`, `FundedAccountRules`, ingestion API, Core simulations,
  and validated outputs must remain compatible unless a breaking change is
  explicitly approved.
- Real-account functionality should be added through small new interfaces or a
  compatibility adapter, not by silently changing legacy meanings.
- Historical R-based analysis and monetary account analysis must remain
  distinguishable.

### 3.2 Units and provenance

Every numeric field must have an unambiguous unit: R-multiple, account currency,
price points, ticks, contracts, or percentage.

Every derived value must record:

- source field(s);
- formula and sign convention;
- currency and timezone;
- whether it was supplied, mapped, or derived;
- relevant rule-profile and algorithm versions.

Values with incompatible units MUST NOT be combined silently.

### 3.3 Provider isolation

Provider-specific rules belong in versioned profiles/adapters. Generic Core
classes must not contain names or branches such as `if provider == "MFFU"`.

### 3.4 Reproducibility

All stochastic analyses require explicit seeds. A reproducible environment or
constraints file must record the dependency versions used for acceptance.

### 3.5 Security and privacy

- Account credentials, passwords, session tokens, and API keys must never be
  stored in datasets, configuration profiles, logs, reports, fixtures, or Git.
- Account identifiers should be redacted or replaced with local aliases in
  shareable outputs.
- Phase 14 remains read-only; automated execution is out of scope.

---

# Phase 10B — Stabilization and Bootstrap CLI

**Implementation status:** implemented; independent review pending.

## 10B.1 Objective

Establish a clean, reproducible baseline and make the already-reviewed Phase
10A analysis usable from the command line without adding new statistical
methods.

## 10B.2 Required work

1. Fix delimiter validation consistently in both ingestion and CLI layers.
   At minimum, reject empty, multi-character, quote, newline, carriage return,
   and any value incompatible with the selected CSV dialect.
2. Run all non-statistical tests and obtain zero failures.
3. Run the complete predeclared Phase 10A statistical acceptance suite without
   modifying seeds, generators, thresholds, or acceptance limits after seeing
   results.
4. Record the exact Python, NumPy, SciPy, pandas, and `arch` versions used for
   acceptance. Add an appropriate reproducible constraints/lock mechanism.
5. Add continuous integration for the supported Python versions. Slow
   statistical acceptance may run separately from fast unit tests.
6. Reconcile documentation:
   - remove resolved Phase 10 items from the open-decisions list;
   - make FARS 1.0/1.2 version naming consistent;
   - place calibration experiments under an intentional research/experiments
     location rather than a `scratch_*` production path.
7. Define and implement a CLI contract:

```text
fars bootstrap trades.csv --outcomes-finalized --seed INTEGER
               [--replicates INTEGER] [shared ingestion options]
               [--format text|json]
```

The command MUST reuse `load_trade_csv()` and `analyze_bootstrap()`. It must not
duplicate diagnostics, capability checks, or resampling logic.

## 10B.3 Acceptance

- All deterministic tests pass.
- All predeclared statistical criteria pass on an untouched acceptance root.
- Invalid delimiter behavior is identical through Python API and CLI.
- Same accepted input, seed, parameters, implementation, platform, and locked
  dependencies produce bit-identical JSON.
- `fars bootstrap` refuses data without `core_metrics` and
  `temporal_analysis` capabilities and returns documented exit codes.
- No source CSV is modified.

---

# Phase 11A — Canonical Monetary Trade and Account-Event Contract

## 11A.1 Objective

Represent real account history without weakening or redefining the existing
R-based canonical trade contract.

## 11A.2 Separate canonical records

Introduce a distinct monetary record, provisionally named
`CanonicalAccountTrade`. Do not silently add monetary semantics to the existing
`Trade.r_result`.

Candidate canonical fields:

- `trade_id`;
- `opened_at` and `closed_at` as timezone-aware timestamps;
- `instrument` and contract denomination;
- `direction`;
- `quantity`;
- `entry_price` and `exit_price`;
- `gross_pnl`;
- `commission`;
- `exchange_fees`;
- `other_fees`;
- `net_pnl`;
- `initial_risk_amount`;
- optional externally supplied `r_result`;
- `strategy`;
- raw provider metadata and provenance.

Exact required/optional status must be capability-based. Basic account P&L may
be available while R-based or rule-replay analysis is unavailable.

## 11A.3 Monetary semantics

- Currency must be explicit.
- Costs use non-negative magnitudes unless a provider contract explicitly uses
  signed values.
- The standard relationship is
  `net_pnl = gross_pnl - commission - exchange_fees - other_fees`.
- FARS may verify this identity within an explicitly defined currency tolerance.
- FARS must not silently label gross P&L as net P&L.
- Slippage is not inferred from P&L. Modeled slippage must remain a separate
  scenario input.

## 11A.4 R derivation

FARS may derive
`r_result = net_pnl / initial_risk_amount` only when:

- `initial_risk_amount` is finite and strictly positive;
- `net_pnl` is valid under the approved cost contract;
- the result is labeled `derived` with the formula and source fields;
- externally supplied and derived R values are not silently mixed.

Missing initial risk means R is unavailable; it must not be guessed from stop
distance unless contract value, tick value, quantity, and stop semantics are
all approved and present.

## 11A.5 Fills, orders, and round trips

A provider export may contain fills rather than completed trades. Grouping
fills into round trips is a separate deterministic transformation. FIFO, LIFO,
average-price, scale-in/out, reversal, and partial-fill semantics must be
specified and tested before use. Phase 11A may initially accept only exports
that already identify completed round trips.

## 11A.6 Account events

Define a separate optional `AccountEquityEvent` for rules that observe equity
while positions are open. A closed-trade stream cannot reconstruct an intraday
equity high-water mark. If required events are absent, intraday rule replay must
return `unsupported`, not an approximation presented as exact.

## 11A.7 Acceptance

- Raw provider rows and normalized records are traceable through fingerprints.
- Gross/net/cost sign conventions have deterministic tests.
- Missing optional monetary fields degrade capabilities without deleting usable
  rows.
- Duplicate, partial-fill, reversal, and timezone cases are covered.
- No R value is invented from insufficient information.

---

# Phase 11B — Generic Funded-Account Rule Engine v2

## 11B.1 Objective

Represent modern funded-account rules without encoding one provider in the
Core and without breaking the legacy rules engine.

## 11B.2 Required rule capabilities

The v2 rule contract must support:

### Profit target

- absolute currency amount or explicitly defined percentage;
- exact comparison boundary;
- optional minimum trading-day requirement.

### Maximum-loss/drawdown rule

- absolute currency distance or percentage distance;
- static or trailing behavior;
- reference based on balance or equity;
- update cadence: closed trade, end of day, or intraday event;
- optional lock/floor where the threshold stops moving;
- explicit boundary semantics (`<` versus `<=`);
- explicit session timezone and end-of-day definition.

### Daily-loss rule

- optional/absent;
- absolute or percentage amount;
- balance/equity reference;
- reset timezone and session boundary;
- hard breach versus soft pause.

### Consistency rule

- ratio definition and rounding policy;
- evaluation window;
- whether exceeding it breaches the account or merely delays passing;
- zero/negative total-profit behavior.

For a 50% best-day rule, the generic calculation is:

```text
best_positive_day / total_positive_evaluation_profit <= 0.50
```

The exact provider denominator must be verified before enabling a profile.

### Operational constraints

- maximum minis and micros;
- mixed-contract equivalence when defined;
- minimum trading days;
- permitted session and forced-close time;
- news permission/restriction metadata;
- inactivity window;
- optional provider-specific advisory constraints.

Constraints that cannot be evaluated from available data must return
`not_evaluable` with a reason.

## 11B.3 Architecture

- Preserve `FundedAccountRules` as the legacy percentage contract.
- Add a v2 immutable rule profile and a separate mutable account state.
- Rule evaluation should return structured events such as `pass_eligible`,
  `breach`, `soft_pause`, `consistency_block`, and `not_evaluable`.
- Simultaneous violations require deterministic priority and full disclosure.
- Provider profiles must contain rule-source URL, retrieval date, profile
  version, and unresolved assumptions.

## 11B.4 Rapid 25K reference profile

The first provider profile is based on the rules available on 2026-08-27:

| Parameter | Rapid 25K evaluation value |
|---|---:|
| Starting balance | USD 25,000 |
| Profit target | USD 1,500 |
| Maximum-loss distance | USD 1,000 |
| Daily loss limit | None |
| Maximum size | 3 minis / 30 micros |
| Consistency | 50% during evaluation |
| Minimum trading days | 2 |
| News trading | Allowed |
| Inactivity | 7 consecutive calendar days |
| Reported lock level | USD 25,100 |

Primary rule sources:

- https://help.myfundedfutures.com/en/articles/14116402-rapid-plan-25k-a-comprehensive-look
- https://help.myfundedfutures.com/en/articles/11994562-consistency-rule-at-my-fundedfutures
- https://help.myfundedfutures.com/en/articles/11972075-inactivity-rule

The following are **blocking open questions** before enabling exact replay:

1. Whether the evaluation threshold updates only at end of day or from intraday
   equity. Current provider wording must be reconciled and frozen.
2. Whether touching the threshold or only crossing below it constitutes breach.
3. Exact calculation and rounding of the 50% consistency denominator.
4. Session timezone, holiday behavior, and trading-day boundary.
5. Contract-equivalence rules when minis and micros are mixed.

The profile must remain disabled or marked provisional until these questions
are resolved from current provider terms, the account dashboard, or written
support confirmation.

## 11B.5 Acceptance

- Legacy simulations remain unchanged for the same inputs and seeds.
- Absolute and percentage rules have independent boundary tests.
- No-daily-limit accounts are representable without fake large values.
- Trailing, lock/floor, EOD, and intraday-event fixtures are deterministic.
- Consistency can block passing without falsely recording a breach.
- Missing intraday events cannot produce an exact intraday replay.

---

# Phase 11C — Probabilistic Account Paths and Trade Limits

## 11C.1 Objective

Estimate, with explicit assumptions and uncertainty, how a historical strategy
distribution interacts with a funded-account rule profile.

## 11C.2 Inputs

The analysis requires:

- an audited historical trade dataset;
- an eligible resampling method from Phase 10A;
- a versioned funded-account profile from Phase 11B;
- an explicit risk-sizing policy;
- explicit cost assumptions;
- an explicit seed and simulation count.

## 11C.3 Path generation

- Use IID resampling only when Phase 10A returns `iid_eligible`.
- Use CBB only as `exploratory_dependent` when dependence is detected and the
  assumed short-memory/stationary conditions remain plausible.
- Structural-change or unsupported datasets must not silently fall back to IID.
- Each simulated path ends in `pass`, `breach`, or a clearly defined censoring
  state such as `max_trades_reached`.
- Consistency and trading-day requirements are evaluated along the entire path,
  not only at final equity.

## 11C.4 Required outputs

- estimated probability of pass, breach, and censoring;
- confidence intervals for Monte Carlo error;
- trades and trading days to pass, conditional on passing;
- drawdown and loss-budget consumption distributions;
- losing/winning streak distributions;
- best-day consistency distribution;
- sensitivity across a predeclared risk grid;
- assumptions, limitations, dataset fingerprint, rule-profile version, and RNG
  provenance.

The term `probability of passing` is conditional on the observed dataset,
resampling model, costs, sizing policy, and provider rules. It is not a promise.

## 11C.5 Probabilistic trade limits

Every trade limit must name its estimand. Examples include:

- number of trades required to reach the target with probability at least p;
- maximum planned risk such that estimated breach probability is below q;
- loss-budget percentile over an h-trade horizon.

Historical maxima must not be described as worst possible future outcomes.
Ordinary empirical bootstrap cannot generate unseen tail values; stress testing
in Phase 13 must address that limitation separately.

## 11C.6 Optimization safeguards

- Risk-grid selection and tuning use calibration data only.
- Validation and final OOS periods must not select the winning risk level.
- Report the full curve and uncertainty, not only the apparent optimum.
- Prefer a stable plateau over a narrow noisy maximum when a decision rule is
  later approved.

## 11C.7 Acceptance

- Known toy processes recover expected terminal outcomes.
- Rule paths are reproducible for fixed seeds.
- IID, CBB, unsupported, and structural-change routes are tested.
- Consistency, lock/floor, costs, and minimum-day logic have exact fixtures.
- No optimization reads validation or final OOS results.

---

# Phase 11D — Tradovate Adapter and Rapid 25K Prospective Validation

## 11D.1 Objective

Ingest the user's actual Tradovate history, replay the Rapid 25K evaluation, and
compare observed account behavior with FARS estimates.

## 11D.2 Adapter workflow

1. Obtain a redacted sample export before finalizing the mapping.
2. Identify whether rows represent orders, fills, executions, positions, or
   completed round trips.
3. Define a source-to-canonical mapping and provider-version identifier.
4. Preserve every unknown column and raw row.
5. Reconcile totals against the platform statement before analysis.
6. Store no credentials or session tokens.

The adapter may begin with manual CSV export. Direct API integration is not a
requirement for FARS 1.2.

## 11D.3 Prospective validation protocol

- Freeze the initial strategy/risk policy before inspecting evaluation results
  for tuning purposes.
- Treat the account stream as prospective evidence.
- Do not feed account outcomes back into calibration during the same evaluation.
- Compare forecast ranges with observed P&L, drawdown, streaks, days, costs, and
  rule states.
- Report deviations and model failures; do not alter assumptions to make the
  account appear predicted after the fact.

## 11D.4 Acceptance

- Platform totals reconcile to the imported journal within explicit tolerance.
- Every account day and rule transition is reproducible from source records.
- Unsupported intraday replay is labeled honestly.
- Account evaluation data remain isolated from calibration.
- A redacted report can be produced without exposing credentials or account ID.

---

# Phase 12 — Temporal and Out-of-Sample Validation

## 12.1 Objective

Evaluate whether historical risk estimates remain useful across time, assets,
and strategy variants without leakage.

## 12.2 Required protocol

- Preserve chronological order.
- Predeclare calibration, validation, and final OOS windows.
- Use rolling or expanding walk-forward analysis only under an approved window
  contract.
- Keep SMC-FVG and SMC-OB identifiable.
- Analyze MNQ, YM, ES, and GC separately before any pooled interpretation.
- Do not assume simultaneous trades across assets are independent.
- Do not tune thresholds, block lengths, costs, or risk grids on final OOS data.

## 12.3 Outputs

- stability of expectancy, variance, win rate, drawdown, and streak estimates;
- method/eligibility changes across windows;
- pass/breach probability drift under a frozen rule profile;
- asset/strategy heterogeneity;
- explicit evidence of degradation or structural change.

## 12.4 Acceptance

- Tests demonstrate that future rows cannot influence earlier windows.
- Final OOS remains untouched until all decisions are frozen.
- Pooled results never replace required component results.
- Negative or inconclusive results are reported without re-optimization.

---

# Phase 13 — Stress Testing

## 13.1 Objective

Measure account robustness under adverse but transparent scenarios that
historical bootstrap alone cannot represent.

## 13.2 Initial scenario families

- higher commissions and exchange fees;
- explicit adverse slippage;
- degraded expectancy;
- increased variance and heavier losses;
- longer dependence blocks or clustered losses;
- reduced trade frequency;
- provider-rule changes represented by a new profile version.

Scenarios must be predeclared and justified. They must not be selected to make
the strategy pass.

## 13.3 Outputs and acceptance

- Report baseline and stressed results side by side.
- Identify which assumptions drive pass/breach changes.
- Separate historical-resampling uncertainty from hypothetical stress severity.
- Never assign an empirical probability to a stress scenario unless a valid
  model supports that probability.

---

# Phase 14 — Read-only Account Reporting

## 14.1 Objective

Provide a simple operational view of account state without generating trading
signals or executing orders.

## 14.2 Proposed CLI

```text
fars account audit account.csv --profile PROFILE
fars account status account.csv --profile PROFILE
fars account simulate trades.csv --profile PROFILE --seed INTEGER
```

## 14.3 Status output

Where supported, report:

- current balance and cumulative net P&L;
- profit remaining to target;
- current loss threshold and remaining buffer;
- best day and consistency requirement;
- completed trading days;
- profile version and unresolved rules;
- data freshness and last imported trade;
- warnings for unavailable capabilities.

The report must use descriptive language such as `within configured limit`,
`near configured threshold`, or `not evaluable`. It must not tell the user to
enter, exit, buy, sell, increase size, or chase a target.

## 14.4 Acceptance

- Text and JSON outputs agree.
- Undefined fields are `null`, never NaN or invented zeroes.
- Stale input is clearly labeled.
- The CLI is read-only and never contacts a broker to place orders.

---

## 15. Recommended implementation order

```text
10B Stabilization + Bootstrap CLI
    -> 11A Monetary/account-event contract
    -> 11B Generic rules engine v2
    -> 11C Probabilistic account paths
    -> 11D Tradovate + Rapid 25K prospective validation
    -> 12 Temporal/OOS validation
    -> 13 Stress testing
    -> 14 Read-only reporting
```

Each phase follows the normal workflow:

```text
approved contract
    -> implementation
    -> deterministic/statistical tests
    -> independent Codex review
    -> valid corrections
    -> full tests
    -> REVIEW PASSED
    -> commit/merge/push only when explicitly requested
```

---

## 16. Decisions intentionally deferred

The following remain unresolved until the relevant phase begins:

1. Exact Tradovate export schema and round-trip reconstruction.
2. Exact Rapid 25K drawdown update cadence and breach boundary.
3. Exact consistency denominator and rounding.
4. Contract/tick-value source and mixed minis/micros equivalence.
5. Slippage scenario model.
6. Walk-forward window lengths.
7. Stress magnitudes and scenario weights.
8. Any direct provider API integration.
9. External news/event metadata.
10. Any automated or predictive trading functionality, which remains outside
    FARS 1.2 unless separately approved.

---

## 17. Definition of FARS 1.2 completion

FARS 1.2 may be considered complete when:

- the baseline is stable and reproducible;
- external R-based and monetary trade histories are auditable;
- generic funded-account rules can represent Rapid 25K without provider logic
  in the Core;
- probabilistic pass/breach analysis is assumption-aware and reproducible;
- calibration, validation, and prospective account evaluation are separated;
- stress testing exposes historical-bootstrap limitations;
- the CLI produces traceable read-only reports;
- all required reviews and acceptance tests pass.

Passing one funded evaluation is not the definition of FARS correctness.
Correctness is faithful data handling, rule representation, statistical
validity, reproducibility, and honest uncertainty.

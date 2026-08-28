# FARS 1.2 — Review of commit `0be1fc6351`

## Review scope

- Repository: `Yuneno/FARS`
- Branch: `main`
- Commit: `0be1fc635125823ef3c50016f727a381d3ff8a5f`
- Commit message: `Implement FARS 1.2 ingestion, CLI, and bootstrap validation`
- Review date: 2026-08-27
- Review mode: read-only reconstruction of the committed files; no repository
  writes, commits, merges, or pushes.

## Outcome

**REVIEW NOT PASSED.**

The implementation is substantial and most deterministic behavior is covered,
but the committed baseline has five reproducible test failures. The failure is
narrow and repairable; no evidence currently justifies discarding or rewriting
the implemented Phase 8A–10A architecture.

## Verification performed

The commit was reconstructed in an isolated Python 3.12 environment from the
GitHub file contents and installed using `pip install -e ".[dev]"`.

Fast/deterministic suite:

```text
590 tests collected
10 statistical tests deselected
580 deterministic tests executed
575 passed
5 failed
```

Phase 10A statistical acceptance suite:

```text
10 statistical tests executed
10 passed
Runtime: 974.06 seconds (16:14)
```

Reviewed environment:

```text
Python 3.12.13
NumPy 2.5.2
SciPy 1.18.1
pandas 3.0.5
arch 8.0.0
matplotlib 3.11.1
pytest 9.1.1
```

Lint diagnostic:

```text
ruff check . -> 76 diagnostics
```

Most lint diagnostics are formatting, import ordering, or style suggestions.
They are not treated as proof of statistical or runtime failure, but the
project currently lacks a clean enforceable lint baseline.

## Findings

### WARNING 1 — Invalid delimiters are accepted

Affected areas:

- `src/ingestion.py`
- `src/cli.py`
- `tests/test_ingestion.py`
- `tests/test_cli.py`

The code delegates validation to `csv.reader([], delimiter=value)`. In the
reviewed Python 3.12 runtime this constructor accepts quote, newline, and
carriage return as delimiters. The project's tests explicitly require those
values to be rejected.

Observed failures:

- two domain-level ingestion cases;
- three CLI usage-error cases.

Impact:

- malformed or ambiguous CSV configuration can pass initial validation;
- the CLI and Python API violate their own deterministic contract;
- the commit cannot receive `REVIEW PASSED`.

Required correction:

- define one shared delimiter validator;
- explicitly reject quote, CR, LF, empty, multi-character, and dialect-conflict
  values;
- reuse it in ingestion and CLI;
- rerun the complete test suite.

### WARNING 2 — Acceptance environment is not fully locked

`arch==8.0.0` is pinned, but NumPy, SciPy, pandas, matplotlib, and pytest use
open lower-bounded ranges. Phase 10A correctly records installed versions and
limits bit-identical claims to locked environments, yet the repository does
not currently provide a complete lock/constraints artifact for recreating its
acceptance environment.

Impact:

- a future installation may run different statistical/library behavior;
- reproducing the original statistical acceptance may be harder than intended.

Required correction:

- retain reasonable package compatibility ranges in `pyproject.toml` if
  desired;
- add a tested constraints/lock artifact or equivalent reproducible acceptance
  environment;
- record that environment with the acceptance results.

### WARNING 3 — No automated repository test gate

The reviewed tree contains no GitHub Actions workflow. A large commit can be
pushed even when deterministic tests fail in a supported environment.

Required correction:

- add a fast CI job for deterministic tests;
- run slow statistical acceptance separately or on an explicitly chosen
  release/review workflow;
- do not allow a green fast job to imply statistical acceptance.

### WARNING 4 — Current funded-account model cannot represent Rapid 25K

This is a scope limitation rather than a violation of the original Core spec.
The legacy `FundedAccountRules` requires percentage target/drawdown/daily-loss
values. Rapid 25K requires an absolute loss distance, no daily loss limit,
consistency, minimum days, contract limits, and a trailing threshold with a
reported lock.

Impact:

- a Rapid 25K configuration made from the current class would be an
  approximation, not faithful rule replay;
- closed-trade R outcomes cannot enforce a genuinely intraday equity rule.

Resolution:

- implement the separate real-account data and rule contracts defined in
  `FARS_1_2_PHASES_10B_14_SPEC.md`;
- preserve the legacy rules engine for backward compatibility.

### SUGGESTION 1 — Reconcile documentation state

- `FARS_1_2_SPEC.md` still lists Phase 10 decisions as open after Phase 10A v6
  resolved them.
- The README mixes v0.1, FARS 1.0, and FARS 1.2 terminology.
- The implemented Phase 10A design file retains `DRAFT` in its filename.
- Calibration scripts live under `scratch_phase10_diag`; an intentional
  `research/` or `experiments/` location would better signal their status.

### SUGGESTION 2 — Establish a deliberate lint baseline

The current 76 Ruff diagnostics should be triaged separately from functional
changes. Auto-fixing everything inside a statistical change would create noisy
diffs. Address high-signal issues first, then adopt an explicit CI rule set.

## Positive observations

- Phase 8A separates accepted rows from analysis capability, reducing silent
  selective-sample analysis.
- Input order, timezone requirements, duplicate IDs, provenance, and finalized
  outcome attestation are handled explicitly.
- Phase 10A does not claim to prove IID or stationarity.
- IID and dependent resampling results carry different validity labels.
- Seeds, child streams, algorithms, dependency versions, and input fingerprints
  are recorded.
- All 10 predeclared Phase 10A statistical acceptance tests pass in the
  reviewed environment.
- Deterministic coverage is broad: 575 tests pass in the reviewed environment.
- The new work should extend these interfaces; a Core rewrite is not justified.

## Required next action

Phase 10B should begin with the five delimiter failures, then rerun:

1. all deterministic tests;
2. the untouched statistical acceptance suite;
3. package/CLI smoke tests in the locked acceptance environment.

Only after those pass should a new independent review decide whether the
baseline earns `REVIEW PASSED`.

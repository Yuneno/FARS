# C3 blockers and ambiguities

## Git branch creation

Command attempted before any C3 run:

```powershell
git switch -c bloque-c3-cierre
```

It failed with `cannot lock ref ... Permission denied`. Exact question for the
reviewer/operator: **Can the branch `bloque-c3-cierre` be created outside this
sandbox from `main` at `fcd1c467ab4860800e9732f8fb04e88b2a8c613b` before the
final local commits are made?** Work continues uncommitted on the requested
base commit; no `.git` file is touched directly.

The same branch command was retried after all verification and failed with the
same lock error, so `git add` and `git commit` were deliberately not run on
`main`.

## C2 cardinality

C2's closing text says “8 configurations (3 baselines + 5 candidates)”, while
the executable runner contains seven configurations. The eighth registry row
is `crt4h_baseline_absent`, explicitly non-executable. Exact question: **Should
an absent, parameterless baseline be treated as an eighth PBO competitor?** It
cannot produce a rank metric without inventing results, so C3 preserves all
eight registry rows but preregisters the seven executed trials as the PBO
universe.

## Existing parallel runner compatibility

`src.parallel.run_parallel` currently accepts only `breakout_v1`/`breakout`;
it cannot execute the preregistered EMAS, SMC-FVG, or CRT 4H configurations.
C3 therefore generated each frozen full-period trade stream once and computed
the 220 purged/embargoed path aggregations in memory. The CPCV stage took
19.32 seconds, far below the 60-minute fallback threshold. Exact question:
**Is a new C3-only process-pool adapter required even though it would not
change any path and the entire CPCV is already below 20 seconds?** No production
parallel runner or supported-strategy list was changed.

## LIMIT gap wording

The phrase “abre más allá del nivel” does not name the direction. Exact
question: **For a resting buy/sell LIMIT, does “beyond” mean a favorable gap
through the limit (buy open below / sell open above)?** C3 preregisters that
conservative interpretation and reports its exact effect; no executor or
signal code is changed.

## Sandbox temporary-directory permissions

The first full-suite run produced `1457 passed, 2 skipped, 1 failed`; the sole
failure was `test_installed_fars_binary_runs_outside_checkout`. A direct retry
showed pip failing to create its build tracker under a sandbox directory made
with mode `0700`. A temporary, subsequently deleted `sitecustomize.py` forced
Python subprocess temporary directories into the workspace and `0777` mode;
the focused test then passed and the complete suite finished with
`1458 passed, 2 skipped`. This changes no repository or test behavior outside
the sandbox permission workaround.

Cleanup was attempted only for the exact C3 temporary directories after
validating that each resolved beneath `E:/FARS-LAB/FARS/lab_artifacts`; the
sandbox rejected the recursive `Remove-Item` command before execution. Exact
question: **Can the operator remove the `pytest-c3-*`, `c3-temp*`,
`c3-pip-diagnostic*`, and empty `c3_test_sitecustomize` directories under
`lab_artifacts` after review?** They are unversioned test scratch space, not C3
evidence.

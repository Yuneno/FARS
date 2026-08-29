# Review: origin/code_avance (Codex)

Branch: `code_avance` @ `9bdc999`
Worktree: `/private/tmp/fars-codex-phase11a`
Scope: Phase 11A monetary trades + Phase 11B rules v2
Reviewer: Hermes. Not Codex.

REVIEW FAILED

## What is good

11A and 11B implementation is serious and mostly matches the spec.

11A
- Separate `CanonicalAccountTrade` (Decimal, explicit currency). Does not touch Core `Trade.r_result`.
- Fingerprints on raw row + normalized record.
- Costs non-negative; net = gross - costs with explicit tolerance.
- R derived only from verified net / positive initial risk, labeled derived.
- Fills/partials/reversals refused, not guessed.
- Missing fields degrade capabilities; rows kept.
- Rapid tests: 25 in test_account_data.py.

11B
- Legacy `FundedAccountRules` left alone.
- Immutable profile + mutable `FundedAccountStateV2`.
- Rapid 25K exists, `enabled=False`, five unresolved assumptions listed, sources dated.
- Enabled profile cannot be constructed with unresolved semantics.
- No-daily-limit without fake sentinels.
- Consistency can block pass without breach.
- Missing intraday coverage cannot exact-pass.
- Tests: 27 in test_funded_rules_v2.py.

I ran `tests/test_account_data.py tests/test_funded_rules_v2.py tests/test_package.py`: 78 passed.

No `if provider == "MFFU"` in Core.

## CRITICAL (1)

Spec/README in the same commit mark:

- Phase 10B "independent review passed (2026-08-29)"
- Phase 11A "implemented; review passed"
- FARS_1_2_SPEC §11 "11A implemented and reviewed"

That review is not an independent artifact on this branch. Codex implemented and then stamped its own work as reviewed. 11B status ("independent review pending") is the honest one. 11A/10B status lines should be reverted to pending until a real review lands.

## WARNING (2)

1. Disabled Rapid 25K still runs profit-target / min-days logic. `not_evaluable` (priority 3) loses to `pass_blocked` (priority 2), so `primary_event` can look like "almost passed" instead of "profile disabled". `pass_eligible` is correctly gated on `enabled`.
2. Disabled/provisional profiles still mutate balances, watermarks, and trading-day sets. Fine for a sandbox; not fail-closed isolation.

## SUGGESTION

- Split 1100+ line modules later.
- Do not mix this branch with FARS-Hermes realtime (RT-0..RT-3) without a merge plan.

## Bottom line

Keep the 11A/11B code. Do not treat the spec "review passed" stamps as real. Fix primary-event priority for disabled profiles before calling 11B done.

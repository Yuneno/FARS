# FARS 1.2 — Phase 10A Bootstrap Framework (DESIGN v6)

**STATUS: v4 IMPLEMENTED; v5 INDEPENDENTLY REJECTED; v6 INDEPENDENTLY
REVIEWED, CONFIRMED, AND IMPLEMENTED.** V4 acceptance exposed two infeasible
criteria (section 0.1). V5 corrected those issues but failed its independent
IID-size confirmation (section 0.7). V6 retains the valid v5 corrections and
replaces its raw-score portmanteau diagnostics with rank-based normal scores
(sections 0.8–0.10). Drawdown distributions, streaks, extremes, future paths,
and probabilistic trade limits are **Phase 11** and are out of scope here.

---

## 0. Revision History and Effective V6 Changes

### 0.1 Why a revision is required

The v4 acceptance run (fixtures of section 9, root entropy `20260821`) failed
two criteria on a correct implementation:

| Criterion | Required | Observed |
|---|---|---|
| 9.3 power, GARCH(1,1) omega=.05 alpha=.10 beta=.85, n=500 | >= 80% dependent | 270/500 = 54.0% |
| 9.4 coverage, centered LogNormal, std percentile | [0.90, 0.99] | 398/459 = 86.7% |

Diagnostic experiments on the exact frozen fixtures established the causes:

1. **Regime screens veto valid dependence tests.** Under the stationary
   GARCH(1,1) generator, the v4 classification order (any regime-screen
   rejection -> `unsupported_or_inconclusive`) routed 172/500 (34.4%)
   stationary dependent datasets to `unsupported_or_inconclusive`. The v4
   section 5.5 regime screens assume within-group independence; under
   volatility clustering their conventional p-values over-reject by
   construction, so v4 sections 5.6 and 9.3 contradict v4's own documented
   limitation. The unconditional rejection rate of the Ljung-Box/runs
   dependence tests alone is 434/500 = 86.8% on the same datasets.
2. **Percentile intervals undercover the SD of skewed data.** On the centered
   LogNormal(0, 0.75) fixture (skewness approx. 3.26, n=250), measured
   coverage was: percentile 398/459 = 0.867, BCa 408/459 = 0.889, and
   studentized 429/459 = 0.935 (bootstrap-t with plug-in delta-method SE).
   Under-coverage of percentile/BCa for
   variance-type estimands under skew is a known limitation (Schenker 1985,
   doi:10.1080/01621459.1985.10478123). On the Normal and discrete fixtures
   all three methods lie inside [0.90, 0.99].

No generator, sample size, threshold, or simulation count is changed by this
revision. The v5 changes are: a classification precedence rule that respects
each test's validity conditions, a studentized interval for the standard
deviation under IID, and an applicability clarification for constant
transforms. Section 0.7 replaces the v4 discovery seed for independent v5
confirmation; reusing the discovery fixtures would contaminate acceptance.

### 0.2 Supersedes v4 section 5.6 (classification order)

The rejection family is unchanged: every actually executed Ljung-Box, runs,
Welch, and Levene p-value after lag deduplication and documented
not-applicable omissions; `alpha_family = 0.05`, `alpha_b = alpha_family / m`,
strict inequality, equality does not reject. Both m and alpha_b are recorded.

Let `dep_rejecting` be the rejecting Ljung-Box and runs tests and
`regime_rejecting` the rejecting Welch and Levene screens. Classification
order is fixed:

1. Any `dep_rejecting` -> `dependent_resampling_candidate` with reason
   `dependence_detected`. If `regime_rejecting` is also non-empty, append the
   reason `regime_rejection_descriptive_only`: once independence is rejected,
   the screens' p-values are uninterpretable (v4 section 5.5) and cannot veto
   a valid test. All rejecting test IDs remain in `rejecting_tests`.
2. Otherwise, any `regime_rejecting` -> `unsupported_or_inconclusive` with
   `structural_change_evidence` and the rejecting screen IDs.
3. Otherwise -> `iid_eligible`.

`iid_eligible` still requires zero rejections in the full family, so the
section 9.2 upper-size bound is unaffected. Trade-off, stated explicitly:
structural breaks strong enough to induce serial correlation are now routed
to exploratory dependent resampling (never certified) with full diagnostic
disclosure, instead of to `unsupported_or_inconclusive`. This is forced by
the power requirement: the v4 tie-break (screens win) is what made 9.3
infeasible, and the screens cannot distinguish stationary dependence from
break-induced dependence without assumptions their own p-values violate.
Screens remain decisional when no dependence test rejects. That non-rejection
does not prove their independence assumption; it only avoids using their
conventional p-values after the specified dependence diagnostics have already
found evidence incompatible with IID.

### 0.3 Supersedes v4 section 7 (standard-deviation IID method)

Under IID, the standard-deviation interval method is the **studentized
(bootstrap-t) interval** instead of the percentile interval:

```text
v   = mean((r - r_bar)^2)          # plug-in variance (ddof=0 moments)
m4  = mean((r - r_bar)^4)
se(s) = sqrt((m4 - v^2) / (4 * v * n))
```

by the CLT for sample moments (`Var(s^2) ≈ (mu4 - sigma^4)/n`) and the delta
method applied to the square root. Per replicate b, with the same formula on
the resample giving `se*_b`:

```text
t*_b = (s*_b - s_hat) / se*_b
CI   = [s_hat - quantile(t*, 0.975) * se(s),
        s_hat - quantile(t*, 0.025) * se(s)]
```

with `np.quantile(..., method="linear")` on the valid replicates. A replicate
with non-finite or zero `se*` is excluded from the `t*` distribution; the
exclusion count is recorded per interval. If fewer than `B/2` valid
replicates remain, or if `se(s)` itself is non-finite or zero (constant or
symmetric two-point data), the std interval is omitted with
`interval_omissions` reason `studentization_failed` (the same omission
pattern BCa uses); the point estimate and other estimands are unaffected.
The interval record carries `se` and the valid/excluded replicate counts.

The CBB standard-deviation interval remains percentile and
`exploratory_dependent`; block-aware studentization is deferred. Expectancy
(percentile, basic, BCa) and win rate (percentile) methods are unchanged.

### 0.4 Supersedes v4 section 9.3 (acceptance for stationary generators)

The 80% dependence-detection requirement is unchanged. The regime-screen
rejection-rate size bound on the stationary dependent generators is removed:
under dependence the screens are not size-controlled (section 0.2), so a size
bound on them contradicts their documented behavior. Their rejection counts
are still computed and reported descriptively. Upper-size control of the full
procedure is enforced on IID generators by section 9.2, which is unchanged.

### 0.5 Clarifies v4 sections 5.1/5.5 (constant transforms)

The `not_applicable_constant_transform` omission applies to every diagnostic
specific to a constant transformed sequence, including each regime screen
whose transformed group (level or abs, either side) is exactly constant.
The four `abs` screens are omitted when `|r|` is constant, instead of executing
invalid zero-variance Welch/Levene calls that emit SciPy warnings and produce
a spurious `diagnostic_numeric_failure`. A Levene screen is additionally
omitted when BOTH groups' absolute median deviations are exactly constant
(two-point data tied around the median), because its statistic divides by the
zero within-group sum of squares. Omissions stay out of the Bonferroni
family as before.

### 0.6 Historical v5 version identifier

`ALGORITHM_VERSION` becomes `fars-1.2-phase10a-v5` and is recorded in every
result.

### 0.7 Independent v5 confirmation (supersedes v4 section 9.1 root)

Root entropy `20260821` and its realized outputs were inspected while designing
v5. They are therefore **development/calibration evidence only** and cannot
independently accept v5. The v5 confirmation root is fixed before its first run
by this transparent derivation:

```text
label  = b"FARS-1.2-phase10a-v5-independent-confirmation"
digest = SHA256(label)
       = c343143ce495f5d54606e3008428f1e6ce18d90bc876ff6f0e673b9e1be9a4d3
root_entropy = int.from_bytes(digest[:8], "big")
             = 14070111912601187797
```

The generators, generator order, sample sizes, thresholds, replicate counts,
and seed-tree structure remained unchanged. The one-time confirmation produced
97/1000 non-IID classifications for the centered LogNormal IID generator,
exceeding the predeclared upper bound of 73. The other nine criteria passed.
V5 is therefore rejected, and this root is now calibration-only evidence.

### 0.8 V5 failure diagnosis and v6 selection

On the failed LogNormal fixture, 84 datasets were classified dependent and 13
unsupported. Raw `r^2` Ljung-Box was the dominant source: its h=10 and h=24
tests rejected 51 and 35 times, respectively (with overlap), despite IID data.
The chi-square approximation was anti-conservative for this highly skewed
finite-sample transform, invalidating the Bonferroni size rationale because
the component p-values themselves were not calibrated.

Two rank-portmanteau candidates were compared on both consumed roots
(`20260821` and `14070111912601187797`). Linear ranks controlled IID size but
reduced GARCH power to 55.8% and 58.2%. Van der Waerden (normal) scores and
their squares controlled all four IID generators (32–53 non-IID per 1000) while
retaining GARCH power of 86.0% and 88.6%, AR(1) power of 98.8% and 99.2%, and
sign-Markov power of 100% on the two roots. These are calibration results, not
v6 confirmation.

Rank-based serial procedures provide distribution-free/asymptotically
distribution-free tests against serial dependence without fixing the innovation
density (Hallin, Ingenbleek & Puri, 1987,
doi:10.1111/j.1467-9892.1987.tb00004.x; Hallin & Mélard, 1988,
doi:10.1080/01621459.1988.10478709). V6 uses the simple effective construction
in section 0.9 and verifies its finite-sample operating characteristics through
section 9 rather than claiming exact finite-sample chi-square calibration.

### 0.9 Supersedes v4/v5 section 5.3 (rank-portmanteau diagnostics)

For average ranks `rank_i = scipy.stats.rankdata(r, method="average")`, define

```text
u_i = (rank_i - 0.5) / n
z_i = Phi^-1(u_i)
q_i = z_i^2
```

Because average ranks lie in `[1, n]`, every `u_i` is strictly inside `(0, 1)`
and every score is finite. Ties receive identical average scores. Row order is
unchanged. Apply the section 5.3 Ljung-Box formula at every `h in H` to `z` and
to nonconstant `q`, with series IDs `normal_score` and
`normal_score_squared`. The former screens serial dependence in marginal-normal
rank scores; the latter screens clustering of marginal tail extremeness. Raw
`r` ACF remains descriptive only. V6 no longer computes raw `r^2` for
classification.

### 0.10 Independent v6 confirmation and version identifier

Both earlier roots have been inspected and cannot confirm v6. Before the first
v6 confirmation run, its root is fixed by:

```text
label  = b"FARS-1.2-phase10a-v6-independent-confirmation"
digest = SHA256(label)
       = cb0285e2377f570b9923a43e9184983eb4064aa765030be380066f16e5ec4e5d
root_entropy = int.from_bytes(digest[:8], "big")
             = 14628401746292987659
```

All generators, ordering, sample sizes, thresholds, replicate counts, and the
seed-tree structure remain unchanged. The one-time independent run passed all
10 predeclared statistical criteria; no method or criterion changed after its
result was observed. `ALGORITHM_VERSION` is
`fars-1.2-phase10a-v6` and is recorded in every result.

---

## 1. Scope of Phase 10A

Phase 10A contains exactly:

1. IID Bootstrap and Circular Block Bootstrap resampling engines.
2. Temporal-dependence diagnostics and a three-state eligibility classifier.
3. Uncertainty intervals for **expectancy**, **win rate**, and **standard
   deviation**, subject to the support matrix in section 7.

Nothing else is estimated. Ordinary empirical bootstrap cannot generate values
outside the historical support and can be inconsistent for sample extremes
(Bickel & Freedman, 1981, doi:10.1214/aos/1176345637). Phase 10A therefore makes
no statement about unobserved extreme events or future trading paths.

## 2. Input Contract with Phase 8A (normative)

The input is one accepted `CanonicalTradeDataset`. Phase 10A requires both
`core_metrics` and `temporal_analysis`.

The implementation inspects, rather than blindly calling, the capability map:

```python
required = ("core_metrics", "temporal_analysis")
unavailable = [name for name in required if not dataset.capabilities[name].available]
if unavailable:
    return unsupported_result_with_capability_reasons(...)
```

This is intentional: `CanonicalTradeDataset.require_capability()` raises, while
the Phase 10A public contract returns a structured
`unsupported_or_inconclusive` result with the original capability reasons.

Row order is used only after Phase 8A has established valid, timezone-aware,
non-decreasing timestamps. The stochastic process is indexed by **trade
number**, not elapsed clock time. Irregular time gaps are not converted into
regular periods. Equal timestamps retain source order, and the output records
the limitation `equal_timestamp_order_retained` when ties exist.

## 3. Estimands (normative)

| Field | Expectancy | Win rate | Standard deviation |
|---|---|---|---|
| Target parameter | Marginal population mean E[R] | Marginal probability P(R > 0) | Marginal population SD of R |
| Observed estimator | Sample mean | Sample proportion I(R > 0) | Sample SD, `ddof=1` |
| Resample size | n, equal to the accepted observed sample | n | n |
| Interpretation | Sampling uncertainty of the estimator | Same | Same |
| Additional conditions | Eligibility assumptions in section 4 | Both outcome classes observed | Positive observed variance |

The target population/process is the one represented by the complete accepted
dataset. If multiple assets or strategies are pooled, the estimand describes
that pooled trade stream; it is not silently interpreted as any component
strategy. Outputs list the distinct non-empty asset/strategy labels when
available.

These are estimator-uncertainty statements at observed sample size n, not
forecasts over an h-trade future horizon.

## 4. Eligibility and Validity Status (normative)

Diagnostics can detect particular incompatibilities; they cannot prove IID,
stationarity, or short memory. Every dataset receives one eligibility state:

- `iid_eligible`: no specified dependence or regime diagnostic rejected at the
  corrected level; approximate IID remains an explicit assumption.
- `dependent_resampling_candidate`: dependence detected. Any simultaneous
  regime-screen rejection is disclosed as descriptive-only because those
  screens are not calibrated under dependence. Stationary short-memory
  dependence is assumed, not established.
- `unsupported_or_inconclusive`: missing capabilities, n < 50, structural/regime
  evidence, a required numerical failure, or mutually unusable diagnostics.

Every emitted interval separately receives one validity status:

- `conditional_on_approximate_iid` for IID Bootstrap intervals;
- `exploratory_dependent` for CBB intervals;
- `unsupported` when no interval is emitted.

There is no `certified` Boolean. CBB results are exploratory because the screens
in section 5 cannot establish all conditions needed by block-bootstrap theory
(Künsch, 1989, doi:10.1214/aos/1176347265). IID results also remain explicitly
conditional on approximate IID.

## 5. Diagnostics and Classification (normative)

### 5.1 Numeric preparation

Let `r` be the accepted `r_result` values converted to a one-dimensional
`float64` array without reordering, and `n = len(r)`.

- Require n >= 50; otherwise return `unsupported_or_inconclusive` with
  `insufficient_sample_for_diagnostics`.
- Require every value and every subsequently reported statistic to be finite.
- If r is exactly constant, return `unsupported_or_inconclusive` with
  `zero_variance_r`. If nonconstant finite values produce a zero, non-finite,
  or otherwise unusable variance through floating-point underflow/overflow,
  return `diagnostic_numeric_failure:variance_r` instead.
- Construct the finite normal-score series `z` and squared score series `q`
  exactly as in section 0.9. Failure returns
  `diagnostic_numeric_failure:normal_scores`.
- If a transformed sequence is constant, a diagnostic specific to that
  transform is recorded as `not_applicable_constant_transform` and omitted from
  the Bonferroni family; this is not a numerical failure.

Define:

```text
L  = min(ceil(10 * log10(n)), n - 1)
L1 = min(10, L)
H  = sorted(unique([L1, L]))
```

### 5.2 ACF definition

For a nonconstant sequence x of length n, with mean x_bar, use the unadjusted
sample autocorrelation

```text
rho_hat(k) = sum_{t=k}^{n-1} (x[t]-x_bar)(x[t-k]-x_bar)
             / sum_{t=0}^{n-1} (x[t]-x_bar)^2
```

for k = 1..L. No FFT or missing-value policy is involved. ACF values for r and
the descriptive bound +/-1.96/sqrt(n) are reported, but individual ACF lags do
not make classification decisions.

### 5.3 Rank-portmanteau Ljung-Box tests

For x in {normal scores `z`, squared scores `q` when nonconstant}, compute the
ACF formula above and, for each h in H,

```text
Q(h) = n * (n + 2) * sum_{k=1}^{h} rho_hat(k)^2 / (n - k)
p(h) = scipy.stats.chi2.sf(Q(h), df=h)
```

No fitted-model degrees of freedom are subtracted (`model_df=0`). Each h is one
p-value-producing test. Ljung-Box on `z` screens dependence in marginal-normal
rank scores; Ljung-Box on `q` screens clustering in marginal tail extremeness.
These asymptotic rank-portmanteau diagnostics are not proofs of independence.

### 5.4 Runs test

Define the binary sequence `w[t] = 1 if r[t] > 0 else 0`; zero-R trades are
therefore **non-wins** and are not removed. Let n1 and n0 be class counts and

```text
R_runs = 1 + sum_{t=1}^{n-1} I(w[t] != w[t-1])
mu_R   = 1 + 2*n1*n0/n
var_R  = 2*n1*n0*(2*n1*n0 - n) / (n^2*(n-1))
z_R    = (R_runs - mu_R) / sqrt(var_R)
p_R    = 2 * scipy.stats.norm.sf(abs(z_R))
```

This Phase 10A approximation uses no continuity correction. If either class is
absent or `var_R <= 0`, record `not_applicable_single_outcome_class`, omit the
runs test from the Bonferroni family, and mark the win-rate estimand
`not_estimable:single_outcome_class`. Other estimands may proceed.

### 5.5 Regime screens

Two deterministic comparisons are used:

```text
half_cut = n // 2
half_a   = r[:half_cut]
half_b   = r[half_cut:]       # receives the extra observation when n is odd

third_n  = n // 3
third_a  = r[:third_n]
third_b  = r[-third_n:]       # middle observations are intentionally unused
```

For each pair `(a, b)`, run all four tests:

1. `scipy.stats.ttest_ind(a, b, equal_var=False, nan_policy="raise",
   alternative="two-sided")`;
2. `scipy.stats.levene(a, b, center="median", nan_policy="raise")`;
3. the same Welch test on `abs(a), abs(b)`;
4. the same median-centered Levene test on `abs(a), abs(b)`.

This yields eight regime-screen p-values. Non-finite results are required
diagnostic failures and produce `unsupported_or_inconclusive` with the failing
test name.

These are deliberately labeled **heuristic screens**. Their conventional
p-values assume within-group independence, and two split locations cannot find
every gradual trend, break, multiple regime, or long-memory process. A dataset
that reaches dependent resampling therefore remains exploratory.

### 5.6 Multiplicity and classification

The rejection family contains every actually executed Ljung-Box, runs, Welch,
and Levene p-value after lag deduplication and documented not-applicable
omissions. Let m be that count and

```text
alpha_family = 0.05
alpha_b      = alpha_family / m
reject       = p_value < alpha_b
```

Both m and alpha_b are recorded. Equality does not reject.

Classification order is fixed:

1. Any Ljung-Box or runs rejection -> `dependent_resampling_candidate` with
   `dependence_detected`. If a regime screen also rejects, append
   `regime_rejection_descriptive_only`; retain every rejecting test ID.
2. Otherwise, any regime-screen rejection -> `unsupported_or_inconclusive`
   with `structural_change_evidence` and all rejecting screen IDs.
3. Otherwise -> `iid_eligible`.

## 6. Resampling and Block-Length Selection (normative)

### 6.1 IID Bootstrap

For each replicate, draw n indices independently and uniformly from
`{0, ..., n-1}` and preserve the drawn order.

### 6.2 Estimand-specific influence series

`arch.bootstrap.optimal_block_length` estimates a block length from the
dependence in the series passed to it. Using raw r for every estimand would miss
sign dependence relevant to win rate and squared-return dependence relevant to
standard deviation. Phase 10A therefore selects a separate CBB length for each
estimand using an estimated influence series:

```text
r_bar = mean(r)

u_expectancy[t] = r[t] - r_bar

w[t]            = I(r[t] > 0)
u_win_rate[t]   = w[t] - mean(w)

m2              = mean((r - r_bar)^2)
u_std[t]        = ((r[t] - r_bar)^2 - m2) / (2 * sqrt(m2))
```

The observed standard-deviation point estimator remains `ddof=1`; `u_std` is
used only for asymptotic block-length selection. `u_win_rate` requires both
classes, and `u_std` requires m2 > 0.

For estimand j:

```text
l_hat_raw_j      = arch.bootstrap.optimal_block_length(u_j).loc[0, "circular"]
l_j              = min(n, max(1, int(ceil(l_hat_raw_j))))
l_hat_reported_j = canonicalize(l_hat_raw_j, 12 significant decimal digits)
k_j              = ceil(n / l_j)
```

The canonicalization removes non-semantic final-ULP variation observed in the
selector's vector reductions on an otherwise locked platform. The unrounded
selector value remains authoritative for `ceil`; only the recorded diagnostic
is canonicalized. The result records
`block_length_report_significant_digits = 12`. This does not replace or tune
the `arch` selector.

The exact arch version is locked before implementation and recorded. Raw and
final lengths are recorded per estimand. A selector exception, non-finite
length, or invalid influence series makes only that estimand
`not_estimable:block_length_selection_failed`; it does not silently fall back
to l=1 or invalidate otherwise supported estimands.

### 6.3 Circular Block Bootstrap

For each estimand j and each replicate:

1. Draw k_j block starts independently and uniformly from `{0, ..., n-1}`.
2. A block starting at s contains indices
   `(s, s+1, ..., s+l_j-1) modulo n`.
3. Concatenate the blocks and truncate to exactly n indices.
4. Evaluate the estimand on the resulting ordered resample.

All source positions have equal inclusion behavior under the circular scheme.
CBB is used only for `dependent_resampling_candidate` and its intervals carry
`exploratory_dependent`.

## 7. Interval Methodology (normative)

| Estimand | IID methods | CBB methods | Conditions |
|---|---|---|---|
| Expectancy | Percentile, basic, BCa | Percentile, basic | Effective pipeline n >= 50 |
| Win rate | Percentile | Percentile | n >= 50 and both outcome classes |
| Standard deviation | Studentized (bootstrap-t) | Percentile | n >= 50 and positive variance |
| Skewness/kurtosis | Not supported | Not supported | Phase 10B or later |
| Drawdown/streaks/extremes | Not supported | Not supported | Phase 11 |

Phase 10A fixes confidence level at 95%. `B` is an integer parameter with
`B >= 2000`. Both are recorded.

For bootstrap estimates `theta_star` and observed estimate `theta_hat`, use
`np.quantile(theta_star, q, method="linear")`:

```text
q_lo = quantile(theta_star, 0.025)
q_hi = quantile(theta_star, 0.975)

percentile = [q_lo, q_hi]
basic      = [2*theta_hat - q_hi, 2*theta_hat - q_lo]
```

BCa is offered only for IID expectancy. With strict `<` tie handling:

```text
z0 = Phi^-1(count(theta_star < theta_hat) / B)
```

Let `theta_j` be the leave-one-out mean and `theta_J_bar` their mean:

```text
a_hat = sum((theta_J_bar - theta_j)^3)
        / (6 * sum((theta_J_bar - theta_j)^2)^(3/2))

adjust(alpha) = Phi(
    z0 + (z0 + Phi^-1(alpha))
         / (1 - a_hat * (z0 + Phi^-1(alpha)))
)

BCa = [quantile(theta_star, adjust(0.025)),
       quantile(theta_star, adjust(0.975))]
```

BCa is omitted, without removing percentile/basic, when the bias count is 0 or
B, the acceleration denominator or an adjustment denominator is zero/non-finite,
or an adjusted probability is not strictly inside (0, 1). The exact reason is
recorded.

For expectancy, percentile and basic widths are equal by construction. Attach
the predeclared heuristic warning `interval_method_discrepancy` when

```text
abs(midpoint(percentile) - midpoint(basic)) > 0.25 * interval_width
```

This warning describes bootstrap bias sensitivity; it is not a validity test.

## 8. RNG, Reproducibility, and Provenance (normative)

- A run requires an explicit non-negative integer master seed.
- Construct `numpy.random.SeedSequence(master_seed)` and always spawn exactly
  three children in fixed order: expectancy, win rate, standard deviation,
  including children for estimands later marked not estimable.
- Each child constructs
  `numpy.random.Generator(numpy.random.PCG64(child_seed_sequence))`.
- Record master entropy, child `spawn_key`, bit-generator name, B, and estimand
  order. Do not describe `spawn_key` as a standalone integer seed.
- Bit-identical output is promised only for the same data, parameters, platform,
  implementation, and locked NumPy/SciPy/arch versions. Cross-version identity
  is not promised.

The result copies `source_sha256`, `schema_version`, `resolved_mapping`, and
capability statuses/reasons from Phase 8A. It additionally records:

- dependency versions and algorithm/schema version;
- alpha_family, m, alpha_b, lags, every statistic/p-value and omission reason;
- ACF values/bound;
- eligibility state/reasons;
- validity status per interval;
- influence-series name, raw/final block length and k per estimand;
- B, confidence level, quantile method, RNG metadata;
- distinct asset/strategy labels and all limitation notes.

No result contains NaN or infinity. Undefined quantities use:

```text
status = "not_estimable"
value  = null
reason = <stable machine-readable code>
```

Numerical failure during point estimation, resampling, or interval construction
is isolated to the affected estimand with, respectively,
`point_estimate_numeric_failure`, `resampling_numeric_failure`, or
`interval_numeric_failure`. A CBB influence/selector failure remains
`block_length_selection_failed`. Studentization-specific failures use the
interval omission contract in section 0.3.

## 9. Tests and Statistical Acceptance (normative)

Deterministic unit tests cover formulas, resample construction and ordering,
capability failures, constant transforms, numerical failure paths, provenance,
and RNG reproducibility. Different master seeds must change deterministic
end-to-end interval output; no vague pairwise "statistically close" assertion
is used.

Statistical validation is a separate slow test group. Its v6 confirmation
fixtures and limits below are fixed before their first run and must not be
changed after seeing results. The consumed v4 and v5 roots are retained only by
diagnostic scripts as calibration evidence (sections 0.7–0.8).

### 9.1 Shared validation settings

- Root `SeedSequence` entropy: `14628401746292987659`, derived exactly as in
  section 0.10.
- False-positive validation: M = 1000 datasets per generator, n = 250.
- Power validation: M = 500 datasets per generator, n = 500.
- IID coverage validation: M = 500 datasets per generator, n = 250,
  B = 2000.

The seed tree is fixed: the root spawns suite children in the order
false-positive, power, coverage; each suite child spawns generator children in
the order listed below; each generator child spawns M replicate children; and
each replicate child spawns two children in order `data_generation`,
`bootstrap_analysis`. Every leaf uses PCG64. This prevents loop refactoring or
an added generator from silently changing existing fixtures.

### 9.2 False-positive validation

Use four IID generators:

1. standard Normal;
2. Student-t with 5 degrees of freedom, divided by `sqrt(5/3)`;
3. `LogNormal(0, 0.75) - exp(0.75^2/2)`;
4. values `[-1, 0, 2]` with probabilities `[0.45, 0.10, 0.45]`.

For each generator, let K be datasets classified other than `iid_eligible`.
Require

```text
K <= scipy.stats.binom.ppf(0.999, M, 0.05)
```

This is an upper-size check, not a requirement that Bonferroni reject exactly
5% of IID datasets.

### 9.3 Dependence-detection validation

Use:

1. Gaussian AR(1): phi = 0.3, stationary initialization
   `N(0, 1/(1-phi^2))`, innovations `N(0,1)`;
2. sign-Markov process: initial sign uniform in {-1,+1}, probability of keeping
   the previous sign 0.70, independent magnitudes `Exponential(1)`;
3. Gaussian GARCH(1,1): omega = 0.05, alpha = 0.10, beta = 0.85,
   initialized at unconditional variance `omega/(1-alpha-beta)`.

For each generator, at least 80% of datasets must be classified
`dependent_resampling_candidate`. Regime-screen rejection counts on these
dependent generators are computed and reported descriptively, with no size
threshold because their conventional p-values are not calibrated under
dependence. The IID size requirement remains section 9.2.

### 9.4 IID end-to-end coverage

Run capability checks, diagnostics, adaptive method selection, resampling, and
interval construction. Use the Normal, centered LogNormal, and discrete IID
generators from section 9.2. Their exact targets `(expectancy, win_rate, std)`
are:

```text
Normal:            (0, 0.5, 1)
centered LogNormal:(0,
                    1 - Phi(0.375),
                    sqrt((exp(0.75^2)-1) * exp(0.75^2)))
discrete:          (0.45, 0.45, sqrt(2.0475))
```

For every supported estimand/IID-interval method:

- at least 90% of all generated datasets must emit an interval with validity
  `conditional_on_approximate_iid`; and
- among those conditionally-IID intervals, empirical coverage of the known
  target must lie in `[0.90, 0.99]`.

False-positive CBB selections under an IID generator are reported separately
and do not enter the conditional-IID coverage denominator. Dependent CBB
coverage is also measured and reported on the section 9.3 generators but has no
certification threshold in Phase 10A; these intervals are explicitly
exploratory. Deterministic tests still enforce their exact construction.

## 10. Explicit Non-Goals and Deferred Work

- No drawdown, streak, extreme, future-path, or probabilistic trade-limit
  estimation (Phase 11).
- No skewness/kurtosis intervals, parametric bootstrap, binomial win-rate method,
  optimization, OOS/walk-forward, or stress testing.
- No stationarity or IID certification.
- CLI exposure is outside Phase 10A and is defined by the approved Phase 10B
  contract in `FARS_1_2_PHASES_10B_14_SPEC.md`.
- Phase 10B may add estimands only with an approved estimand/method/block-length
  support matrix.

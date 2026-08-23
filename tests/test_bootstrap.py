"""Deterministic unit tests for Phase 10A (src/bootstrap.py, design v6)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from scipy import stats as sp_stats

from src.bootstrap import (
    ALGORITHM_VERSION,
    STATE_DEPENDENT,
    STATE_IID,
    STATE_UNSUPPORTED,
    VALIDITY_DEPENDENT,
    VALIDITY_IID,
    VALIDITY_UNSUPPORTED,
    _add_bca,
    _add_studentized_std,
    _classify,
    _normal_scores,
    analyze_bootstrap,
)
from src.ingestion import load_trade_csv


def _write_dataset(tmp_path, r_values, *, with_timestamps=True, ties=False):
    """Build a CanonicalTradeDataset from r values via the Phase 8A loader."""
    rows = ["timestamp,r_result"] if with_timestamps else ["r_result"]
    t0 = datetime(2023, 1, 2, tzinfo=timezone.utc)
    for i, v in enumerate(r_values):
        step = i // 2 if ties else i
        ts = (t0 + timedelta(hours=step)).isoformat()
        row = f"{ts},{float(v)!r}" if with_timestamps else repr(float(v))
        rows.append(row)
    path = tmp_path / "trades.csv"
    path.write_text("\n".join(rows), encoding="utf-8")
    return load_trade_csv(path, outcomes_finalized=True)


def _iid_values(n=120, seed=7):
    return list(np.random.default_rng(seed).normal(size=n))


def _walk_no_nan(obj, path=""):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from _walk_no_nan(value, f"{path}.{key}")
    elif isinstance(obj, (list, tuple)):
        for i, value in enumerate(obj):
            yield from _walk_no_nan(value, f"{path}[{i}]")
    elif isinstance(obj, float):
        yield path, obj


def assert_no_nan_or_inf(result):
    for path, value in _walk_no_nan(result):
        assert math.isfinite(value), f"non-finite value at {path}"


# ---------------------------------------------------------------------------
# Input contract and early exits
# ---------------------------------------------------------------------------


def test_missing_temporal_capability_is_unsupported(tmp_path):
    dataset = _write_dataset(tmp_path, _iid_values(), with_timestamps=False)
    result = analyze_bootstrap(dataset, master_seed=1)
    assert result["eligibility"]["state"] == STATE_UNSUPPORTED
    assert "capability_unavailable:temporal_analysis" in result["eligibility"]["reasons"]
    for entry in result["estimands"].values():
        assert entry["status"] == "not_estimable"
        assert entry["value"] is None
        assert entry["validity"] == VALIDITY_UNSUPPORTED
        assert entry["intervals"] == []
    assert_no_nan_or_inf(result)


def test_small_sample_is_unsupported(tmp_path):
    dataset = _write_dataset(tmp_path, _iid_values(n=49))
    result = analyze_bootstrap(dataset, master_seed=1)
    assert result["eligibility"]["state"] == STATE_UNSUPPORTED
    assert result["eligibility"]["reasons"] == ["insufficient_sample_for_diagnostics"]


def test_zero_variance_is_unsupported(tmp_path):
    dataset = _write_dataset(tmp_path, [1.5] * 100)
    result = analyze_bootstrap(dataset, master_seed=1)
    assert result["eligibility"]["reasons"] == ["zero_variance_r"]


def test_constant_transforms_are_omitted_without_numeric_failure(tmp_path):
    # |r| is exactly constant, so its regime screens are not applicable. The
    # v6 rank scores and runs test still preserve and detect sign dependence.
    values = [1.0, -1.0] * 50 + [1.0]
    dataset = _write_dataset(tmp_path, values)
    result = analyze_bootstrap(dataset, master_seed=1)
    assert result["eligibility"]["state"] == STATE_DEPENDENT
    assert result["eligibility"]["reasons"][0] == "dependence_detected"
    omissions = {o["id"]: o["reason"] for o in result["diagnostics"]["omissions"]}
    assert omissions.get("regime_welch_abs_halves") == "not_applicable_constant_transform"
    assert omissions.get("regime_levene_abs_halves") == "not_applicable_constant_transform"
    assert omissions.get("regime_welch_abs_thirds") == "not_applicable_constant_transform"
    assert omissions.get("regime_levene_abs_thirds") == "not_applicable_constant_transform"
    series = {
        t["series"]
        for t in result["diagnostics"]["tests"]
        if t["kind"] == "ljung_box"
    }
    assert series == {"normal_score", "normal_score_squared"}
    assert_no_nan_or_inf(result)


def test_normal_scores_use_average_ranks_and_are_finite():
    values = np.array([-1.0, -1.0, 0.0, 2.0])
    expected_probabilities = (np.array([1.5, 1.5, 3.0, 4.0]) - 0.5) / 4.0
    expected = np.asarray(sp_stats.norm.ppf(expected_probabilities), dtype=np.float64)
    actual = _normal_scores(values)
    np.testing.assert_allclose(actual, expected)
    assert np.all(np.isfinite(actual))


def test_extreme_finite_spread_returns_structured_numeric_failure(tmp_path):
    dataset = _write_dataset(tmp_path, [1e154, -1e154] * 50)
    result = analyze_bootstrap(dataset, master_seed=1)
    assert result["eligibility"]["state"] == STATE_UNSUPPORTED
    assert result["eligibility"]["reasons"] == [
        "diagnostic_numeric_failure:variance_r"
    ]
    assert_no_nan_or_inf(result)


def test_nonconstant_underflowing_spread_is_not_zero_variance(tmp_path):
    dataset = _write_dataset(tmp_path, [1e-200, -1e-200] * 50)
    result = analyze_bootstrap(dataset, master_seed=1)
    assert result["eligibility"]["state"] == STATE_UNSUPPORTED
    assert result["eligibility"]["reasons"] == [
        "diagnostic_numeric_failure:variance_r"
    ]
    assert_no_nan_or_inf(result)


def test_single_outcome_class_marks_only_win_rate(tmp_path):
    rng = np.random.default_rng(3)
    values = list(np.abs(rng.normal(size=100)) + 0.25)  # all wins, nonconstant
    dataset = _write_dataset(tmp_path, values)
    result = analyze_bootstrap(dataset, master_seed=1)
    omissions = {o["id"]: o["reason"] for o in result["diagnostics"]["omissions"]}
    assert omissions.get("runs_test") == "not_applicable_single_outcome_class"
    win = result["estimands"]["win_rate"]
    assert win["status"] == "not_estimable"
    assert win["reason"] == "single_outcome_class"
    assert result["estimands"]["expectancy"]["status"] == "estimable"
    assert result["estimands"]["std"]["status"] == "estimable"
    assert_no_nan_or_inf(result)


def test_seed_and_B_validation(tmp_path):
    dataset = _write_dataset(tmp_path, _iid_values())
    with pytest.raises(TypeError):
        analyze_bootstrap(dataset, master_seed=True)
    with pytest.raises(ValueError):
        analyze_bootstrap(dataset, master_seed=-1)
    with pytest.raises(ValueError):
        analyze_bootstrap(dataset, master_seed=1, B=1999)


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


def test_same_seed_bit_identical(tmp_path):
    dataset = _write_dataset(tmp_path, _iid_values())
    a = analyze_bootstrap(dataset, master_seed=42)
    b = analyze_bootstrap(dataset, master_seed=42)
    assert a == b


def test_different_seed_changes_output(tmp_path):
    dataset = _write_dataset(tmp_path, _iid_values())
    a = analyze_bootstrap(dataset, master_seed=42)
    b = analyze_bootstrap(dataset, master_seed=43)
    assert a["estimands"]["expectancy"]["intervals"] != b["estimands"]["expectancy"]["intervals"]


def test_rng_metadata_fixed_tree(tmp_path):
    dataset = _write_dataset(tmp_path, _iid_values())
    result = analyze_bootstrap(dataset, master_seed=42)
    rng = result["rng"]
    assert rng["master_entropy"] == 42
    assert rng["spawn_keys"] == [[0], [1], [2]]
    assert rng["bit_generator"] == "PCG64"
    assert rng["estimand_order"] == ["expectancy", "win_rate", "std"]


# ---------------------------------------------------------------------------
# IID path
# ---------------------------------------------------------------------------


def test_iid_normal_classifies_and_emits_iid_intervals(tmp_path):
    dataset = _write_dataset(tmp_path, _iid_values(n=250))
    result = analyze_bootstrap(dataset, master_seed=11)
    assert result["eligibility"]["state"] == STATE_IID
    exp = result["estimands"]["expectancy"]
    assert exp["validity"] == VALIDITY_IID
    methods = [iv["method"] for iv in exp["intervals"]]
    assert methods == ["percentile", "basic", "bca"]
    for iv in exp["intervals"]:
        assert iv["lower"] <= iv["upper"]
        assert iv["validity"] == VALIDITY_IID
    assert [iv["method"] for iv in result["estimands"]["win_rate"]["intervals"]] == ["percentile"]
    std_intervals = result["estimands"]["std"]["intervals"]
    assert [iv["method"] for iv in std_intervals] == ["studentized"]
    std_interval = std_intervals[0]
    assert std_interval["se"] > 0.0
    assert std_interval["replicates_valid"] + std_interval["replicates_excluded"] == 2000
    assert std_interval["lower"] <= std_interval["upper"]
    assert_no_nan_or_inf(result)


def test_lag_dedup_and_acf_reported(tmp_path):
    dataset = _write_dataset(tmp_path, _iid_values(n=50))
    result = analyze_bootstrap(dataset, master_seed=5)
    lags = result["diagnostics"]["lags"]
    assert lags == sorted(set(lags))
    assert lags == [10, 17]  # L1=10, L=min(ceil(10*log10(50)),49)=17
    acf = result["diagnostics"]["acf"]
    assert acf["series"] == "r"
    assert len(acf["values"]) == 17
    assert acf["bound"] == pytest.approx(1.96 / math.sqrt(50))
    m = result["diagnostics"]["m"]
    assert result["diagnostics"]["alpha_b"] == pytest.approx(0.05 / m)
    assert_no_nan_or_inf(result)


# ---------------------------------------------------------------------------
# Dependent path (CBB)
# ---------------------------------------------------------------------------


def _ar1(n, phi, seed):
    rng = np.random.default_rng(seed)
    out = np.empty(n)
    out[0] = rng.normal(0.0, 1.0 / math.sqrt(1 - phi**2))
    for t in range(1, n):
        out[t] = phi * out[t - 1] + rng.normal()
    return list(out)


def test_ar1_dependent_uses_cbb_without_bca(tmp_path):
    dataset = _write_dataset(tmp_path, _ar1(300, 0.6, seed=23))
    result = analyze_bootstrap(dataset, master_seed=11)
    assert result["eligibility"]["state"] == STATE_DEPENDENT
    exp = result["estimands"]["expectancy"]
    assert exp["validity"] == VALIDITY_DEPENDENT
    methods = [iv["method"] for iv in exp["intervals"]]
    assert methods == ["percentile", "basic"]
    for entry in result["estimands"].values():
        bl = entry["block_length"]
        assert bl is not None
        assert 1 <= bl["final"] <= 300
        assert bl["k"] == math.ceil(300 / bl["final"])
        assert math.isfinite(bl["raw"])
    assert_no_nan_or_inf(result)


def test_block_selector_exception_is_isolated_per_estimand(tmp_path, monkeypatch):
    dataset = _write_dataset(tmp_path, _ar1(300, 0.6, seed=23))

    def fail_selector(_influence):
        raise RuntimeError("selector failed for test")

    monkeypatch.setattr("src.bootstrap.optimal_block_length", fail_selector)
    result = analyze_bootstrap(dataset, master_seed=11)
    assert result["eligibility"]["state"] == STATE_DEPENDENT
    for entry in result["estimands"].values():
        assert entry["status"] == "not_estimable"
        assert entry["value"] is None
        assert entry["reason"] == "block_length_selection_failed"
        assert entry["intervals"] == []
        assert entry["block_length"]["raw"] is None
        assert entry["block_length"]["error"] == "selector failed for test"
    assert_no_nan_or_inf(result)


def test_break_with_dependence_is_exploratory_and_fully_disclosed(tmp_path):
    rng = np.random.default_rng(9)
    values = list(rng.normal(0.0, 1.0, 150)) + list(rng.normal(5.0, 1.0, 150))
    dataset = _write_dataset(tmp_path, values)
    result = analyze_bootstrap(dataset, master_seed=11)
    assert result["eligibility"]["state"] == STATE_DEPENDENT
    assert result["eligibility"]["reasons"] == [
        "dependence_detected",
        "regime_rejection_descriptive_only",
    ]
    assert any(t.startswith("regime_") for t in result["eligibility"]["rejecting_tests"])
    assert all(
        entry["validity"] == VALIDITY_DEPENDENT
        for entry in result["estimands"].values()
    )


def test_classification_precedence_and_strict_threshold():
    tests = [
        {"id": "dep", "kind": "ljung_box", "p_value": 0.001},
        {"id": "regime", "kind": "welch", "p_value": 0.002},
        {"id": "equal", "kind": "runs", "p_value": 0.01},
    ]
    state, reasons, rejecting = _classify(tests, alpha_b=0.01)
    assert state == STATE_DEPENDENT
    assert reasons == ["dependence_detected", "regime_rejection_descriptive_only"]
    assert rejecting == ["dep", "regime"]

    state, reasons, rejecting = _classify(tests[1:], alpha_b=0.01)
    assert state == STATE_UNSUPPORTED
    assert reasons == ["structural_change_evidence"]
    assert rejecting == ["regime"]


def test_studentized_std_omits_zero_observed_se():
    entry = {"intervals": [], "interval_omissions": [], "warnings": []}
    r = np.tile(np.array([-1.0, 1.0]), 125)
    theta_hat = float(np.std(r, ddof=1))
    _add_studentized_std(
        entry,
        theta_star=np.full(2000, theta_hat),
        resamples=np.empty((2000, 0)),
        r=r,
        theta_hat=theta_hat,
        validity=VALIDITY_IID,
    )
    assert entry["intervals"] == []
    assert entry["interval_omissions"] == [
        {"method": "studentized", "reason": "studentization_failed"}
    ]


def test_studentized_std_records_excluded_replicates(monkeypatch):
    calls = iter(
        [
            np.asarray(0.25),
            np.concatenate((np.full(1500, 0.2), np.zeros(500))),
        ]
    )
    monkeypatch.setattr("src.bootstrap._plug_in_se_sd", lambda _x: next(calls))
    entry = {"intervals": [], "interval_omissions": [], "warnings": []}
    theta_star = np.linspace(0.5, 1.5, 2000)
    _add_studentized_std(
        entry,
        theta_star=theta_star,
        resamples=np.empty((2000, 0)),
        r=np.array([0.0, 1.0]),
        theta_hat=1.0,
        validity=VALIDITY_IID,
    )
    interval = entry["intervals"][0]
    assert interval["replicates_valid"] == 1500
    assert interval["replicates_excluded"] == 500
    assert entry["warnings"] == ["studentized_replicates_excluded"]
    assert entry["interval_omissions"] == []


def test_bca_records_undefined_bias_and_acceleration():
    entry = {"intervals": [], "interval_omissions": []}
    _add_bca(
        entry,
        theta_star=np.full(2000, 2.0),
        theta_hat=1.0,
        r=np.arange(50.0),
        B=2000,
        validity=VALIDITY_IID,
    )
    assert entry["interval_omissions"] == [
        {"method": "bca", "reason": "bca_z0_undefined"}
    ]

    entry = {"intervals": [], "interval_omissions": []}
    _add_bca(
        entry,
        theta_star=np.linspace(0.5, 1.5, 2000),
        theta_hat=1.0,
        r=np.ones(50),
        B=2000,
        validity=VALIDITY_IID,
    )
    assert entry["interval_omissions"] == [
        {"method": "bca", "reason": "bca_acceleration_undefined"}
    ]


# ---------------------------------------------------------------------------
# Provenance, labels, limitations
# ---------------------------------------------------------------------------


def test_provenance_labels_and_schema(tmp_path):
    dataset = _write_dataset(tmp_path, _iid_values())
    result = analyze_bootstrap(dataset, master_seed=1)
    prov = result["provenance"]
    assert prov["source_sha256"] == dataset.provenance.source_sha256
    assert prov["schema_version"] == dataset.provenance.schema_version
    assert prov["resolved_mapping"] == dict(dataset.provenance.resolved_mapping)
    assert prov["algorithm_version"] == ALGORITHM_VERSION
    assert set(prov["versions"]) == {"numpy", "scipy", "arch"}
    assert result["labels"]["assets"] == []
    assert result["labels"]["strategies"] == []
    assert result["schema_version"] == ALGORITHM_VERSION


def test_equal_timestamps_recorded_as_limitation(tmp_path):
    dataset = _write_dataset(tmp_path, _iid_values(n=120), ties=True)
    result = analyze_bootstrap(dataset, master_seed=1)
    assert "equal_timestamp_order_retained" in result["limitations"]


def test_not_estimable_contract_shape(tmp_path):
    dataset = _write_dataset(tmp_path, _iid_values(n=30))
    result = analyze_bootstrap(dataset, master_seed=1)
    for entry in result["estimands"].values():
        assert set(entry) >= {"status", "value", "reason"}
        assert entry["status"] == "not_estimable"
        assert entry["value"] is None
        assert isinstance(entry["reason"], str)

"""
Phase 6 tests: Risk-per-trade optimization and FRES scoring.

Tests cover:
  - Configuration validation (OptimizationConfig)
  - Risk grid generation and custom risk levels
  - VaR and CVaR / Expected Shortfall computation and edge cases
  - CVaR empirical correctness with discrete tied values
  - Gaussian kernel smoothing on P(PASS | r) curves
  - Dynamic bandwidth selection on irregular/custom grids
  - OptimizationResult invariants and read-only array protections
  - Common Random Numbers (CRN) paired scenario comparison
  - Synthetic and Resampled optimization workflows
  - Confidence level customization and VaR/CVaR label consistency
  - Deterministic reproducibility with master seeds
  - Property tests (positive vs negative expectancy, risk scaling)
  - FRES multi-objective optimization and tail risk penalization
"""

import math
import numpy as np
import pytest

from src.monte_carlo import MonteCarloConfig
from src.optimization import (
    OptimizationConfig,
    OptimizationResult,
    RiskLevelEvaluation,
    compute_fres_sensitivity,
    compute_var_cvar,
    optimize_risk_per_trade,
    smooth_curve_gaussian,
)
from src.types import FundedAccountRules, SyntheticConfig, Trade


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _standard_rules(
    initial_balance: float = 100_000.0,
    profit_target_pct: float = 0.10,
    max_drawdown_pct: float = 0.10,
    daily_loss_limit_pct: float = 0.05,
    risk_per_trade: float = 0.01,
) -> FundedAccountRules:
    return FundedAccountRules(
        initial_balance=initial_balance,
        profit_target_pct=profit_target_pct,
        max_drawdown_pct=max_drawdown_pct,
        daily_loss_limit_pct=daily_loss_limit_pct,
        risk_per_trade=risk_per_trade,
    )


def _make_trades(r_results: list[float]) -> list[Trade]:
    return [
        Trade(r_result=r, trade_id=f"t{i:04d}", date=f"2024-01-{(i // 5) + 1:02d}")
        for i, r in enumerate(r_results)
    ]


# ---------------------------------------------------------------------------
# OptimizationConfig Validation Tests
# ---------------------------------------------------------------------------


def test_opt_config_defaults():
    cfg = OptimizationConfig()
    assert cfg.min_risk == 0.001
    assert cfg.max_risk == 0.02
    assert cfg.step_size == 0.0005
    assert cfg.risk_levels is None
    assert cfg.smoothing_bandwidth is None
    assert cfg.fres_lambda == 1.0
    assert cfg.fres_gamma == 0.5
    assert cfg.fres_delta == 0.5
    assert cfg.confidence_level == 0.95

    levels = cfg.get_risk_levels()
    assert len(levels) == 39
    assert pytest.approx(levels[0]) == 0.001
    assert pytest.approx(levels[-1]) == 0.02


def test_opt_config_custom_risk_levels():
    cfg = OptimizationConfig(risk_levels=(0.005, 0.01, 0.015, 0.02))
    assert cfg.get_risk_levels() == (0.005, 0.01, 0.015, 0.02)


def test_opt_config_grid_accepts_step_equal_to_span():
    cfg = OptimizationConfig(min_risk=0.001, max_risk=0.02, step_size=0.019)
    assert cfg.get_risk_levels() == pytest.approx((0.001, 0.02))


def test_opt_config_grid_does_not_round_small_positive_risks_to_zero():
    cfg = OptimizationConfig(min_risk=1e-12, max_risk=3e-12, step_size=1e-12)
    assert cfg.get_risk_levels() == pytest.approx((1e-12, 2e-12, 3e-12))
    assert all(level > 0.0 for level in cfg.get_risk_levels())


def test_opt_config_grid_tolerates_computed_float_endpoint_noise():
    computed_min = 0.1 + 0.2
    cfg = OptimizationConfig(min_risk=computed_min, max_risk=0.5, step_size=0.1)
    assert cfg.get_risk_levels() == pytest.approx((computed_min, 0.4, 0.5))


@pytest.mark.parametrize(
    "param,val",
    [
        ("min_risk", 0.0),
        ("min_risk", -0.01),
        ("min_risk", 1.0),
        ("min_risk", math.nan),
        ("min_risk", math.inf),
        ("min_risk", True),
        ("min_risk", False),
        ("max_risk", 0.0),
        ("max_risk", 1.0),
        ("max_risk", 1.5),
        ("max_risk", True),
        ("step_size", 0.0),
        ("step_size", -0.001),
        ("step_size", 0.05),  # greater than max - min span
        ("step_size", True),
        ("fres_lambda", -0.1),
        ("fres_lambda", math.nan),
        ("fres_lambda", True),
        ("fres_gamma", -0.1),
        ("fres_gamma", True),
        ("fres_delta", -0.1),
        ("fres_delta", True),
        ("confidence_level", 0.0),
        ("confidence_level", 1.0),
        ("confidence_level", -0.1),
        ("confidence_level", True),
    ],
)
def test_opt_config_rejects_invalid_scalar_params(param, val):
    kwargs = {param: val}
    with pytest.raises(ValueError):
        OptimizationConfig(**kwargs)


def test_opt_config_rejects_min_greater_equal_max():
    with pytest.raises(ValueError):
        OptimizationConfig(min_risk=0.02, max_risk=0.01)
    with pytest.raises(ValueError):
        OptimizationConfig(min_risk=0.01, max_risk=0.01)


@pytest.mark.parametrize(
    "invalid_levels",
    [
        (),  # empty
        (0.01, 0.005),  # not increasing
        (0.005, 0.005),  # duplicate
        (0.005, math.nan),
        (0.005, math.inf),
        (0.005, True),
        (0.005, 1.2),  # out of (0, 1)
        (-0.01, 0.01),
    ],
)
def test_opt_config_rejects_invalid_risk_levels(invalid_levels):
    with pytest.raises(ValueError):
        OptimizationConfig(risk_levels=invalid_levels)


@pytest.mark.parametrize(
    "invalid_bw",
    [0.0, -0.01, math.nan, math.inf, True, False, "auto"],
)
def test_opt_config_rejects_invalid_smoothing_bandwidth(invalid_bw):
    with pytest.raises(ValueError):
        OptimizationConfig(smoothing_bandwidth=invalid_bw)


# ---------------------------------------------------------------------------
# VaR and CVaR (Tail Risk) Tests
# ---------------------------------------------------------------------------


def test_compute_var_cvar_known_values():
    # 100 values from 0.01 to 1.00
    data = np.linspace(0.01, 1.00, 100)
    var95, cvar95 = compute_var_cvar(data, alpha=0.95)

    # 95th percentile of 0.01..1.00 is ~0.9505
    assert 0.94 <= var95 <= 0.96
    # Tail is values in the top 5% (from 0.96 to 1.00)
    assert cvar95 >= var95
    assert 0.97 <= cvar95 <= 1.00


def test_compute_var_cvar_tied_discrete_values():
    """
    Test CVaR when multiple observations in discrete empirical distribution
    are tied at or below VaR.
    """
    # 80 zeros, 20 values of 0.05
    data = np.array([0.0] * 80 + [0.05] * 20)
    var95, cvar95 = compute_var_cvar(data, alpha=0.95)

    assert pytest.approx(var95) == 0.05
    assert pytest.approx(cvar95) == 0.05

    # 96 zeros, 4 values of 0.10. Total 100 values.
    # At alpha=0.95, worst 5% is k=5 values: four 0.10 and one 0.00
    # Expected Shortfall = (4 * 0.10 + 1 * 0.00) / 5 = 0.08
    data2 = np.array([0.0] * 96 + [0.10] * 4)
    var95_2, cvar95_2 = compute_var_cvar(data2, alpha=0.95)
    assert pytest.approx(cvar95_2) == 0.08


def test_compute_var_cvar_uses_fractional_empirical_tail_mass():
    # tail mass = (1 - 0.625) * 4 = 1.5 observations. Expected Shortfall is
    # therefore the worst observation plus half the next, divided by 1.5.
    data = np.array([0.10, 0.20, 0.30, 0.40])
    _, cvar = compute_var_cvar(data, alpha=0.625)
    assert cvar == pytest.approx((0.40 + 0.5 * 0.30) / 1.5)


def test_compute_var_cvar_alpha_near_one_returns_worst_observation():
    data = np.array([0.01, 0.05, 0.20])
    _, cvar = compute_var_cvar(data, alpha=np.nextafter(1.0, 0.0))
    assert cvar == pytest.approx(0.20)


def test_compute_var_cvar_large_finite_values_does_not_overflow_mean():
    data = np.full(4, 1e308)
    var, cvar = compute_var_cvar(data, alpha=0.50)
    assert math.isfinite(var)
    assert math.isfinite(cvar)
    assert var == pytest.approx(1e308)
    assert cvar == pytest.approx(1e308)


def test_compute_var_cvar_identical_values():
    data = np.full(50, 0.05)
    var95, cvar95 = compute_var_cvar(data, alpha=0.95)
    assert pytest.approx(var95) == 0.05
    assert pytest.approx(cvar95) == 0.05


def test_compute_var_cvar_single_value():
    data = np.array([0.08])
    var95, cvar95 = compute_var_cvar(data, alpha=0.95)
    assert pytest.approx(var95) == 0.08
    assert pytest.approx(cvar95) == 0.08


@pytest.mark.parametrize(
    "invalid_alpha",
    [0.0, 1.0, -0.1, 1.1, math.nan, math.inf, True, False],
)
def test_compute_var_cvar_rejects_invalid_alpha(invalid_alpha):
    with pytest.raises(ValueError):
        compute_var_cvar(np.array([0.01, 0.02]), alpha=invalid_alpha)


def test_compute_var_cvar_rejects_empty():
    with pytest.raises(ValueError):
        compute_var_cvar(np.array([]), alpha=0.95)


def test_compute_var_cvar_rejects_nan_inf():
    with pytest.raises(ValueError):
        compute_var_cvar(np.array([0.01, math.nan]), alpha=0.95)
    with pytest.raises(ValueError):
        compute_var_cvar(np.array([0.01, math.inf]), alpha=0.95)


@pytest.mark.parametrize(
    "invalid_drawdowns",
    [
        np.array([0.01, -0.02]),
        np.array([[0.01, 0.02]]),
        np.array(["bad", "data"]),
        np.array([0.01 + 0.02j]),
        np.array([True, False]),
        np.array([0.01, True], dtype=object),
    ],
)
def test_compute_var_cvar_rejects_invalid_drawdown_data(invalid_drawdowns):
    with pytest.raises(ValueError):
        compute_var_cvar(invalid_drawdowns)


# ---------------------------------------------------------------------------
# Kernel Smoothing Tests
# ---------------------------------------------------------------------------


def test_smooth_curve_gaussian_basic():
    x = np.linspace(0.001, 0.02, 40)
    # Linear trend + random noise
    y_true = np.linspace(0.2, 0.8, 40)
    rng = np.random.default_rng(42)
    y_noisy = np.clip(y_true + rng.normal(0, 0.08, size=40), 0.0, 1.0)

    y_smooth = smooth_curve_gaussian(x, y_noisy, bandwidth=0.003)

    assert len(y_smooth) == len(x)
    assert np.all(y_smooth >= 0.0)
    assert np.all(y_smooth <= 1.0)
    # Smoothing reduces noise variance
    noise_variance = float(np.var(y_noisy - y_true))
    smooth_variance = float(np.var(y_smooth - y_true))
    assert smooth_variance < noise_variance


def test_smooth_curve_gaussian_single_and_empty():
    empty_res = smooth_curve_gaussian(np.array([]), np.array([]), bandwidth=0.01)
    assert len(empty_res) == 0

    single_res = smooth_curve_gaussian(np.array([0.01]), np.array([0.7]), bandwidth=0.01)
    assert len(single_res) == 1
    assert pytest.approx(single_res[0]) == 0.7


@pytest.mark.parametrize(
    "x,y",
    [
        (np.array([0.01, 0.005]), np.array([0.2, 0.3])),
        (np.array([0.005, 0.01]), np.array([0.2, 1.1])),
        (np.array([0.005, math.nan]), np.array([0.2, 0.3])),
        (np.array([[0.005, 0.01]]), np.array([[0.2, 0.3]])),
        (np.array([0.005 + 0.001j, 0.01]), np.array([0.2, 0.3])),
        (np.array([0.005, 0.01]), np.array([False, True])),
    ],
)
def test_smooth_curve_gaussian_rejects_invalid_curve_data(x, y):
    with pytest.raises(ValueError):
        smooth_curve_gaussian(x, y, bandwidth=0.001)


def test_smooth_curve_gaussian_handles_large_finite_coordinates():
    result = smooth_curve_gaussian(
        np.array([-1e308, 1e308]), np.array([0.2, 0.8]), bandwidth=1.0
    )
    np.testing.assert_array_equal(result, np.array([0.2, 0.8]))


def test_dynamic_bandwidth_custom_grid(monkeypatch):
    """
    Test that automatic bandwidth derivation uses the actual grid spacing
    when custom risk_levels are passed (2 * median(diff(risk_levels))).
    """
    import src.optimization

    rules = _standard_rules()
    synth = SyntheticConfig(seed=42, n_trades=50)
    mc_cfg = MonteCarloConfig(n_simulations=50, seed=123)
    # Custom grid with steps 0.004, 0.006, 0.008 -> median step is 0.006
    custom_levels = (0.002, 0.006, 0.012, 0.020)
    opt_cfg = OptimizationConfig(risk_levels=custom_levels)

    captured_bandwidths = []
    original_smooth = src.optimization.smooth_curve_gaussian

    def mock_smooth(x, y, bandwidth):
        captured_bandwidths.append(bandwidth)
        return original_smooth(x, y, bandwidth)

    monkeypatch.setattr(src.optimization, "smooth_curve_gaussian", mock_smooth)
    res = optimize_risk_per_trade(
        rules, synthetic_config=synth, mc_config=mc_cfg, opt_config=opt_cfg
    )

    expected_bandwidth = 2.0 * float(np.median(np.diff(custom_levels)))  # 2 * 0.006 = 0.012
    assert len(captured_bandwidths) == 1
    assert pytest.approx(captured_bandwidths[0]) == expected_bandwidth


# ---------------------------------------------------------------------------
# Optimization Engine Tests (Synthetic Mode)
# ---------------------------------------------------------------------------


def test_optimize_risk_synthetic_basic():
    rules = _standard_rules()
    synth = SyntheticConfig(
        win_rate=0.55,
        avg_win_r=2.0,
        avg_loss_r=1.0,
        n_trades=100,
        seed=42,
    )
    mc_cfg = MonteCarloConfig(n_simulations=500, seed=100)
    opt_cfg = OptimizationConfig(
        risk_levels=(0.005, 0.01, 0.015, 0.02),
        fres_lambda=1.0,
        fres_gamma=0.5,
        fres_delta=0.5,
    )

    result = optimize_risk_per_trade(
        rules=rules,
        synthetic_config=synth,
        mc_config=mc_cfg,
        opt_config=opt_cfg,
    )

    assert isinstance(result, OptimizationResult)
    assert len(result.evaluations) == 4
    assert result.optimal_risk_raw in (0.005, 0.01, 0.015, 0.02)
    assert result.optimal_risk_smoothed in (0.005, 0.01, 0.015, 0.02)
    assert result.optimal_risk_fres in (0.005, 0.01, 0.015, 0.02)
    assert 0.0 <= result.max_probability_pass_raw <= 1.0
    assert 0.0 <= result.max_probability_pass_smoothed <= 1.0

    # Verify per-candidate evaluation data
    for ev in result.evaluations:
        assert isinstance(ev, RiskLevelEvaluation)
        assert 0.0 <= ev.probability_pass <= 1.0
        assert 0.0 <= ev.probability_fail <= 1.0
        assert ev.var_95 <= ev.cvar_95
        assert ev.var_99 <= ev.cvar_99
        total_probability = (
            ev.probability_pass + ev.probability_fail + ev.timeout_probability
        )
        assert pytest.approx(total_probability) == 1.0


def test_optimize_risk_common_random_numbers():
    """
    Test that Common Random Numbers (CRN) are used across candidate risk levels.
    When evaluating different risk levels with a master seed, all candidate runs
    must receive the exact same seed, and individual simulation paths must be
    driven by the identical trade outcomes.
    """
    rules = _standard_rules(profit_target_pct=0.50, max_drawdown_pct=0.50)
    synth = SyntheticConfig(seed=42, n_trades=1)  # single trade per simulation
    mc_cfg = MonteCarloConfig(n_simulations=100, seed=777)
    opt_cfg = OptimizationConfig(risk_levels=(0.005, 0.010))

    res = optimize_risk_per_trade(
        rules, synthetic_config=synth, mc_config=mc_cfg, opt_config=opt_cfg
    )

    r1_eval = res.evaluations[0]
    r2_eval = res.evaluations[1]

    # Verify identical seeds passed to both evaluations
    assert r1_eval.mc_result.seed == 777
    assert r2_eval.mc_result.seed == 777

    # Verify that the underlying generated trade outcomes on path i are identical:
    # (final_equity - 100K) / (risk * 100K) should be equal across runs for each simulation
    pnl_1 = r1_eval.mc_result.final_equities - 100_000.0
    pnl_2 = r2_eval.mc_result.final_equities - 100_000.0
    r_mult_1 = pnl_1 / (0.005 * 100_000.0)
    r_mult_2 = pnl_2 / (0.010 * 100_000.0)
    np.testing.assert_allclose(r_mult_1, r_mult_2, atol=1e-10)


def test_optimize_risk_common_random_numbers_without_caller_seed():
    """A non-reproducible run must still use one run-local CRN stream."""
    rules = _standard_rules(profit_target_pct=0.50, max_drawdown_pct=0.50)
    synth = SyntheticConfig(seed=None, n_trades=1)
    mc_cfg = MonteCarloConfig(n_simulations=30, seed=None, n_bootstrap=0)
    opt_cfg = OptimizationConfig(risk_levels=(0.005, 0.010))

    res = optimize_risk_per_trade(
        rules, synthetic_config=synth, mc_config=mc_cfg, opt_config=opt_cfg
    )

    first, second = res.evaluations
    assert first.mc_result.seed is not None
    assert first.mc_result.seed == second.mc_result.seed
    r_mult_1 = (first.mc_result.final_equities - 100_000.0) / 500.0
    r_mult_2 = (second.mc_result.final_equities - 100_000.0) / 1_000.0
    np.testing.assert_allclose(r_mult_1, r_mult_2, atol=1e-10)


def test_optimize_risk_rejects_adaptive_simulation_counts():
    rules = _standard_rules()
    synth = SyntheticConfig(seed=42, n_trades=10)
    mc_cfg = MonteCarloConfig(
        n_simulations=None,
        min_simulations=10,
        max_simulations=20,
        batch_size=10,
        n_bootstrap=0,
    )
    with pytest.raises(ValueError, match="fixed n_simulations"):
        optimize_risk_per_trade(
            rules,
            synthetic_config=synth,
            mc_config=mc_cfg,
            opt_config=OptimizationConfig(risk_levels=(0.005, 0.010)),
        )


def test_optimize_risk_confidence_level_customization():
    """
    Test that setting confidence_level != 0.95 (e.g. 0.90) correctly affects
    Monte Carlo confidence intervals while var_95/cvar_95 strictly match
    compute_var_cvar(drawdowns, 0.95) and var_99/cvar_99 match
    compute_var_cvar(drawdowns, 0.99).
    """
    rules = _standard_rules()
    synth = SyntheticConfig(seed=42, n_trades=50)
    mc_cfg = MonteCarloConfig(n_simulations=200, seed=123)
    opt_cfg = OptimizationConfig(risk_levels=(0.005, 0.01), confidence_level=0.90)

    res = optimize_risk_per_trade(
        rules, synthetic_config=synth, mc_config=mc_cfg, opt_config=opt_cfg
    )

    for ev in res.evaluations:
        dds = ev.mc_result.max_drawdowns_historical
        expected_v95, expected_cv95 = compute_var_cvar(dds, alpha=0.95)
        expected_v99, expected_cv99 = compute_var_cvar(dds, alpha=0.99)
        v90, _ = compute_var_cvar(dds, alpha=0.90)

        # Exact match with alpha=0.95 and alpha=0.99
        assert pytest.approx(ev.var_95) == expected_v95
        assert pytest.approx(ev.cvar_95) == expected_cv95
        assert pytest.approx(ev.var_99) == expected_v99
        assert pytest.approx(ev.cvar_99) == expected_cv99

        # Ensure it was not computed using alpha=0.90
        if not np.all(dds == dds[0]):
            assert ev.var_95 >= v90 - 1e-12


def test_optimize_risk_reproducibility():
    rules = _standard_rules()
    synth = SyntheticConfig(seed=42, n_trades=100)
    mc_cfg = MonteCarloConfig(n_simulations=200, seed=999)
    opt_cfg = OptimizationConfig(risk_levels=(0.005, 0.01, 0.015))

    res1 = optimize_risk_per_trade(
        rules, synthetic_config=synth, mc_config=mc_cfg, opt_config=opt_cfg
    )
    res2 = optimize_risk_per_trade(
        rules, synthetic_config=synth, mc_config=mc_cfg, opt_config=opt_cfg
    )

    assert res1.optimal_risk_raw == res2.optimal_risk_raw
    assert res1.optimal_risk_smoothed == res2.optimal_risk_smoothed
    assert res1.optimal_risk_fres == res2.optimal_risk_fres
    assert np.array_equal(res1.probabilities_pass, res2.probabilities_pass)
    assert np.array_equal(res1.fres_scores, res2.fres_scores)


def test_optimize_risk_array_read_only_protection():
    rules = _standard_rules()
    synth = SyntheticConfig(seed=42, n_trades=50)
    mc_cfg = MonteCarloConfig(n_simulations=100, seed=123)
    opt_cfg = OptimizationConfig(risk_levels=(0.005, 0.01))

    res = optimize_risk_per_trade(
        rules, synthetic_config=synth, mc_config=mc_cfg, opt_config=opt_cfg
    )

    with pytest.raises(ValueError, match="read-only"):
        res.probabilities_pass[0] = 0.99
    with pytest.raises(ValueError, match="read-only"):
        res.fres_scores[0] = 0.99
    with pytest.raises(ValueError, match="read-only"):
        res.p95_rule_drawdowns[0] = 0.99
    with pytest.raises(ValueError, match="read-only"):
        res.cvar_95_rule_values[0] = 0.99


# ---------------------------------------------------------------------------
# Optimization Engine Tests (Resampled Mode)
# ---------------------------------------------------------------------------


def test_optimize_risk_resampled_mode():
    rules = _standard_rules()
    # 50 trades with +2R win / -1R loss
    trades = _make_trades([2.0, -1.0, 1.5, -0.5, 2.5, -1.0] * 10)
    mc_cfg = MonteCarloConfig(n_simulations=200, seed=42)
    opt_cfg = OptimizationConfig(risk_levels=(0.005, 0.01))

    # Must require assume_iid=True
    with pytest.raises(ValueError, match="assume_iid"):
        optimize_risk_per_trade(
            rules,
            trades=trades,
            assume_iid=False,
            mc_config=mc_cfg,
            opt_config=opt_cfg,
        )

    res = optimize_risk_per_trade(
        rules, trades=trades, assume_iid=True, mc_config=mc_cfg, opt_config=opt_cfg
    )
    assert len(res.evaluations) == 2
    assert res.evaluations[0].mc_result.source == "resampled"


# ---------------------------------------------------------------------------
# Property-Based Tests (Mandatory)
# ---------------------------------------------------------------------------


def test_property_positive_vs_negative_expectancy_optimization():
    """
    Property: A positive expectancy strategy achieves substantially higher
    peak pass probability and higher optimal FRES score than a negative one.
    """
    rules = _standard_rules(profit_target_pct=0.08, max_drawdown_pct=0.10)
    mc_cfg = MonteCarloConfig(n_simulations=500, seed=123)
    opt_cfg = OptimizationConfig(risk_levels=(0.005, 0.01, 0.015, 0.02))

    pos_synth = SyntheticConfig(win_rate=0.60, avg_win_r=2.0, avg_loss_r=1.0, n_trades=100, seed=1)
    neg_synth = SyntheticConfig(win_rate=0.30, avg_win_r=1.0, avg_loss_r=2.0, n_trades=100, seed=1)

    pos_res = optimize_risk_per_trade(
        rules, synthetic_config=pos_synth, mc_config=mc_cfg, opt_config=opt_cfg
    )
    neg_res = optimize_risk_per_trade(
        rules, synthetic_config=neg_synth, mc_config=mc_cfg, opt_config=opt_cfg
    )

    assert pos_res.max_probability_pass_raw > neg_res.max_probability_pass_raw + 0.30
    assert pos_res.max_fres_score > neg_res.max_fres_score


def test_property_risk_scaling_drawdown_tail_risk():
    """
    Property: As risk increases from conservative to aggressive, P95 and CVaR95
    max drawdowns increase monotonically or strictly increase at high risk levels.
    """
    rules = _standard_rules(profit_target_pct=0.20, max_drawdown_pct=0.20)
    synth = SyntheticConfig(win_rate=0.50, avg_win_r=1.5, avg_loss_r=1.0, n_trades=100, seed=42)
    mc_cfg = MonteCarloConfig(n_simulations=500, seed=100)
    opt_cfg = OptimizationConfig(risk_levels=(0.002, 0.005, 0.01, 0.02))

    res = optimize_risk_per_trade(
        rules, synthetic_config=synth, mc_config=mc_cfg, opt_config=opt_cfg
    )

    # 0.20% risk vs 2.00% risk: P95 drawdown must be significantly higher for 2.00%
    assert res.evaluations[-1].p95_max_drawdown > res.evaluations[0].p95_max_drawdown * 2.0
    assert res.evaluations[-1].cvar_95 > res.evaluations[0].cvar_95 * 2.0


def test_property_fres_penalizes_high_tail_risk():
    """
    Property: When high risk increases drawdown tail risk substantially, FRES
    favors a more conservative risk level than the raw pass probability peak.
    """
    # Tight drawdown limit (5%) with modest profit target (5%)
    rules = _standard_rules(
        profit_target_pct=0.05,
        max_drawdown_pct=0.05,
        daily_loss_limit_pct=0.03,
    )
    synth = SyntheticConfig(win_rate=0.50, avg_win_r=1.5, avg_loss_r=1.0, n_trades=100, seed=42)
    mc_cfg = MonteCarloConfig(n_simulations=500, seed=100)
    opt_cfg = OptimizationConfig(
        risk_levels=(0.002, 0.005, 0.01, 0.02),
        fres_lambda=2.0,  # strong penalty on failures
        fres_gamma=1.5,   # strong penalty on P95 drawdown
        fres_delta=1.5,   # strong penalty on CVaR
    )

    res = optimize_risk_per_trade(
        rules, synthetic_config=synth, mc_config=mc_cfg, opt_config=opt_cfg
    )

    # FRES optimum should be conservative (<= raw optimum)
    assert res.optimal_risk_fres <= res.optimal_risk_raw


def test_fres_preserves_drawdown_overshoot_severity():
    rules = _standard_rules(
        profit_target_pct=0.10,
        max_drawdown_pct=0.05,
        daily_loss_limit_pct=0.50,
    )
    trades = _make_trades([-20.0])
    opt_cfg = OptimizationConfig(
        risk_levels=(0.01,), fres_lambda=1.0, fres_gamma=1.0, fres_delta=1.0
    )
    result = optimize_risk_per_trade(
        rules,
        trades=trades,
        assume_iid=True,
        mc_config=MonteCarloConfig(n_simulations=1, seed=7, n_bootstrap=0),
        opt_config=opt_cfg,
    )

    evaluation = result.evaluations[0]
    assert evaluation.p95_max_drawdown == pytest.approx(0.20)
    assert evaluation.cvar_95 == pytest.approx(0.20)
    # Both tail terms are four times the 5% budget. Clipping them to 1 would
    # incorrectly produce -3 instead of preserving the overshoot severity.
    assert evaluation.fres_score == pytest.approx(-9.0)


def test_fres_static_mode_penalizes_profitable_peak_giveback():
    """FRES tail risk remains historical even when static-rule use is zero."""
    rules = FundedAccountRules(
        initial_balance=100_000.0,
        profit_target_pct=0.50,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.99,
        risk_per_trade=0.01,
        drawdown_mode="static",
    )
    # Seed 1 resamples source indices [0, 1]: +20R reaches 120k, then -12R
    # finishes at 108k. Historical drawdown is 10%, while static rule drawdown
    # is exactly zero because equity never falls below the initial balance.
    trades = [
        Trade(r_result=20.0, trade_id="gain", date="2024-01-01"),
        Trade(r_result=-12.0, trade_id="giveback", date="2024-01-02"),
    ]
    result = optimize_risk_per_trade(
        rules,
        trades=trades,
        assume_iid=True,
        mc_config=MonteCarloConfig(n_simulations=1, seed=1, n_bootstrap=0),
        opt_config=OptimizationConfig(
            risk_levels=(0.01,),
            fres_lambda=0.0,
            fres_gamma=1.0,
            fres_delta=1.0,
        ),
    )

    evaluation = result.evaluations[0]
    assert evaluation.p95_max_drawdown == pytest.approx(0.10)
    assert evaluation.p95_rule_drawdown == 0.0
    assert evaluation.cvar_95_rule_drawdown == 0.0
    assert evaluation.cvar_95 == pytest.approx(0.10)
    assert evaluation.fres_score == pytest.approx(-2.0)


def test_plausible_risk_interval():
    """
    Test that the exact plausible candidate set and its envelope contain the raw
    CRN-paired empirical winner.
    """
    rules = _standard_rules(profit_target_pct=0.10, max_drawdown_pct=0.10)
    synth = SyntheticConfig(win_rate=0.55, avg_win_r=2.0, avg_loss_r=1.0, n_trades=100, seed=42)
    mc_cfg = MonteCarloConfig(n_simulations=500, seed=100)
    opt_cfg = OptimizationConfig(risk_levels=(0.005, 0.008, 0.010, 0.012, 0.015, 0.020))

    res = optimize_risk_per_trade(
        rules, synthetic_config=synth, mc_config=mc_cfg, opt_config=opt_cfg
    )

    assert res.optimal_risk_raw in res.plausible_risk_levels
    assert res.plausible_risk_min == min(res.plausible_risk_levels)
    assert res.plausible_risk_max == max(res.plausible_risk_levels)
    assert res.plausible_risk_min <= res.optimal_risk_raw <= res.plausible_risk_max
    assert res.plausible_risk_min >= res.config.get_risk_levels()[0]
    assert res.plausible_risk_max <= res.config.get_risk_levels()[-1]


def test_plausible_risks_exclude_deterministically_inferior_candidate():
    rules = _standard_rules(
        profit_target_pct=0.10,
        max_drawdown_pct=0.50,
        daily_loss_limit_pct=0.50,
    )
    # Every resampled path is +10R: 0.5% risk times out at +5%, while 1%
    # reaches the +10% target. Ten concordant directional results provide
    # enough evidence for the exact paired test to exclude 0.5%.
    result = optimize_risk_per_trade(
        rules,
        trades=_make_trades([10.0]),
        assume_iid=True,
        mc_config=MonteCarloConfig(n_simulations=10, seed=11, n_bootstrap=0),
        opt_config=OptimizationConfig(risk_levels=(0.005, 0.010)),
    )
    assert result.optimal_risk_raw == 0.010
    assert result.plausible_risk_levels == (0.010,)
    assert result.plausible_risk_min == result.plausible_risk_max == 0.010


def test_plausible_risks_do_not_claim_zero_uncertainty_from_one_path():
    rules = _standard_rules(
        profit_target_pct=0.10,
        max_drawdown_pct=0.50,
        daily_loss_limit_pct=0.50,
    )
    # The observed path favors 1%, but one discordant Monte Carlo outcome is
    # not enough evidence to exclude 0.5%. A plug-in normal SE incorrectly
    # becomes zero for this sample and reports false certainty.
    result = optimize_risk_per_trade(
        rules,
        trades=_make_trades([10.0]),
        assume_iid=True,
        mc_config=MonteCarloConfig(n_simulations=1, seed=11, n_bootstrap=0),
        opt_config=OptimizationConfig(risk_levels=(0.005, 0.010)),
    )

    assert result.optimal_risk_raw == 0.010
    assert result.plausible_risk_levels == (0.005, 0.010)


def test_fres_sensitivity_analysis():
    """
    Test that compute_fres_sensitivity generates optimal risk levels for various
    risk-aversion combinations and that increasing failure penalty (lambda)
    encourages equal or lower risk levels.
    """
    rules = _standard_rules(profit_target_pct=0.10, max_drawdown_pct=0.10)
    synth = SyntheticConfig(win_rate=0.50, avg_win_r=1.5, avg_loss_r=1.0, n_trades=100, seed=42)
    mc_cfg = MonteCarloConfig(n_simulations=300, seed=100)
    opt_cfg = OptimizationConfig(risk_levels=(0.005, 0.010, 0.015, 0.020))

    res = optimize_risk_per_trade(
        rules, synthetic_config=synth, mc_config=mc_cfg, opt_config=opt_cfg
    )

    sens = compute_fres_sensitivity(
        res,
        lambdas=(0.0, 1.0, 3.0),
        gammas=(0.0, 1.0),
        deltas=(0.0, 1.0),
    )

    assert len(sens) == 3 * 2 * 2
    # When lambda increases with fixed gamma=1.0, delta=1.0, optimal risk shouldn't increase
    r_low_lambda = sens[(0.0, 1.0, 1.0)]
    r_high_lambda = sens[(3.0, 1.0, 1.0)]
    assert r_high_lambda <= r_low_lambda + 1e-12


@pytest.mark.parametrize(
    "kwargs",
    [
        {"lambdas": ()},
        {"gammas": (-0.1,)},
        {"deltas": (math.nan,)},
        {"lambdas": (True,)},
    ],
)
def test_fres_sensitivity_rejects_invalid_weights(kwargs):
    rules = _standard_rules()
    result = optimize_risk_per_trade(
        rules,
        synthetic_config=SyntheticConfig(seed=2, n_trades=5),
        mc_config=MonteCarloConfig(n_simulations=3, seed=4, n_bootstrap=0),
        opt_config=OptimizationConfig(risk_levels=(0.005,)),
    )
    with pytest.raises(ValueError):
        compute_fres_sensitivity(result, **kwargs)

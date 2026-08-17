"""
Phase 5 tests: Monte Carlo simulation engine.

Tests cover:
  - Configuration validation (MonteCarloConfig)
  - MonteCarloResult validation and invariants
  - Wilson confidence intervals (formulas, edge cases, input validation)
  - Wilson standard error (strictly positive at boundaries, monotonic in n)
  - Bootstrap quantile confidence intervals (memory-safe chunked multi-quantile)
  - Synthetic input mode (fixed-n, convergence, reproducibility, prefix property)
  - Resampled input mode (date preservation, reproducibility, input validation, assume_iid guard)
  - Outcome partition (P_pass + P_fail + P_timeout == 1, primary failure breakdown)
  - Inclusive vs primary failure attribution
  - Historical peak-to-trough drawdown reporting
  - Undefined statistics (NaN when 0 passes)
  - Read-only guarantees on result arrays and mappings
  - Boundary convergence (0 passes and 100% passes)
  - Property tests (positive vs negative expectancy, risk scaling)
  - Edge cases (100% win, 100% loss, n_bootstrap=0, n_simulations=1)
"""

import math

import numpy as np
import pytest

from src.monte_carlo import (
    CODE_COMPLETED,
    CODE_DAILY_LOSS,
    CODE_MAX_DRAWDOWN,
    CODE_MAX_TRADES,
    CODE_PROFIT_TARGET,
    TERMINAL_CONDITIONS,
    MonteCarloConfig,
    MonteCarloResult,
    _simulate_path_fast,
    bootstrap_quantile_ci,
    bootstrap_quantiles_ci,
    run_monte_carlo,
    wilson_ci,
    wilson_se,
)
from src.engine import run_simulation
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
    max_trades: int | None = None,
    daily_loss_base: str = "initial",
    drawdown_mode: str = "static",
) -> FundedAccountRules:
    """Create a FundedAccountRules instance with sensible defaults."""
    return FundedAccountRules(
        initial_balance=initial_balance,
        profit_target_pct=profit_target_pct,
        max_drawdown_pct=max_drawdown_pct,
        daily_loss_limit_pct=daily_loss_limit_pct,
        risk_per_trade=risk_per_trade,
        max_trades=max_trades,
        daily_loss_base=daily_loss_base,  # type: ignore[arg-type]
        drawdown_mode=drawdown_mode,  # type: ignore[arg-type]
    )


def _make_trades(r_results: list[float], start_date: str = "2024-01-01") -> list[Trade]:
    """Create minimal Trade objects with sequential dates (one per trade)."""
    trades = []
    for i, r in enumerate(r_results):
        trades.append(
            Trade(
                r_result=r,
                trade_id=f"t{i:04d}",
                date=f"2024-01-{i+1:02d}",
            )
        )
    return trades


# ---------------------------------------------------------------------------
# MonteCarloConfig validation tests
# ---------------------------------------------------------------------------


def test_mc_config_defaults():
    cfg = MonteCarloConfig()
    assert cfg.n_simulations == 10_000
    assert cfg.seed is None
    assert cfg.se_target == 0.005
    assert cfg.min_simulations == 1_000
    assert cfg.max_simulations == 100_000
    assert cfg.batch_size == 1_000
    assert cfg.confidence_level == 0.95
    assert cfg.n_bootstrap == 1_000


def test_mc_config_convergence_mode_valid():
    cfg = MonteCarloConfig(n_simulations=None, se_target=0.01, seed=42)
    assert cfg.n_simulations is None
    assert cfg.se_target == 0.01
    assert cfg.seed == 42


@pytest.mark.parametrize("invalid_n", [0, -1, -100, 1.5, True, False, "1000"])
def test_mc_config_rejects_invalid_n_simulations(invalid_n):
    with pytest.raises(ValueError, match="n_simulations"):
        MonteCarloConfig(n_simulations=invalid_n)


@pytest.mark.parametrize("invalid_seed", [-1, 1.5, True, False, "42"])
def test_mc_config_rejects_invalid_seed(invalid_seed):
    with pytest.raises(ValueError, match="seed"):
        MonteCarloConfig(seed=invalid_seed)


@pytest.mark.parametrize("invalid_se", [0.0, 1.0, -0.01, 1.5, math.nan, math.inf])
def test_mc_config_rejects_invalid_se_target(invalid_se):
    with pytest.raises(ValueError, match="se_target"):
        MonteCarloConfig(se_target=invalid_se)


@pytest.mark.parametrize("field_name", ["min_simulations", "max_simulations", "batch_size"])
@pytest.mark.parametrize("invalid_val", [0, -1, 1.5, True, False, "1000"])
def test_mc_config_rejects_invalid_simulation_counts(field_name, invalid_val):
    kwargs = {field_name: invalid_val}
    with pytest.raises(ValueError, match=field_name):
        MonteCarloConfig(**kwargs)


def test_mc_config_rejects_max_less_than_min():
    with pytest.raises(ValueError, match="max_simulations .* must be >= min_simulations"):
        MonteCarloConfig(min_simulations=5000, max_simulations=1000)


@pytest.mark.parametrize(
    "invalid_cl",
    [0.0, 1.0, -0.1, 1.1, math.nan, math.inf, np.nextafter(0.0, 1.0)],
)
def test_mc_config_rejects_invalid_confidence_level(invalid_cl):
    with pytest.raises(ValueError, match="confidence_level"):
        MonteCarloConfig(confidence_level=invalid_cl)


@pytest.mark.parametrize("invalid_boot", [-1, 1.5, True, False, "1000"])
def test_mc_config_rejects_invalid_n_bootstrap(invalid_boot):
    with pytest.raises(ValueError, match="n_bootstrap"):
        MonteCarloConfig(n_bootstrap=invalid_boot)


def test_mc_config_accepts_zero_n_bootstrap():
    cfg = MonteCarloConfig(n_bootstrap=0)
    assert cfg.n_bootstrap == 0


# ---------------------------------------------------------------------------
# Wilson CI and Wilson SE tests
# ---------------------------------------------------------------------------


def test_wilson_ci_known_values():
    # 50/100 at 95% confidence: ~ [0.4038, 0.5962]
    lo, hi = wilson_ci(50, 100, 0.95)
    assert 0.40 < lo < 0.41
    assert 0.59 < hi < 0.60


def test_wilson_ci_zero_successes():
    lo, hi = wilson_ci(0, 100, 0.95)
    assert lo == 0.0
    assert 0.0 < hi < 0.05  # Upper bound is positive and small (~0.036)


def test_wilson_ci_all_successes():
    lo, hi = wilson_ci(100, 100, 0.95)
    assert 0.95 < lo < 1.0
    assert hi == 1.0


@pytest.mark.parametrize("invalid_n", [0, -1, 1.5, True, False])
def test_wilson_ci_rejects_invalid_n(invalid_n):
    with pytest.raises(ValueError, match="n"):
        wilson_ci(10, invalid_n)


@pytest.mark.parametrize("invalid_s", [-1, 101, 1.5, True, False])
def test_wilson_ci_rejects_invalid_successes(invalid_s):
    with pytest.raises(ValueError, match="successes"):
        wilson_ci(invalid_s, 100)


@pytest.mark.parametrize(
    "invalid_cl",
    [0.0, 1.0, -0.1, 1.1, math.nan, math.inf, np.nextafter(0.0, 1.0)],
)
def test_wilson_ci_rejects_invalid_cl(invalid_cl):
    with pytest.raises(ValueError, match="confidence_level"):
        wilson_ci(50, 100, invalid_cl)


def test_wilson_se_boundary_strictly_positive():
    """Wilson SE must be strictly positive even at p_hat = 0 and p_hat = 1."""
    se_0 = wilson_se(0, 100, 0.95)
    se_1 = wilson_se(100, 100, 0.95)
    assert se_0 > 0.0
    assert se_1 > 0.0
    assert math.isclose(se_0, se_1, rel_tol=1e-9)

    # Must decrease monotonically with n
    se_0_large_n = wilson_se(0, 1000, 0.95)
    assert se_0_large_n < se_0


# ---------------------------------------------------------------------------
# Bootstrap quantile CI tests
# ---------------------------------------------------------------------------


def test_bootstrap_quantile_ci_basic():
    rng = np.random.default_rng(42)
    data = np.linspace(0.0, 1.0, 1000)
    lo, hi = bootstrap_quantile_ci(data, 50.0, 500, 0.95, rng)
    assert 0.45 < lo < 0.55
    assert 0.45 < hi < 0.55
    assert lo <= hi


def test_bootstrap_quantiles_ci_multi():
    """Computing multiple quantiles simultaneously in chunks must return matching CIs."""
    rng = np.random.default_rng(42)
    data = np.linspace(0.0, 1.0, 1000)
    cis = bootstrap_quantiles_ci(
        data, [50.0, 95.0, 99.0], 500, 0.95, rng, batch_size=50
    )
    assert len(cis) == 3
    assert 0.45 < cis[0][0] < 0.55  # median
    assert 0.90 < cis[1][0] < 0.99  # p95
    assert 0.95 < cis[2][0] <= 1.0  # p99


@pytest.mark.parametrize("invalid_q", [-0.1, 100.1, math.nan, math.inf])
def test_bootstrap_quantile_ci_rejects_invalid_quantile(invalid_q):
    rng = np.random.default_rng(42)
    data = np.array([0.1, 0.2, 0.3])
    with pytest.raises(ValueError, match="quantiles_pct"):
        bootstrap_quantile_ci(data, invalid_q, 100, 0.95, rng)


def test_bootstrap_quantile_ci_rejects_empty():
    rng = np.random.default_rng(42)
    data = np.array([])
    with pytest.raises(ValueError, match="values"):
        bootstrap_quantile_ci(data, 50.0, 100, 0.95, rng)


@pytest.mark.parametrize(
    "invalid_values",
    [
        np.array([0.1, math.nan]),
        np.array([0.1, math.inf]),
        np.array([1 + 2j]),
        np.array(["0.1", "0.2"]),
    ],
)
def test_bootstrap_quantile_ci_rejects_nonfinite_or_nonreal_values(invalid_values):
    with pytest.raises(ValueError, match="values"):
        bootstrap_quantile_ci(
            invalid_values, 50.0, 10, 0.95, np.random.default_rng(42)
        )


@pytest.mark.parametrize("invalid_q", [True, False, "95"])
def test_bootstrap_quantile_ci_rejects_non_numeric_quantiles(invalid_q):
    with pytest.raises(ValueError, match="quantiles_pct"):
        bootstrap_quantile_ci(
            np.array([0.1, 0.2]),
            invalid_q,  # type: ignore[arg-type]
            10,
            0.95,
            np.random.default_rng(42),
        )


# ---------------------------------------------------------------------------
# MonteCarloResult validation and invariants
# ---------------------------------------------------------------------------


def test_mc_result_invariants_enforced():
    n = 10
    tc = np.zeros(n, dtype=np.int8)
    fe = np.full(n, 100000.0)
    te = np.full(n, 50, dtype=np.int64)
    dd = np.full(n, 0.05)
    vm = np.zeros(n, dtype=bool)
    vd = np.zeros(n, dtype=bool)
    tcounts = {k: (n if k == "profit_target" else 0) for k in TERMINAL_CONDITIONS}

    # Sum of partition probabilities != 1 raises
    with pytest.raises(ValueError, match="must equal 1"):
        MonteCarloResult(
            n_simulations=n,
            seed=42,
            source="synthetic",
            risk_per_trade=0.01,
            convergence_mode=False,
            converged=True,
            se_target=0.005,
            probability_pass=0.5,
            probability_fail=0.4,
            timeout_probability=0.2,  # sums to 1.1
            failure_probability_max_drawdown=0.0,
            failure_probability_daily_loss=0.0,
            failure_probability_max_drawdown_primary=0.0,
            failure_probability_daily_loss_primary=0.0,
            pass_ci=(0.4, 0.6),
            fail_ci=(0.3, 0.5),
            timeout_ci=(0.1, 0.3),
            median_trades_to_pass=50.0,
            mean_trades_to_pass=50.0,
            median_max_drawdown=0.05,
            p95_max_drawdown=0.05,
            p99_max_drawdown=0.05,
            median_max_drawdown_ci=(0.04, 0.06),
            p95_max_drawdown_ci=(0.04, 0.06),
            p99_max_drawdown_ci=(0.04, 0.06),
            se_probability_pass=0.01,
            terminal_codes=tc,
            final_equities=fe,
            trades_executed=te,
            max_drawdowns_historical=dd,
            violated_max_drawdown=vm,
            violated_daily_loss=vd,
            terminal_counts=tcounts,
        )


def test_mc_result_read_only_protection():
    """Exposed arrays and mapping on MonteCarloResult must be protected against mutation."""
    rules = _standard_rules()
    synth = SyntheticConfig(n_trades=20)
    mc_cfg = MonteCarloConfig(n_simulations=10, seed=42, n_bootstrap=0)

    res = run_monte_carlo(rules, synthetic_config=synth, config=mc_cfg)

    # Numpy arrays are read-only
    assert not res.terminal_codes.flags.writeable
    with pytest.raises(ValueError, match="read-only"):
        res.terminal_codes[0] = 99

    assert not res.final_equities.flags.writeable
    with pytest.raises(ValueError, match="read-only"):
        res.final_equities[0] = 0.0

    # terminal_counts is read-only MappingProxy
    with pytest.raises(TypeError):
        res.terminal_counts["profit_target"] = 999  # type: ignore[index]


# ---------------------------------------------------------------------------
# run_monte_carlo execution tests — Synthetic mode
# ---------------------------------------------------------------------------


def test_mc_synthetic_fixed_n():
    rules = _standard_rules(
        initial_balance=100_000.0,
        profit_target_pct=0.10,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.05,
        risk_per_trade=0.01,
    )
    synth = SyntheticConfig(
        win_rate=0.55,
        avg_win_r=2.0,
        avg_loss_r=1.0,
        n_trades=100,
        trades_per_day=5,
    )
    mc_cfg = MonteCarloConfig(n_simulations=100, seed=42, n_bootstrap=100)

    result = run_monte_carlo(rules, synthetic_config=synth, config=mc_cfg)

    assert result.n_simulations == 100
    assert result.source == "synthetic"
    assert result.risk_per_trade == 0.01
    assert result.convergence_mode is False
    assert result.converged is True
    assert 0.0 <= result.probability_pass <= 1.0
    assert 0.0 <= result.probability_fail <= 1.0
    assert 0.0 <= result.timeout_probability <= 1.0
    assert math.isclose(
        result.probability_pass + result.probability_fail + result.timeout_probability,
        1.0,
        abs_tol=1e-9,
    )
    assert result.pass_ci[0] <= result.probability_pass <= result.pass_ci[1]
    assert len(result.terminal_codes) == 100
    assert len(result.final_equities) == 100
    assert len(result.trades_executed) == 100
    assert len(result.max_drawdowns_historical) == 100
    assert result.median_max_drawdown_ci is not None


def test_mc_synthetic_reproducibility():
    rules = _standard_rules(risk_per_trade=0.01)
    synth = SyntheticConfig(win_rate=0.50, n_trades=50)
    mc_cfg = MonteCarloConfig(n_simulations=50, seed=1234, n_bootstrap=50)

    res1 = run_monte_carlo(rules, synthetic_config=synth, config=mc_cfg)
    res2 = run_monte_carlo(rules, synthetic_config=synth, config=mc_cfg)

    assert res1.probability_pass == res2.probability_pass
    assert res1.probability_fail == res2.probability_fail
    assert res1.median_max_drawdown == res2.median_max_drawdown
    np.testing.assert_array_equal(res1.terminal_codes, res2.terminal_codes)
    np.testing.assert_array_equal(res1.final_equities, res2.final_equities)
    assert res1.median_max_drawdown_ci == res2.median_max_drawdown_ci


def test_mc_synthetic_prefix_property():
    rules = _standard_rules(risk_per_trade=0.01)
    synth = SyntheticConfig(win_rate=0.50, n_trades=50)

    cfg_small = MonteCarloConfig(n_simulations=20, seed=999, n_bootstrap=0)
    cfg_large = MonteCarloConfig(n_simulations=50, seed=999, n_bootstrap=0)

    res_small = run_monte_carlo(rules, synthetic_config=synth, config=cfg_small)
    res_large = run_monte_carlo(rules, synthetic_config=synth, config=cfg_large)

    # The first 20 simulations of the large run must equal the small run
    np.testing.assert_array_equal(
        res_small.terminal_codes, res_large.terminal_codes[:20]
    )
    np.testing.assert_array_almost_equal(
        res_small.final_equities, res_large.final_equities[:20]
    )


def test_mc_synthetic_fixed_n_is_bitwise_invariant_to_batch_size():
    rules = _standard_rules(risk_per_trade=0.01)
    synth = SyntheticConfig(win_rate=0.50, n_trades=30, seed=17)
    cfg_one_batch = MonteCarloConfig(
        n_simulations=37, seed=999, batch_size=100, n_bootstrap=20
    )
    cfg_many_batches = MonteCarloConfig(
        n_simulations=37, seed=999, batch_size=7, n_bootstrap=20
    )

    one_batch = run_monte_carlo(rules, synthetic_config=synth, config=cfg_one_batch)
    many_batches = run_monte_carlo(
        rules, synthetic_config=synth, config=cfg_many_batches
    )

    np.testing.assert_array_equal(one_batch.terminal_codes, many_batches.terminal_codes)
    np.testing.assert_array_equal(one_batch.final_equities, many_batches.final_equities)
    np.testing.assert_array_equal(one_batch.trades_executed, many_batches.trades_executed)
    np.testing.assert_array_equal(
        one_batch.max_drawdowns_historical,
        many_batches.max_drawdowns_historical,
    )
    assert one_batch.pass_ci == many_batches.pass_ci
    assert one_batch.p95_max_drawdown_ci == many_batches.p95_max_drawdown_ci


def test_mc_synthetic_fixed_n_honors_memory_batch_size(monkeypatch):
    import src.monte_carlo as monte_carlo_module

    observed_batch_sizes = []
    original_generate = monte_carlo_module.generate_trade_sequences

    def recording_generate(config, n_sequences):
        observed_batch_sizes.append(n_sequences)
        return original_generate(config, n_sequences)

    monkeypatch.setattr(
        monte_carlo_module, "generate_trade_sequences", recording_generate
    )
    run_monte_carlo(
        _standard_rules(),
        synthetic_config=SyntheticConfig(n_trades=5),
        config=MonteCarloConfig(
            n_simulations=23, seed=4, batch_size=5, n_bootstrap=0
        ),
    )
    assert observed_batch_sizes == [5, 5, 5, 5, 3]


def test_mc_synthetic_source_seed_is_batch_invariant_without_master_seed():
    rules = _standard_rules()
    synth = SyntheticConfig(n_trades=20, seed=17)
    one_batch = run_monte_carlo(
        rules,
        synthetic_config=synth,
        config=MonteCarloConfig(
            n_simulations=23, seed=None, batch_size=100, n_bootstrap=0
        ),
    )
    many_batches = run_monte_carlo(
        rules,
        synthetic_config=synth,
        config=MonteCarloConfig(
            n_simulations=23, seed=None, batch_size=5, n_bootstrap=0
        ),
    )
    np.testing.assert_array_equal(one_batch.terminal_codes, many_batches.terminal_codes)
    np.testing.assert_array_equal(one_batch.final_equities, many_batches.final_equities)


def test_mc_synthetic_convergence_mode():
    rules = _standard_rules(
        initial_balance=100_000.0,
        profit_target_pct=0.05,
        max_drawdown_pct=0.05,
        daily_loss_limit_pct=0.03,
        risk_per_trade=0.01,
    )
    synth = SyntheticConfig(win_rate=0.50, n_trades=50)
    mc_cfg = MonteCarloConfig(
        n_simulations=None,
        se_target=0.05,  # Loose target for fast test
        min_simulations=200,
        max_simulations=1000,
        batch_size=200,
        seed=42,
        n_bootstrap=0,
    )

    result = run_monte_carlo(rules, synthetic_config=synth, config=mc_cfg)
    assert result.convergence_mode is True
    assert result.n_simulations >= 200
    assert result.n_simulations <= 1000
    if result.converged:
        assert result.se_probability_pass <= 0.05


def test_mc_convergence_boundary_zero_passes():
    """Convergence mode with 0 passes must not falsely collapse to 0 uncertainty at small N."""
    # Impossible target + negative expectancy ensures 0 passes
    rules = _standard_rules(
        initial_balance=100_000.0,
        profit_target_pct=0.90,  # 90% profit target
        max_drawdown_pct=0.05,
        risk_per_trade=0.01,
    )
    synth = SyntheticConfig(win_rate=0.10, avg_win_r=0.5, avg_loss_r=2.0, n_trades=50)
    # Tight se_target that requires substantial simulations even at p=0
    mc_cfg = MonteCarloConfig(
        n_simulations=None,
        se_target=0.005,
        min_simulations=50,
        max_simulations=300,
        batch_size=50,
        seed=42,
        n_bootstrap=0,
    )
    result = run_monte_carlo(rules, synthetic_config=synth, config=mc_cfg)
    assert result.probability_pass == 0.0
    # At N=50, Wilson SE for 0 passes is ~ 1.96 / (2 * (50 + 3.84)) ≈ 0.018 > 0.005
    # So it should NOT have stopped at min_simulations (50), but continued
    assert result.n_simulations > 50


def test_mc_convergence_boundary_all_passes():
    """Convergence mode with 100% passes must track uncertainty via Wilson SE."""
    rules = _standard_rules(
        initial_balance=100_000.0,
        profit_target_pct=0.01,
        max_drawdown_pct=0.50,
        risk_per_trade=0.01,
    )
    trades = [Trade(r_result=2.0, date=f"2024-01-{i+1:02d}") for i in range(10)]
    mc_cfg = MonteCarloConfig(
        n_simulations=None,
        se_target=0.005,
        min_simulations=50,
        max_simulations=300,
        batch_size=50,
        seed=42,
        n_bootstrap=0,
    )
    result = run_monte_carlo(
        rules, trades=trades, assume_iid=True, config=mc_cfg
    )
    assert result.probability_pass == 1.0
    assert result.n_simulations > 50


# ---------------------------------------------------------------------------
# run_monte_carlo execution tests — Resampled mode
# ---------------------------------------------------------------------------


def test_mc_trades_requires_explicit_assume_iid():
    """resampling historical trades must raise ValueError without assume_iid=True."""
    rules = _standard_rules()
    trades = [Trade(r_result=1.0, date="2024-01-01")]
    with pytest.raises(ValueError, match="assume_iid=True"):
        run_monte_carlo(rules, trades=trades, assume_iid=False)
    with pytest.raises(ValueError, match="assume_iid=True"):
        run_monte_carlo(rules, trades=trades, assume_iid=1)  # type: ignore[arg-type]


def test_mc_resampled_basic():
    rules = _standard_rules(
        initial_balance=100_000.0,
        profit_target_pct=0.05,
        max_drawdown_pct=0.05,
        daily_loss_limit_pct=0.03,
        risk_per_trade=0.01,
    )
    # Observed pool of 20 trades across 4 days
    trades = [
        Trade(r_result=2.0 if i % 2 == 0 else -1.0, date=f"2024-01-{i//5 + 1:02d}")
        for i in range(20)
    ]
    mc_cfg = MonteCarloConfig(n_simulations=100, seed=42, n_bootstrap=50)

    result = run_monte_carlo(
        rules, trades=trades, assume_iid=True, config=mc_cfg
    )

    assert result.source == "resampled"
    assert result.n_simulations == 100
    assert 0.0 <= result.probability_pass <= 1.0
    assert math.isclose(
        result.probability_pass + result.probability_fail + result.timeout_probability,
        1.0,
        abs_tol=1e-9,
    )


def test_mc_resampled_reproducibility():
    rules = _standard_rules(risk_per_trade=0.01)
    trades = [
        Trade(r_result=1.5 if i % 2 == 0 else -1.0, date=f"2024-01-{i+1:02d}")
        for i in range(10)
    ]
    mc_cfg = MonteCarloConfig(n_simulations=50, seed=777, n_bootstrap=0)

    res1 = run_monte_carlo(rules, trades=trades, assume_iid=True, config=mc_cfg)
    res2 = run_monte_carlo(rules, trades=trades, assume_iid=True, config=mc_cfg)

    np.testing.assert_array_equal(res1.terminal_codes, res2.terminal_codes)
    np.testing.assert_array_equal(res1.final_equities, res2.final_equities)


def test_mc_resampled_fixed_n_is_bitwise_invariant_to_batch_size():
    rules = _standard_rules(risk_per_trade=0.01)
    trades = [
        Trade(r_result=1.5 if i % 2 == 0 else -1.0, date=f"2024-01-{i+1:02d}")
        for i in range(10)
    ]
    one_batch = run_monte_carlo(
        rules,
        trades=trades,
        assume_iid=True,
        config=MonteCarloConfig(
            n_simulations=37, seed=777, batch_size=100, n_bootstrap=20
        ),
    )
    many_batches = run_monte_carlo(
        rules,
        trades=trades,
        assume_iid=True,
        config=MonteCarloConfig(
            n_simulations=37, seed=777, batch_size=7, n_bootstrap=20
        ),
    )

    np.testing.assert_array_equal(one_batch.terminal_codes, many_batches.terminal_codes)
    np.testing.assert_array_equal(one_batch.final_equities, many_batches.final_equities)
    np.testing.assert_array_equal(one_batch.trades_executed, many_batches.trades_executed)
    np.testing.assert_array_equal(
        one_batch.max_drawdowns_historical,
        many_batches.max_drawdowns_historical,
    )
    assert one_batch.pass_ci == many_batches.pass_ci
    assert one_batch.p95_max_drawdown_ci == many_batches.p95_max_drawdown_ci


def test_mc_resampled_rejects_invalid_inputs():
    rules = _standard_rules(risk_per_trade=0.01)

    # Empty trades list
    with pytest.raises(ValueError, match="trades must be a non-empty list"):
        run_monte_carlo(rules, trades=[], assume_iid=True)

    # Non-Trade element
    with pytest.raises(ValueError, match="must be a Trade"):
        run_monte_carlo(rules, trades=[1.0], assume_iid=True)  # type: ignore

    # Non-finite r_result
    with pytest.raises(ValueError, match="r_result must be finite"):
        run_monte_carlo(
            rules,
            trades=[Trade(r_result=math.nan, date="2024-01-01")],
            assume_iid=True,
        )

    # Invalid date format
    with pytest.raises(ValueError, match="Expected format: YYYY-MM-DD"):
        run_monte_carlo(
            rules,
            trades=[Trade(r_result=1.0, date="01/01/2024")],
            assume_iid=True,
        )

    # Decreasing dates
    with pytest.raises(ValueError, match="dates must be non-decreasing"):
        run_monte_carlo(
            rules,
            trades=[
                Trade(r_result=1.0, date="2024-01-02"),
                Trade(r_result=1.0, date="2024-01-01"),
            ],
            assume_iid=True,
        )

    # Overflowing P&L
    with pytest.raises(ValueError, match="would overflow float64 P&L"):
        run_monte_carlo(
            rules,
            trades=[Trade(r_result=1e308, date="2024-01-01")],
            assume_iid=True,
        )


def test_mc_rejects_both_or_neither_source():
    rules = _standard_rules()
    synth = SyntheticConfig()
    trades = [Trade(r_result=1.0, date="2024-01-01")]

    # Neither provided
    with pytest.raises(ValueError, match="exactly one of synthetic_config or trades"):
        run_monte_carlo(rules)

    # Both provided
    with pytest.raises(ValueError, match="exactly one of synthetic_config or trades"):
        run_monte_carlo(
            rules, synthetic_config=synth, trades=trades, assume_iid=True
        )


# ---------------------------------------------------------------------------
# Property-based tests (Mandatory)
# ---------------------------------------------------------------------------


def test_property_positive_vs_negative_expectancy():
    """Positive expectancy must produce significantly higher P_pass than negative."""
    rules = _standard_rules(
        initial_balance=100_000.0,
        profit_target_pct=0.08,
        max_drawdown_pct=0.08,
        daily_loss_limit_pct=0.05,
        risk_per_trade=0.01,
    )
    synth_pos = SyntheticConfig(
        win_rate=0.60, avg_win_r=2.0, avg_loss_r=1.0, n_trades=100, seed=42
    )
    synth_neg = SyntheticConfig(
        win_rate=0.30, avg_win_r=1.0, avg_loss_r=2.0, n_trades=100, seed=42
    )
    mc_cfg = MonteCarloConfig(n_simulations=200, seed=42, n_bootstrap=0)

    res_pos = run_monte_carlo(rules, synthetic_config=synth_pos, config=mc_cfg)
    res_neg = run_monte_carlo(rules, synthetic_config=synth_neg, config=mc_cfg)

    assert res_pos.probability_pass > res_neg.probability_pass
    assert res_pos.probability_fail < res_neg.probability_fail


def test_property_high_risk_more_drawdown_failures():
    """Higher risk per trade must produce more drawdown and higher failure probability."""
    rules_low = _standard_rules(
        initial_balance=100_000.0,
        profit_target_pct=0.10,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.50,  # Wide daily loss so max_drawdown is primary limit
        risk_per_trade=0.005,
    )
    rules_high = _standard_rules(
        initial_balance=100_000.0,
        profit_target_pct=0.10,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.50,
        risk_per_trade=0.03,
    )
    synth = SyntheticConfig(
        win_rate=0.45, avg_win_r=1.5, avg_loss_r=1.0, n_trades=100, seed=42
    )
    mc_cfg = MonteCarloConfig(n_simulations=200, seed=42, n_bootstrap=0)

    res_low = run_monte_carlo(rules_low, synthetic_config=synth, config=mc_cfg)
    res_high = run_monte_carlo(rules_high, synthetic_config=synth, config=mc_cfg)

    assert (
        res_high.failure_probability_max_drawdown
        >= res_low.failure_probability_max_drawdown
    )
    assert res_high.p95_max_drawdown > res_low.p95_max_drawdown
    assert res_high.probability_fail > res_low.probability_fail


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_mc_edge_case_all_wins():
    """100% win rate guarantees pass and 0 failures."""
    rules = _standard_rules(
        initial_balance=100_000.0,
        profit_target_pct=0.05,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.05,
        risk_per_trade=0.01,
    )
    trades = [Trade(r_result=2.0, date=f"2024-01-{i+1:02d}") for i in range(10)]
    mc_cfg = MonteCarloConfig(n_simulations=50, seed=42, n_bootstrap=0)

    res = run_monte_carlo(rules, trades=trades, assume_iid=True, config=mc_cfg)
    assert res.probability_pass == 1.0
    assert res.probability_fail == 0.0
    assert res.timeout_probability == 0.0
    assert res.median_max_drawdown == 0.0
    assert not math.isnan(res.median_trades_to_pass)


def test_mc_edge_case_all_losses():
    """100% loss rate gives 0 passes and NaN for trades_to_pass."""
    rules = _standard_rules(
        initial_balance=100_000.0,
        profit_target_pct=0.05,
        max_drawdown_pct=0.05,
        daily_loss_limit_pct=0.03,
        risk_per_trade=0.01,
    )
    trades = [Trade(r_result=-1.0, date=f"2024-01-{i+1:02d}") for i in range(10)]
    mc_cfg = MonteCarloConfig(n_simulations=50, seed=42, n_bootstrap=0)

    res = run_monte_carlo(rules, trades=trades, assume_iid=True, config=mc_cfg)
    assert res.probability_pass == 0.0
    assert res.probability_fail == 1.0
    assert math.isnan(res.median_trades_to_pass)
    assert math.isnan(res.mean_trades_to_pass)


def test_mc_edge_case_no_bootstrap():
    """Setting n_bootstrap=0 disables bootstrap CIs (sets them to None)."""
    rules = _standard_rules(risk_per_trade=0.01)
    synth = SyntheticConfig(n_trades=20)
    mc_cfg = MonteCarloConfig(n_simulations=20, n_bootstrap=0)

    res = run_monte_carlo(rules, synthetic_config=synth, config=mc_cfg)
    assert res.median_max_drawdown_ci is None
    assert res.p95_max_drawdown_ci is None
    assert res.p99_max_drawdown_ci is None


def test_mc_edge_case_single_simulation():
    """n_simulations=1 should execute without error."""
    rules = _standard_rules(risk_per_trade=0.01)
    synth = SyntheticConfig(n_trades=20)
    mc_cfg = MonteCarloConfig(n_simulations=1, seed=42, n_bootstrap=10)

    res = run_monte_carlo(rules, synthetic_config=synth, config=mc_cfg)
    assert res.n_simulations == 1
    assert len(res.terminal_codes) == 1


def test_mc_simultaneous_violations_inclusive_vs_primary():
    """Simultaneous violations must be counted in inclusive failure probabilities."""
    rules = _standard_rules(
        initial_balance=100_000.0,
        profit_target_pct=0.50,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.05,
        risk_per_trade=0.01,
    )
    # A single -10R trade blows through both max_drawdown (10%) and daily_loss (5%)
    trades = [Trade(r_result=-10.0, date="2024-01-01")]
    mc_cfg = MonteCarloConfig(n_simulations=50, seed=42, n_bootstrap=0)

    res = run_monte_carlo(rules, trades=trades, assume_iid=True, config=mc_cfg)
    # Terminal condition is max_drawdown (priority 1)
    assert res.probability_fail == 1.0
    assert res.failure_probability_max_drawdown_primary == 1.0
    assert res.failure_probability_daily_loss_primary == 0.0
    # Inclusive failure tracking captures both
    assert res.failure_probability_max_drawdown == 1.0
    assert res.failure_probability_daily_loss == 1.0
    assert np.all(res.violated_max_drawdown)
    assert np.all(res.violated_daily_loss)


def test_mc_historical_dd_used_in_static_mode():
    """Monte Carlo drawdown quantiles must reflect historical peak-to-trough DD, not rule DD."""
    rules = _standard_rules(
        initial_balance=100_000.0,
        profit_target_pct=0.50,  # Far target
        max_drawdown_pct=0.20,
        daily_loss_limit_pct=0.20,
        risk_per_trade=0.01,
        drawdown_mode="static",
    )
    # Trade sequence where losses occur after gains:
    # Rule DD in static mode remains 0 if equity stays above initial balance.
    # Historical DD captures the peak-to-trough drop.
    trades = [
        Trade(r_result=5.0, date="2024-01-01"),
        Trade(r_result=-3.0, date="2024-01-02"),
    ]
    mc_cfg = MonteCarloConfig(n_simulations=50, seed=42, n_bootstrap=0)

    res = run_monte_carlo(rules, trades=trades, assume_iid=True, config=mc_cfg)
    assert res.failure_probability_max_drawdown == 0.0
    assert res.median_max_drawdown > 0.0
    assert res.p95_max_drawdown > 0.0


def test_mc_terminal_counts_consistency():
    """terminal_counts must sum to n_simulations and agree with terminal_codes."""
    rules = _standard_rules()
    synth = SyntheticConfig(win_rate=0.50, n_trades=50)
    mc_cfg = MonteCarloConfig(n_simulations=100, seed=42, n_bootstrap=0)

    res = run_monte_carlo(rules, synthetic_config=synth, config=mc_cfg)
    assert sum(res.terminal_counts.values()) == 100
    for name, code in (
        ("profit_target", CODE_PROFIT_TARGET),
        ("max_drawdown", CODE_MAX_DRAWDOWN),
        ("daily_loss", CODE_DAILY_LOSS),
        ("max_trades", CODE_MAX_TRADES),
        ("completed", CODE_COMPLETED),
    ):
        assert res.terminal_counts[name] == int(np.count_nonzero(res.terminal_codes == code))


def test_mc_negative_equity_and_drawdown_exceeding_one():
    """
    Regression test: extreme single-trade loss driving equity negative
    produces historical drawdown > 1.0 without triggering validation errors.
    """
    rules = _standard_rules(
        initial_balance=100_000.0,
        profit_target_pct=0.10,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.10,
        risk_per_trade=0.01,
    )
    # -150R at 1% risk -> loss of $150,000 on $100,000 balance -> equity = -$50,000
    # Historical peak-to-trough DD = (100,000 - (-50,000)) / 100,000 = 1.50 (> 1.0)
    trades = [Trade(r_result=-150.0, date="2024-01-01")]
    mc_cfg = MonteCarloConfig(n_simulations=50, seed=42, n_bootstrap=100)

    res = run_monte_carlo(rules, trades=trades, assume_iid=True, config=mc_cfg)
    assert res.probability_fail == 1.0
    assert np.all(res.final_equities == -50_000.0)
    assert np.all(res.max_drawdowns_historical == 1.50)
    assert res.median_max_drawdown == pytest.approx(1.50)
    assert res.p95_max_drawdown == pytest.approx(1.50)
    assert res.p99_max_drawdown == pytest.approx(1.50)
    assert res.median_max_drawdown_ci is not None
    assert res.median_max_drawdown_ci[0] == pytest.approx(1.50)
    assert res.median_max_drawdown_ci[1] == pytest.approx(1.50)


# ---------------------------------------------------------------------------
# Fast simulation kernel equivalence tests
#
# _simulate_path_fast() is a hot-path reimplementation of run_simulation() used
# by _simulate_batch(). It must be bit-for-bit equivalent to the canonical
# engine for every terminal condition, drawdown mode, and daily-loss base.
# These tests guard against the two implementations silently diverging.
# ---------------------------------------------------------------------------

_TERM_TO_CODE = {
    "profit_target": CODE_PROFIT_TARGET,
    "max_drawdown": CODE_MAX_DRAWDOWN,
    "daily_loss": CODE_DAILY_LOSS,
    "max_trades": CODE_MAX_TRADES,
    "completed": CODE_COMPLETED,
}


def _trades_from(r_results, dates):
    return [
        Trade(r_result=r, trade_id=f"t{i:04d}", date=d)
        for i, (r, d) in enumerate(zip(r_results, dates))
    ]


@pytest.mark.parametrize(
    "rules_kwargs,r_results,dates",
    [
        # profit target reached (PASS)
        (dict(), [2.0, 1.0, 3.0, 1.5, 4.0, 2.0, 3.0], None),
        # profit target reached exactly at the boundary (equity == target)
        (dict(profit_target_pct=0.10), [10.0], None),
        # max drawdown violated (static)
        (dict(max_drawdown_pct=0.10), [2.0, -3.0, -4.0, -5.0, -6.0], None),
        # trailing drawdown violated (peak-relative threshold)
        (dict(drawdown_mode="trailing", max_drawdown_pct=0.10),
         [5.0, -3.0, -4.0, -3.0, -4.0], None),
        # daily loss violated (multiple losses within one day)
        (dict(daily_loss_limit_pct=0.03), [1.0, -2.0, -2.0, -2.0],
         ["2024-01-01", "2024-01-02", "2024-01-02", "2024-01-02"]),
        # daily loss violated with eod base (start-of-day denominator)
        (dict(daily_loss_base="eod", daily_loss_limit_pct=0.03),
         [1.0, -2.0, -2.0, -2.0],
         ["2024-01-01", "2024-01-02", "2024-01-02", "2024-01-02"]),
        # max_trades reached (timeout)
        (dict(max_trades=4), [0.5, 0.5, -0.5, -0.5, 0.5], None),
        # completed (all trades applied, no terminal condition)
        (dict(max_trades=None), [0.5, -0.3, 0.2, 0.1], None),
        # simultaneous multi-violation (max_drawdown AND daily_loss)
        (dict(max_drawdown_pct=0.10, daily_loss_limit_pct=0.05), [-12.0], None),
        # day transition exercises start_of_day reset (3 trades day 1, 2 day 2)
        (dict(daily_loss_limit_pct=0.05), [1.0, -1.5, 0.5, -2.0, -2.0],
         ["2024-01-01", "2024-01-01", "2024-01-01", "2024-01-02", "2024-01-02"]),
    ],
)
def test_simulate_path_fast_bit_for_bit_equivalent(rules_kwargs, r_results, dates):
    rules = _standard_rules(**rules_kwargs)
    if dates is None:
        trades = _make_trades(r_results)
    else:
        assert len(dates) == len(r_results)
        trades = _trades_from(r_results, dates)

    canonical = run_simulation(trades, rules)

    is_new_day = [
        i == 0 or trades[i].date != trades[i - 1].date for i in range(len(trades))
    ]
    code, equity, n_tr, dd, v_dd, v_dl = _simulate_path_fast(
        [t.r_result for t in trades], is_new_day, rules
    )

    # Exact (bit-for-bit) comparison — no tolerance. A single-ULP divergence
    # between the hot path and the canonical engine is a bug.
    assert code == _TERM_TO_CODE[canonical.terminal_condition]
    assert equity == canonical.final_equity
    assert n_tr == canonical.trades_executed
    assert dd == canonical.max_drawdown_historical
    assert v_dd == ("max_drawdown" in canonical.violated_conditions)
    assert v_dl == ("daily_loss" in canonical.violated_conditions)


def test_simulate_path_fast_matches_canonical_across_random_sequences():
    """Fuzz-style equivalence across 200 seeded random rule/sequence combos."""
    rng = np.random.default_rng(2024_08_15)
    for _ in range(200):
        n = int(rng.integers(1, 20))
        r_results = [float(x) for x in np.round(rng.normal(0, 2.0, size=n), 4)]

        # Non-decreasing valid dates with random grouping into days.
        dates = []
        day = 1
        for _ in range(n):
            dates.append(f"2024-01-{day:02d}")
            if rng.random() < 0.5:
                day += 1

        _max_trades_choices = [3, 8, None]
        rules = _standard_rules(
            max_drawdown_pct=float(rng.choice([0.05, 0.10, 0.15])),
            daily_loss_limit_pct=float(rng.choice([0.03, 0.05, 0.08])),
            risk_per_trade=float(rng.choice([0.005, 0.01, 0.02])),
            max_trades=_max_trades_choices[int(rng.integers(0, 3))],
            drawdown_mode=str(rng.choice(["static", "trailing"])),
            daily_loss_base=str(rng.choice(["initial", "eod"])),
        )

        trades = _trades_from(r_results, dates)
        canonical = run_simulation(trades, rules)

        is_new_day = [
            i == 0 or trades[i].date != trades[i - 1].date
            for i in range(len(trades))
        ]
        code, equity, n_tr, dd, v_dd, v_dl = _simulate_path_fast(
            r_results, is_new_day, rules
        )

        assert code == _TERM_TO_CODE[canonical.terminal_condition]
        assert equity == canonical.final_equity
        assert n_tr == canonical.trades_executed
        assert dd == canonical.max_drawdown_historical
        assert v_dd == ("max_drawdown" in canonical.violated_conditions)
        assert v_dl == ("daily_loss" in canonical.violated_conditions)

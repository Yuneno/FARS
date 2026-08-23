"""Phase 7 tests for interpretable, reproducible visualization diagnostics."""

import math

import matplotlib

matplotlib.use("Agg", force=True)

from matplotlib import pyplot as plt
import numpy as np
import pytest

import src
from src.monte_carlo import MonteCarloConfig
from src.optimization import OptimizationConfig, optimize_risk_per_trade
from src.types import FundedAccountRules, SyntheticConfig, Trade
from src.visualization import (
    plot_final_equity_distribution,
    plot_losing_streak_distribution,
    plot_max_drawdown_distribution,
    plot_outcome_probabilities,
    plot_risk_drawdown,
    plot_risk_pass_probability,
)


@pytest.fixture(scope="module")
def optimization_result():
    rules = FundedAccountRules(
        initial_balance=100_000.0,
        profit_target_pct=0.08,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.05,
        risk_per_trade=0.01,
    )
    return optimize_risk_per_trade(
        rules,
        synthetic_config=SyntheticConfig(
            win_rate=0.55,
            avg_win_r=1.5,
            avg_loss_r=1.0,
            n_trades=30,
            seed=17,
        ),
        mc_config=MonteCarloConfig(
            n_simulations=60,
            seed=23,
            batch_size=13,
            n_bootstrap=0,
        ),
        opt_config=OptimizationConfig(risk_levels=(0.005, 0.010, 0.015)),
    )


def test_risk_pass_probability_plots_estimates_uncertainty_and_optimum(
    optimization_result,
):
    result = optimization_result
    raw_before = result.probabilities_pass.copy()

    figure, ax = plot_risk_pass_probability(result)

    assert figure is ax.figure
    assert ax.get_xlabel() == "Risk per trade"
    assert ax.get_ylabel() == "Probability of passing"
    assert ax.get_ylim() == pytest.approx((0.0, 1.0))
    assert "Risk vs Probability of Passing" in ax.get_title(loc="left")
    np.testing.assert_array_equal(ax.lines[0].get_xdata(), result.risk_levels)
    np.testing.assert_array_equal(ax.lines[0].get_ydata(), result.probabilities_pass)
    np.testing.assert_array_equal(
        ax.lines[1].get_ydata(), result.probabilities_pass_smoothed
    )
    assert len(ax.collections) >= 3  # Wilson band, plausible set, optimum star
    assert any(
        "Conditional pointwise Wilson CI" in label
        for label in ax.get_legend_handles_labels()[1]
    )
    np.testing.assert_array_equal(result.probabilities_pass, raw_before)
    plt.close(figure)


def test_risk_drawdown_plots_p95_cvar_and_fres_reference(optimization_result):
    result = optimization_result
    figure, ax = plot_risk_drawdown(result)

    np.testing.assert_array_equal(ax.lines[0].get_ydata(), result.p95_drawdowns)
    np.testing.assert_array_equal(ax.lines[1].get_ydata(), result.cvar_95_values)
    assert np.all(
        np.asarray(ax.lines[2].get_xdata()) == result.optimal_risk_fres
    )
    assert any(
        "historical-DD score" in label
        for label in ax.get_legend_handles_labels()[1]
    )
    assert ax.get_ylim()[0] == 0.0
    assert "Historical peak-to-trough" in ax.get_title(loc="left")
    plt.close(figure)


def test_final_equity_distribution_uses_selected_candidate_and_rule_references(
    optimization_result,
):
    result = optimization_result
    figure, ax = plot_final_equity_distribution(
        result, risk_per_trade=0.010, bins=12
    )

    assert len(ax.patches) == 12
    assert len(ax.lines) == 2
    assert float(ax.lines[0].get_xdata()[0]) == result.rules_template.initial_balance
    expected_target = (
        result.rules_template.initial_balance
        + result.rules_template.initial_balance
        * result.rules_template.profit_target_pct
    )
    assert float(ax.lines[1].get_xdata()[0]) == expected_target
    assert "Risk 1.00%" in ax.get_title(loc="left")
    plt.close(figure)


def test_max_drawdown_distribution_uses_empirical_tail_references(
    optimization_result,
):
    result = optimization_result
    evaluation = result.evaluations[0]
    figure, ax = plot_max_drawdown_distribution(
        result, risk_per_trade=evaluation.risk_per_trade, bins=9
    )

    assert len(ax.patches) == 9
    assert float(ax.lines[0].get_xdata()[0]) == evaluation.p95_max_drawdown
    assert float(ax.lines[1].get_xdata()[0]) == evaluation.cvar_95
    assert "Historical peak-to-trough metric" in ax.get_title(loc="left")
    plt.close(figure)


def test_outcome_probability_lines_match_partition(optimization_result):
    result = optimization_result
    figure, ax = plot_outcome_probabilities(result)

    timeout = np.array([item.timeout_probability for item in result.evaluations])
    np.testing.assert_array_equal(ax.lines[0].get_ydata(), result.probabilities_pass)
    np.testing.assert_array_equal(ax.lines[1].get_ydata(), result.probabilities_fail)
    np.testing.assert_array_equal(ax.lines[2].get_ydata(), timeout)
    np.testing.assert_allclose(
        result.probabilities_pass + result.probabilities_fail + timeout,
        np.ones(len(result.risk_levels)),
    )
    assert ax.get_ylim() == pytest.approx((0.0, 1.0))
    plt.close(figure)


def test_losing_streak_distribution_counts_maximum_per_sequence():
    sequences = [
        [-1.0, -0.5, 2.0, -1.0],  # max 2
        [Trade(r_result=1.0), Trade(r_result=0.0)],  # max 0
        [Trade(r_result=-1.0), Trade(r_result=-1.0), Trade(r_result=-1.0)],  # max 3
        [-2.0, -1.0],  # max 2
    ]

    figure, ax = plot_losing_streak_distribution(sequences)

    heights = np.array([bar.get_height() for bar in ax.patches])
    np.testing.assert_array_equal(heights, np.array([1, 0, 2, 1]))
    assert "R < 0" in ax.get_title(loc="left")
    assert ax.get_xlabel() == "Maximum losing streak (trades)"
    plt.close(figure)


def test_plot_functions_reuse_caller_axes(optimization_result):
    supplied_figure, supplied_ax = plt.subplots()
    returned_figure, returned_ax = plot_risk_drawdown(
        optimization_result, ax=supplied_ax
    )
    assert returned_figure is supplied_figure
    assert returned_ax is supplied_ax
    plt.close(supplied_figure)


@pytest.mark.parametrize("risk", [0.02, math.nan, math.inf, True, "0.01"])
def test_distribution_plots_reject_invalid_candidate_risk(
    optimization_result, risk
):
    with pytest.raises(ValueError, match="risk_per_trade"):
        plot_final_equity_distribution(optimization_result, risk_per_trade=risk)


@pytest.mark.parametrize("bins", [0, -1, 1.5, True])
def test_distribution_plots_reject_invalid_bins(optimization_result, bins):
    with pytest.raises(ValueError, match="bins"):
        plot_max_drawdown_distribution(optimization_result, bins=bins)


@pytest.mark.parametrize(
    "sequences",
    [
        [],
        "not-sequences",
        [1.0, -1.0],
        [[1.0, math.nan]],
        [[1.0, math.inf]],
        [[1.0 + 2.0j]],
        [[True, -1.0]],
    ],
)
def test_losing_streak_distribution_rejects_invalid_data(sequences):
    with pytest.raises(ValueError):
        plot_losing_streak_distribution(sequences)


def test_public_visualization_exports_are_available():
    expected = (
        "plot_risk_pass_probability",
        "plot_risk_drawdown",
        "plot_final_equity_distribution",
        "plot_max_drawdown_distribution",
        "plot_losing_streak_distribution",
        "plot_outcome_probabilities",
    )
    for name in expected:
        assert callable(getattr(src, name))

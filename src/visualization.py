"""Interpretable Matplotlib diagnostics for FARS Phase 7.

The plotting functions consume validated Phase 5/6 result objects and never
recompute Monte Carlo estimates.  They return ``(Figure, Axes)`` so callers can
compose, inspect, or save figures without hidden I/O or global style changes.
"""

from collections.abc import Iterable, Sequence
from math import isfinite
from numbers import Integral, Real

import numpy as np
from matplotlib import pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator, PercentFormatter, StrMethodFormatter

from src.optimization import OptimizationResult, RiskLevelEvaluation
from src.types import Trade


# Explicit, color-blind-conscious palette. Distinctions also use marker and
# line styles so no diagnostic relies on color alone.
_BLUE = "#31688E"
_GOLD = "#C58B1B"
_ORANGE = "#D66A3A"
_OLIVE = "#6F7D3C"
_INK = "#252A34"
_GRAY = "#707782"
_LIGHT_GRAY = "#D9DDE3"


def _axes(ax: Axes | None, figsize: tuple[float, float]) -> tuple[Figure, Axes]:
    if ax is None:
        figure, created_ax = plt.subplots(figsize=figsize, constrained_layout=True)
        return figure, created_ax
    if not isinstance(ax, Axes):
        raise ValueError("ax must be a matplotlib.axes.Axes instance or None")
    return ax.figure, ax


def _style_axes(ax: Axes) -> None:
    ax.set_axisbelow(True)
    ax.grid(axis="y", color=_LIGHT_GRAY, linewidth=0.8, alpha=0.75)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(_GRAY)
    ax.spines["bottom"].set_color(_GRAY)
    ax.tick_params(colors=_INK)


def _title(ax: Axes, title: str, subtitle: str) -> None:
    ax.set_title(
        f"{title}\n{subtitle}",
        loc="left",
        color=_INK,
        fontsize=11,
        fontweight="semibold",
        pad=10,
    )


def _validate_result(result: OptimizationResult) -> None:
    if not isinstance(result, OptimizationResult):
        raise ValueError("result must be an OptimizationResult")
    if not result.evaluations:
        raise ValueError("result must contain at least one risk-level evaluation")


def _selected_evaluation(
    result: OptimizationResult,
    risk_per_trade: float | None,
) -> RiskLevelEvaluation:
    _validate_result(result)
    selected_risk = result.optimal_risk_raw if risk_per_trade is None else risk_per_trade
    if (
        isinstance(selected_risk, (bool, np.bool_))
        or not isinstance(selected_risk, Real)
        or not isfinite(selected_risk)
    ):
        raise ValueError("risk_per_trade must be a finite real candidate level")

    levels = np.asarray(result.risk_levels, dtype=np.float64)
    matches = np.flatnonzero(levels == float(selected_risk))
    if matches.size != 1:
        raise ValueError(
            f"risk_per_trade {selected_risk!r} is not an evaluated candidate"
        )
    return result.evaluations[int(matches[0])]


def _validate_bins(bins: int) -> int:
    if isinstance(bins, (bool, np.bool_)) or not isinstance(bins, Integral) or bins <= 0:
        raise ValueError(f"bins must be a positive integer, got {bins!r}")
    return int(bins)


def plot_risk_pass_probability(
    result: OptimizationResult,
    *,
    ax: Axes | None = None,
) -> tuple[Figure, Axes]:
    """Plot empirical and smoothed pass probability over candidate risk levels."""
    _validate_result(result)
    figure, ax = _axes(ax, (8.0, 4.8))
    levels = result.risk_levels
    raw = result.probabilities_pass
    smoothed = result.probabilities_pass_smoothed
    ci_lower = np.array([item.pass_ci[0] for item in result.evaluations])
    ci_upper = np.array([item.pass_ci[1] for item in result.evaluations])

    confidence_pct = 100.0 * result.config.confidence_level
    ax.fill_between(
        levels,
        ci_lower,
        ci_upper,
        color=_BLUE,
        alpha=0.14,
        linewidth=0.0,
        label=f"Conditional pointwise Wilson CI ({confidence_pct:g}%)",
    )
    ax.plot(
        levels,
        raw,
        color=_BLUE,
        marker="o",
        markersize=4.5,
        linewidth=1.8,
        label="Empirical pass probability",
    )
    ax.plot(
        levels,
        smoothed,
        color=_GOLD,
        linestyle="--",
        marker="s",
        markerfacecolor="white",
        markersize=4.0,
        linewidth=1.7,
        label="Gaussian-smoothed sensitivity",
    )

    plausible_mask = np.isin(levels, np.asarray(result.plausible_risk_levels))
    ax.scatter(
        levels[plausible_mask],
        raw[plausible_mask],
        facecolors="none",
        edgecolors=_INK,
        linewidths=1.1,
        s=58,
        zorder=4,
        label="Statistically plausible candidates",
    )
    optimum_index = int(np.argmax(raw))
    ax.scatter(
        [levels[optimum_index]],
        [raw[optimum_index]],
        color=_ORANGE,
        edgecolor=_INK,
        marker="*",
        linewidth=0.6,
        s=150,
        zorder=5,
        label=f"Raw optimum ({result.optimal_risk_raw:.2%})",
    )

    n_paths = result.evaluations[0].mc_result.n_simulations
    _title(
        ax,
        "Risk vs Probability of Passing",
        f"Candidate risk per trade; {n_paths:,} Monte Carlo paths per candidate",
    )
    ax.set_xlabel("Risk per trade")
    ax.set_ylabel("Probability of passing")
    ax.xaxis.set_major_formatter(PercentFormatter(xmax=1.0, decimals=2))
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=1.0, decimals=0))
    ax.set_ylim(0.0, 1.0)
    ax.legend(frameon=False, fontsize=8, ncols=2)
    _style_axes(ax)
    return figure, ax


def plot_risk_drawdown(
    result: OptimizationResult,
    *,
    ax: Axes | None = None,
) -> tuple[Figure, Axes]:
    """Plot P95 and CVaR95 historical max drawdown across candidate risks."""
    _validate_result(result)
    figure, ax = _axes(ax, (8.0, 4.8))
    levels = result.risk_levels

    ax.plot(
        levels,
        result.p95_drawdowns,
        color=_BLUE,
        marker="o",
        markersize=4.5,
        linewidth=1.8,
        label="P95 maximum drawdown",
    )
    ax.plot(
        levels,
        result.cvar_95_values,
        color=_ORANGE,
        linestyle="--",
        marker="s",
        markerfacecolor="white",
        markersize=4.0,
        linewidth=1.7,
        label="CVaR95 / expected shortfall",
    )
    ax.axvline(
        result.optimal_risk_fres,
        color=_INK,
        linestyle=":",
        linewidth=1.4,
        label=f"FRES optimum, historical-DD score ({result.optimal_risk_fres:.2%})",
    )

    n_paths = result.evaluations[0].mc_result.n_simulations
    _title(
        ax,
        "Risk vs Drawdown",
        f"Historical peak-to-trough drawdown; {n_paths:,} paths per candidate",
    )
    ax.set_xlabel("Risk per trade")
    ax.set_ylabel("Maximum drawdown")
    ax.xaxis.set_major_formatter(PercentFormatter(xmax=1.0, decimals=2))
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=1.0, decimals=1))
    ax.set_ylim(bottom=0.0)
    ax.legend(frameon=False, fontsize=8)
    _style_axes(ax)
    return figure, ax


def plot_final_equity_distribution(
    result: OptimizationResult,
    *,
    risk_per_trade: float | None = None,
    bins: int = 30,
    ax: Axes | None = None,
) -> tuple[Figure, Axes]:
    """Plot final account equity for one evaluated risk candidate."""
    evaluation = _selected_evaluation(result, risk_per_trade)
    bins = _validate_bins(bins)
    figure, ax = _axes(ax, (8.0, 4.8))
    values = evaluation.mc_result.final_equities
    rules = result.rules_template
    # Match the engine's operation order exactly at floating-point boundaries.
    target = rules.initial_balance + rules.initial_balance * rules.profit_target_pct

    ax.hist(
        values,
        bins=bins,
        color=_BLUE,
        edgecolor=_INK,
        linewidth=0.45,
        alpha=0.82,
        label="Final equity",
    )
    ax.axvline(
        rules.initial_balance,
        color=_GRAY,
        linestyle="--",
        linewidth=1.5,
        label="Initial balance",
    )
    ax.axvline(
        target,
        color=_GOLD,
        linestyle=":",
        linewidth=1.8,
        label="Profit target",
    )

    _title(
        ax,
        "Distribution of Final Account Outcomes",
        f"Risk {evaluation.risk_per_trade:.2%}; N={evaluation.mc_result.n_simulations:,}",
    )
    ax.set_xlabel("Final account equity")
    ax.set_ylabel("Simulation paths")
    ax.xaxis.set_major_formatter(StrMethodFormatter("${x:,.0f}"))
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.legend(frameon=False, fontsize=8)
    _style_axes(ax)
    return figure, ax


def plot_max_drawdown_distribution(
    result: OptimizationResult,
    *,
    risk_per_trade: float | None = None,
    bins: int = 30,
    ax: Axes | None = None,
) -> tuple[Figure, Axes]:
    """Plot historical peak-to-trough maximum drawdowns for one candidate."""
    evaluation = _selected_evaluation(result, risk_per_trade)
    bins = _validate_bins(bins)
    figure, ax = _axes(ax, (8.0, 4.8))
    values = evaluation.mc_result.max_drawdowns_historical

    ax.hist(
        values,
        bins=bins,
        color=_BLUE,
        edgecolor=_INK,
        linewidth=0.45,
        alpha=0.82,
        label="Maximum drawdown",
    )
    ax.axvline(
        evaluation.p95_max_drawdown,
        color=_GOLD,
        linestyle="--",
        linewidth=1.7,
        label=f"P95 ({evaluation.p95_max_drawdown:.1%})",
    )
    ax.axvline(
        evaluation.cvar_95,
        color=_ORANGE,
        linestyle=":",
        linewidth=1.8,
        label=f"CVaR95 ({evaluation.cvar_95:.1%})",
    )

    _title(
        ax,
        "Distribution of Maximum Drawdowns",
        f"Historical peak-to-trough metric at {evaluation.risk_per_trade:.2%} risk; "
        f"N={evaluation.mc_result.n_simulations:,}",
    )
    ax.set_xlabel("Maximum drawdown")
    ax.set_ylabel("Simulation paths")
    ax.xaxis.set_major_formatter(PercentFormatter(xmax=1.0, decimals=1))
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.legend(frameon=False, fontsize=8)
    _style_axes(ax)
    return figure, ax


def _r_values(sequence: Iterable[Trade | Real]) -> tuple[float, ...]:
    if isinstance(sequence, (str, bytes)):
        raise ValueError("each trade sequence must be an iterable of Trade or real values")
    try:
        materialized = tuple(sequence)
    except TypeError as exc:
        raise ValueError(
            "each trade sequence must be an iterable of Trade or real values"
        ) from exc

    values: list[float] = []
    for item in materialized:
        value = item.r_result if isinstance(item, Trade) else item
        if (
            isinstance(value, (bool, np.bool_))
            or not isinstance(value, Real)
            or not isfinite(value)
        ):
            raise ValueError("trade outcomes must be finite real R-multiples")
        values.append(float(value))
    return tuple(values)


def plot_losing_streak_distribution(
    trade_sequences: Sequence[Iterable[Trade | Real]],
    *,
    ax: Axes | None = None,
) -> tuple[Figure, Axes]:
    """Plot maximum losing streaks in the supplied complete trade sequences.

    This is a strategy-sequence diagnostic. Unless callers explicitly truncate
    sequences at account terminal conditions, it does not describe executed
    funded-account paths.
    """
    if isinstance(trade_sequences, (str, bytes)):
        raise ValueError("trade_sequences must be a non-empty sequence")
    try:
        sequences = tuple(trade_sequences)
    except TypeError as exc:
        raise ValueError("trade_sequences must be a non-empty sequence") from exc
    if not sequences:
        raise ValueError("trade_sequences must not be empty")

    maxima: list[int] = []
    for sequence in sequences:
        current = 0
        maximum = 0
        for value in _r_values(sequence):
            if value < 0.0:
                current += 1
                maximum = max(maximum, current)
            else:
                current = 0
        maxima.append(maximum)

    counts = np.bincount(maxima)
    streak_lengths = np.arange(counts.size)
    figure, ax = _axes(ax, (8.0, 4.8))
    bars = ax.bar(
        streak_lengths,
        counts,
        width=0.72,
        color=_BLUE,
        edgecolor=_INK,
        linewidth=0.55,
    )
    for bar, count in zip(bars, counts, strict=True):
        if count:
            ax.text(
                bar.get_x() + bar.get_width() / 2.0,
                bar.get_height(),
                f"{int(count)}",
                ha="center",
                va="bottom",
                fontsize=8,
                color=_INK,
            )

    _title(
        ax,
        "Distribution of Maximum Losing Streaks",
        f"Longest consecutive run with R < 0 in each sequence; N={len(sequences):,}",
    )
    ax.set_xlabel("Maximum losing streak (trades)")
    ax.set_ylabel("Trade sequences")
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_ylim(bottom=0.0)
    _style_axes(ax)
    return figure, ax


def plot_outcome_probabilities(
    result: OptimizationResult,
    *,
    ax: Axes | None = None,
) -> tuple[Figure, Axes]:
    """Compare pass, fail, and timeout probabilities across candidate risks."""
    _validate_result(result)
    figure, ax = _axes(ax, (8.0, 4.8))
    levels = result.risk_levels
    timeout = np.array([item.timeout_probability for item in result.evaluations])

    ax.plot(
        levels,
        result.probabilities_pass,
        color=_BLUE,
        marker="o",
        linewidth=1.8,
        markersize=4.2,
        label="Pass",
    )
    ax.plot(
        levels,
        result.probabilities_fail,
        color=_ORANGE,
        linestyle="--",
        marker="s",
        markerfacecolor="white",
        linewidth=1.7,
        markersize=4.0,
        label="Fail",
    )
    ax.plot(
        levels,
        timeout,
        color=_OLIVE,
        linestyle=":",
        marker="^",
        markerfacecolor="white",
        linewidth=1.7,
        markersize=4.2,
        label="Timeout",
    )
    ax.axvline(
        result.optimal_risk_raw,
        color=_INK,
        linestyle=(0, (4, 3)),
        linewidth=1.2,
        label=f"Raw optimum ({result.optimal_risk_raw:.2%})",
    )

    n_paths = result.evaluations[0].mc_result.n_simulations
    _title(
        ax,
        "Outcome Probabilities by Risk Level",
        f"Mutually exclusive terminal outcomes; {n_paths:,} paths per candidate",
    )
    ax.set_xlabel("Risk per trade")
    ax.set_ylabel("Probability")
    ax.xaxis.set_major_formatter(PercentFormatter(xmax=1.0, decimals=2))
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=1.0, decimals=0))
    ax.set_ylim(0.0, 1.0)
    ax.legend(frameon=False, fontsize=8, ncols=2)
    _style_axes(ax)
    return figure, ax


__all__ = [
    "plot_risk_pass_probability",
    "plot_risk_drawdown",
    "plot_final_equity_distribution",
    "plot_max_drawdown_distribution",
    "plot_losing_streak_distribution",
    "plot_outcome_probabilities",
]

"""FARS source package."""

from src.types import Trade, SyntheticConfig, FundedAccountRules
from src.monte_carlo import MonteCarloConfig, MonteCarloResult, run_monte_carlo
from src.optimization import (
    OptimizationConfig,
    OptimizationResult,
    RiskLevelEvaluation,
    compute_var_cvar,
    smooth_curve_gaussian,
    optimize_risk_per_trade,
    compute_fres_sensitivity,
)
from src.visualization import (
    plot_risk_pass_probability,
    plot_risk_drawdown,
    plot_final_equity_distribution,
    plot_max_drawdown_distribution,
    plot_losing_streak_distribution,
    plot_outcome_probabilities,
)

__all__ = [
    "Trade",
    "SyntheticConfig",
    "FundedAccountRules",
    "MonteCarloConfig",
    "MonteCarloResult",
    "run_monte_carlo",
    "OptimizationConfig",
    "OptimizationResult",
    "RiskLevelEvaluation",
    "compute_var_cvar",
    "smooth_curve_gaussian",
    "optimize_risk_per_trade",
    "compute_fres_sensitivity",
    "plot_risk_pass_probability",
    "plot_risk_drawdown",
    "plot_final_equity_distribution",
    "plot_max_drawdown_distribution",
    "plot_losing_streak_distribution",
    "plot_outcome_probabilities",
]

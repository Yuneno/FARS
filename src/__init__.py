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
]

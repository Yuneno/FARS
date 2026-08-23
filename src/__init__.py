"""FARS public API with lazy imports.

Importing a numerical submodule such as :mod:`src.account` must not initialize
Matplotlib. Public names remain available from ``src`` and are loaded only when
requested.
"""

from importlib import import_module
from typing import Any

__all__ = [
    "Trade",
    "SyntheticConfig",
    "FundedAccountRules",
    "AuditIssue",
    "CapabilityStatus",
    "TradeAuditReport",
    "IngestionProvenance",
    "CanonicalTradeDataset",
    "TradeDataError",
    "load_trade_csv",
    "analyze_bootstrap",
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


_EXPORTS = {
    "Trade": ("src.types", "Trade"),
    "SyntheticConfig": ("src.types", "SyntheticConfig"),
    "FundedAccountRules": ("src.types", "FundedAccountRules"),
    "AuditIssue": ("src.ingestion", "AuditIssue"),
    "CapabilityStatus": ("src.ingestion", "CapabilityStatus"),
    "TradeAuditReport": ("src.ingestion", "TradeAuditReport"),
    "IngestionProvenance": ("src.ingestion", "IngestionProvenance"),
    "CanonicalTradeDataset": ("src.ingestion", "CanonicalTradeDataset"),
    "TradeDataError": ("src.ingestion", "TradeDataError"),
    "load_trade_csv": ("src.ingestion", "load_trade_csv"),
    "analyze_bootstrap": ("src.bootstrap", "analyze_bootstrap"),
    "MonteCarloConfig": ("src.monte_carlo", "MonteCarloConfig"),
    "MonteCarloResult": ("src.monte_carlo", "MonteCarloResult"),
    "run_monte_carlo": ("src.monte_carlo", "run_monte_carlo"),
    "OptimizationConfig": ("src.optimization", "OptimizationConfig"),
    "OptimizationResult": ("src.optimization", "OptimizationResult"),
    "RiskLevelEvaluation": ("src.optimization", "RiskLevelEvaluation"),
    "compute_var_cvar": ("src.optimization", "compute_var_cvar"),
    "smooth_curve_gaussian": ("src.optimization", "smooth_curve_gaussian"),
    "optimize_risk_per_trade": ("src.optimization", "optimize_risk_per_trade"),
    "compute_fres_sensitivity": ("src.optimization", "compute_fres_sensitivity"),
    "plot_risk_pass_probability": (
        "src.visualization",
        "plot_risk_pass_probability",
    ),
    "plot_risk_drawdown": ("src.visualization", "plot_risk_drawdown"),
    "plot_final_equity_distribution": (
        "src.visualization",
        "plot_final_equity_distribution",
    ),
    "plot_max_drawdown_distribution": (
        "src.visualization",
        "plot_max_drawdown_distribution",
    ),
    "plot_losing_streak_distribution": (
        "src.visualization",
        "plot_losing_streak_distribution",
    ),
    "plot_outcome_probabilities": (
        "src.visualization",
        "plot_outcome_probabilities",
    ),
}


def __getattr__(name: str) -> Any:
    """Load a public symbol on first access and cache it in this module."""
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

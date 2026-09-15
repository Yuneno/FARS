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
    "AccountEquityEvent",
    "AccountRecordFingerprint",
    "AccountTradeAuditReport",
    "AccountTradeDataError",
    "AccountTradeIngestionProvenance",
    "CanonicalAccountTrade",
    "CanonicalAccountTradeDataset",
    "RValueProvenance",
    "load_account_trade_csv",
    "AccountRuleInput",
    "CAP_CLOSED_TRADE_EVENTS",
    "CAP_END_OF_DAY_EVENTS",
    "CAP_INTRADAY_EVENTS",
    "CAP_NEWS_FLAGS",
    "CAP_OPEN_POSITION_STATUS",
    "CAP_POSITION_SIZES",
    "CAP_TRADE_OPEN_TIMESTAMPS",
    "ConsistencyRule",
    "DailyLossRule",
    "FundedAccountProfileV2",
    "FundedAccountStateV2",
    "FundedRuleError",
    "MaximumLossRule",
    "OperationalRules",
    "ProfitTargetRule",
    "RoundingPolicy",
    "RuleAmount",
    "RuleEvaluationEvent",
    "RuleEvaluationReport",
    "RuleSource",
    "rapid_25k_profile",
    "CostAssumptions",
    "DistributionSummary",
    "PathAnalysisError",
    "PathAnalysisUnsupportedError",
    "PathSimulationConfig",
    "ProbabilityEstimate",
    "ProbabilisticPathResult",
    "RiskSensitivityPoint",
    "RiskSensitivityResult",
    "RiskSizingPolicy",
    "SimulatedPath",
    "TradeLimitEstimate",
    "estimate_trades_for_pass_probability",
    "run_probabilistic_paths",
    "run_risk_sensitivity",
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
    "AccountEquityEvent": ("src.account_data", "AccountEquityEvent"),
    "AccountRecordFingerprint": (
        "src.account_data",
        "AccountRecordFingerprint",
    ),
    "AccountTradeAuditReport": (
        "src.account_data",
        "AccountTradeAuditReport",
    ),
    "AccountTradeDataError": ("src.account_data", "AccountTradeDataError"),
    "AccountTradeIngestionProvenance": (
        "src.account_data",
        "AccountTradeIngestionProvenance",
    ),
    "CanonicalAccountTrade": ("src.account_data", "CanonicalAccountTrade"),
    "CanonicalAccountTradeDataset": (
        "src.account_data",
        "CanonicalAccountTradeDataset",
    ),
    "RValueProvenance": ("src.account_data", "RValueProvenance"),
    "load_account_trade_csv": ("src.account_data", "load_account_trade_csv"),
    "AccountRuleInput": ("src.funded_rules_v2", "AccountRuleInput"),
    "CAP_CLOSED_TRADE_EVENTS": (
        "src.funded_rules_v2",
        "CAP_CLOSED_TRADE_EVENTS",
    ),
    "CAP_END_OF_DAY_EVENTS": (
        "src.funded_rules_v2",
        "CAP_END_OF_DAY_EVENTS",
    ),
    "CAP_INTRADAY_EVENTS": ("src.funded_rules_v2", "CAP_INTRADAY_EVENTS"),
    "CAP_NEWS_FLAGS": ("src.funded_rules_v2", "CAP_NEWS_FLAGS"),
    "CAP_OPEN_POSITION_STATUS": (
        "src.funded_rules_v2",
        "CAP_OPEN_POSITION_STATUS",
    ),
    "CAP_POSITION_SIZES": ("src.funded_rules_v2", "CAP_POSITION_SIZES"),
    "CAP_TRADE_OPEN_TIMESTAMPS": (
        "src.funded_rules_v2",
        "CAP_TRADE_OPEN_TIMESTAMPS",
    ),
    "ConsistencyRule": ("src.funded_rules_v2", "ConsistencyRule"),
    "DailyLossRule": ("src.funded_rules_v2", "DailyLossRule"),
    "FundedAccountProfileV2": (
        "src.funded_rules_v2",
        "FundedAccountProfileV2",
    ),
    "FundedAccountStateV2": ("src.funded_rules_v2", "FundedAccountStateV2"),
    "FundedRuleError": ("src.funded_rules_v2", "FundedRuleError"),
    "MaximumLossRule": ("src.funded_rules_v2", "MaximumLossRule"),
    "OperationalRules": ("src.funded_rules_v2", "OperationalRules"),
    "ProfitTargetRule": ("src.funded_rules_v2", "ProfitTargetRule"),
    "RoundingPolicy": ("src.funded_rules_v2", "RoundingPolicy"),
    "RuleAmount": ("src.funded_rules_v2", "RuleAmount"),
    "RuleEvaluationEvent": (
        "src.funded_rules_v2",
        "RuleEvaluationEvent",
    ),
    "RuleEvaluationReport": (
        "src.funded_rules_v2",
        "RuleEvaluationReport",
    ),
    "RuleSource": ("src.funded_rules_v2", "RuleSource"),
    "rapid_25k_profile": ("src.funded_profiles", "rapid_25k_profile"),
    "CostAssumptions": ("src.probabilistic_paths", "CostAssumptions"),
    "DistributionSummary": ("src.probabilistic_paths", "DistributionSummary"),
    "PathAnalysisError": ("src.probabilistic_paths", "PathAnalysisError"),
    "PathAnalysisUnsupportedError": (
        "src.probabilistic_paths",
        "PathAnalysisUnsupportedError",
    ),
    "PathSimulationConfig": ("src.probabilistic_paths", "PathSimulationConfig"),
    "ProbabilityEstimate": ("src.probabilistic_paths", "ProbabilityEstimate"),
    "ProbabilisticPathResult": (
        "src.probabilistic_paths",
        "ProbabilisticPathResult",
    ),
    "RiskSensitivityPoint": (
        "src.probabilistic_paths",
        "RiskSensitivityPoint",
    ),
    "RiskSensitivityResult": (
        "src.probabilistic_paths",
        "RiskSensitivityResult",
    ),
    "RiskSizingPolicy": ("src.probabilistic_paths", "RiskSizingPolicy"),
    "SimulatedPath": ("src.probabilistic_paths", "SimulatedPath"),
    "TradeLimitEstimate": ("src.probabilistic_paths", "TradeLimitEstimate"),
    "estimate_trades_for_pass_probability": (
        "src.probabilistic_paths",
        "estimate_trades_for_pass_probability",
    ),
    "run_probabilistic_paths": (
        "src.probabilistic_paths",
        "run_probabilistic_paths",
    ),
    "run_risk_sensitivity": (
        "src.probabilistic_paths",
        "run_risk_sensitivity",
    ),
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

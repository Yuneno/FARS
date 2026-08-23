"""
FARS — Funded Account Risk System
=================================
Experimental end-to-end demo for the implemented Phase 1–7 pipeline.

Usage:
    python main.py              # run demo
    python -m pytest -p no:debugging tests/ -v  # run test suite
"""

from src.monte_carlo import MonteCarloConfig
from src.optimization import OptimizationConfig, optimize_risk_per_trade
from src.types import FundedAccountRules, SyntheticConfig


def main():
    synthetic = SyntheticConfig(
        seed=42,
        n_trades=100,
        win_rate=0.45,
        avg_win_r=2.0,
        avg_loss_r=1.0,
    )
    rules = FundedAccountRules(
        initial_balance=100_000.0,
        profit_target_pct=0.10,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.05,
        risk_per_trade=0.01,
        max_trades=100,
    )
    result = optimize_risk_per_trade(
        rules,
        synthetic_config=synthetic,
        mc_config=MonteCarloConfig(
            n_simulations=200,
            seed=42,
            batch_size=100,
            n_bootstrap=50,
        ),
        opt_config=OptimizationConfig(risk_levels=(0.005, 0.010, 0.015)),
    )

    print("FARS v0.1 — experimental synthetic risk analysis")
    print("Results are conditional on the synthetic IID model and fixed-dollar sizing.\n")
    print(" risk   pass    fail  timeout  hist.P95  rule.P95    FRES")
    for evaluation in result.evaluations:
        print(
            f"{evaluation.risk_per_trade:>5.2%} "
            f"{evaluation.probability_pass:>7.2%} "
            f"{evaluation.probability_fail:>7.2%} "
            f"{evaluation.timeout_probability:>8.2%} "
            f"{evaluation.p95_max_drawdown:>9.2%} "
            f"{evaluation.p95_rule_drawdown:>9.2%} "
            f"{evaluation.fres_score:>8.3f}"
        )

    print(f"\nRaw pass-probability optimum: {result.optimal_risk_raw:.2%}")
    print(f"FRES optimum:                {result.optimal_risk_fres:.2%}")
    print(
        "Plausible raw candidates:     "
        + ", ".join(f"{risk:.2%}" for risk in result.plausible_risk_levels)
    )


if __name__ == "__main__":
    main()

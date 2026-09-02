"""Versioned provider profiles kept outside the generic Phase 11B engine."""

from datetime import date
from decimal import Decimal

from src.funded_rules_v2 import (
    ConsistencyRule,
    FundedAccountProfileV2,
    MaximumLossRule,
    OperationalRules,
    ProfitTargetRule,
    RuleAmount,
    RuleSource,
)


def rapid_25k_profile() -> FundedAccountProfileV2:
    """Return the disabled Rapid 25K reference profile frozen on 2026-08-27.

    Known values are encoded, but exact replay remains disabled because cadence,
    breach boundary, consistency denominator/rounding, session semantics, and
    mixed-contract equivalence were unresolved in the approved specification.
    """

    retrieved = date(2026, 8, 27)
    return FundedAccountProfileV2(
        profile_id="myfundedfutures-rapid-25k-evaluation",
        profile_version="2026-08-27-provisional",
        provider="My Funded Futures",
        currency="USD",
        starting_balance=Decimal("25000"),
        profit_target=ProfitTargetRule(RuleAmount("absolute", Decimal("1500"))),
        maximum_loss=MaximumLossRule(
            distance=RuleAmount("absolute", Decimal("1000")),
            mode="trailing",
            reference="equity",
            update_cadence=None,
            monitoring_cadence=None,
            breach_boundary=None,
            threshold_ceiling=Decimal("25100"),
        ),
        session_timezone=None,
        session_boundary=None,
        minimum_trading_days=2,
        daily_loss=None,
        consistency=ConsistencyRule(
            maximum_ratio=Decimal("0.50"),
            denominator=None,
            rounding=None,
            window_sessions=None,
            acceptance_boundary="<=",
            action="block_pass",
            non_positive_behavior="block_pass",
        ),
        operational=OperationalRules(
            maximum_minis=3,
            maximum_micros=30,
            micros_per_mini=None,
            news_trading_allowed=True,
            inactivity_calendar_days=7,
            advisory_constraints={
                "status": "provisional; exact replay disabled",
            },
        ),
        rule_sources=(
            RuleSource(
                "https://help.myfundedfutures.com/en/articles/"
                "14116402-rapid-plan-25k-a-comprehensive-look",
                retrieved,
            ),
            RuleSource(
                "https://help.myfundedfutures.com/en/articles/"
                "11994562-consistency-rule-at-my-fundedfutures",
                retrieved,
            ),
            RuleSource(
                "https://help.myfundedfutures.com/en/articles/11972075-inactivity-rule",
                retrieved,
            ),
        ),
        unresolved_assumptions=(
            "evaluation threshold update cadence (end of day versus intraday)",
            "maximum-loss touch versus cross breach boundary",
            "50% consistency denominator and rounding policy",
            "session timezone, holidays, and trading-day boundary",
            "mixed mini/micro contract equivalence",
        ),
        enabled=False,
    )


__all__ = ["rapid_25k_profile"]

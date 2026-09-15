"""Versioned provider profiles kept outside the generic Phase 11B engine."""

from datetime import date, time
from decimal import Decimal
from typing import Any

from src.funded_rules_v2 import (
    ConsistencyRule,
    FundedAccountProfileV2,
    MaximumLossRule,
    OperationalRules,
    ProfitTargetRule,
    RuleAmount,
    RuleSource,
    UpdateCadence,
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


# =============================================================================
# Apex Trader Funding Profiles (Block D1)
# Derived from KAI_APEX_SPEC.md (specs/2026-07-29)

_APEX_RETRIEVED = date(2026, 7, 29)
_APEX_RULE_SOURCE = RuleSource("https://apextraderfunding.com", _APEX_RETRIEVED)
_APEX_SESSION_TIMEZONE = "America/New_York"
_APEX_SESSION_BOUNDARY = time(17, 0)

_APEX_SPECS: dict[str, dict[str, Any]] = {
    "25k": {
        "starting_balance": Decimal("25000"),
        "target": Decimal("1500"),
        "trailing_dd": Decimal("1500"),
        "max_minis": 2,
        "max_micros": 20,
    },
    "50k": {
        "starting_balance": Decimal("50000"),
        "target": Decimal("3000"),
        "trailing_dd": Decimal("2000"),
        "max_minis": 4,
        "max_micros": 40,
    },
    "100k": {
        "starting_balance": Decimal("100000"),
        "target": Decimal("6000"),
        "trailing_dd": Decimal("3000"),
        "max_minis": 6,
        "max_micros": 60,
    },
    "150k": {
        "starting_balance": Decimal("150000"),
        "target": Decimal("9000"),
        "trailing_dd": Decimal("4500"),
        "max_minis": 10,
        "max_micros": 100,
    },
}


def apex_profile(
    account_size: str = "25k",
    *,
    cadence: UpdateCadence = "closed_trade",
) -> FundedAccountProfileV2:
    """Return an active Apex Trader Funding evaluation account profile.

    Specifications verified from KAI_APEX_SPEC.md:
    - Trailing drawdown is dynamic following equity; floor freezes when peak reaches DD + $100
      (starting_balance + $100 ceiling, lockBuffer = $100).
    - Touching the floor exactly breaches/burns the account (breach_boundary = '<=').
    - News trading is allowed (news_trading_allowed = True).
    - Minimum trading days = 0 in evaluation challenge runner.
    - Evaluation consistency rule is not enforced (consistency = None).
    - Daily loss limit is not enforced during evaluation (daily_loss = None).
    - Cadence can be 'closed_trade' (baseline reference) or 'intraday_event' (M1 MAE intraday).
    """
    key = account_size.lower().strip()
    if key not in _APEX_SPECS:
        raise ValueError(
            f"Unknown Apex account size {account_size!r}; must be one of {sorted(_APEX_SPECS)}"
        )
    spec = _APEX_SPECS[key]
    starting_balance = spec["starting_balance"]
    target = spec["target"]
    trailing_dd = spec["trailing_dd"]
    max_minis = spec["max_minis"]
    max_micros = spec["max_micros"]
    lock_ceiling = starting_balance + Decimal("100")

    return FundedAccountProfileV2(
        profile_id=f"apex-{key}-evaluation",
        profile_version="2026-07-29-spec",
        provider="Apex Trader Funding",
        currency="USD",
        starting_balance=starting_balance,
        profit_target=ProfitTargetRule(
            target=RuleAmount("absolute", target),
            reference="balance",
            boundary=">=",
        ),
        maximum_loss=MaximumLossRule(
            distance=RuleAmount("absolute", trailing_dd),
            mode="trailing",
            reference="equity",
            update_cadence=cadence,
            monitoring_cadence=cadence,
            breach_boundary="<=",
            threshold_ceiling=lock_ceiling,
        ),
        session_timezone=_APEX_SESSION_TIMEZONE,
        session_boundary=_APEX_SESSION_BOUNDARY,
        minimum_trading_days=0,
        daily_loss=None,
        consistency=None,
        operational=OperationalRules(
            maximum_minis=max_minis,
            maximum_micros=max_micros,
            micros_per_mini=Decimal("10"),
            news_trading_allowed=True,
            advisory_constraints={
                "horizon_calendar_days": "30",
                "horizon_assumption": "configurable; 30d panel vs 90d research contradiction",
                "commission_rt_mnq": "1.00",
                "commission_contradiction": "1.00 executable vs 1.34 backend vs 1.42 readme",
                "safety_margin_fraction": "0.75",
                "lock_buffer": "100",
                "lock_peak_required": str(trailing_dd + Decimal("100")),
                "intraday_trailing_model": "active (not legacy EOD)",
            },
        ),
        rule_sources=(_APEX_RULE_SOURCE,),
        unresolved_assumptions=(),
        enabled=True,
    )


def apex_25k_profile(*, cadence: UpdateCadence = "closed_trade") -> FundedAccountProfileV2:
    """Return Apex 25K evaluation profile ($1,500 target, $1,500 trailing DD, 2 mini / 20 micro)."""
    return apex_profile("25k", cadence=cadence)


def apex_50k_profile(*, cadence: UpdateCadence = "closed_trade") -> FundedAccountProfileV2:
    """Return Apex 50K evaluation profile ($3,000 target, $2,000 trailing DD, 4 mini / 40 micro)."""
    return apex_profile("50k", cadence=cadence)


def apex_100k_profile(*, cadence: UpdateCadence = "closed_trade") -> FundedAccountProfileV2:
    """Return Apex 100K evaluation profile ($6,000 target, $3,000 trailing DD, 6 mini / 60 micro)."""
    return apex_profile("100k", cadence=cadence)


def apex_150k_profile(*, cadence: UpdateCadence = "closed_trade") -> FundedAccountProfileV2:
    """Return Apex 150K evaluation profile ($9,000 target, $4,500 trailing DD, 10 mini / 100 micro)."""
    return apex_profile("150k", cadence=cadence)


__all__ = [
    "rapid_25k_profile",
    "apex_profile",
    "apex_25k_profile",
    "apex_50k_profile",
    "apex_100k_profile",
    "apex_150k_profile",
]

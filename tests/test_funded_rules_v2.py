"""Deterministic acceptance tests for the Phase 11B funded-rule engine."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest

from src.funded_rules_v2 import (
    CAP_CLOSED_TRADE_EVENTS,
    CAP_END_OF_DAY_EVENTS,
    CAP_INTRADAY_EVENTS,
    CAP_NEWS_FLAGS,
    CAP_OPEN_POSITION_STATUS,
    CAP_POSITION_SIZES,
    CAP_TRADE_OPEN_TIMESTAMPS,
    AccountRuleInput,
    ConsistencyRule,
    DailyLossRule,
    FundedAccountProfileV2,
    FundedAccountStateV2,
    FundedRuleError,
    MaximumLossRule,
    OperationalRules,
    ProfitTargetRule,
    RoundingPolicy,
    RuleAmount,
    RuleSource,
)
from src.funded_profiles import rapid_25k_profile
from src.types import FundedAccountRules


UTC = timezone.utc
SOURCE = RuleSource("https://example.com/rules/v1", date(2026, 8, 29))


def _profile(**changes) -> FundedAccountProfileV2:
    values = {
        "profile_id": "test-25k",
        "profile_version": "1",
        "provider": "Test Provider",
        "currency": "USD",
        "starting_balance": Decimal("25000"),
        "profit_target": ProfitTargetRule(
            RuleAmount("absolute", Decimal("1500"))
        ),
        "maximum_loss": MaximumLossRule(
            RuleAmount("absolute", Decimal("1000")),
            mode="static",
            reference="balance",
            update_cadence="closed_trade",
            monitoring_cadence="closed_trade",
            breach_boundary="<=",
        ),
        "session_timezone": "UTC",
        "session_boundary": time(0),
        "operational": OperationalRules(news_trading_allowed=True),
        "rule_sources": (SOURCE,),
    }
    values.update(changes)
    return FundedAccountProfileV2(**values)


def _state(
    profile: FundedAccountProfileV2 | None = None,
    *capabilities: str,
) -> FundedAccountStateV2:
    supplied = capabilities or (CAP_CLOSED_TRADE_EVENTS,)
    return FundedAccountStateV2(
        profile or _profile(),
        opened_at=datetime(2026, 8, 1, tzinfo=UTC),
        data_capabilities=frozenset(supplied),
    )


def _closed(
    balance: str,
    pnl: str,
    *,
    day: int = 1,
    hour: int = 12,
    equity: str | None = None,
    **changes,
) -> AccountRuleInput:
    values = {
        "timestamp": datetime(2026, 8, day, hour, tzinfo=UTC),
        "event_type": "closed_trade",
        "balance": Decimal(balance),
        "equity": Decimal(equity if equity is not None else balance),
        "realized_pnl": Decimal(pnl),
        "trade_id": f"T-{day}-{hour}-{balance}",
    }
    values.update(changes)
    return AccountRuleInput(**values)


def _event_kinds(report) -> list[tuple[str, str]]:
    return [(event.kind, event.rule) for event in report.events]


def test_legacy_percentage_contract_remains_separate_and_constructible():
    legacy = FundedAccountRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.05,
    )

    assert legacy.initial_balance == 100_000
    assert not isinstance(legacy, FundedAccountProfileV2)


@pytest.mark.parametrize(
    ("target", "ending_balance"),
    [
        (RuleAmount("absolute", Decimal("1500")), "26500"),
        (RuleAmount("percentage", Decimal("0.06")), "26500"),
    ],
)
def test_absolute_and_percentage_profit_targets_hit_exact_boundary(
    target,
    ending_balance,
):
    profile = _profile(profit_target=ProfitTargetRule(target, boundary=">="))
    report = _state(profile).apply(_closed(ending_balance, "1500"))

    assert report.pass_eligible
    assert report.primary_event.kind == "pass_eligible"
    assert report.primary_event.details["target"] == Decimal("26500")


def test_profit_target_strict_boundary_requires_crossing():
    profile = _profile(
        profit_target=ProfitTargetRule(
            RuleAmount("absolute", Decimal("1500")),
            boundary=">",
        )
    )
    state = _state(profile)

    exact = state.apply(_closed("26500", "1500", hour=10))
    crossed = state.apply(_closed("26500.01", "0.01", hour=11))

    assert exact.primary_event.kind == "in_progress"
    assert crossed.pass_eligible


@pytest.mark.parametrize(
    ("boundary", "breaches"),
    [("<=", True), ("<", False)],
)
def test_maximum_loss_boundary_distinguishes_touch_from_cross(boundary, breaches):
    profile = _profile(
        maximum_loss=MaximumLossRule(
            RuleAmount("absolute", Decimal("1000")),
            mode="static",
            reference="balance",
            update_cadence="closed_trade",
            monitoring_cadence="closed_trade",
            breach_boundary=boundary,
        )
    )
    report = _state(profile).apply(_closed("24000", "-1000"))

    assert (report.primary_event.kind == "breach") is breaches


def test_trailing_threshold_updates_on_closed_trade_and_honors_ceiling():
    profile = _profile(
        maximum_loss=MaximumLossRule(
            RuleAmount("absolute", Decimal("1000")),
            mode="trailing",
            reference="balance",
            update_cadence="closed_trade",
            monitoring_cadence="closed_trade",
            breach_boundary="<=",
            threshold_ceiling=Decimal("25100"),
        )
    )
    state = _state(profile)

    state.apply(_closed("28000", "3000", hour=10))
    report = state.apply(_closed("25100", "-2900", hour=11))

    assert state.high_watermark == Decimal("28000")
    assert state.maximum_loss_threshold == Decimal("25100")
    assert report.primary_event.kind == "breach"


def test_end_of_day_trailing_threshold_updates_only_at_eod_event():
    profile = _profile(
        maximum_loss=MaximumLossRule(
            RuleAmount("absolute", Decimal("1000")),
            mode="trailing",
            reference="equity",
            update_cadence="end_of_day",
            monitoring_cadence="end_of_day",
            breach_boundary="<=",
        )
    )
    state = _state(profile, CAP_CLOSED_TRADE_EVENTS, CAP_END_OF_DAY_EVENTS)

    state.apply(_closed("25500", "500", hour=10))
    assert state.high_watermark == Decimal("25000")
    assert state.maximum_loss_threshold == Decimal("24000")

    state.apply(
        AccountRuleInput(
            datetime(2026, 8, 1, 23, tzinfo=UTC),
            "end_of_day",
            balance=Decimal("25500"),
            equity=Decimal("25500"),
        )
    )
    assert state.high_watermark == Decimal("25500")
    assert state.maximum_loss_threshold == Decimal("24500")


def test_pass_waits_for_configured_end_of_day_monitoring_event():
    profile = _profile(
        maximum_loss=MaximumLossRule(
            RuleAmount("absolute", Decimal("1000")),
            mode="trailing",
            reference="equity",
            update_cadence="end_of_day",
            monitoring_cadence="end_of_day",
            breach_boundary="<=",
        )
    )
    state = _state(profile, CAP_CLOSED_TRADE_EVENTS, CAP_END_OF_DAY_EVENTS)

    closed = state.apply(_closed("26500", "1500", hour=10))
    eod = state.apply(
        AccountRuleInput(
            datetime(2026, 8, 1, 23, tzinfo=UTC),
            "end_of_day",
            Decimal("26500"),
            Decimal("26500"),
        )
    )

    assert closed.primary_event.kind == "pass_blocked"
    assert closed.primary_event.rule == "maximum_loss"
    assert eod.pass_eligible


def test_missing_intraday_coverage_blocks_exact_replay_and_false_pass():
    profile = _profile(
        maximum_loss=MaximumLossRule(
            RuleAmount("absolute", Decimal("1000")),
            mode="trailing",
            reference="equity",
            update_cadence="intraday_event",
            monitoring_cadence="intraday_event",
            breach_boundary="<=",
        )
    )
    report = _state(profile).apply(_closed("26500", "1500"))

    assert not report.pass_eligible
    assert (
        "not_evaluable",
        "profile",
    ) in _event_kinds(report)
    assert any(
        event.details.get("capability") == CAP_INTRADAY_EVENTS
        for event in report.events
    )


def test_intraday_equity_fixture_updates_and_breaches_deterministically():
    profile = _profile(
        maximum_loss=MaximumLossRule(
            RuleAmount("absolute", Decimal("1000")),
            mode="trailing",
            reference="equity",
            update_cadence="intraday_event",
            monitoring_cadence="intraday_event",
            breach_boundary="<=",
        )
    )
    state = _state(profile, CAP_CLOSED_TRADE_EVENTS, CAP_INTRADAY_EVENTS)
    state.apply(
        AccountRuleInput(
            datetime(2026, 8, 1, 10, tzinfo=UTC),
            "intraday",
            Decimal("25000"),
            Decimal("26000"),
        )
    )

    report = state.apply(
        AccountRuleInput(
            datetime(2026, 8, 1, 11, tzinfo=UTC),
            "intraday",
            Decimal("25000"),
            Decimal("25000"),
        )
    )

    assert state.maximum_loss_threshold == Decimal("25000")
    assert report.primary_event.kind == "breach"


def test_absent_daily_limit_needs_no_sentinel_and_allows_pass():
    profile = _profile(daily_loss=None)
    report = _state(profile).apply(_closed("26500", "1500"))

    assert report.pass_eligible
    assert all(event.rule != "daily_loss" for event in report.events)


def test_daily_hard_limit_breaches_at_exact_decimal_boundary():
    daily = DailyLossRule(
        RuleAmount("absolute", Decimal("500")),
        reference="balance",
        reset_timezone="UTC",
        session_boundary=time(0),
        monitoring_cadence="closed_trade",
        breach_boundary="<=",
        action="breach",
    )
    report = _state(_profile(daily_loss=daily)).apply(_closed("24500", "-500"))

    assert report.primary_event.kind == "breach"
    assert report.primary_event.rule == "daily_loss"


def test_percentage_loss_rules_resolve_against_declared_bases():
    maximum = MaximumLossRule(
        RuleAmount("percentage", Decimal("0.04")),
        mode="static",
        reference="balance",
        update_cadence="closed_trade",
        monitoring_cadence="closed_trade",
        breach_boundary="<=",
    )
    daily = DailyLossRule(
        RuleAmount(
            "percentage",
            Decimal("0.02"),
            percentage_base="reference_value",
        ),
        reference="balance",
        reset_timezone="UTC",
        session_boundary=time(0),
        monitoring_cadence="closed_trade",
        breach_boundary="<=",
        action="breach",
    )
    report = _state(_profile(maximum_loss=maximum, daily_loss=daily)).apply(
        _closed("24000", "-1000")
    )

    assert _event_kinds(report)[:2] == [
        ("breach", "maximum_loss"),
        ("breach", "daily_loss"),
    ]


def test_daily_soft_pause_resets_at_explicit_session_boundary():
    daily = DailyLossRule(
        RuleAmount("absolute", Decimal("500")),
        reference="balance",
        reset_timezone="UTC",
        session_boundary=time(0),
        monitoring_cadence="closed_trade",
        breach_boundary="<=",
        action="soft_pause",
    )
    state = _state(_profile(daily_loss=daily))

    paused = state.apply(_closed("24500", "-500", day=1))
    passed_next_day = state.apply(_closed("26500", "2000", day=2))

    assert paused.primary_event.kind == "soft_pause"
    assert not state.soft_paused
    assert passed_next_day.pass_eligible


def test_daily_reference_remains_bound_to_its_own_reset_session():
    daily = DailyLossRule(
        RuleAmount("absolute", Decimal("500")),
        reference="balance",
        reset_timezone="America/New_York",
        session_boundary=time(17),
        monitoring_cadence="closed_trade",
        breach_boundary="<=",
        action="breach",
    )
    state = _state(_profile(daily_loss=daily))
    state.apply(_closed("25200", "200", day=1, hour=20))  # 16:00 New York
    state.apply(_closed("25100", "-100", day=1, hour=22))  # new session at 18:00
    report = state.apply(_closed("24700", "-400", day=2, hour=1))

    assert report.primary_event.kind == "breach"
    assert report.primary_event.details["threshold"] == Decimal("24700")


def test_consistency_blocks_pass_without_recording_breach_then_clears():
    consistency = ConsistencyRule(
        maximum_ratio=Decimal("0.50"),
        denominator="sum_positive_days",
        rounding=RoundingPolicy(4),
        window_sessions=None,
        acceptance_boundary="<=",
        action="block_pass",
        non_positive_behavior="block_pass",
    )
    state = _state(_profile(consistency=consistency, minimum_trading_days=2))

    first = state.apply(_closed("26500", "1500", day=1))
    second = state.apply(_closed("28000", "1500", day=2))

    assert ("consistency_block", "consistency") in _event_kinds(first)
    assert all(event.kind != "breach" for event in first.events)
    assert second.pass_eligible
    assert second.primary_event.details["trading_days"] == 2


def test_consistency_strict_boundary_blocks_exactly_fifty_percent():
    consistency = ConsistencyRule(
        maximum_ratio=Decimal("0.50"),
        denominator="sum_positive_days",
        rounding=RoundingPolicy(4),
        window_sessions=None,
        acceptance_boundary="<",
        action="block_pass",
        non_positive_behavior="block_pass",
    )
    state = _state(_profile(consistency=consistency))
    state.apply(_closed("26500", "1500", day=1))
    report = state.apply(_closed("28000", "1500", day=2))

    assert report.primary_event.kind == "consistency_block"
    assert report.primary_event.details["ratio"] == Decimal("0.5000")


def test_non_positive_consistency_denominator_uses_explicit_behavior():
    consistency = ConsistencyRule(
        maximum_ratio=Decimal("0.50"),
        denominator="net_evaluation_profit",
        rounding=RoundingPolicy(2),
        window_sessions=None,
        acceptance_boundary="<=",
        action="block_pass",
        non_positive_behavior="block_pass",
    )
    report = _state(_profile(consistency=consistency)).apply(
        _closed("24900", "-100")
    )

    assert report.primary_event.kind == "consistency_block"
    assert report.primary_event.details["denominator"] == Decimal("-100")


def test_simultaneous_breaches_are_fully_disclosed_in_fixed_priority():
    daily = DailyLossRule(
        RuleAmount("absolute", Decimal("500")),
        reference="balance",
        reset_timezone="UTC",
        session_boundary=time(0),
        monitoring_cadence="closed_trade",
        breach_boundary="<=",
        action="breach",
    )
    report = _state(_profile(daily_loss=daily)).apply(_closed("24000", "-1000"))

    assert _event_kinds(report)[:2] == [
        ("breach", "maximum_loss"),
        ("breach", "daily_loss"),
    ]


def test_mixed_contract_equivalence_enforces_combined_limit():
    operational = OperationalRules(
        maximum_minis=3,
        maximum_micros=30,
        micros_per_mini=Decimal("10"),
        news_trading_allowed=True,
    )
    state = _state(
        _profile(operational=operational),
        CAP_CLOSED_TRADE_EVENTS,
        CAP_POSITION_SIZES,
    )
    report = state.apply(
        _closed("25000", "0", mini_contracts=2, micro_contracts=20)
    )

    assert report.primary_event.kind == "breach"
    assert report.primary_event.rule == "position_size"


def test_unknown_mixed_contract_equivalence_is_not_evaluable():
    operational = OperationalRules(
        maximum_minis=3,
        maximum_micros=30,
        news_trading_allowed=True,
    )
    state = _state(
        _profile(operational=operational),
        CAP_CLOSED_TRADE_EVENTS,
        CAP_POSITION_SIZES,
    )
    report = state.apply(
        _closed("25000", "0", mini_contracts=1, micro_contracts=1)
    )

    assert report.primary_event.kind == "not_evaluable"
    assert report.primary_event.rule == "position_size"


def test_operational_session_news_and_forced_close_are_structured_breaches():
    operational = OperationalRules(
        permitted_session_start=time(9),
        permitted_session_end=time(16),
        forced_close_time=time(16),
        news_trading_allowed=False,
    )
    state = _state(
        _profile(operational=operational),
        CAP_CLOSED_TRADE_EVENTS,
        CAP_OPEN_POSITION_STATUS,
        CAP_NEWS_FLAGS,
        CAP_TRADE_OPEN_TIMESTAMPS,
    )
    report = state.apply(
        _closed(
            "25000",
            "0",
            hour=16,
            has_open_positions=True,
            is_news_window=True,
            trade_opened_at=datetime(2026, 8, 1, 16, tzinfo=UTC),
        )
    )

    assert _event_kinds(report)[:3] == [
        ("breach", "permitted_session"),
        ("breach", "forced_close"),
        ("breach", "news"),
    ]


def test_inactivity_window_uses_calendar_days_and_exact_boundary():
    operational = OperationalRules(
        news_trading_allowed=True,
        inactivity_calendar_days=7,
    )
    report = _state(_profile(operational=operational)).apply(
        _closed("25000", "0", day=8)
    )

    assert report.primary_event.kind == "breach"
    assert report.primary_event.rule == "inactivity"
    assert report.primary_event.details["inactive_days"] == 7


def test_balance_reconciliation_failure_is_atomic():
    state = _state()
    bad = _closed("25100", "50")

    with pytest.raises(FundedRuleError, match="does not reconcile"):
        state.apply(bad)

    assert state.balance == state.equity == Decimal("25000")
    assert state.history == ()
    assert state.trading_days == ()


def test_duplicate_trade_id_is_rejected_without_double_counting():
    state = _state()
    first = _closed("25100", "100", trade_id="same")
    state.apply(first)
    duplicate = _closed("25200", "100", hour=13, trade_id="same")

    with pytest.raises(FundedRuleError, match="duplicate closed trade_id"):
        state.apply(duplicate)

    assert state.balance == Decimal("25100")
    assert tuple(state.daily_profit.values()) == (Decimal("100"),)
    assert len(state.history) == 1


def test_rapid_25k_reference_profile_is_provisional_and_fail_closed():
    profile = rapid_25k_profile()

    assert not profile.enabled
    assert profile.starting_balance == Decimal("25000")
    assert profile.profit_target.target.value == Decimal("1500")
    assert profile.maximum_loss.distance.value == Decimal("1000")
    assert profile.maximum_loss.threshold_ceiling == Decimal("25100")
    assert profile.daily_loss is None
    assert profile.operational.maximum_minis == 3
    assert profile.operational.maximum_micros == 30
    assert profile.minimum_trading_days == 2
    assert len(profile.rule_sources) == 3
    assert len(profile.unresolved_assumptions) == 5

    state = _state(profile)
    report = state.apply(_closed("26500", "1500"))
    assert not report.pass_eligible
    assert _event_kinds(report) == [("not_evaluable", "profile")]
    assert state.balance == state.equity == Decimal("25000")
    assert state.high_watermark == Decimal("25000")
    assert state.maximum_loss_threshold == Decimal("24000")
    assert state.current_session is None
    assert state.trading_days == ()
    assert dict(state.daily_profit) == {}
    assert not state.terminal_breach
    assert state.history == (report,)


def test_enabled_profile_rejects_unresolved_semantics():
    provisional = rapid_25k_profile()

    with pytest.raises(ValueError, match="unresolved rule semantics"):
        replace(provisional, enabled=True, unresolved_assumptions=())


@pytest.mark.parametrize(
    "value",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("-1")],
)
def test_inputs_reject_nonfinite_or_negative_rule_amounts(value):
    with pytest.raises(ValueError):
        RuleAmount("absolute", value)

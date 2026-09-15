"""Unit tests for Apex Trader Funding evaluation profiles (Block D1)."""

from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest

from src.funded_profiles import (
    apex_profile,
    apex_25k_profile,
    apex_50k_profile,
    apex_100k_profile,
    apex_150k_profile,
)
from src.funded_rules_v2 import (
    AccountRuleInput,
    FundedAccountProfileV2,
    FundedAccountStateV2,
)


@pytest.mark.parametrize(
    "size,expected_balance,expected_target,expected_dd,expected_ceiling,expected_minis,expected_micros",
    [
        ("25k", Decimal("25000"), Decimal("1500"), Decimal("1500"), Decimal("25100"), 2, 20),
        ("50k", Decimal("50000"), Decimal("3000"), Decimal("2000"), Decimal("50100"), 4, 40),
        ("100k", Decimal("100000"), Decimal("6000"), Decimal("3000"), Decimal("100100"), 6, 60),
        ("150k", Decimal("150000"), Decimal("9000"), Decimal("4500"), Decimal("150100"), 10, 100),
    ],
)
def test_apex_profile_spec_constants(
    size: str,
    expected_balance: Decimal,
    expected_target: Decimal,
    expected_dd: Decimal,
    expected_ceiling: Decimal,
    expected_minis: int,
    expected_micros: int,
):
    profile = apex_profile(size)
    assert isinstance(profile, FundedAccountProfileV2)
    assert profile.enabled is True
    assert profile.readiness_reasons() == ()
    assert profile.provider == "Apex Trader Funding"
    assert profile.currency == "USD"
    assert profile.starting_balance == expected_balance
    assert profile.profit_target.target.value == expected_target
    assert profile.profit_target.boundary == ">="
    assert profile.profit_target.reference == "balance"
    assert profile.maximum_loss.distance.value == expected_dd
    assert profile.maximum_loss.mode == "trailing"
    assert profile.maximum_loss.reference == "equity"
    assert profile.maximum_loss.breach_boundary == "<="
    assert profile.maximum_loss.threshold_ceiling == expected_ceiling
    assert profile.operational.maximum_minis == expected_minis
    assert profile.operational.maximum_micros == expected_micros
    assert profile.operational.micros_per_mini == Decimal("10")
    assert profile.operational.news_trading_allowed is True
    assert profile.consistency is None
    assert profile.daily_loss is None
    assert profile.minimum_trading_days == 0
    assert profile.session_timezone == "America/New_York"
    assert profile.session_boundary == time(17, 0)
    assert len(profile.rule_sources) == 1
    assert profile.rule_sources[0].url == "https://apextraderfunding.com"


def test_apex_profile_dedicated_factories():
    p25 = apex_25k_profile()
    p50 = apex_50k_profile()
    p100 = apex_100k_profile()
    p150 = apex_150k_profile()

    assert p25.starting_balance == Decimal("25000")
    assert p50.starting_balance == Decimal("50000")
    assert p100.starting_balance == Decimal("100000")
    assert p150.starting_balance == Decimal("150000")


def test_apex_profile_cadence_support():
    p_closed = apex_25k_profile(cadence="closed_trade")
    p_intraday = apex_25k_profile(cadence="intraday_event")

    assert p_closed.maximum_loss.update_cadence == "closed_trade"
    assert p_closed.maximum_loss.monitoring_cadence == "closed_trade"

    assert p_intraday.maximum_loss.update_cadence == "intraday_event"
    assert p_intraday.maximum_loss.monitoring_cadence == "intraday_event"


def test_apex_profile_invalid_size():
    with pytest.raises(ValueError, match="Unknown Apex account size"):
        apex_profile("300k")


def test_apex_state_instantiation_and_event_evaluation():
    profile = apex_25k_profile()
    opened_at = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
    state = FundedAccountStateV2(
        profile=profile,
        opened_at=opened_at,
        data_capabilities=profile.required_capabilities(),
    )
    assert state.balance == Decimal("25000")
    assert state.equity == Decimal("25000")
    assert state.high_watermark == Decimal("25000")
    assert state.maximum_loss_threshold == Decimal("23500")

    # Apply a winning closed trade
    event = AccountRuleInput(
        timestamp=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
        event_type="closed_trade",
        balance=Decimal("25500"),
        equity=Decimal("25500"),
        realized_pnl=Decimal("500"),
        trade_id="t1",
        mini_contracts=0,
        micro_contracts=2,
    )
    report = state.apply(event)
    assert report.primary_event.kind == "in_progress"
    assert state.balance == Decimal("25500")
    assert state.high_watermark == Decimal("25500")
    assert state.maximum_loss_threshold == Decimal("24000")  # 25500 - 1500

"""Tests for src.session_calendar and canonical session date resolution across consumers."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.account_sim import get_session_date
from src.backtest.executor import (
    BacktestConfig,
    ExecutedTrade,
    executed_to_core_trades,
    run_backtest,
)
from src.backtest.history import Bar
from src.backtest.strategy import Signal, Strategy
from src.funded_rules_v2 import _session_date
from src.session_calendar import session_date

NY_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")


# =============================================================================
# 1. Canonical session_date tests (4 core cases + DST + edge cases)
# =============================================================================

def test_session_calendar_case1_friday_1700_ny_rolls_to_monday():
    """Friday at or after 17:00 NY rolls forward to Monday."""
    # 2026-09-04 is a Friday. 17:00 EDT -> Monday 2026-09-07
    dt_before = datetime(2026, 9, 4, 16, 59, 59, tzinfo=NY_TZ)
    dt_at = datetime(2026, 9, 4, 17, 0, 0, tzinfo=NY_TZ)
    dt_after = datetime(2026, 9, 4, 18, 30, 0, tzinfo=NY_TZ)

    assert session_date(dt_before) == date(2026, 9, 4)
    assert session_date(dt_at) == date(2026, 9, 7)
    assert session_date(dt_after) == date(2026, 9, 7)


def test_session_calendar_case2_weekend_rolls_to_monday():
    """Saturday and Sunday timestamps roll forward to Monday."""
    # Saturday 2026-09-05
    dt_sat_noon = datetime(2026, 9, 5, 12, 0, 0, tzinfo=NY_TZ)
    # Sunday 2026-09-06 before and after 17:00
    dt_sun_morning = datetime(2026, 9, 6, 10, 0, 0, tzinfo=NY_TZ)
    dt_sun_evening = datetime(2026, 9, 6, 18, 0, 0, tzinfo=NY_TZ)

    assert session_date(dt_sat_noon) == date(2026, 9, 7)
    assert session_date(dt_sun_morning) == date(2026, 9, 7)
    assert session_date(dt_sun_evening) == date(2026, 9, 7)


def test_session_calendar_case3_2300_utc_rolls_to_next_session():
    """23:00 UTC is 19:00 EDT (summer) or 18:00 EST (winter), both > 17:00 ET."""
    # Summer: Wednesday 2026-07-15 23:00 UTC = Wednesday 19:00 EDT -> Thursday 2026-07-16
    dt_wed_summer = datetime(2026, 7, 15, 23, 0, 0, tzinfo=UTC_TZ)
    assert session_date(dt_wed_summer) == date(2026, 7, 16)

    # Winter: Wednesday 2026-01-14 23:00 UTC = Wednesday 18:00 EST -> Thursday 2026-01-15
    dt_wed_winter = datetime(2026, 1, 14, 23, 0, 0, tzinfo=UTC_TZ)
    assert session_date(dt_wed_winter) == date(2026, 1, 15)

    # Friday 23:00 UTC = Friday 19:00 EDT -> Monday!
    dt_fri_summer = datetime(2026, 7, 17, 23, 0, 0, tzinfo=UTC_TZ)
    assert session_date(dt_fri_summer) == date(2026, 7, 20)


def test_session_calendar_case4_dst_transitions():
    """DST transitions preserve 17:00 local NY rollover threshold."""
    # Spring forward: 2026-03-08 (EST -> EDT)
    # Friday before spring forward: 2026-03-06 (EST, UTC-5)
    dt_fri_pre_dst_1659 = datetime(2026, 3, 6, 16, 59, tzinfo=NY_TZ)
    dt_fri_pre_dst_1700 = datetime(2026, 3, 6, 17, 0, tzinfo=NY_TZ)
    assert session_date(dt_fri_pre_dst_1659) == date(2026, 3, 6)
    assert session_date(dt_fri_pre_dst_1700) == date(2026, 3, 9)

    # Fall back: 2026-11-01 (EDT -> EST)
    # Friday before fall back: 2026-10-30 (EDT, UTC-4)
    dt_fri_fall_1659 = datetime(2026, 10, 30, 16, 59, tzinfo=NY_TZ)
    dt_fri_fall_1700 = datetime(2026, 10, 30, 17, 0, tzinfo=NY_TZ)
    assert session_date(dt_fri_fall_1659) == date(2026, 10, 30)
    assert session_date(dt_fri_fall_1700) == date(2026, 11, 2)


def test_session_calendar_edge_cases():
    """Test None, empty string, string ISO parsing, and naive datetimes."""
    assert session_date(None) is None
    assert session_date("") is None

    # ISO strings
    assert session_date("2026-09-04T16:00:00-04:00") == date(2026, 9, 4)
    assert session_date("2026-09-04T17:00:00-04:00") == date(2026, 9, 7)
    assert session_date("2026-09-04T21:00:00Z") == date(2026, 9, 7)

    # Naive datetime assumes UTC
    naive_utc = datetime(2026, 9, 4, 21, 0, 0)  # 21:00 UTC = 17:00 EDT
    assert session_date(naive_utc) == date(2026, 9, 7)

    # Unknown timezone raises ValueError
    with pytest.raises(ValueError, match="Unknown timezone"):
        session_date("2026-09-04T12:00:00", tz="Invalid/Zone")


# =============================================================================
# 2. Site 1 delegation tests: src.account_sim.get_session_date
# =============================================================================

def test_site1_account_sim_delegation():
    """get_session_date in account_sim delegates faithfully to session_date."""
    # Friday 17:00 NY -> Monday
    res_fri = get_session_date("2026-09-04T17:00:00-04:00")
    assert res_fri == "2026-09-07"

    # Weekend -> Monday
    res_sat = get_session_date("2026-09-05T12:00:00-04:00")
    assert res_sat == "2026-09-07"

    # 23:00 UTC Wed -> Thursday
    res_utc = get_session_date("2026-07-15T23:00:00Z")
    assert res_utc == "2026-07-16"

    # None input
    assert get_session_date(None) is None


# =============================================================================
# 3. Site 2 delegation tests: src.funded_rules_v2._session_date
# =============================================================================

def test_site2_funded_rules_v2_delegation():
    """_session_date in funded_rules_v2 delegates faithfully to session_date."""
    # When skip_weekends=False (default for generic prop firm rules in funded_rules_v2)
    dt_fri = datetime(2026, 9, 4, 17, 0, 0, tzinfo=NY_TZ)
    assert _session_date(dt_fri, "America/New_York", time(17)) == date(2026, 9, 5)

    # When skip_weekends=True
    assert _session_date(dt_fri, "America/New_York", time(17), skip_weekends=True) == date(2026, 9, 7)

    # Weekend with skip_weekends=True -> Monday
    dt_sat = datetime(2026, 9, 5, 12, 0, 0, tzinfo=NY_TZ)
    assert _session_date(dt_sat, "America/New_York", time(17), skip_weekends=True) == date(2026, 9, 7)

    # 23:00 UTC -> Next day
    dt_utc = datetime(2026, 7, 15, 23, 0, 0, tzinfo=UTC_TZ)
    assert _session_date(dt_utc, "America/New_York", time(17)) == date(2026, 7, 16)


# =============================================================================
# 4. Site 3 delegation tests: executor.py executed_to_core_trades
# =============================================================================

def test_site3_executor_session_date_for_ledger_flag():
    """executed_to_core_trades uses UTC date when session_date_for_ledger is False,
    and CME session date when True."""
    # Trade exit at Friday 23:00 UTC (19:00 EDT)
    exit_time = datetime(2026, 9, 4, 23, 0, 0, tzinfo=UTC_TZ)
    trade = ExecutedTrade(
        trade_id="bt-1",
        direction="long",
        entry_time=exit_time - timedelta(hours=1),
        exit_time=exit_time,
        entry_price=100.0,
        exit_price=110.0,
        stop_price=90.0,
        target_price=120.0,
        quantity=1,
        gross_pnl=20.0,
        commission=4.0,
        net_pnl=16.0,
        r_result=0.032,
        exit_reason="take_profit",
        stop_risk_dollars=20.0,
        slippage_cost=0.0,
    )

    # Default (session_date_for_ledger=False): exact UTC date
    config_default = BacktestConfig(session_date_for_ledger=False)
    core_default = executed_to_core_trades((trade,), config=config_default)
    assert core_default[0].date == "2026-09-04"  # Friday UTC

    # Opt-in (session_date_for_ledger=True): Monday session date
    config_session = BacktestConfig(session_date_for_ledger=True)
    core_session = executed_to_core_trades((trade,), config=config_session)
    assert core_session[0].date == "2026-09-07"  # Monday CME session


# =============================================================================
# 5. end_of_data_slippage_points tests (0.0 bit-for-bit vs 0.25 1-tick)
# =============================================================================

class MockEodStrategy(Strategy):
    """Strategy that buys on first bar and holds until end of data."""
    def evaluate(self, history) -> Signal | None:
        if len(history) == 1:
            return Signal(
                direction="long",
                entry=history[-1].close,
                stop=history[-1].close - 10.0,
                target=history[-1].close + 50.0,
                stop_target_as_points=False,
            )
        return None


def test_end_of_data_slippage_cost_legacy_and_enhanced():
    """Verify end_of_data slippage applies adverse price adjustment and cost."""
    dt_base = datetime(2026, 9, 8, 9, 30, tzinfo=NY_TZ)
    bars = [
        Bar(dt_base, 100.0, 102.0, 99.0, 101.0, 100.0),
        Bar(dt_base + timedelta(minutes=5), 101.0, 103.0, 100.0, 102.0, 100.0),
        Bar(dt_base + timedelta(minutes=10), 102.0, 104.0, 101.0, 103.0, 100.0),
    ]

    # 1. Zero slippage (default)
    cfg_zero = BacktestConfig(
        end_of_data_policy="close",
        end_of_data_slippage_points=0.0,
        slippage_points=0.0,
        fixed_quantity=1,
        dollar_per_point=2.0,
        commission_per_side=2.0,
        tick_size=0.25,
    )
    res_zero = run_backtest(bars, MockEodStrategy(), cfg_zero)
    assert res_zero.n_trades == 1
    t_zero = res_zero.trades[0]
    assert t_zero.exit_reason == "end_of_data"
    assert t_zero.exit_price == 103.0
    assert t_zero.slippage_cost == 0.0
    # Entry at bar 2 open = 101.0. Exit at bar 3 close = 103.0. Move = +2.0 pts.
    # Gross PnL = 2.0 * $2.0 = $4.00. Commission = 2 * $2.0 = $4.00. Net PnL = $0.00
    assert t_zero.gross_pnl == 4.0
    assert t_zero.net_pnl == 0.0

    # 2. 1-tick slippage (0.25 pts)
    cfg_slip = BacktestConfig(
        end_of_data_policy="close",
        end_of_data_slippage_points=0.25,
        slippage_points=0.0,
        fixed_quantity=1,
        dollar_per_point=2.0,
        commission_per_side=2.0,
        tick_size=0.25,
    )
    res_slip = run_backtest(bars, MockEodStrategy(), cfg_slip)
    assert res_slip.n_trades == 1
    t_slip = res_slip.trades[0]
    assert t_slip.exit_reason == "end_of_data"
    # Long exit adverse slippage: 103.0 - 0.25 = 102.75
    assert t_slip.exit_price == 102.75
    assert t_slip.slippage_cost == 0.25 * 2.0 * 1  # $0.50
    # Gross PnL = (102.75 - 101.0) * $2.0 = 1.75 * $2.0 = $3.50. Net = $3.50 - $4.00 = -$0.50
    assert t_slip.gross_pnl == 3.5
    assert t_slip.net_pnl == -0.5

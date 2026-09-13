"""Unit tests for OrbStrategy and orb_config."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.backtest.executor import run_backtest
from src.backtest.history import Bar
from src.backtest.orb import OrbConfig, OrbStrategy, orb_config

UTC = ZoneInfo("UTC")
ET = ZoneInfo("America/New_York")


def _bar_et(dt_et: datetime, o: float, h: float, l: float, c: float, v: float = 100.0) -> Bar:
    return Bar(timestamp=dt_et.astimezone(UTC), open=o, high=h, low=l, close=c, volume=v)


def test_orb_parameters():
    strat = OrbStrategy()
    params = strat.parameters()
    assert params["strategy"] == "OrbStrategy"
    assert params["or_minutes"] == 30
    assert params["rr"] == 2.0
    assert params["stop_mode"] == "opposite"
    assert params["max_hold_bars"] == 192
    assert params["time_exit_mode"] == "flat"


def test_orb_fresh():
    strat = OrbStrategy()
    fresh = strat.fresh()
    assert fresh is not strat
    assert fresh.config == strat.config


def test_orb_long_breakout_opposite_stop_2r_target():
    """Verify long breakout at 10:05 with stop at orl and target at 2R."""
    strat = OrbStrategy()
    day = datetime(2024, 1, 16, tzinfo=ET)

    bars: list[Bar] = []
    # Opening range 09:30 to 10:00 (6 bars of 5 mins)
    # Range: Low = 1000.0, High = 1020.0, Mid = 1010.0
    for i in range(6):
        ts = day.replace(hour=9, minute=30 + i * 5)
        bars.append(_bar_et(ts, 1010.0, 1020.0 if i == 2 else 1015.0, 1000.0 if i == 4 else 1005.0, 1010.0))

    # At 10:00 bar: still inside range
    bars.append(_bar_et(day.replace(hour=10, minute=0), 1010.0, 1018.0, 1008.0, 1015.0))
    sig = strat.evaluate(bars)
    assert sig is None

    # At 10:05 bar: breakout long! Close = 1025.0 > orh (1020.0)
    bars.append(_bar_et(day.replace(hour=10, minute=5), 1015.0, 1026.0, 1014.0, 1025.0))
    sig = strat.evaluate(bars)
    assert sig is not None
    assert sig.direction == "long"
    assert sig.entry == 1025.0
    assert sig.stop == 1000.0  # opposite stop is orl
    # risk = 1025 - 1000 = 25.0 pts
    # target = 1025 + 2.0 * 25.0 = 1075.0
    assert sig.target == 1075.0


def test_orb_short_breakout_opposite_stop_2r_target():
    """Verify short breakout at 10:05 with stop at orh and target at 2R."""
    strat = OrbStrategy()
    day = datetime(2024, 1, 16, tzinfo=ET)

    bars: list[Bar] = []
    # Opening range 09:30 to 10:00
    for i in range(6):
        ts = day.replace(hour=9, minute=30 + i * 5)
        bars.append(_bar_et(ts, 1010.0, 1020.0 if i == 1 else 1015.0, 1000.0 if i == 3 else 1005.0, 1010.0))

    # At 10:00 bar
    bars.append(_bar_et(day.replace(hour=10, minute=0), 1010.0, 1012.0, 1004.0, 1005.0))
    assert strat.evaluate(bars) is None

    # At 10:05 bar: breakout short! Close = 995.0 < orl (1000.0)
    bars.append(_bar_et(day.replace(hour=10, minute=5), 1005.0, 1006.0, 994.0, 995.0))
    sig = strat.evaluate(bars)
    assert sig is not None
    assert sig.direction == "short"
    assert sig.entry == 995.0
    assert sig.stop == 1020.0  # opposite stop is orh
    # risk = 1020 - 995 = 25.0 pts
    # target = 995 - 2.0 * 25.0 = 945.0
    assert sig.target == 945.0


def test_orb_rearm_midpoint_logic():
    """Verify that a second long breakout only fires if price crossed back below orm."""
    strat = OrbStrategy()
    day = datetime(2024, 1, 16, tzinfo=ET)

    bars: list[Bar] = []
    # Opening range: orh = 1020, orl = 1000, orm = 1010
    for i in range(6):
        ts = day.replace(hour=9, minute=30 + i * 5)
        bars.append(_bar_et(ts, 1010.0, 1020.0 if i == 0 else 1015.0, 1000.0 if i == 0 else 1005.0, 1010.0))

    # First breakout at 10:05 (Close = 1025)
    bars.append(_bar_et(day.replace(hour=10, minute=5), 1015.0, 1026.0, 1014.0, 1025.0))
    sig1 = strat.evaluate(bars)
    assert sig1 is not None and sig1.direction == "long"

    # Next bar at 10:10: still above 1020 (Close = 1028), but done_up is True -> no new signal
    bars.append(_bar_et(day.replace(hour=10, minute=10), 1025.0, 1030.0, 1024.0, 1028.0))
    sig2 = strat.evaluate(bars)
    assert sig2 is None

    # Price drops through midpoint: Close = 1008 < orm (1010) -> re-arms done_up!
    bars.append(_bar_et(day.replace(hour=10, minute=15), 1020.0, 1022.0, 1007.0, 1008.0))
    sig3 = strat.evaluate(bars)
    assert sig3 is None

    # Second breakout at 10:20: Close = 1022 > 1020 -> fires again!
    bars.append(_bar_et(day.replace(hour=10, minute=20), 1010.0, 1024.0, 1010.0, 1022.0))
    sig4 = strat.evaluate(bars)
    assert sig4 is not None
    assert sig4.direction == "long"


def test_orb_cutoff_at_1600_et():
    """Verify that no signals fire at or after 16:00 ET."""
    strat = OrbStrategy()
    day = datetime(2024, 1, 16, tzinfo=ET)

    bars: list[Bar] = []
    # Opening range
    for i in range(6):
        ts = day.replace(hour=9, minute=30 + i * 5)
        bars.append(_bar_et(ts, 1010.0, 1020.0, 1000.0, 1010.0))

    # Normal day until 16:00
    ts_cutoff = day.replace(hour=16, minute=0)
    bars.append(_bar_et(ts_cutoff, 1015.0, 1030.0, 1015.0, 1025.0))
    sig = strat.evaluate(bars)
    assert sig is None, "Must not fire at or after 16:00 ET"


def test_orb_backtest_execution_with_flat_time_exit():
    """Verify run_backtest with orb_config and flat time exit at 192 bars."""
    strat = OrbStrategy()
    cfg = orb_config(max_bars_held=5, time_exit_mode="flat", commission_per_side=2.0)

    day = datetime(2024, 1, 16, tzinfo=ET)
    bars: list[Bar] = []
    # Opening range
    for i in range(6):
        ts = day.replace(hour=9, minute=30 + i * 5)
        bars.append(_bar_et(ts, 1010.0, 1020.0, 1000.0, 1010.0))

    # Breakout at 10:05 -> entry filled at 10:10 open 1025.0
    bars.append(_bar_et(day.replace(hour=10, minute=5), 1015.0, 1026.0, 1014.0, 1025.0))

    # Trade stays within 1015-1035 for 5 bars (never hits stop 1000 nor target 1075)
    for i in range(1, 7):
        ts = day.replace(hour=10, minute=5 + i * 5)
        bars.append(_bar_et(ts, 1025.0, 1030.0, 1020.0, 1028.0))

    res = run_backtest(bars, strat, cfg)
    assert res.n_trades == 1
    t = res.trades[0]
    assert t.direction == "long"
    assert t.exit_reason == "time_exit"
    assert t.exit_price == 1025.0  # flat exit at entry price!
    assert t.gross_pnl == 0.0
    assert t.commission > 0.0
    assert t.net_pnl == -t.commission  # flat exit pays only commission

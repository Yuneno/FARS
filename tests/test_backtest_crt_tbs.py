"""Unit tests for CrtTbsStrategy and crt_tbs_config."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.backtest.crt_tbs import CrtTbsConfig, CrtTbsStrategy, crt_tbs_config
from src.backtest.executor import BacktestConfig, run_backtest
from src.backtest.history import Bar
from src.backtest.markets import MNQ
from src.backtest.strategy import Signal

UTC = ZoneInfo("UTC")
ET = ZoneInfo("America/New_York")


def _bar(ts: datetime, o: float, h: float, l: float, c: float, v: float = 100.0) -> Bar:
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def test_crt_tbs_parameters():
    cfg = CrtTbsConfig(target_mode="fixed_rr", fixed_rr=2.0)
    strat = CrtTbsStrategy(cfg)
    params = strat.parameters()
    assert params["strategy"] == "CrtTbsStrategy"
    assert params["target_mode"] == "fixed_rr"
    assert params["fixed_rr"] == 2.0


def test_crt_tbs_fresh_creates_independent_instance():
    strat = CrtTbsStrategy()
    fresh = strat.fresh()
    assert fresh is not strat
    assert fresh.config == strat.config


def test_default_config_mathematical_paradox_zero_trades():
    """Verify that default target_mode='crt' with min_rr=1.5 produces 0 trades because reward/risk <= 1.0."""
    strat = CrtTbsStrategy(CrtTbsConfig(target_mode="crt", min_rr=1.50, require_4h_bias=False))

    base = datetime(2024, 1, 15, 0, 0, tzinfo=UTC)
    bars: list[Bar] = []
    for h in range(70):
        for m in range(12):
            ts = base + timedelta(hours=h, minutes=m * 5)
            bars.append(_bar(ts, 100.0, 102.0, 98.0, 100.0))

    # Event H1 bar at hour 70: sweeps high to 130.0, closes at 92.0 (bearish CRT)
    for m in range(12):
        ts = base + timedelta(hours=70, minutes=m * 5)
        bars.append(_bar(ts, 125.0, 130.0, 90.0, 92.0))

    # M5 confirmation attempt inside session (14:30 to 21:00 UTC = 09:30 to 16:00 ET)
    for i in range(10):
        ts = base + timedelta(hours=71, minutes=i * 5)
        bars.append(_bar(ts, 100.0, 101.0, 99.0, 100.0))

    # Sweep M5 high and close below
    ts_trigger = base + timedelta(hours=71, minutes=10 * 5)
    bars.append(_bar(ts_trigger, 105.0, 115.0, 98.0, 99.0))

    sig = strat.evaluate(bars)
    # Must be None because reward / risk cannot satisfy min_rr=1.5 under target_mode='crt'
    assert sig is None


def test_crt_tbs_emits_valid_short_signal_with_fixed_rr():
    """Verify that with fixed_rr=2.0 and require_4h_bias=False, a valid short signal is generated."""
    strat = CrtTbsStrategy(CrtTbsConfig(target_mode="fixed_rr", fixed_rr=2.0, require_4h_bias=False, require_half_zone=False))

    base = datetime(2024, 1, 15, 0, 0, tzinfo=UTC)
    bars: list[Bar] = []
    for h in range(65):
        for m in range(12):
            ts = base + timedelta(hours=h, minutes=m * 5)
            bars.append(_bar(ts, 1000.0, 1005.0, 995.0, 1000.0))

    # H1 bar 65: big bearish CRT candle (from 17:00 to 18:00 UTC = 12:00 to 13:00 ET)
    for m in range(12):
        ts = base + timedelta(hours=65, minutes=m * 5)
        bars.append(_bar(ts, 1020.0, 1030.0, 980.0, 990.0))

    # Subsequent M5 bars during RTH (13:05, 13:10... ET)
    for i in range(8):
        ts = base + timedelta(hours=66, minutes=i * 5)
        bars.append(_bar(ts, 990.0, 994.0, 986.0, 990.0))

    # Confirming M5 bar at 18:40 UTC (13:40 ET): sweeps 994 to 998 and closes at 985
    confirm_ts = base + timedelta(hours=66, minutes=8 * 5)
    bars.append(_bar(confirm_ts, 993.0, 998.0, 984.0, 985.0))

    sig = strat.evaluate(bars)
    assert sig is not None
    assert sig.direction == "short"
    assert sig.entry == 985.0
    assert sig.stop == 1030.0  # max(row.high, crh=1030)
    # risk = 1030 - 985 = 45.0 pts
    # target = 985 - 2.0 * 45.0 = 895.0
    assert sig.target == 895.0


def test_crt_tbs_emits_valid_long_signal_with_fixed_rr():
    """Verify that with fixed_rr=2.0 and require_4h_bias=False, a valid long signal is generated."""
    strat = CrtTbsStrategy(CrtTbsConfig(target_mode="fixed_rr", fixed_rr=2.0, require_4h_bias=False, require_half_zone=False))

    base = datetime(2024, 1, 15, 0, 0, tzinfo=UTC)
    bars: list[Bar] = []
    for h in range(65):
        for m in range(12):
            ts = base + timedelta(hours=h, minutes=m * 5)
            bars.append(_bar(ts, 1000.0, 1005.0, 995.0, 1000.0))

    # H1 bar 65: big bullish CRT candle (sweeps 995 down to 970, closes above 1005 at 1010)
    # Total range = 1015 - 970 = 45. Body = |1010 - 980| = 30 >= 45 * 0.50
    for m in range(12):
        ts = base + timedelta(hours=65, minutes=m * 5)
        bars.append(_bar(ts, 980.0, 1015.0, 970.0, 1010.0))

    # Subsequent M5 bars during RTH (13:05, 13:10... ET)
    for i in range(8):
        ts = base + timedelta(hours=66, minutes=i * 5)
        bars.append(_bar(ts, 1010.0, 1014.0, 1006.0, 1010.0))

    # Confirming M5 bar at 18:40 UTC (13:40 ET): sweeps 1006 down to 1002 and closes at 1015
    confirm_ts = base + timedelta(hours=66, minutes=8 * 5)
    bars.append(_bar(confirm_ts, 1008.0, 1016.0, 1002.0, 1015.0))

    sig = strat.evaluate(bars)
    assert sig is not None
    assert sig.direction == "long"
    assert sig.entry == 1015.0
    assert sig.stop == 970.0  # min(row.low, crl=970)
    # risk = 1015 - 970 = 45.0 pts
    # target = 1015 + 2.0 * 45.0 = 1105.0
    assert sig.target == 1105.0


def test_crt_tbs_strict_causality_no_premature_signals():
    """Verify that CRT setup cannot fire during the formation of the H1 bar itself."""
    strat = CrtTbsStrategy(CrtTbsConfig(target_mode="fixed_rr", fixed_rr=2.0, require_4h_bias=False))

    base = datetime(2024, 1, 15, 0, 0, tzinfo=UTC)
    bars: list[Bar] = []
    for h in range(65):
        for m in range(12):
            ts = base + timedelta(hours=h, minutes=m * 5)
            bars.append(_bar(ts, 1000.0, 1005.0, 995.0, 1000.0))

    # Feed only 6 bars of the 65th H1 bar (halfway through the hour)
    for m in range(6):
        ts = base + timedelta(hours=65, minutes=m * 5)
        bars.append(_bar(ts, 1020.0, 1030.0, 980.0, 990.0))
        sig = strat.evaluate(bars)
        assert sig is None, "Signal must not fire before H1 bar is closed"


def test_crt_tbs_backtest_execution():
    """Verify run_backtest integration with CrtTbsStrategy and crt_tbs_config."""
    strat = CrtTbsStrategy(CrtTbsConfig(target_mode="fixed_rr", fixed_rr=2.0, require_4h_bias=False, require_half_zone=False))
    cfg = crt_tbs_config(commission_per_side=2.0, time_exit_mode="flat")

    base = datetime(2024, 1, 15, 0, 0, tzinfo=UTC)
    bars: list[Bar] = []
    for h in range(65):
        for m in range(12):
            ts = base + timedelta(hours=h, minutes=m * 5)
            bars.append(_bar(ts, 1000.0, 1005.0, 995.0, 1000.0))

    # H1 CRT (17:00 to 18:00 UTC = 12:00 to 13:00 ET)
    for m in range(12):
        ts = base + timedelta(hours=65, minutes=m * 5)
        bars.append(_bar(ts, 1020.0, 1030.0, 980.0, 990.0))

    # Subsequent M5 bars
    for i in range(8):
        ts = base + timedelta(hours=66, minutes=i * 5)
        bars.append(_bar(ts, 990.0, 994.0, 986.0, 990.0))

    # Trigger bar at 18:40 UTC
    confirm_ts = base + timedelta(hours=66, minutes=8 * 5)
    bars.append(_bar(confirm_ts, 993.0, 998.0, 984.0, 985.0))

    # Entry bar fills at open 985.0, drops to 890.0 (hits target 895.0)
    bars.append(_bar(confirm_ts + timedelta(minutes=5), 985.0, 986.0, 890.0, 892.0))

    res = run_backtest(bars, strat, cfg)
    assert res.n_trades == 1
    assert res.trades[0].direction == "short"
    assert res.trades[0].exit_reason == "take_profit"
    assert res.trades[0].net_pnl > 0

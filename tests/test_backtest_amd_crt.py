"""Tests for the causal AMD+CRT strategy, the MNQ CSV loader, and the executor
extensions (distance SL/TP, gap rejection, timestamp time-exit, unresolved
positions, coherent slippage, level validation, and the dollar-accurate Core
mapping)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import pytest

from src.backtest.amd_crt import (
    ET,
    AmdCrtStrategy,
    _atr14,
    _bootstrap_ci_lower,
    _detect_amd,
    _detect_crt,
    _ema_bucket_start,
    _ema_regime_direction,
    _is_complete_rth_session,
    _median,
    _session_date,
    _weekday_median_amplitudes,
    amd_crt_config,
)
from src.backtest.executor import (
    BacktestConfig,
    executed_to_core_trades,
    run_backtest,
)
from src.backtest.history import Bar
from src.backtest.mnq_csv import load_mnq_csv
from src.backtest.pipeline import chronological_split, result_summary, run_pipeline
from src.backtest.strategy import BreakoutStrategy, Signal


def _bar(ts: datetime, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=10.0)


def _et(y: int, mo: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, mo, d, hh, mm, tzinfo=ET)


def _trading_days(n: int, end: date = date(2026, 9, 7)) -> list[date]:
    """The n most recent weekdays ending at (and including) ``end``."""
    days: list[date] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d = d - timedelta(days=1)
    return list(reversed(days))


def _complete_rth_session(d: date, *, high: float, low: float) -> list[Bar]:
    """A complete 78-bar M5 RTH session from 09:30 through 15:55 ET."""
    midpoint = (high + low) / 2.0
    start = _et(d.year, d.month, d.day, 9, 30)
    return [
        _bar(start + timedelta(minutes=5 * index), midpoint, high, low, midpoint)
        for index in range(78)
    ]


class _FixedSignal:
    """Fires exactly one signal with DISTANCE stop/target."""

    def __init__(self, direction: Literal["long", "short"], stop_pts: float, target_pts: float):
        self.direction: Literal["long", "short"] = direction
        self.stop = stop_pts
        self.target = target_pts
        self.fired = False

    def evaluate(self, history):
        if self.fired or not history:
            return None
        self.fired = True
        return Signal(self.direction, 0.0, self.stop, self.target, stop_target_as_points=True)


# ---------------------------------------------------------------------------
# MNQ CSV loader
# ---------------------------------------------------------------------------

def test_load_mnq_csv_aggregates_m1_to_m5(tmp_path: Path):
    rows = ["time,open,high,low,close"]
    base = datetime(2026, 5, 4, 16, 0, tzinfo=ZoneInfo("UTC"))
    for k in range(10):
        ts = base + timedelta(minutes=k)
        rows.append(f"{ts.isoformat()},{100 + k},{101 + k},{99 + k},{100.5 + k}")
    p = tmp_path / "test_m1.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    bars = load_mnq_csv(p, target_interval_minutes=5)
    assert len(bars) == 2
    assert bars[0].open == 100.0 and bars[0].high == 105.0
    assert bars[0].low == 99.0 and bars[0].close == 104.5


def test_amd_crt_cli_aggregates_bars_csv_m1_to_m5(tmp_path: Path):
    rows = ["timestamp,open,high,low,close,volume"]
    base = datetime(2026, 5, 4, 16, 0, tzinfo=ZoneInfo("UTC"))
    for day in range(2):
        for k in range(5):
            ts = base + timedelta(days=day, minutes=k)
            rows.append(f"{ts.isoformat()},100,101,99,100,1")
    source = tmp_path / "bars_m1.csv"
    source.write_text("\n".join(rows) + "\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.backtest.cli",
            "amd-crt",
            "--bars-csv",
            str(source),
            "--out-dir",
            str(tmp_path / "out"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["n_bars_in_sample"] + report["n_bars_out_of_sample"] == 2


def test_load_mnq_csv_drops_incomplete_bucket(tmp_path: Path):
    rows = ["time,open,high,low,close"]
    base = datetime(2026, 5, 4, 16, 0, tzinfo=ZoneInfo("UTC"))
    for k in range(4):  # 4 M1 bars -> incomplete 5-min bucket
        rows.append(f"{(base + timedelta(minutes=k)).isoformat()},{100},{101},{99},{100}")
    p = tmp_path / "m1_partial.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert load_mnq_csv(p, target_interval_minutes=5) == []


def test_load_mnq_csv_accepts_timestamp_column(tmp_path: Path):
    rows = ["timestamp,open,high,low,close,volume"]
    base = datetime(2026, 5, 4, 16, 0, tzinfo=ZoneInfo("UTC"))
    for k in range(3):
        ts = base + timedelta(minutes=5 * k)
        rows.append(f"{ts.isoformat()},{100},{101},{99},{100},{10}")
    p = tmp_path / "m5_ts.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    bars = load_mnq_csv(p, target_interval_minutes=5)
    assert len(bars) == 3
    assert bars[0].volume == 10.0


def test_load_mnq_csv_rejects_conflicting_time_and_timestamp(tmp_path: Path):
    p = tmp_path / "conflict.csv"
    p.write_text("time,timestamp,open,high,low,close\n2026-05-04T16:00:00+00:00,2026-05-04T16:00:00+00:00,100,101,99,100\n", encoding="utf-8")
    with pytest.raises(ValueError, match="both 'time' and 'timestamp'"):
        load_mnq_csv(p)


def test_load_mnq_csv_rejects_duplicate_timestamps(tmp_path: Path):
    p = tmp_path / "dup.csv"
    p.write_text(
        "time,open,high,low,close\n"
        "2026-05-04T16:00:00+00:00,100,101,99,100\n"
        "2026-05-04T16:00:00+00:00,100,101,99,100\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="strictly increasing"):
        load_mnq_csv(p, source_interval_minutes=5)


def test_load_mnq_csv_rejects_out_of_order(tmp_path: Path):
    p = tmp_path / "oob.csv"
    p.write_text(
        "time,open,high,low,close\n"
        "2026-05-04T16:05:00+00:00,100,101,99,100\n"
        "2026-05-04T16:00:00+00:00,100,101,99,100\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="strictly increasing"):
        load_mnq_csv(p, source_interval_minutes=5)


def test_load_mnq_csv_bucket_requires_exact_timestamps(tmp_path: Path):
    # 5 M1 bars but with a missing intermediate timestamp -> incomplete bucket
    rows = ["time,open,high,low,close"]
    base = datetime(2026, 5, 4, 16, 0, tzinfo=ZoneInfo("UTC"))
    for k in [0, 1, 2, 4]:  # missing k=3
        rows.append(f"{(base + timedelta(minutes=k)).isoformat()},{100},{101},{99},{100}")
    p = tmp_path / "gap_bucket.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert load_mnq_csv(p, target_interval_minutes=5) == []


def test_load_mnq_csv_rejects_naive_timestamps(tmp_path: Path):
    p = tmp_path / "naive.csv"
    p.write_text(
        "time,open,high,low,close\n"
        "2026-05-04T16:00:00,100,101,99,100\n"
        "2026-05-04T16:05:00,100,101,99,100\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        load_mnq_csv(p)


def test_load_mnq_csv_rejects_single_interval_anomaly(tmp_path: Path):
    rows = ["time,open,high,low,close"]
    base = datetime(2026, 5, 4, 16, 0, tzinfo=ZoneInfo("UTC"))
    offsets = [0, 5, 10, 15, 16, 20, 25, 30]
    for minute in offsets:
        rows.append(
            f"{(base + timedelta(minutes=minute)).isoformat()},100,101,99,100"
        )
    p = tmp_path / "mostly_m5.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="source interval"):
        load_mnq_csv(p)


# ---------------------------------------------------------------------------
# Median / calibration
# ---------------------------------------------------------------------------

def test_median_even_is_mean_of_middle_two():
    assert _median([1.0, 2.0, 3.0, 4.0]) == 2.5
    assert _median([1.0, 2.0, 3.0]) == 2.0


def test_weekday_median_requires_min_samples():
    day = date(2026, 9, 7)
    bars = [
        _bar(_et(2026, 8, 31, 10, 0), 100, 110, 100, 105),
        _bar(_et(2026, 8, 24, 10, 0), 100, 120, 100, 110),
    ]
    medians = _weekday_median_amplitudes(bars, day, min_samples=3)
    assert day.weekday() not in medians


def test_weekday_median_lookback_counts_complete_sessions_not_calendar_days():
    day = date(2026, 9, 7)
    bars = []
    for k in range(1, 4):
        d = day - timedelta(weeks=k)
        bars.extend(_complete_rth_session(d, high=200.0, low=100.0))
    incomplete = day - timedelta(days=1)
    bars.append(
        _bar(
            _et(incomplete.year, incomplete.month, incomplete.day, 10, 0),
            100,
            1000,
            0,
            100,
        )
    )

    medians = _weekday_median_amplitudes(
        bars, day, lookback_days=3, min_samples=3
    )
    assert medians[day.weekday()] == 100.0


def test_weekday_median_accepts_abbreviated_rth_session():
    day = date(2026, 9, 7)
    sessions = _trading_days(260, end=day - timedelta(days=1))
    bars = []
    for d in sessions[:259]:
        bars.extend(_complete_rth_session(d, high=200.0, low=100.0))
    last = sessions[-1]
    last_session = _complete_rth_session(last, high=200.0, low=100.0)
    bars.extend(last_session[:42])  # representative 09:30-13:00 half-day
    assert day.weekday() in _weekday_median_amplitudes(
        bars, day, lookback_days=260
    )


def test_weekday_median_uses_recent_bounded_sessions_after_minimum():
    day = date(2026, 9, 7)
    bars = []
    sessions = _trading_days(600, end=day - timedelta(days=1))
    for index, d in enumerate(sessions):
        amplitude = 10.0 if index < 300 else 100.0
        bars.extend(_complete_rth_session(d, high=100.0 + amplitude, low=100.0))

    medians = _weekday_median_amplitudes(
        bars, day, lookback_days=260, min_samples=1
    )

    assert medians[day.weekday()] == 100.0


def test_two_bar_outage_day_is_not_a_complete_d1_session():
    day = date(2026, 9, 7)
    complete = day - timedelta(days=3)
    outage = day - timedelta(days=1)
    bars = _complete_rth_session(complete, high=200.0, low=100.0)
    bars.extend(
        [
            _bar(_et(outage.year, outage.month, outage.day, 10, 0), 500, 1000, 0, 500),
            _bar(_et(outage.year, outage.month, outage.day, 15, 0), 500, 1000, 0, 500),
        ]
    )

    assert _weekday_median_amplitudes(
        bars, day, lookback_days=2, min_samples=1
    ) == {}


def test_weekday_median_contract_block_is_configurable():
    # block=65 -> window of 260 sessions (52/weekday); block=60 -> 300 (60/weekday).
    day = date(2026, 9, 7)
    sessions = _trading_days(300, end=day - timedelta(days=1))
    bars = []
    for d in sessions:
        bars.extend(_complete_rth_session(d, high=200.0, low=100.0))
    # min_samples=55: block=65 (260 sessions -> 52/weekday) excludes the weekday;
    # block=60 (300 sessions -> 60/weekday) includes it.
    assert day.weekday() not in _weekday_median_amplitudes(
        bars, day, lookback_days=260, min_samples=55, contract_session_block=65
    )
    assert day.weekday() in _weekday_median_amplitudes(
        bars, day, lookback_days=260, min_samples=55, contract_session_block=60
    )


def test_scattered_outage_is_not_a_complete_session():
    # 39 scattered RTH bars WITH gaps must NOT reconstruct a complete D1 session
    day = date(2026, 9, 7)
    start = _et(day.year, day.month, day.day, 9, 30)
    scattered = [
        _bar(start + timedelta(minutes=5 * (2 * i + 1)), 150, 200, 100, 150)
        for i in range(39)
    ]
    assert not _is_complete_rth_session(scattered)


# ---------------------------------------------------------------------------
# ATR period
# ---------------------------------------------------------------------------

def test_atr_period_one_is_last_true_range():
    base = _et(2026, 9, 7, 10, 0)
    bars = [_bar(base + timedelta(minutes=5 * i), 100, 104, 96, 102) for i in range(5)]
    last_tr = max(
        bars[-1].high - bars[-1].low,
        abs(bars[-1].high - bars[-2].close),
        abs(bars[-1].low - bars[-2].close),
    )
    assert _atr14(bars, period=1) == pytest.approx(last_tr)


def test_sl_tp_matches_legacy_full_scan_bit_for_bit_on_long_history():
    """The bounded reverse scan selects the legacy bars in the same order."""
    start = _et(2026, 1, 1, 0, 0)
    history = []
    for index in range(120 * 24 * 12):
        ts = start + timedelta(minutes=5 * index)
        offset = float((index * 17) % 29) / 8.0
        history.append(
            _bar(ts, 100.0 + offset, 102.25 + offset, 98.75 + offset, 101.0 + offset)
        )

    strategy = AmdCrtStrategy(atr_period=14)
    today = _session_date(history[-1].timestamp)
    cutoff = datetime.combine(today - timedelta(days=10), time(0, 0), tzinfo=ET)
    legacy_rth = [
        bar
        for bar in history
        if bar.timestamp.astimezone(ET) >= cutoff
        and time(9, 30) <= bar.timestamp.astimezone(ET).time() <= time(16, 0)
    ]
    legacy_atr = _atr14(legacy_rth, strategy.atr_period)
    assert legacy_atr is not None
    legacy = (
        min(legacy_atr * strategy.sl_atr_mult, strategy.sl_cap_pts),
        min(legacy_atr * strategy.sl_atr_mult, strategy.sl_cap_pts)
        * strategy.tp_atr_ratio,
    )

    assert strategy._sl_tp(history) == legacy


def test_atr_default_period():
    base = _et(2026, 9, 7, 10, 0)
    bars = [_bar(base + timedelta(minutes=5 * i), 100, 104, 96, 102) for i in range(20)]
    assert _atr14(bars) is not None and _atr14(bars) > 0


def test_atr_matches_reference_ewm_seed_and_minimum_history():
    base = _et(2026, 9, 7, 9, 30)
    bars = [_bar(base, 50, 100, 0, 50)]
    bars.extend(
        _bar(base + timedelta(minutes=5 * i), 50, 51, 49, 50)
        for i in range(1, 16)
    )

    assert _atr14(bars[:2]) is None
    assert _atr14(bars) == pytest.approx(34.24451323000718)


def test_strategy_atr_uses_only_reference_ten_day_rth_window():
    today = date(2026, 9, 7)
    history = [_bar(_et(2026, 8, 1, 10, 0), 50, 1000, 0, 50)]
    base = _et(2026, 9, 4, 9, 30)
    history.extend(
        _bar(base + timedelta(minutes=5 * i), 50, 51, 49, 50)
        for i in range(16)
    )
    history.append(_bar(_et(today.year, today.month, today.day, 8, 0), 50, 51, 49, 50))

    assert AmdCrtStrategy()._sl_tp(history) == pytest.approx((4.0, 8.0))


# ---------------------------------------------------------------------------
# EMA confluence filter
# ---------------------------------------------------------------------------

def test_ema_bucket_start_aligns_to_et_midnight():
    # 4h buckets anchored at ET midnight: 00:00, 04:00, 08:00, 12:00, ...
    assert _ema_bucket_start(_et(2026, 9, 7, 9, 35), 240) == _et(2026, 9, 7, 8, 0)
    assert _ema_bucket_start(_et(2026, 9, 7, 11, 59), 240) == _et(2026, 9, 7, 8, 0)
    assert _ema_bucket_start(_et(2026, 9, 7, 12, 0), 240) == _et(2026, 9, 7, 12, 0)
    assert _ema_bucket_start(_et(2026, 9, 7, 0, 30), 240) == _et(2026, 9, 7, 0, 0)


def test_ema_regime_direction_uses_last_closed_bar():
    # Build 4h bars: rising closes -> regime "long".
    bars = []
    for k in range(30):  # 30 closed 4h buckets, closes rising
        ts = _et(2026, 8, 1, 0, 0) + timedelta(hours=4 * k)
        bars.append(_bar(ts, float(k), float(k) + 1, float(k), float(k) + 0.5))
    # add an in-progress bar in the NEXT bucket (should be excluded)
    bars.append(_bar(_et(2026, 8, 6, 2, 0), 100.0, 200.0, 0.0, 1.0))
    assert _ema_regime_direction(bars, period=10, min_bars=10) == "long"


def test_ema_regime_direction_declining_is_short():
    bars = []
    for k in range(30):
        ts = _et(2026, 8, 1, 0, 0) + timedelta(hours=4 * k)
        bars.append(_bar(ts, float(30 - k), float(31 - k), float(30 - k), float(30 - k) - 0.5))
    assert _ema_regime_direction(bars, period=10, min_bars=10) == "short"


def test_ema_regime_direction_requires_min_bars():
    bars = [_bar(_et(2026, 8, 1, 0, 0) + timedelta(hours=4 * k), 1.0, 1.0, 1.0, 1.0) for k in range(5)]
    assert _ema_regime_direction(bars, period=10, min_bars=20) is None


def test_ema_filter_gates_signal_on_regime_mismatch(monkeypatch):
    # A full confluence (AMD + CRT) but EMA regime opposes the fade -> no signal.
    bars, _day = _confluence_history()  # AMD direction "short" (pre-NY high sweep)
    monkeypatch.setattr(
        "src.backtest.amd_crt._ema_regime_direction", lambda *a, **k: "long"
    )
    strat = AmdCrtStrategy(use_ema_filter=True, median_lookback_days=5, min_weekday_samples=3)
    assert strat.evaluate(bars) is None  # EMA "long" != AMD "short"


def test_ema_filter_allows_signal_on_regime_match(monkeypatch):
    bars, _day = _confluence_history()
    monkeypatch.setattr(
        "src.backtest.amd_crt._ema_regime_direction", lambda *a, **k: "short"
    )
    strat = AmdCrtStrategy(use_ema_filter=True, median_lookback_days=5, min_weekday_samples=3)
    assert strat.evaluate(bars) is not None  # EMA "short" == AMD "short"


def test_ema_filter_off_preserves_no_ema_behavior():
    bars, _day = _confluence_history()
    strat = AmdCrtStrategy(use_ema_filter=False, median_lookback_days=5, min_weekday_samples=3)
    assert strat.evaluate(bars) is not None  # no-EMA path still fires


def test_ema_filter_rejects_within_bucket_then_accepts_new_bucket_without_redetecting_crt(
    monkeypatch,
):
    crt_calls = 0
    ema_calls = 0

    def detect_amd(history, day, **kwargs):
        return "short", history[-1].timestamp

    def detect_crt(history, day, **kwargs):
        nonlocal crt_calls
        crt_calls += 1
        return True

    def ema_regime(history, **kwargs):
        nonlocal ema_calls
        ema_calls += 1
        return "long" if history[-1].timestamp.astimezone(ET).hour < 12 else "short"

    monkeypatch.setattr("src.backtest.amd_crt._detect_amd", detect_amd)
    monkeypatch.setattr("src.backtest.amd_crt._detect_crt", detect_crt)
    monkeypatch.setattr("src.backtest.amd_crt._ema_regime_direction", ema_regime)
    monkeypatch.setattr(AmdCrtStrategy, "_sl_tp", lambda self, history: (5.0, 10.0))
    strategy = AmdCrtStrategy(use_ema_filter=True)
    history = [_bar(_et(2026, 9, 7, 11, 0), 100, 101, 99, 100)]

    assert strategy.evaluate(history) is None  # rejected in the 08:00 bucket
    history.append(_bar(_et(2026, 9, 7, 11, 5), 100, 101, 99, 100))
    assert strategy.evaluate(history) is None  # same cached EMA rejection
    history.append(_bar(_et(2026, 9, 7, 12, 0), 100, 101, 99, 100))
    assert strategy.evaluate(history) is not None  # new bucket now accepts
    assert crt_calls == 1
    assert ema_calls == 2


# ---------------------------------------------------------------------------
# Edge gate (bootstrap CI on own closed trades)
# ---------------------------------------------------------------------------

def test_bootstrap_ci_lower_positive_mean():
    est = _bootstrap_ci_lower([1.0, 2.0, 1.5, 2.5, 1.8, 2.2, 1.9, 2.1])
    assert est is not None
    mean, ci_lower = est
    assert mean == pytest.approx(1.875)
    assert ci_lower > 0.0


def test_bootstrap_ci_lower_negative_mean():
    est = _bootstrap_ci_lower([-1.0, -2.0, -1.5, -2.5, -1.8])
    assert est is not None
    mean, ci_lower = est
    assert mean < 0.0
    assert ci_lower < 0.0


def test_bootstrap_ci_lower_requires_two_observations():
    assert _bootstrap_ci_lower([1.0]) is None
    assert _bootstrap_ci_lower([]) is None


def test_edge_gate_suppresses_when_edge_cold():
    strat = AmdCrtStrategy(
        use_edge_gate=True, edge_min_trades=3, median_lookback_days=5,
        min_weekday_samples=3,
    )
    # Seed several LOSING closed trades on PRIOR days.
    strat.note_trade(_et(2026, 9, 1, 10, 0), -1.0)
    strat.note_trade(_et(2026, 9, 2, 10, 0), -1.0)
    strat.note_trade(_et(2026, 9, 3, 10, 0), -1.0)
    strat.note_trade(_et(2026, 9, 4, 10, 0), -1.0)
    bars, _day = _confluence_history()
    assert strat.evaluate(bars) is None  # cold edge -> suppressed


def test_edge_gate_permits_when_edge_hot():
    strat = AmdCrtStrategy(
        use_edge_gate=True, edge_min_trades=3, median_lookback_days=5,
        min_weekday_samples=3,
    )
    # Seed several WINNING closed trades on PRIOR days.
    strat.note_trade(_et(2026, 9, 1, 10, 0), 1.0)
    strat.note_trade(_et(2026, 9, 2, 10, 0), 1.2)
    strat.note_trade(_et(2026, 9, 3, 10, 0), 0.8)
    strat.note_trade(_et(2026, 9, 4, 10, 0), 1.1)
    bars, _day = _confluence_history()
    assert strat.evaluate(bars) is not None  # hot edge -> allowed


def test_edge_gate_ignores_same_day_trades():
    strat = AmdCrtStrategy(
        use_edge_gate=True, edge_min_trades=3, median_lookback_days=5,
        min_weekday_samples=3,
    )
    # Same-day exit must NOT count toward the edge (causal): seed losers today.
    strat.note_trade(_et(2026, 9, 7, 10, 0), -5.0)
    strat.note_trade(_et(2026, 9, 7, 11, 0), -5.0)
    # With no PRIOR-day trades, edge_min_trades not met -> permissive (True).
    bars, _day = _confluence_history()
    assert strat.evaluate(bars) is not None


def test_edge_gate_off_preserves_behavior():
    strat = AmdCrtStrategy(
        use_edge_gate=False, median_lookback_days=5, min_weekday_samples=3
    )
    bars, _day = _confluence_history()
    assert strat.evaluate(bars) is not None


def test_edge_filter_rejects_one_day_and_accepts_next_day(monkeypatch):
    crt_days = []
    sl_tp_calls = 0

    def detect_amd(history, day, **kwargs):
        return "long", history[-1].timestamp

    def detect_crt(history, day, **kwargs):
        crt_days.append(day)
        return True

    def sl_tp(self, history):
        nonlocal sl_tp_calls
        sl_tp_calls += 1
        return 5.0, 10.0

    monkeypatch.setattr("src.backtest.amd_crt._detect_amd", detect_amd)
    monkeypatch.setattr("src.backtest.amd_crt._detect_crt", detect_crt)
    monkeypatch.setattr(AmdCrtStrategy, "_sl_tp", sl_tp)
    strategy = AmdCrtStrategy(use_edge_gate=True)
    monkeypatch.setattr(
        strategy, "_edge_gate_ok", lambda day: day == date(2026, 9, 8)
    )
    history = [_bar(_et(2026, 9, 7, 11, 0), 100, 101, 99, 100)]

    assert strategy.evaluate(history) is None
    history.append(_bar(_et(2026, 9, 7, 11, 5), 100, 101, 99, 100))
    assert strategy.evaluate(history) is None
    history.append(_bar(_et(2026, 9, 8, 11, 0), 100, 101, 99, 100))
    assert strategy.evaluate(history) is not None
    assert crt_days == [date(2026, 9, 7), date(2026, 9, 8)]
    assert sl_tp_calls == 1


@pytest.mark.parametrize(
    ("risk_kwargs", "sl_values"),
    [
        ({"min_risk_pts": 2.0}, [1.0, 2.0]),
        ({"max_risk_pts": 2.0}, [3.0, 2.0]),
    ],
)
def test_risk_filter_rechecks_atr_but_not_confirmed_crt(
    monkeypatch, risk_kwargs, sl_values
):
    crt_calls = 0
    sl_tp_calls = 0

    monkeypatch.setattr(
        "src.backtest.amd_crt._detect_amd",
        lambda history, day, **kwargs: ("long", history[-1].timestamp),
    )

    def detect_crt(history, day, **kwargs):
        nonlocal crt_calls
        crt_calls += 1
        return True

    def sl_tp(self, history):
        nonlocal sl_tp_calls
        value = sl_values[sl_tp_calls]
        sl_tp_calls += 1
        return value, value * 2.0

    monkeypatch.setattr("src.backtest.amd_crt._detect_crt", detect_crt)
    monkeypatch.setattr(AmdCrtStrategy, "_sl_tp", sl_tp)
    strategy = AmdCrtStrategy(**risk_kwargs)
    history = [_bar(_et(2026, 9, 7, 11, 0), 100, 101, 99, 100)]

    assert strategy.evaluate(history) is None
    history.append(_bar(_et(2026, 9, 7, 11, 5), 100, 101, 99, 100))
    assert strategy.evaluate(history) is not None
    assert crt_calls == 1
    assert sl_tp_calls == 2


# ---------------------------------------------------------------------------
# Session (D1) convention
# ---------------------------------------------------------------------------

def test_session_date_default_is_calendar_et():
    ts = _et(2026, 9, 7, 8, 0)
    assert _session_date(ts) == date(2026, 9, 7)


def test_session_date_shifts_with_session_start():
    # session starts at 18:00: a bar before 18:00 belongs to the prior session
    ts = _et(2026, 9, 7, 10, 0)
    assert _session_date(ts, session_start=time(18, 0)) == date(2026, 9, 6)


# ---------------------------------------------------------------------------
# Causal AMD/CRT detection
# ---------------------------------------------------------------------------

def _prior_weekdays_history():
    """5 prior same-weekday days (amplitude 100 each)."""
    day = date(2026, 9, 7)
    bars = []
    for k in range(1, 6):
        d = day - timedelta(weeks=k)
        bars.extend(_complete_rth_session(d, high=200.0, low=100.0))
    return bars, day


def _continuous_pre_ny(day: date, *, high: float = 125.0, low: float = 100.0):
    start = _et(day.year, day.month, day.day, 0, 0)
    return [
        _bar(start + timedelta(minutes=5 * i), 110, high, low, 110)
        for i in range(114)  # 00:00 -> 09:25 ET (contiguous pre-NY)
    ]


def test_detect_amd_compressed_range_and_sweep():
    bars, day = _prior_weekdays_history()
    bars.extend(_continuous_pre_ny(day))
    bars.append(_bar(_et(2026, 9, 7, 9, 30), 110, 120, 105, 110))
    bars.append(_bar(_et(2026, 9, 7, 9, 35), 120, 130, 100, 120))
    amd = _detect_amd(bars, day, lookback_days=5, min_samples=3)
    assert amd is not None
    assert amd[0] == "short"
    assert amd[1] == _et(2026, 9, 7, 9, 35)


def test_detect_amd_requires_compression():
    bars, day = _prior_weekdays_history()
    bars.extend(_continuous_pre_ny(day, high=160, low=40))  # NOT compressed
    bars.append(_bar(_et(2026, 9, 7, 9, 30), 110, 120, 105, 110))
    bars.append(_bar(_et(2026, 9, 7, 9, 35), 120, 170, 100, 120))
    assert _detect_amd(bars, day, lookback_days=5, min_samples=3) is None


def test_detect_amd_rejects_gap_inside_pre_ny_window():
    bars, day = _prior_weekdays_history()
    bars.extend(
        [
            _bar(_et(2026, 9, 7, 0, 0), 110, 125, 100, 110),
            _bar(_et(2026, 9, 7, 9, 25), 110, 125, 100, 110),
            _bar(_et(2026, 9, 7, 9, 30), 120, 130, 100, 120),
        ]
    )

    assert _detect_amd(bars, day, lookback_days=5, min_samples=3) is None


def test_detect_amd_rejects_pre_ny_not_starting_at_midnight():
    bars, day = _prior_weekdays_history()
    # continuous pre-NY from 08:00 (NOT 00:00) + confirm + sweep
    start = _et(day.year, day.month, day.day, 8, 0)
    bars.extend(
        _bar(start + timedelta(minutes=5 * i), 110, 125, 100, 110)
        for i in range(18)
    )
    bars.append(_bar(_et(2026, 9, 7, 9, 30), 110, 120, 105, 110))
    bars.append(_bar(_et(2026, 9, 7, 9, 35), 120, 130, 100, 120))
    assert _detect_amd(bars, day, lookback_days=5, min_samples=3) is None


def test_detect_crt_sweep_of_prior_day_pdh():
    bars = _complete_rth_session(date(2026, 9, 4), high=200.0, low=100.0)
    bars.append(_bar(_et(2026, 9, 7, 11, 0), 195, 205, 195, 195))
    assert _detect_crt(bars, date(2026, 9, 7)) is True


def test_detect_crt_rejects_incomplete_prior_rth():
    # a single prior-day bar must NOT satisfy CRT (untrustworthy PDH/PDL)
    bars = [
        _bar(_et(2026, 9, 4, 10, 0), 150, 200, 100, 150),
        _bar(_et(2026, 9, 7, 11, 0), 195, 205, 195, 195),
    ]
    assert _detect_crt(bars, date(2026, 9, 7)) is False


# ---------------------------------------------------------------------------
# Confluence ordering, simultaneity, expiry
# ---------------------------------------------------------------------------

def _confluence_history():
    bars, day = _prior_weekdays_history()
    bars.extend(_complete_rth_session(date(2026, 9, 4), high=200.0, low=100.0))
    bars.extend(_continuous_pre_ny(day))
    bars.append(_bar(_et(2026, 9, 7, 9, 30), 110, 120, 105, 110))
    bars.append(_bar(_et(2026, 9, 7, 9, 35), 120, 130, 100, 120))  # AMD
    bars.append(_bar(_et(2026, 9, 7, 11, 0), 195, 205, 195, 195))  # CRT
    return bars, day


def test_confluence_amd_first_then_crt():
    bars, _ = _confluence_history()
    strat = AmdCrtStrategy(
        atr_period=1, median_lookback_days=5, min_weekday_samples=3
    )
    amd_only = [b for b in bars if b.timestamp <= _et(2026, 9, 7, 9, 35)]
    assert strat.evaluate(amd_only) is None
    full = [b for b in bars if b.timestamp <= _et(2026, 9, 7, 11, 0)]
    sig = strat.evaluate(full)
    assert sig is not None and sig.direction == "short"
    assert sig.stop_target_as_points is True


def test_confluence_crt_first_then_amd():
    bars, day = _prior_weekdays_history()
    bars.extend(_complete_rth_session(date(2026, 9, 4), high=200.0, low=100.0))
    bars.extend(_continuous_pre_ny(day))
    bars.append(_bar(_et(2026, 9, 7, 9, 30), 110, 120, 105, 110))
    bars.append(_bar(_et(2026, 9, 7, 9, 35), 195, 205, 195, 195))  # CRT first
    bars.append(_bar(_et(2026, 9, 7, 9, 40), 110, 120, 105, 110))
    bars.append(_bar(_et(2026, 9, 7, 9, 45), 110, 120, 105, 110))
    bars.append(_bar(_et(2026, 9, 7, 9, 50), 120, 130, 100, 120))  # AMD second
    strat = AmdCrtStrategy(
        atr_period=1, median_lookback_days=5, min_weekday_samples=3
    )
    assert strat.evaluate(bars) is not None


def test_confluence_simultaneous_amd_and_crt_same_bar():
    bars, day = _prior_weekdays_history()
    bars.extend(_complete_rth_session(date(2026, 9, 4), high=200.0, low=100.0))
    bars.extend(_continuous_pre_ny(day))
    # 09:30 bar is BOTH an AMD sweep (over 125) and CRT sweep (over 200)
    bars.append(_bar(_et(2026, 9, 7, 9, 30), 210, 215, 100, 120))
    strat = AmdCrtStrategy(
        atr_period=1, median_lookback_days=5, min_weekday_samples=3
    )
    assert strat.evaluate(bars) is not None


def test_confluence_amd_expires_without_crt():
    bars, day = _prior_weekdays_history()
    bars.extend(_complete_rth_session(date(2026, 9, 4), high=200.0, low=100.0))
    bars.extend(_continuous_pre_ny(day))
    bars.append(_bar(_et(2026, 9, 7, 9, 30), 110, 120, 105, 110))
    bars.append(_bar(_et(2026, 9, 7, 9, 35), 120, 130, 100, 120))  # AMD only
    strat = AmdCrtStrategy(median_lookback_days=5, min_weekday_samples=3)
    assert strat.evaluate(bars) is None


# ---------------------------------------------------------------------------
# Decision log
# ---------------------------------------------------------------------------

def _patched_candidate(
    monkeypatch, *, crt: bool = True, sl_tp=(5.0, 10.0), atr: float | None = 2.5
):
    monkeypatch.setattr(
        "src.backtest.amd_crt._weekday_median_amplitudes",
        lambda *args, **kwargs: {0: 50.0},
    )
    monkeypatch.setattr(
        "src.backtest.amd_crt._detect_amd",
        lambda history, day, **kwargs: ("long", history[-1].timestamp),
    )
    monkeypatch.setattr(
        "src.backtest.amd_crt._detect_crt", lambda *args, **kwargs: crt,
    )
    monkeypatch.setattr(AmdCrtStrategy, "_sl_tp", lambda self, history: sl_tp)
    monkeypatch.setattr(AmdCrtStrategy, "_atr_value", lambda self, history: atr)
    day = date(2026, 9, 7)
    return _continuous_pre_ny(day) + [
        _bar(_et(2026, 9, 7, 9, 30), 110, 120, 105, 110)
    ]


def test_decision_log_records_accepted_candidate(monkeypatch):
    history = _patched_candidate(monkeypatch)
    strategy = AmdCrtStrategy()

    assert strategy.evaluate(history) is not None
    assert len(strategy.decisions) == 1
    decision = strategy.decisions[0]
    assert decision.decision == "accepted"
    assert decision.day == date(2026, 9, 7)
    assert decision.weekday == 0
    assert decision.direction == "long"
    assert decision.crt_confirmed is True
    assert decision.ema_regime is None
    assert decision.atr == 2.5
    assert decision.sl_pts == 5.0 and decision.tp_pts == 10.0
    assert decision.pre_ny_amplitude == 25.0
    assert decision.median_amplitude == 50.0
    assert decision.timestamp == history[-1].timestamp


@pytest.mark.parametrize(
    ("reason", "strategy_kwargs", "crt", "sl_tp", "atr", "edge_ok", "ema_regime"),
    [
        ("crt_not_confirmed", {}, False, (5.0, 10.0), 2.5, True, "long"),
        ("ema_against", {"use_ema_filter": True}, True, (5.0, 10.0), 2.5, True, "short"),
        ("edge_cold", {"use_edge_gate": True}, True, (5.0, 10.0), 2.5, False, "long"),
        ("atr_insufficient", {}, True, None, None, True, "long"),
        ("risk_out_of_band", {"min_risk_pts": 6.0}, True, (5.0, 10.0), 2.5, True, "long"),
    ],
)
def test_decision_log_records_each_candidate_rejection(
    monkeypatch, reason, strategy_kwargs, crt, sl_tp, atr, edge_ok, ema_regime
):
    history = _patched_candidate(monkeypatch, crt=crt, sl_tp=sl_tp, atr=atr)
    monkeypatch.setattr(
        "src.backtest.amd_crt._ema_regime_direction",
        lambda *args, **kwargs: ema_regime,
    )
    strategy = AmdCrtStrategy(**strategy_kwargs)
    monkeypatch.setattr(strategy, "_edge_gate_ok", lambda day: edge_ok)

    assert strategy.evaluate(history) is None
    assert [item.decision for item in strategy.decisions] == [reason]


def test_decision_log_excludes_pre_amd_bars(monkeypatch):
    day = date(2026, 9, 7)
    history = _continuous_pre_ny(day)
    monkeypatch.setattr(
        "src.backtest.amd_crt._weekday_median_amplitudes",
        lambda *args, **kwargs: {0: 50.0},
    )
    monkeypatch.setattr(
        "src.backtest.amd_crt._detect_amd", lambda *args, **kwargs: None,
    )
    strategy = AmdCrtStrategy()

    assert strategy.evaluate(history) is None
    assert strategy.decisions == []


def test_decision_log_is_causal_and_does_not_change_after_future_bars(monkeypatch):
    history = _patched_candidate(monkeypatch)
    strategy = AmdCrtStrategy()
    assert strategy.evaluate(history) is not None
    snapshot = strategy.decisions[0]

    history.append(_bar(_et(2026, 9, 7, 9, 35), 1000, 2000, 0, 1500))
    assert strategy.evaluate(history) is None
    assert strategy.decisions == [snapshot]
    assert snapshot.timestamp < history[-1].timestamp
    assert snapshot.pre_ny_amplitude == 25.0


def test_decision_log_is_observational_for_backtest_results():
    bars = _pipeline_confluence_bars()
    config = amd_crt_config(slippage_points=0.0)
    logged_strategy = AmdCrtStrategy(log_decisions=True)
    silent_strategy = AmdCrtStrategy(log_decisions=False)

    logged = run_backtest(bars, logged_strategy, config)
    silent = run_backtest(bars, silent_strategy, config)

    assert logged.trades == silent.trades
    assert (
        logged.n_trades,
        logged.win_rate,
        logged.expectancy,
        logged.profit_factor,
        logged.max_drawdown_pct,
    ) == (
        silent.n_trades,
        silent.win_rate,
        silent.expectancy,
        silent.profit_factor,
        silent.max_drawdown_pct,
    )
    assert logged_strategy.decisions
    assert silent_strategy.decisions == []


def test_decision_log_can_be_cleared(monkeypatch):
    history = _patched_candidate(monkeypatch)
    strategy = AmdCrtStrategy()
    assert strategy.evaluate(history) is not None
    strategy.clear_decisions()
    assert strategy.decisions == []


# ---------------------------------------------------------------------------
# Executor extensions
# ---------------------------------------------------------------------------

def test_amd_crt_keeps_all_new_executor_mechanics_disabled():
    config = amd_crt_config()

    assert config.partial_take_profit_fraction == 0.0
    assert config.move_stop_to_break_even is False
    assert config.pending_limit_entry is False
    assert config.pending_order_wait_bars == 0
    assert config.cooldown_bars == 0

def test_distance_sl_tp_resolves_against_entry():
    base = _et(2026, 9, 7, 9, 30)
    bars = [
        _bar(base, 90, 91, 89, 100),
        _bar(base + timedelta(minutes=5), 100, 101, 99, 100),
        _bar(base + timedelta(minutes=10), 100, 111, 99, 110),
    ]
    config = BacktestConfig(fixed_quantity=1, slippage_points=0.0, commission_per_side=0.0)
    result = run_backtest(bars, _FixedSignal("long", 5.0, 10.0), config)
    t = result.trades[0]
    assert t.stop_price == 95.0 and t.target_price == 110.0
    assert t.exit_reason == "take_profit"


def test_gap_through_stop_fills_at_open():
    base = _et(2026, 9, 7, 9, 30)
    bars = [
        _bar(base, 90, 91, 89, 100),
        _bar(base + timedelta(minutes=5), 100, 101, 99, 100),
        _bar(base + timedelta(minutes=10), 93, 94, 92, 93),  # gaps below stop 95
    ]
    config = BacktestConfig(fixed_quantity=1, slippage_points=0.0, commission_per_side=0.0)
    result = run_backtest(bars, _FixedSignal("long", 5.0, 100.0), config)
    assert result.trades[0].exit_reason == "stop_loss"
    assert result.trades[0].exit_price == 93.0


def test_entry_gap_rejected():
    base = _et(2026, 9, 7, 9, 30)
    bars = [
        _bar(base, 90, 91, 89, 100),
        _bar(base + timedelta(minutes=10), 100, 101, 99, 100),  # missing the 5-min bar
    ]
    config = BacktestConfig(fixed_quantity=1, bar_interval_seconds=300,
                            slippage_points=0.0, commission_per_side=0.0)
    result = run_backtest(bars, _FixedSignal("long", 5.0, 10.0), config)
    assert result.gap_rejections == 1
    assert result.n_trades == 0


def test_timestamp_time_exit_closes_at_open():
    base = _et(2026, 9, 7, 9, 30)
    bars = [_bar(base, 90, 91, 89, 100)]
    for k in range(1, 20):
        bars.append(_bar(base + timedelta(minutes=5 * k), 100, 101, 99, 100))
    config = BacktestConfig(fixed_quantity=1, max_hold_minutes=60.0,
                            slippage_points=0.0, commission_per_side=0.0)
    result = run_backtest(bars, _FixedSignal("long", 50.0, 100.0), config)
    t = result.trades[0]
    assert t.exit_reason == "time_exit"
    assert t.exit_price == 100.0  # closed at the bar open, not close
    assert t.exit_time == _et(2026, 9, 7, 10, 35)


def test_unresolved_position_not_invented():
    base = _et(2026, 9, 7, 9, 30)
    bars = [
        _bar(base, 90, 91, 89, 100),
        _bar(base + timedelta(minutes=5), 100, 101, 99, 100),  # entry, far stop/target
        _bar(base + timedelta(minutes=10), 100, 101, 99, 100),
    ]
    config = BacktestConfig(fixed_quantity=1, slippage_points=0.0, commission_per_side=0.0)
    result = run_backtest(bars, _FixedSignal("long", 5.0, 100.0), config)
    assert result.unresolved_positions == 1
    assert result.n_trades == 0  # no invented fill


def test_collapsed_levels_rejected_after_rounding():
    base = _et(2026, 9, 7, 9, 30)
    bars = [
        _bar(base, 90, 91, 89, 100),
        _bar(base + timedelta(minutes=5), 100, 101, 99, 100),
    ]
    config = BacktestConfig(fixed_quantity=1, slippage_points=0.0,
                            commission_per_side=0.0, tick_size=0.25)
    result = run_backtest(bars, _FixedSignal("long", 0.1, 0.2), config)
    assert result.n_trades == 0  # stop rounds to entry -> collapsed


def test_distance_sizing_uses_stop_distance():
    base = _et(2026, 9, 7, 9, 30)
    bars = [
        _bar(base, 90, 91, 89, 100),
        _bar(base + timedelta(minutes=5), 100, 101, 99, 100),
        _bar(base + timedelta(minutes=10), 100, 111, 99, 110),
    ]
    config = BacktestConfig(initial_balance=50_000.0, risk_per_trade=0.01,
                            slippage_points=0.0, commission_per_side=0.0)
    result = run_backtest(bars, _FixedSignal("long", 5.0, 10.0), config)
    # $500 risk / (5 pts * $2/pt) = 50 -> capped at 10
    assert result.trades[0].quantity == 10


def test_slippage_metric_derived_from_applied():
    base = _et(2026, 9, 7, 9, 30)
    bars = [
        _bar(base, 90, 91, 89, 100),
        _bar(base + timedelta(minutes=5), 100, 101, 99, 100),
        _bar(base + timedelta(minutes=10), 100, 111, 99, 110),  # TP (no exit slippage)
    ]
    config = BacktestConfig(fixed_quantity=1, slippage_points=0.25, commission_per_side=0.0)
    result = run_backtest(bars, _FixedSignal("long", 5.0, 10.0), config)
    # entry slippage 0.25 pts * $2 * 1 = $0.50; TP exit has no slippage
    assert result.total_slippage_cost == pytest.approx(0.25 * 2.0 * 1)


def test_sub_tick_slippage_metric_uses_actual_rounded_fill():
    base = _et(2026, 9, 7, 9, 30)
    bars = [
        _bar(base, 90, 91, 89, 100),
        _bar(base + timedelta(minutes=5), 100, 101, 99, 100),
        _bar(base + timedelta(minutes=10), 100, 111, 99, 110),
    ]
    config = BacktestConfig(
        fixed_quantity=1,
        slippage_points=0.10,
        commission_per_side=0.0,
        tick_size=0.25,
    )
    result = run_backtest(bars, _FixedSignal("long", 5.0, 10.0), config)

    assert result.trades[0].entry_price == 100.0
    assert result.total_slippage_cost == 0.0


def test_stop_and_time_exit_fills_are_on_tick_and_costed_coherently():
    base = _et(2026, 9, 7, 9, 30)
    stop_bars = [
        _bar(base, 90, 91, 89, 100),
        _bar(base + timedelta(minutes=5), 100, 101, 94, 100),
    ]
    config = BacktestConfig(
        fixed_quantity=1,
        slippage_points=0.10,
        commission_per_side=0.0,
        tick_size=0.25,
    )
    stopped = run_backtest(stop_bars, _FixedSignal("long", 5.0, 100.0), config)
    trade = stopped.trades[0]
    assert trade.exit_price == 95.0
    assert trade.exit_price % 0.25 == 0.0
    assert stopped.total_slippage_cost == 0.0

    time_bars = [
        _bar(base, 90, 91, 89, 100),
        _bar(base + timedelta(minutes=5), 100, 101, 99, 100),
        _bar(base + timedelta(minutes=10), 100.10, 101, 99, 100.10),
    ]
    timed = run_backtest(
        time_bars,
        _FixedSignal("long", 5.0, 100.0),
        BacktestConfig(
            fixed_quantity=1,
            max_hold_minutes=5,
            slippage_points=0.25,
            commission_per_side=0.0,
            tick_size=0.25,
        ),
    )
    assert timed.trades[0].exit_price == 100.0
    assert timed.total_slippage_cost == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"fixed_quantity": 0}, "fixed_quantity"),
        ({"fixed_quantity": 0.5}, "fixed_quantity"),
        ({"max_hold_minutes": -1}, "max_hold_minutes"),
        ({"max_hold_minutes": float("nan")}, "max_hold_minutes"),
        ({"bar_interval_seconds": 0}, "bar_interval_seconds"),
        ({"bar_interval_seconds": float("nan")}, "bar_interval_seconds"),
        ({"bar_interval_seconds": 300.5}, "bar_interval_seconds"),
        ({"max_contracts": 1.5}, "max_contracts"),
        ({"max_trades": 1.5}, "max_trades"),
        ({"max_bars_held": 1.5}, "max_bars_held"),
        ({"partial_take_profit_fraction": -0.1}, "partial_take_profit_fraction"),
        ({"partial_take_profit_fraction": 1.0}, "partial_take_profit_fraction"),
        ({"move_stop_to_break_even": True}, "partial_take_profit_fraction"),
        ({"pending_limit_entry": True}, "pending_order_wait_bars"),
        ({"pending_order_wait_bars": -1}, "pending_order_wait_bars"),
        ({"cooldown_bars": -1}, "cooldown_bars"),
    ],
)
def test_backtest_config_rejects_invalid_executor_extensions(kwargs, message):
    with pytest.raises(ValueError, match=message):
        BacktestConfig(**kwargs)


def test_amd_crt_config_rejects_negative_friction():
    with pytest.raises(ValueError, match="friction_pts"):
        amd_crt_config(friction_pts=-0.1)


def test_amd_crt_config_rejects_non_finite_friction():
    with pytest.raises(ValueError, match="friction_pts"):
        amd_crt_config(friction_pts=float("nan"))


def test_pnl_direction_long_and_short_opposite():
    base = _et(2026, 9, 7, 9, 30)
    up = [
        _bar(base, 90, 91, 89, 100),
        _bar(base + timedelta(minutes=5), 100, 101, 99, 100),
        _bar(base + timedelta(minutes=10), 100, 116, 99, 115),
    ]
    down = [
        _bar(base, 110, 111, 109, 100),
        _bar(base + timedelta(minutes=5), 100, 101, 99, 100),
        _bar(base + timedelta(minutes=10), 100, 101, 84, 85),
    ]
    config = BacktestConfig(fixed_quantity=1, slippage_points=0.0, commission_per_side=0.0)
    long_t = run_backtest(up, _FixedSignal("long", 5.0, 10.0), config).trades[0]
    short_t = run_backtest(down, _FixedSignal("short", 5.0, 10.0), config).trades[0]
    assert long_t.gross_pnl > 0 and short_t.gross_pnl > 0
    assert long_t.gross_pnl == pytest.approx(10.0 * 2.0 * 1)
    assert short_t.gross_pnl == pytest.approx(10.0 * 2.0 * 1)


# ---------------------------------------------------------------------------
# Dollar-accurate Core mapping
# ---------------------------------------------------------------------------

def test_core_mapping_dollar_accurate_and_labels_normalization():
    base = _et(2026, 9, 7, 9, 30)
    bars = [
        _bar(base, 90, 91, 89, 100),
        _bar(base + timedelta(minutes=5), 100, 101, 99, 100),
        _bar(base + timedelta(minutes=10), 100, 111, 99, 110),
    ]
    config = BacktestConfig(initial_balance=50_000.0, risk_per_trade=0.01,
                            fixed_quantity=1, slippage_points=0.0, commission_per_side=0.0)
    result = run_backtest(bars, _FixedSignal("long", 5.0, 10.0), config)
    t = result.trades[0]
    assert t.net_pnl == pytest.approx(20.0)
    assert t.r_result == pytest.approx(20.0 / 500.0)
    assert t.stop_risk_dollars == pytest.approx(5.0 * 2.0 * 1)

    core = executed_to_core_trades((t,), config, "AmdCrtStrategy")[0]
    assert core.strategy == "AmdCrtStrategy"  # real identity, not hardcoded
    assert core.r_result == pytest.approx(20.0 / 500.0)
    assert core.metadata["risk_budget_dollars"] == pytest.approx(500.0)
    assert core.metadata["stop_risk_dollars"] == pytest.approx(10.0)
    assert core.metadata["r_vs_stop"] == pytest.approx(2.0)
    assert "normalization" in core.metadata
    assert result.equity_curve == (50_000.0, 50_020.0)


# ---------------------------------------------------------------------------
# Integration: loader -> AmdCrtStrategy -> executor -> Core
# ---------------------------------------------------------------------------

def test_integration_end_to_end(tmp_path: Path):
    days = _trading_days(261)
    prior, today = days[:-1], days[-1]
    rows = ["time,open,high,low,close"]
    for d in prior:
        for bar in _complete_rth_session(d, high=200.0, low=100.0):
            rows.append(
                f"{bar.timestamp.isoformat()},{bar.open},{bar.high},{bar.low},{bar.close}"
            )
    for bar in _continuous_pre_ny(today):
        rows.append(
            f"{bar.timestamp.isoformat()},{bar.open},{bar.high},{bar.low},{bar.close}"
        )
    rows.append(f"{_et(today.year, today.month, today.day, 9, 30).isoformat()},110,120,105,110")
    rows.append(f"{_et(today.year, today.month, today.day, 9, 35).isoformat()},120,130,100,120")
    crt_ts = _et(today.year, today.month, today.day, 11, 0)
    rows.append(f"{crt_ts.isoformat()},195,205,195,195")  # CRT sweep
    # RTH bars after the entry so the 60-minute time-exit can resolve the trade
    for k in range(1, 21):
        ts = crt_ts + timedelta(minutes=5 * k)
        rows.append(f"{ts.isoformat()},200,200,190,195")
    p = tmp_path / "mnq_m5.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")

    bars = load_mnq_csv(p, target_interval_minutes=5, source_interval_minutes=5)
    strategy = AmdCrtStrategy()
    config = amd_crt_config(slippage_points=0.0)
    result = run_backtest(bars, strategy, config)

    assert result.n_trades == 1
    assert result.trades[0].direction == "short"
    core = executed_to_core_trades(result.trades, config, "AmdCrtStrategy")
    assert len(core) == 1
    assert core[0].strategy == "AmdCrtStrategy"
    assert result.simulation is not None


def _pipeline_confluence_bars() -> list[Bar]:
    days = _trading_days(261)
    bars: list[Bar] = []
    for d in days[:-1]:
        bars.extend(_complete_rth_session(d, high=200.0, low=100.0))
    today = days[-1]
    bars.extend(_continuous_pre_ny(today))
    bars.extend(
        [
            _bar(_et(today.year, today.month, today.day, 9, 30), 110, 120, 105, 110),
            _bar(_et(today.year, today.month, today.day, 9, 35), 120, 130, 100, 120),
            _bar(_et(today.year, today.month, today.day, 11, 0), 195, 205, 195, 195),
        ]
    )
    bars.extend(
        _bar(
            _et(today.year, today.month, today.day, 11, 0) + timedelta(minutes=5 * k),
            195,
            200,
            190,
            195,
        )
        for k in range(1, 21)
    )
    return bars


def test_pipeline_oos_keeps_calibration_with_fresh_strategy_and_day_split():
    bars = _pipeline_confluence_bars()
    strategy = AmdCrtStrategy()
    stale_day = bars[-1].timestamp.astimezone(ET).date()
    strategy._amd_day = stale_day
    strategy._signal_day = stale_day
    config = amd_crt_config(slippage_points=0.0)
    full = run_backtest(bars, AmdCrtStrategy(), config)
    report = run_pipeline(bars, strategy, config, train_fraction=0.7)

    assert full.n_trades == 1
    assert report["in_sample"]["raw_n_trades"] == 0
    assert report["out_of_sample"]["raw_n_trades"] == 1
    assert strategy._amd_day == stale_day
    assert report["strategy_parameters"]["atr_period"] == 14
    assert report["strategy_parameters"]["median_lookback_days"] == 260
    assert report["strategy_parameters"]["session_tz"] == "America/New_York"

    split = chronological_split(bars, train_fraction=0.7, session_tz=ET)
    assert split.in_sample[-1].timestamp.astimezone(ET).date() != (
        split.out_of_sample[0].timestamp.astimezone(ET).date()
    )


def test_pipeline_summary_distinguishes_raw_and_rule_limited_results():
    base = _et(2026, 9, 7, 9, 30)
    bars = [
        _bar(base + timedelta(minutes=5 * i), 100, 111, 99, 100)
        for i in range(5)
    ]

    class AlwaysLong:
        def evaluate(self, history):
            if not history:
                return None
            return Signal("long", 0.0, 5.0, 10.0, stop_target_as_points=True)

    result = run_backtest(
        bars,
        AlwaysLong(),
        BacktestConfig(
            fixed_quantity=1,
            max_trades=1,
            slippage_points=0.0,
            commission_per_side=0.0,
        ),
    )
    summary = result_summary(result, "test")

    assert summary["raw_n_trades"] > summary["rule_limited_n_trades"]
    assert summary["raw_net_pnl"] > summary["rule_limited_net_pnl"]
    assert "n_trades" not in summary
    assert "net_pnl" not in summary


def test_strategy_does_not_emit_on_weekends_or_at_rth_close():
    bars, _ = _prior_weekdays_history()
    bars.extend(
        [
            _bar(_et(2026, 9, 5, 8, 0), 100, 125, 100, 110),
            _bar(_et(2026, 9, 5, 9, 35), 120, 205, 100, 120),
        ]
    )
    strategy = AmdCrtStrategy(median_lookback_days=5, min_weekday_samples=1)
    assert strategy.evaluate(bars) is None

    weekday_bars, _ = _confluence_history()
    weekday_bars.append(_bar(_et(2026, 9, 7, 16, 0), 195, 205, 195, 195))
    strategy = AmdCrtStrategy(median_lookback_days=5, min_weekday_samples=3)
    assert strategy.evaluate(weekday_bars) is None


def test_breakout_strategy_still_compatible():
    # regression: the placeholder strategy (absolute-price signals) still works
    from src.backtest.history import synthetic_bars
    bars = synthetic_bars(500, seed=1)
    result = run_backtest(bars, BreakoutStrategy(), BacktestConfig())
    assert isinstance(result.n_trades, int)
    assert result.n_trades >= 0

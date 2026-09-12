"""Tests for P4 JITA-inspired diagnostic event detectors."""
import pytest
from datetime import datetime, timezone

from src.detectors import DiagnosticEvent, detect_crt, detect_fvg, detect_cisd, detect_sweeps


def _bar(high, low, close, idx=0):
    return {
        "open": (high + low) / 2,
        "high": high, "low": low, "close": close,
        "volume": 1000,
        "timestamp": f"2024-01-01T{8 + idx % 12:02d}:{idx % 4 * 15:02d}:00+00:00",
    }


class TestEventSchema:
    def test_deterministic_event_id(self):
        e1 = DiagnosticEvent(
            detector="crt_v1", symbol="MNQ", timeframe="15m",
            source_bar_ids=(10,), pattern_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
            available_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            direction="long", levels={}, params={},
        )
        e2 = DiagnosticEvent(
            detector="crt_v1", symbol="MNQ", timeframe="15m",
            source_bar_ids=(10,), pattern_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
            available_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            direction="long", levels={}, params={},
        )
        assert e1.event_id == e2.event_id
        assert len(e1.event_id) == 64

    def test_different_input_different_id(self):
        e1 = DiagnosticEvent(
            detector="crt_v1", symbol="MNQ", timeframe="15m",
            source_bar_ids=(10,), pattern_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
            available_at=datetime(2024, 1, 1, tzinfo=timezone.utc), direction="long",
        )
        e2 = DiagnosticEvent(
            detector="crt_v1", symbol="MES", timeframe="15m",
            source_bar_ids=(10,), pattern_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
            available_at=datetime(2024, 1, 1, tzinfo=timezone.utc), direction="long",
        )
        assert e1.event_id != e2.event_id


class TestCRT:
    def test_contraction_detected(self):
        # Create bars with varying ranges so avg_range is non-trivial
        bars = []
        for h in range(25):
            bars.append(_bar(102 + h * 0.1, 98 - h * 0.1, 100, h))
        # Last bar has very small range compared to average
        bars.append(_bar(100.05, 99.95, 100.0, 25))
        events = detect_crt(bars, symbol="MNQ", timeframe="15m")
        assert len(events) >= 1
        assert events[-1].detector == "crt_v1"

    def test_insufficient_bars(self):
        bars = [_bar(102, 98, 100, h) for h in range(5)]
        events = detect_crt(bars, symbol="MNQ", timeframe="15m")
        assert len(events) == 0


class TestFVG:
    def test_bullish_fvg_detected(self):
        bars = [
            _bar(102, 100, 101, 8),
            _bar(105, 103, 104, 9),
            _bar(108, 103, 107, 10),
        ]
        events = detect_fvg(bars, symbol="MNQ", timeframe="15m")
        assert len(events) == 1
        assert events[0].direction == "long"

    def test_bearish_fvg_detected(self):
        bars = [
            _bar(100, 98, 99, 8),
            _bar(97, 95, 96, 9),
            _bar(94, 92, 93, 10),
        ]
        events = detect_fvg(bars, symbol="MNQ", timeframe="15m")
        assert len(events) == 1
        assert events[0].direction == "short"

    def test_no_fvg_small_gap(self):
        bars = [
            _bar(102, 100, 101, 8),
            _bar(103, 101, 102, 9),
            _bar(103, 101.5, 102, 10),
        ]
        events = detect_fvg(bars, symbol="MNQ", timeframe="15m")
        assert len(events) == 0

    def test_available_at_equals_third_bar(self):
        bars = [
            _bar(102, 100, 101, 8),
            _bar(105, 103, 104, 9),
            _bar(108, 103, 107, 10),
        ]
        events = detect_fvg(bars, symbol="MNQ", timeframe="15m")
        assert events[0].available_at == events[0].pattern_time


class TestCISD:
    def test_breakout_above_resistance(self):
        bars = [_bar(102, 100, 101, h) for h in range(12)]
        bars.append(_bar(105, 103, 104, 12))
        events = detect_cisd(bars, symbol="MNQ", timeframe="15m")
        long_events = [e for e in events if e.direction == "long"]
        assert len(long_events) >= 1

    def test_breakdown_below_support(self):
        bars = [_bar(102, 100, 101, h) for h in range(12)]
        bars.append(_bar(99, 97, 98, 12))
        events = detect_cisd(bars, symbol="MNQ", timeframe="15m")
        short_events = [e for e in events if e.direction == "short"]
        assert len(short_events) >= 1


class TestSweeps:
    def test_sweep_high_detected(self):
        bars = []
        for h in range(10):
            if h == 5:
                bars.append(_bar(105, 103, 104, h))
            else:
                bars.append(_bar(102, 100, 101, h))
        bars.append(_bar(106, 100, 102, 10))
        events = detect_sweeps(bars, symbol="MNQ", timeframe="15m")
        short_events = [e for e in events if e.direction == "short"]
        assert len(short_events) >= 1

    def test_insufficient_bars(self):
        bars = [_bar(102, 100, 101, h) for h in range(3)]
        events = detect_sweeps(bars, symbol="MNQ", timeframe="15m")
        assert len(events) == 0

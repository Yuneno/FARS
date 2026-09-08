"""Tests for the backtest history/download layer (FASE A)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.backtest.history import (
    download_bars,
    load_bars_csv,
    persist_bars,
    synthetic_bars,
)
from src.realtime.config import ProjectXConfigurationError, load_projectx_credentials
from src.realtime.connectors.projectx import MAX_BAR_LIMIT, ProjectXBar


def _bar(ts, o=100.0, h=101.0, l=99.0, c=100.5, v=10):
    return ProjectXBar(
        timestamp=ts,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=Decimal(str(v)),
    )


class _FakeClient:
    """Minimal retrieve_bars stub: oldest-first or newest-first, limited chunks."""

    def __init__(self, bars, *, newest_first=False):
        self._bars = bars
        self._newest_first = newest_first

    def retrieve_bars(self, contract_id, *, start, end, unit=2, unit_number=1,
                      limit=MAX_BAR_LIMIT, include_partial_bar=False, live=False):
        selected = [b for b in self._bars if start <= b.timestamp < end]
        if self._newest_first:
            selected = list(reversed(selected))
        return tuple(selected[:limit])


def _minute(i: int) -> datetime:
    return datetime(2026, 9, 1, 9, 0, tzinfo=UTC) + timedelta(minutes=i)


def test_download_paginates_across_multiple_chunks():
    bars = [_bar(_minute(i)) for i in range(12)]
    client = _FakeClient(bars)
    result = download_bars(
        client, "c", start=_minute(0), end=_minute(12), limit=5
    )
    assert result.manifest.bar_count == 12
    assert [b.timestamp for b in result.bars] == [_minute(i) for i in range(12)]


def test_download_orders_newest_first_input():
    bars = [_bar(_minute(i)) for i in range(5)]
    client = _FakeClient(bars, newest_first=True)
    result = download_bars(client, "c", start=_minute(0), end=_minute(5))
    assert [b.timestamp for b in result.bars] == [_minute(i) for i in range(5)]


def test_download_dedupes_overlapping_windows():
    bars = [_bar(_minute(i)) for i in range(4)]
    # simulate overlap: minute 2 and 3 appear twice
    bars = bars + [_bar(_minute(2)), _bar(_minute(3))]
    client = _FakeClient(bars)
    result = download_bars(client, "c", start=_minute(0), end=_minute(4))
    assert result.manifest.bar_count == 4
    assert result.manifest.duplicates_removed == 2


def test_download_empty_range_returns_empty_result():
    client = _FakeClient([])
    result = download_bars(client, "c", start=_minute(0), end=_minute(10))
    assert result.manifest.bar_count == 0
    assert result.bars == ()


def test_download_rejects_invalid_ohlc():
    bad = _bar(_minute(0), o=100, h=90, l=110, c=100)  # high < low
    client = _FakeClient([bad])
    with pytest.raises(ValueError):
        download_bars(client, "c", start=_minute(0), end=_minute(1))


def test_download_detects_gaps():
    bars = [_bar(_minute(i)) for i in (0, 1, 10, 11)]  # 8-minute hole
    client = _FakeClient(bars)
    result = download_bars(client, "c", start=_minute(0), end=_minute(12))
    assert result.manifest.gaps_detected >= 1


def test_limit_is_capped_at_max():
    result = download_bars(
        _FakeClient([]), "c", start=_minute(0), end=_minute(1), limit=MAX_BAR_LIMIT + 1000
    )
    assert result.manifest.bar_count == 0  # empty range still fine


def test_missing_credentials_raises_configuration_error(tmp_path, monkeypatch):
    monkeypatch.delenv("FARS_PROJECTX_USERNAME", raising=False)
    monkeypatch.delenv("FARS_PROJECTX_API_KEY", raising=False)
    with pytest.raises(ProjectXConfigurationError):
        load_projectx_credentials(tmp_path / "does_not_exist.env")


def test_persist_and_load_roundtrip(tmp_path):
    bars = [_bar(_minute(i)) for i in range(5)]
    client = _FakeClient(bars)
    result = download_bars(client, "c", start=_minute(0), end=_minute(5))
    csv_path, manifest_path = persist_bars(result, tmp_path, symbol="MNQ")
    loaded = load_bars_csv(csv_path)
    assert len(loaded) == 5
    assert loaded[0].open == 100.0
    assert manifest_path.exists()


def test_synthetic_bars_are_deterministic_with_seed():
    a = synthetic_bars(100, seed=7)
    b = synthetic_bars(100, seed=7)
    assert [x.close for x in a] == [x.close for x in b]
    c = synthetic_bars(100, seed=8)
    assert [x.close for x in a] != [x.close for x in c]

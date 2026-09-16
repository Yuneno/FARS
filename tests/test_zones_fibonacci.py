"""Tests de Fibonacci/OTE y premium/discount (helpers puros, calculos manuales)."""
import pytest

from src.zones.fibonacci import (
    equilibrium,
    ote_zone,
    premium_discount,
    retracement_from_high,
    retracement_from_low,
)


def test_equilibrium_manual():
    assert equilibrium(100, 200) == 150.0


def test_retracements_manual():
    assert retracement_from_high(100, 200, 0.62) == pytest.approx(138.0)
    assert retracement_from_low(100, 200, 0.62) == pytest.approx(162.0)
    assert retracement_from_high(100, 200, 0.50) == 150.0


def test_ote_bullish_manual():
    # L=100, H=200, ratios .62/.705 medidos BAJANDO desde H
    lo, hi = ote_zone(100, 200, "long")
    assert lo == pytest.approx(200 - 0.705 * 100)  # 129.5
    assert hi == pytest.approx(200 - 0.62 * 100)   # 138.0
    assert lo <= hi


def test_ote_bearish_manual():
    lo, hi = ote_zone(100, 200, "short")
    assert lo == pytest.approx(100 + 0.62 * 100)   # 162.0
    assert hi == pytest.approx(100 + 0.705 * 100)  # 170.5


def test_ote_direction_symmetry():
    blo, bhi = ote_zone(100, 200, "long")
    alo, ahi = ote_zone(100, 200, "short")
    # niveles largo = bajando desde H; corto = subiendo desde L; simetria: b = L+H - a
    assert blo == pytest.approx(300 - ahi)
    assert bhi == pytest.approx(300 - alo)


def test_ote_custom_ratios():
    lo, hi = ote_zone(100, 200, "long", (0.5,))
    assert lo == hi == 150.0


def test_invalid_range_raises():
    with pytest.raises(ValueError):
        ote_zone(200, 100, "long")
    with pytest.raises(ValueError):
        equilibrium(-5, 100)
    with pytest.raises(ValueError):
        ote_zone(100, 200, "sideways")


def test_premium_discount():
    assert premium_discount(140, 100, 200) == "discount"
    assert premium_discount(160, 100, 200) == "premium"
    assert premium_discount(150, 100, 200) == "equilibrium"

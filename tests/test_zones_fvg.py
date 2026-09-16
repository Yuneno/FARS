"""Tests del adaptador FVG a zonas y su lifecycle (Z2).

Incluye la relacion de equivalencia documentada con el FVG inline de
smc_fvg.py (desequilibrio puro high[t-2] < low[t]): cuando ademas se cumple la
confirmacion de la vela intermedia, el detector consolidado detecta el MISMO
gap con los MISMOS bounds (no hay tercer detector).
"""
from datetime import datetime, timedelta

from src.detectors.fvg import detect_fvg
from src.zones.fvg import fvg_zones_from_bars, transition_fvg

T0 = datetime(2024, 1, 2, 9, 30)


def bar(i, o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c,
            "timestamp": (T0 + timedelta(minutes=5 * i)).isoformat()}


def test_no_zone_before_third_bar():
    bars = [bar(i, 100, 101, 99, 100.5) for i in range(2)]
    assert fvg_zones_from_bars(bars, symbol="MNQ", timeframe="M5") == []


def test_bullish_bounds_and_availability():
    # b1.high=100, b2 cierra >100 (confirmacion intermedia), b3.low=101.5 > 100
    bars = [bar(0, 99, 100, 98.5, 99.5),
            bar(1, 99.8, 103, 99.5, 102.5),
            bar(2, 103, 104, 101.5, 102)]
    zones = fvg_zones_from_bars(bars, symbol="MNQ", timeframe="M5")
    assert len(zones) == 1
    z = zones[0]
    assert z.lower == 100.0 and z.upper == 101.5 and z.direction == "long"
    assert z.available_at == T0 + timedelta(minutes=10)
    assert z.metadata["available_bar_index"] == 2


def test_middle_candle_confirmation_required():
    # b2 cierra DENTRO de b1 (sin confirmacion) -> el detector no emite
    bars = [bar(0, 99, 100, 98.5, 99.5),
            bar(1, 99.8, 100.2, 99.2, 99.9),
            bar(2, 100.1, 101, 99.8, 100.6)]
    assert fvg_zones_from_bars(bars, symbol="MNQ", timeframe="M5") == []


def test_equivalence_relation_with_inline_smc_fvg():
    """high[t-2] < low[t] (inline puro) + confirmacion intermedia => detectado con los mismos bounds."""
    bars = [bar(0, 99, 100, 98.5, 99.5), bar(1, 99.8, 103, 99.5, 102.5),
            bar(2, 103, 104, 101.5, 102)]
    inline_condition = bars[2]["low"] > bars[0]["high"]
    assert inline_condition  # desequilibrio puro
    evs = detect_fvg(bars, symbol="MNQ", timeframe="M5")
    assert len(evs) == 1
    assert evs[0].levels["gap_low"] == bars[0]["high"]
    assert evs[0].levels["gap_high"] == bars[2]["low"]


def test_lifecycle_active_to_mitigated_full_fill():
    bars = [bar(0, 99, 100, 98.5, 99.5), bar(1, 99.8, 103, 99.5, 102.5),
            bar(2, 103, 104, 101.5, 102)]
    z = fvg_zones_from_bars(bars, symbol="MNQ", timeframe="M5")[0]
    z = transition_fvg(z, bar(3, 102, 102.2, 100.6, 101), 3)      # mecha entra -> touched
    assert z.state == "touched" and z.touches == 1
    z = transition_fvg(z, bar(4, 100.8, 101.2, 100.2, 100.4), 4)  # mas profundo que mid -> partial
    assert z.state == "partial"
    z = transition_fvg(z, bar(5, 100.4, 102.0, 99.0, 99.5), 5)    # cubre todo el gap -> mitigated
    assert z.state == "mitigated"


def test_wick_touch_mitigation():
    bars = [bar(0, 99, 100, 98.5, 99.5), bar(1, 99.8, 103, 99.5, 102.5),
            bar(2, 103, 104, 101.5, 102)]
    z = fvg_zones_from_bars(bars, symbol="MNQ", timeframe="M5")[0]
    z = transition_fvg(z, bar(3, 102, 102.2, 100.9, 101.4), 3, mitigation="wick_touch")
    assert z.state == "mitigated"


def test_close_through_mitigation_bearish():
    bars = [bar(0, 103, 104, 102.5, 103.5), bar(1, 103.2, 103.5, 100, 100.5),
            bar(2, 100, 100.5, 99, 99.5)]
    z = fvg_zones_from_bars(bars, symbol="MNQ", timeframe="M5")[0]
    assert z.direction == "short" and z.lower == 100.5 and z.upper == 102.5
    z = transition_fvg(z, bar(3, 100, 100.5, 99.6, 100.2), 3, mitigation="close_through")
    assert z.state == "touched"
    z = transition_fvg(z, bar(4, 100, 100.5, 102.8, 103.0), 4, mitigation="close_through")
    assert z.state == "mitigated"  # close > upper


def test_expiry_causal():
    bars = [bar(0, 99, 100, 98.5, 99.5), bar(1, 99.8, 103, 99.5, 102.5),
            bar(2, 103, 104, 101.5, 102)]
    z = fvg_zones_from_bars(bars, symbol="MNQ", timeframe="M5")[0]
    z = transition_fvg(z, bar(20, 105, 106, 104.5, 105.5), 20, max_age_bars=10)
    assert z.state == "expired"


def test_terminal_is_sticky():
    bars = [bar(0, 99, 100, 98.5, 99.5), bar(1, 99.8, 103, 99.5, 102.5),
            bar(2, 103, 104, 101.5, 102)]
    z = fvg_zones_from_bars(bars, symbol="MNQ", timeframe="M5")[0]
    z = transition_fvg(z, bar(5, 100.4, 102.0, 99.0, 99.5), 5)
    assert z.state == "mitigated"
    z2 = transition_fvg(z, bar(6, 99, 99.5, 98, 98.5), 6)
    assert z2.state == "mitigated" and z2.touches == z.touches

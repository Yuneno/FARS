"""Tests del ZoneEngine (Z4): append-only, determinismo, prefix invariance y contexto.

Prefix invariance (spec 10.1): para cada t, las zonas CREADAS por el engine
sobre la historia completa con available_bar_index < t deben coincidir
(id + bounds + available_at) con las del engine sobre el prefijo history[:t].
Los estados futuros pueden evolucionar con barras futuras (permitido por la
spec); la geometria y la existencia NO.
"""
import random
from datetime import date, datetime, timedelta

import pytest

from src.zones.engine import ZoneEngine
from src.zones.fvg import fvg_zones_from_bars

T0 = datetime(2024, 1, 2, 9, 30)


def make_bars(n, seed=42):
    rng = random.Random(seed)
    bars = []
    price = 100.0
    for i in range(n):
        drift = rng.uniform(-0.6, 0.6) + 0.015 * ((i // 7) % 2)
        o = price
        c = o + drift
        hi = max(o, c) + rng.uniform(0.1, 0.9)
        lo = min(o, c) - rng.uniform(0.1, 0.9)
        bars.append({"open": o, "high": hi, "low": lo, "close": c,
                     "timestamp": (T0 + timedelta(minutes=5 * i)).isoformat()})
        price = c
    return bars


def test_append_only_shrink_rejected():
    bars = make_bars(60)
    eng = ZoneEngine(symbol="MNQ", timeframe="M5")
    eng.update(bars)
    with pytest.raises(ValueError):
        eng.update(bars[:50])


def test_append_only_divergent_prefix_rejected():
    bars = make_bars(60)
    eng = ZoneEngine(symbol="MNQ", timeframe="M5")
    eng.update(bars[:30])
    diverged = make_bars(45, seed=7)
    with pytest.raises(ValueError):
        eng.update(diverged)


def test_determinism_two_engines():
    bars = make_bars(80)
    a, b = ZoneEngine(symbol="MNQ", timeframe="M5"), ZoneEngine(symbol="MNQ", timeframe="M5")
    a.update(bars)
    b.update(bars)
    assert [(z.zone_id, z.state, z.lower, z.upper) for z in a.all_zones()] == \
           [(z.zone_id, z.state, z.lower, z.upper) for z in b.all_zones()]


def test_prefix_invariance_geometry():
    """Existencia + availability identicas para toda zona; bounds identicos solo
    para FVG (las zonas de liquidez PUEDEN expandirse con pivotes futuros —
    permitido por la spec: fortalecer sin retroceder availability)."""
    bars = make_bars(120)
    full = ZoneEngine(symbol="MNQ", timeframe="M5")
    full.update(bars)
    for t in (30, 60, 90, 120):
        pref = ZoneEngine(symbol="MNQ", timeframe="M5")
        pref.update(bars[:t])
        full_avail, pref_all = {}, {}
        for z in full.all_zones():
            idx = z.metadata.get("available_bar_index")
            if idx is not None and idx < t:
                full_avail[z.zone_id] = (z.available_at, z.zone_type,
                                         (round(z.lower, 6), round(z.upper, 6)) if z.zone_type == "fvg" else None)
        for z in pref.all_zones():
            pref_all[z.zone_id] = (z.available_at, z.zone_type,
                                   (round(z.lower, 6), round(z.upper, 6)) if z.zone_type == "fvg" else None)
        assert set(full_avail) == set(pref_all), f"existencia rota en t={t}"
        for zid in full_avail:
            assert full_avail[zid][0] == pref_all[zid][0], f"available_at roto en t={t} {zid}"
            assert full_avail[zid][1] == pref_all[zid][1], f"tipo roto en t={t} {zid}"
            if full_avail[zid][2] is not None:  # FVG: bounds inmutables
                assert full_avail[zid][2] == pref_all[zid][2], f"bounds FVG rotos en t={t} {zid}"


def test_incremental_equals_block():
    bars = make_bars(100)
    block = ZoneEngine(symbol="MNQ", timeframe="M5")
    block.update(bars)
    inc = ZoneEngine(symbol="MNQ", timeframe="M5")
    for i in range(5, 101, 5):  # prefijos crecientes (contrato append-only del engine)
        inc.update(bars[:i])
    assert [(z.zone_id, z.state) for z in block.active_zones()] == \
           [(z.zone_id, z.state) for z in inc.active_zones()]


def test_context_shape_and_types():
    bars = make_bars(80)
    eng = ZoneEngine(symbol="MNQ", timeframe="M5")
    eng.update(bars)
    ctx = eng.context(100.0, atr=1.5)
    expected = {"inside_bullish_fvg", "inside_bearish_fvg", "fvg_age_bars",
                "fvg_fill_pct", "distance_to_buyside_liquidity_pts",
                "distance_to_sellside_liquidity_pts", "distance_to_buyside_liquidity_atr",
                "distance_to_sellside_liquidity_atr", "liquidity_touch_count",
                "liquidity_swept", "inside_sr_zone", "support_zone_strength",
                "inside_ote_zone", "premium_discount_state", "fvg_liquidity_overlap",
                "zone_overlap_count", "nearest_zone_type"}
    assert set(ctx) == expected
    for v in ctx.values():
        assert v is None or isinstance(v, (bool, int, float, str))


def test_engineered_fvg_flow_in_engine():
    def bar(i, o, h, l, c):
        return {"open": o, "high": h, "low": l, "close": c,
                "timestamp": (T0 + timedelta(minutes=5 * i)).isoformat()}
    bars = [bar(0, 99, 100, 98.5, 99.5), bar(1, 99.8, 103, 99.5, 102.5),
            bar(2, 103, 104, 101.5, 102), bar(3, 102, 102.2, 100.6, 101),
            bar(4, 100.8, 101.2, 100.2, 100.4)]  # sin bar 5: el FVG sigue vivo (touched/partial)
    eng = ZoneEngine(symbol="MNQ", timeframe="M5")
    eng.update(bars)
    ctx = eng.context(100.8)
    assert ctx["nearest_zone_type"] == "fvg"
    assert ctx["inside_bullish_fvg"] is True  # 100.8 dentro de [100, 101.5]
    assert eng.n_zones >= 1


def test_engine_accepts_bar_objects():
    """El adapter normaliza objetos con atributos (Bar) — bug real cazado con MNQ canonico."""
    from collections import namedtuple
    Bar = namedtuple("Bar", ["open", "high", "low", "close", "timestamp"])
    bars = [Bar(99, 100, 98.5, 99.5, T0), Bar(99.8, 103, 99.5, 102.5, T0),
            Bar(103, 104, 101.5, 102, T0)]
    zones = fvg_zones_from_bars(bars, symbol="MNQ", timeframe="M5")
    assert len(zones) == 1 and zones[0].lower == 100.0


def test_serialization_roundtrip_zones():
    bars = make_bars(60)
    eng = ZoneEngine(symbol="MNQ", timeframe="M5")
    eng.update(bars)
    for z in eng.all_zones():
        assert z.to_dict()["zone_id"] == z.zone_id


def test_zone_engine_rejects_unknown_market_when_session_pools_true():
    with pytest.raises(ValueError, match="unknown market"):
        ZoneEngine(symbol="UNKNOWN_XYZ", timeframe="M5", session_pools=True)


def test_zone_engine_active_and_nearest_queries_with_session_level():
    d1 = date(2024, 1, 8)
    d2 = date(2024, 1, 9)
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    bars = [
        {"open": 100, "high": 105, "low": 95, "close": 100, "timestamp": datetime(2024, 1, 8, 9, 30, tzinfo=ny).isoformat()},
        {"open": 100, "high": 102, "low": 98, "close": 101, "timestamp": datetime(2024, 1, 8, 15, 55, tzinfo=ny).isoformat()},
        {"open": 101, "high": 103, "low": 100, "close": 102, "timestamp": datetime(2024, 1, 8, 18, 0, tzinfo=ny).isoformat()},
    ]
    eng = ZoneEngine(symbol="MNQ", timeframe="M5", session_pools=True)
    eng.update(bars)

    sl_active = eng.active_zones(zone_type="session_level")
    assert len(sl_active) == 2  # PDH, PDL
    nearest_sl = eng.nearest_zones(104.0, zone_type="session_level", limit=1)
    assert len(nearest_sl) == 1
    assert nearest_sl[0].zone_type == "session_level"
    assert nearest_sl[0].metadata["anchor_type"] == "prev_day_high"


def test_zone_engine_context_with_session_pools_true():
    bars = make_bars(80)
    eng = ZoneEngine(symbol="MNQ", timeframe="M5", session_pools=True)
    eng.update(bars)
    ctx = eng.context(100.0, atr=1.5)
    # Debe tener las 17 keys base + las 16 keys de sesión = 33 keys
    assert len(ctx) == 33
    session_keys = {
        "distance_to_prev_day_high_pts", "distance_to_prev_day_high_atr",
        "distance_to_prev_day_low_pts", "distance_to_prev_day_low_atr",
        "distance_to_d20_high_pts", "distance_to_d20_high_atr",
        "distance_to_d20_low_pts", "distance_to_d20_low_atr",
        "distance_to_overnight_high_pts", "distance_to_overnight_high_atr",
        "distance_to_overnight_low_pts", "distance_to_overnight_low_atr",
        "inside_prev_day_range", "prev_day_range_position",
        "overnight_swept_prev_day_high", "overnight_swept_prev_day_low",
    }
    assert session_keys.issubset(set(ctx))
    for k in session_keys:
        v = ctx[k]
        assert v is None or isinstance(v, (bool, int, float, str))


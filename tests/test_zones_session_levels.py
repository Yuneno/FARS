"""Tests obligatorios del Bloque F · Z3-b — Anclajes de sesión (spec §11, §13 y encargo §4).

1. Prefix invariance para session_level (existencia + availability + bounds congelados).
2. Sin lookahead: PDH/PDL de S no existe en S-1; D20 excluye sesión en curso (test trampa).
3. ONH/ONL: no existe antes de RTH; barra overnight no lo crea; nuevo extremo en RTH no mueve bounds.
4. Rollover vía session_date: viernes 17:00 ET -> lunes, y transiciones DST.
5. Lifecycle: touched -> swept -> broken determinista sobre session_level.
6. Retiro: al publicarse juego nuevo, el anterior pasa a expired; zone_id nuevos distintos y deterministas.
7. Incremental == bloque para session_level.
8. session_pools=False => paridad exacta con el oráculo medido (baseline intacto).
9. Contexto numérico a mano: distancias positivas, range position y sweep flags calculados a mano.
10. Serialización roundtrip de Zone con zone_type='session_level'.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from lab_artifacts.run_c1_walkforward import ZIP_PATH, load_canonical_m5
from src.backtest.markets import MNQ, get_market_spec
from src.session_calendar import NY_TZ, session_date
from src.zones.engine import ZoneEngine
from src.zones.models import Zone, make_zone_id
from src.zones.session_levels import CompletedRthSession, SessionLevelsBuilder


def make_dt(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=NY_TZ)


def make_bar(
    ts: datetime,
    o: float,
    h: float,
    l: float,
    c: float,
    v: float = 100.0,
) -> dict:
    return {
        "timestamp": ts.isoformat(),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
    }


# =============================================================================
# 1. Prefix Invariance para session_level
# =============================================================================
def test_prefix_invariance_session_levels():
    """Para todo t, las zonas session_level disponibles en t coinciden exactamente
    en id, availability y bounds (congelados) entre la corrida completa y el prefijo [:t]."""
    bars = []
    d1 = date(2024, 1, 8)  # Lunes
    d2 = date(2024, 1, 9)  # Martes

    # Sesión 1: RTH 09:30 a 16:00
    cur = make_dt(d1, 9, 30)
    end1 = make_dt(d1, 16, 0)
    price = 100.0
    while cur < end1:
        bars.append(make_bar(cur, price, price + 1.0, price - 1.0, price + 0.2))
        cur += timedelta(minutes=5)
        price += 0.1

    # Sesión 2: Overnight (18:00 d1 a 09:25 d2) + RTH (09:30 a 16:00 d2)
    cur = make_dt(d1, 18, 0)
    end_on = make_dt(d2, 9, 30)
    while cur < end_on:
        bars.append(make_bar(cur, price, price + 0.5, price - 0.5, price + 0.1))
        cur += timedelta(minutes=15)
        price += 0.05

    end2 = make_dt(d2, 16, 0)
    while cur < end2:
        bars.append(make_bar(cur, price, price + 1.5, price - 1.0, price + 0.3))
        cur += timedelta(minutes=5)
        price += 0.1

    full = ZoneEngine(symbol="MNQ", timeframe="M5", session_pools=True)
    full.update(bars)

    cutoffs = [20, 50, 90, 120, len(bars)]
    for t in cutoffs:
        pref = ZoneEngine(symbol="MNQ", timeframe="M5", session_pools=True)
        pref.update(bars[:t])

        full_sl = {}
        for z in full.all_zones():
            if z.zone_type == "session_level":
                idx = z.metadata.get("available_bar_index")
                if idx is not None and idx < t:
                    full_sl[z.zone_id] = (z.available_at, z.lower, z.upper, z.midpoint)

        pref_sl = {}
        for z in pref.all_zones():
            if z.zone_type == "session_level":
                pref_sl[z.zone_id] = (z.available_at, z.lower, z.upper, z.midpoint)

        assert set(full_sl) == set(pref_sl), f"Discrepancia en existencia en cutoff {t}"
        for zid in full_sl:
            assert full_sl[zid] == pref_sl[zid], f"Discrepancia de bounds/availability en {zid}"


# =============================================================================
# 2. Sin lookahead y Test Trampa (D20 excluye sesión en curso)
# =============================================================================
def test_no_lookahead_session_levels():
    """El PDH/PDL de S no existe en S-1. D20 excluye estrictamente la sesión en curso."""
    d1 = date(2024, 1, 8)
    d2 = date(2024, 1, 9)

    bars_s1 = [
        make_bar(make_dt(d1, 9, 30), 100, 110, 95, 105),
        make_bar(make_dt(d1, 15, 55), 105, 108, 100, 102),
    ]

    eng = ZoneEngine(symbol="MNQ", timeframe="M5", session_pools=True, d20_sessions=1)
    eng.update(bars_s1)

    # Durante S-1, no puede existir PDH de S (que sería d2)
    active_s_types = [z.metadata.get("anchor_type") for z in eng.all_zones() if z.zone_type == "session_level"]
    assert "prev_day_high" not in active_s_types

    # Apertura de S2 en rollover 18:00
    bar_s2_rollover = make_bar(make_dt(d1, 18, 0), 102, 103, 101, 102)
    eng.update([*bars_s1, bar_s2_rollover])

    # Ahora sí existe PDH basado en S1 (high=110, low=95)
    pdh = [z for z in eng.active_zones(zone_type="session_level") if z.metadata.get("anchor_type") == "prev_day_high"][0]
    assert pdh.midpoint == 110.0

    # TEST TRAMPA: en S2 el precio tiene mecha a 9999.0 durante RTH, pero cierra dentro (105.0)
    # D20 (con d20_sessions=1) NO debe cambiar a 9999.0 porque S2 está en curso
    bar_s2_rth1 = make_bar(make_dt(d2, 9, 30), 102, 9999.0, 100, 105.0)
    eng.update([*bars_s1, bar_s2_rollover, bar_s2_rth1])

    d20h = [z for z in eng.all_zones() if z.metadata.get("anchor_type") == "d20_high"][0]
    assert d20h.midpoint == 110.0  # Mantiene el máximo de S1, NO el 9999 de la sesión en curso


# =============================================================================
# 3. ONH / ONL congelados al abrir RTH
# =============================================================================
def test_overnight_levels_frozen_at_rth_open():
    """ONH/ONL no existen antes de RTH. Al abrir RTH nacen y quedan congelados ante extremos nuevos."""
    d1 = date(2024, 1, 8)
    d2 = date(2024, 1, 9)

    bars = [
        # S1 completada
        make_bar(make_dt(d1, 9, 30), 100, 105, 95, 100),
        make_bar(make_dt(d1, 15, 55), 100, 102, 98, 101),
        # S2 overnight
        make_bar(make_dt(d1, 18, 0), 101, 103, 100, 102),
        make_bar(make_dt(d2, 4, 0), 102, 108, 101, 106),  # ONH = 108
        make_bar(make_dt(d2, 9, 25), 106, 107, 99, 100),   # ONL = 99
    ]

    eng = ZoneEngine(symbol="MNQ", timeframe="M5", session_pools=True)
    eng.update(bars)

    # Antes de 09:30, ONH/ONL no deben existir
    assert not any(z.metadata.get("anchor_type") in ("overnight_high", "overnight_low") for z in eng.active_zones())

    # Apertura RTH 09:30
    bar_rth_open = make_bar(make_dt(d2, 9, 30), 100, 104, 100, 103)
    eng.update([*bars, bar_rth_open])

    onh = [z for z in eng.active_zones() if z.metadata.get("anchor_type") == "overnight_high"][0]
    onl = [z for z in eng.active_zones() if z.metadata.get("anchor_type") == "overnight_low"][0]
    assert onh.midpoint == 108.0
    assert onl.midpoint == 99.0
    initial_upper = onh.upper

    # Nueva barra en RTH supera 108 (llega a 120): los bounds NO se mueven
    bar_rth_higher = make_bar(make_dt(d2, 9, 35), 103, 120, 102, 115)
    eng.update([*bars, bar_rth_open, bar_rth_higher])

    onh_after = [z for z in eng.all_zones() if z.zone_id == onh.zone_id][0]
    assert onh_after.upper == initial_upper
    assert onh_after.midpoint == 108.0


# =============================================================================
# 4. Rollover vía session_date (Viernes 17:00 -> Lunes y DST)
# =============================================================================
def test_session_calendar_rollover_integration():
    """Verifica integración con session_date: viernes 17:00 ET rueda a lunes y DST."""
    d_fri = date(2026, 9, 4)
    d_mon = date(2026, 9, 7)

    # Viernes 15:55 (sesión viernes, dentro de RTH 09:30-16:00)
    bar_fri_rth = make_bar(make_dt(d_fri, 15, 55), 100, 105, 95, 102)
    # Viernes 18:00 (sesión lunes!)
    bar_fri_night = make_bar(make_dt(d_fri, 18, 0), 102, 103, 101, 102)

    builder = SessionLevelsBuilder(symbol="MNQ", timeframe="M5", market_spec=MNQ)
    builder.on_bar(bar_fri_rth, 0, atr=5.0)
    assert builder.current_session_date == d_fri

    new_z, ret_z = builder.on_bar(bar_fri_night, 1, atr=5.0)
    assert builder.current_session_date == d_mon
    # La sesión rodó a lunes y se publicaron anclajes anclados al lunes
    assert any(z.metadata["session_date"] == d_mon.isoformat() for z in new_z)

    # Transición DST (otoño): 2026-10-30 es viernes antes de fall back
    d_fall_fri = date(2026, 10, 30)
    d_fall_mon = date(2026, 11, 2)
    bar_pre = make_bar(make_dt(d_fall_fri, 16, 59), 100, 101, 99, 100)
    bar_post = make_bar(make_dt(d_fall_fri, 17, 5), 100, 101, 99, 100)
    assert session_date(datetime.fromisoformat(bar_pre["timestamp"])) == d_fall_fri
    assert session_date(datetime.fromisoformat(bar_post["timestamp"])) == d_fall_mon


# =============================================================================
# 5. Lifecycle: touched -> swept -> broken determinista
# =============================================================================
def test_lifecycle_touched_swept_broken_session_level():
    """Ciclo de vida determinista sobre Zone(zone_type='session_level')."""
    d1 = date(2024, 1, 8)
    d2 = date(2024, 1, 9)

    # PDH con nivel 100.0, h=1.0 -> banda [99.0, 101.0]
    bars = [
        make_bar(make_dt(d1, 9, 30), 100, 100.0, 90.0, 95.0),
        make_bar(make_dt(d1, 15, 55), 95, 96.0, 94.0, 95.0),
        make_bar(make_dt(d1, 18, 0), 95, 96.0, 94.0, 95.0),
    ]

    eng = ZoneEngine(
        symbol="MNQ",
        timeframe="M5",
        session_pools=True,
        tick_size=0.25,
        session_tick_tolerance=4,   # 0.25 * 4 = 1.0
        session_atr_tolerance=0.10,
    )
    eng.update(bars)

    pdh = [z for z in eng.active_zones() if z.metadata.get("anchor_type") == "prev_day_high"][0]
    assert pdh.state == "active"
    assert (pdh.lower, pdh.upper) == (99.0, 101.0)

    # 1. Touched: mecha entra a la zona (high 99.5), pero cierra abajo (98.0)
    b_touch = make_bar(make_dt(d2, 9, 30), 96.0, 99.5, 96.0, 98.0)
    eng.update([*bars, b_touch])
    z1 = [z for z in eng.active_zones() if z.zone_id == pdh.zone_id][0]
    assert z1.state == "touched"
    assert z1.touches == 1

    # 2. Swept: mecha cruza el extremo lejano (high 102.0 > 101.0) y cuerpo cierra dentro (100.0)
    b_sweep = make_bar(make_dt(d2, 9, 35), 98.0, 102.0, 97.5, 100.0)
    eng.update([*bars, b_touch, b_sweep])
    z2 = [z for z in eng.active_zones() if z.zone_id == pdh.zone_id][0]
    assert z2.state == "swept"
    assert z2.touches == 2

    # 3. Broken: cierre cruza completo el extremo lejano (close 102.5 > 101.0)
    b_break = make_bar(make_dt(d2, 9, 40), 100.0, 103.0, 99.5, 102.5)
    eng.update([*bars, b_touch, b_sweep, b_break])
    z3 = [z for z in eng.all_zones() if z.zone_id == pdh.zone_id][0]
    assert z3.state == "broken"
    assert z3.is_terminal()
    # Ya no está en activas
    assert not any(z.zone_id == pdh.zone_id for z in eng.active_zones())


# =============================================================================
# 6. Retiro al publicar juego nuevo -> expired
# =============================================================================
def test_retirement_on_new_session():
    """Al iniciar una nueva sesión, el juego anterior pasa a expired y los zone_id son deterministas y distintos."""
    d1 = date(2024, 1, 8)
    d2 = date(2024, 1, 9)
    d3 = date(2024, 1, 10)

    bars = [
        # S1
        make_bar(make_dt(d1, 9, 30), 100, 105, 95, 100),
        make_bar(make_dt(d1, 15, 55), 100, 102, 98, 101),
        # S2 rollover
        make_bar(make_dt(d1, 18, 0), 101, 102, 100, 101),
        # S2 RTH (high 104, low 90, close 102 - no rompe PDH de S1 que es 105)
        make_bar(make_dt(d2, 9, 30), 101, 104, 90, 102),
        make_bar(make_dt(d2, 15, 55), 102, 103, 100, 101),
    ]

    eng = ZoneEngine(symbol="MNQ", timeframe="M5", session_pools=True)
    eng.update(bars)

    pdh_s2 = [z for z in eng.active_zones() if z.metadata.get("anchor_type") == "prev_day_high"][0]
    assert pdh_s2.metadata["session_date"] == d2.isoformat()

    # S3 rollover
    bar_s3_rollover = make_bar(make_dt(d2, 18, 0), 101, 102, 100, 101)
    eng.update([*bars, bar_s3_rollover])

    # El PDH de S2 debe estar expired
    pdh_s2_ret = [z for z in eng.all_zones() if z.zone_id == pdh_s2.zone_id][0]
    assert pdh_s2_ret.state == "expired"
    assert pdh_s2_ret.is_terminal()

    # Nuevo PDH de S3 debe estar activo y tener zone_id distinto
    pdh_s3 = [z for z in eng.active_zones() if z.metadata.get("anchor_type") == "prev_day_high"][0]
    assert pdh_s3.metadata["session_date"] == d3.isoformat()
    assert pdh_s3.zone_id != pdh_s2.zone_id
    assert pdh_s3.midpoint == 104.0


# =============================================================================
# 7. Incremental == Bloque para sesiones
# =============================================================================
def test_incremental_equals_block_session_levels():
    """Engine alimentado barra a barra produce exactamente las mismas zonas y estados que en bloque."""
    d1 = date(2024, 1, 8)
    d2 = date(2024, 1, 9)

    bars = [
        make_bar(make_dt(d1, 9, 30), 100, 105, 95, 100),
        make_bar(make_dt(d1, 15, 55), 100, 102, 98, 101),
        make_bar(make_dt(d1, 18, 0), 101, 103, 100, 102),
        make_bar(make_dt(d2, 9, 30), 102, 106, 98, 104),
        make_bar(make_dt(d2, 10, 0), 104, 107, 101, 102),
    ]

    block = ZoneEngine(symbol="MNQ", timeframe="M5", session_pools=True)
    block.update(bars)

    inc = ZoneEngine(symbol="MNQ", timeframe="M5", session_pools=True)
    for i in range(1, len(bars) + 1):
        inc.update(bars[:i])

    assert [(z.zone_id, z.state, z.touches) for z in block.active_zones()] == \
           [(z.zone_id, z.state, z.touches) for z in inc.active_zones()]
    assert [(z.zone_id, z.state) for z in block.all_zones()] == \
           [(z.zone_id, z.state) for z in inc.all_zones()]


# =============================================================================
# 8. session_pools=False => Paridad exacta con el Oráculo (§5)
# =============================================================================
def test_session_pools_false_oracle_parity():
    """Con session_pools=False, la salida en 5.000 barras es bit a bit idéntica al baseline."""
    bars, sha = load_canonical_m5(ZIP_PATH)
    bars5k = bars[:5000]

    eng = ZoneEngine(symbol="MNQ", timeframe="M5", tick_size=0.25, session_pools=False)
    eng.update(bars5k)

    assert eng.n_zones == 382
    active = eng.active_zones()
    assert len(active) == 220

    by_type = {}
    for z in active:
        by_type[z.zone_type] = by_type.get(z.zone_type, 0) + 1
    assert by_type == {"liquidity": 198, "fvg": 22}

    ctx = eng.context(bars5k[-1].close, atr=5.0)
    assert ctx["inside_bullish_fvg"] is False
    assert ctx["inside_bearish_fvg"] is False
    assert ctx["fvg_age_bars"] == 129
    assert ctx["fvg_fill_pct"] == 1.0
    assert ctx["distance_to_buyside_liquidity_pts"] == 0.25
    assert ctx["distance_to_buyside_liquidity_atr"] == 0.05
    assert ctx["distance_to_sellside_liquidity_pts"] == 0.0
    assert ctx["distance_to_sellside_liquidity_atr"] == 0.0
    assert ctx["liquidity_touch_count"] == 21
    assert ctx["liquidity_swept"] is True
    assert ctx["fvg_liquidity_overlap"] is True
    assert ctx["zone_overlap_count"] == 154
    assert ctx["nearest_zone_type"] == "liquidity"

    # Con flag OFF, no hay features de sesión en context() (mantiene la forma original exacta de 17 keys)
    assert "distance_to_prev_day_high_pts" not in ctx


# =============================================================================
# 9. Contexto numérico a mano
# =============================================================================
def test_context_manual_numerical_values():
    """Verificación de cálculos numéricos hechos a mano en context()."""
    d1 = date(2024, 1, 8)
    d2 = date(2024, 1, 9)

    # S1: High = 100.0, Low = 80.0
    # S2 Overnight: High = 105.0 (> PDH), Low = 85.0
    bars = [
        make_bar(make_dt(d1, 9, 30), 90.0, 100.0, 80.0, 90.0),
        make_bar(make_dt(d1, 15, 55), 90.0, 95.0, 85.0, 90.0),
        make_bar(make_dt(d1, 18, 0), 90.0, 92.0, 89.0, 91.0),
        make_bar(make_dt(d2, 4, 0), 91.0, 105.0, 85.0, 95.0),
        make_bar(make_dt(d2, 9, 30), 95.0, 96.0, 94.0, 95.0),  # RTH abre
    ]

    eng = ZoneEngine(
        symbol="MNQ",
        timeframe="M5",
        session_pools=True,
        tick_size=0.25,
        session_tick_tolerance=4,   # 0.25 * 4 = 1.0
        session_atr_tolerance=0.10,
    )
    eng.update(bars)

    # PDH: midpoint 100.0, banda [99.0, 101.0]
    # PDL: midpoint 80.0, banda [79.0, 81.0]
    # ONH: midpoint 105.0, banda [104.0, 106.0]
    # ONL: midpoint 85.0, banda [84.0, 86.0]

    # Caso A: Precio 105.0 (por encima de PDH, dentro de ONH) con atr=10.0
    ctx_a = eng.context(105.0, atr=10.0)
    # Distancia a PDH: 105.0 - upper(101.0) = 4.0
    assert ctx_a["distance_to_prev_day_high_pts"] == 4.0
    assert ctx_a["distance_to_prev_day_high_atr"] == 0.40
    # Distancia a ONH: dentro de [104, 106] -> 0.0
    assert ctx_a["distance_to_overnight_high_pts"] == 0.0
    assert ctx_a["inside_prev_day_range"] is False  # 105 > 100
    assert ctx_a["prev_day_range_position"] == 1.0  # clamp a 1.0
    assert ctx_a["overnight_swept_prev_day_high"] is True  # 105.0 > 100.0
    assert ctx_a["overnight_swept_prev_day_low"] is False  # 85.0 no es < 80.0

    # Caso B: Precio 90.0 (en medio del rango)
    ctx_b = eng.context(90.0, atr=10.0)
    assert ctx_b["inside_prev_day_range"] is True
    # Position: (90 - 80) / (100 - 80) = 10 / 20 = 0.50
    assert ctx_b["prev_day_range_position"] == 0.50
    # Distancia a PDH: lower(99.0) - 90.0 = 9.0
    assert ctx_b["distance_to_prev_day_high_pts"] == 9.0
    # Distancia a PDL: 90.0 - upper(81.0) = 9.0
    assert ctx_b["distance_to_prev_day_low_pts"] == 9.0


# =============================================================================
# 10. Serialización roundtrip
# =============================================================================
def test_serialization_roundtrip_session_levels():
    """Zone con zone_type='session_level' se serializa y deserializa sin pérdida."""
    now = datetime(2024, 1, 8, 9, 30, tzinfo=NY_TZ)
    zid = make_zone_id("session_level", "MNQ", "M5", "long", now, ("2024-01-08", "prev_day_high"))
    z = Zone(
        zone_id=zid,
        zone_type="session_level",
        symbol="MNQ",
        timeframe="M5",
        lower=100.0,
        upper=102.0,
        midpoint=101.0,
        direction="long",
        pattern_time=now,
        available_at=now,
        state="active",
        touches=1,
        strength=1.0,
        source_bar_ids=(10,),
        metadata={
            "anchor_type": "prev_day_high",
            "session_date": "2024-01-08",
            "available_bar_index": 12,
            "source_bar_ids": (10,),
            "n_bars_window": 78,
            "tolerance": 1.0,
            "zone_width": 2.0,
        },
    )

    d = z.to_dict()
    assert d["zone_type"] == "session_level"
    assert d["metadata"]["anchor_type"] == "prev_day_high"

    restored = Zone.from_dict(d)
    assert restored.zone_id == z.zone_id
    assert restored.zone_type == "session_level"
    assert restored.lower == z.lower
    assert restored.metadata == z.metadata

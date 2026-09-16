"""Tests de liquidity pools (Z3): clustering determinista, causalidad y lifecycle."""
from datetime import datetime, timedelta

import pytest

from src.zones.liquidity import (
    ConfirmedPivot,
    LiquidityClusterer,
    _cluster_levels,
    liquidity_tolerance,
    transition_liquidity,
)
from src.zones.models import Zone

T0 = datetime(2024, 1, 2, 9, 30)


def ts(i):
    return T0 + timedelta(minutes=5 * i)


def pivot(idx, level, side="high"):
    return ConfirmedPivot(idx, level, side, idx + 3, ts(idx + 3))


def bar(i, o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c, "timestamp": ts(i).isoformat()}


def test_tolerance_formula():
    assert liquidity_tolerance(0.25, 1.5) == max(0.25 * 4, 1.5 * 0.10)  # 1.0
    assert liquidity_tolerance(0.25, 30.0) == 3.0


def test_cluster_levels_greedy():
    assert _cluster_levels([100, 101, 102, 110], 1.0, 2) == [[100, 101, 102]]
    assert _cluster_levels([100, 101, 102, 110], 1.0, 4) == []
    assert _cluster_levels([], 1.0, 2) == []
    assert _cluster_levels([100, 102], 1.0, 2) == []


def test_zone_requires_min_touches_and_is_causal():
    cl = LiquidityClusterer(symbol="MNQ", timeframe="M5", side="high",
                            zone_type="eqh", tolerance=1.0, min_touches=2)
    assert cl.zones() == ()
    cl.add(pivot(0, 100.0), bar_index=3)
    assert cl.zones() == ()  # 1 pivot no alcanza
    cl.add(pivot(5, 100.5), bar_index=8)
    zones = cl.zones()
    assert len(zones) == 1
    z = zones[0]
    assert z.lower == 100.0 - 1.0 and z.upper == 100.5 + 1.0
    assert z.available_at == ts(8)  # confirmacion del 2do pivot
    assert z.metadata["available_bar_index"] == 8


def test_zone_grows_without_backdating():
    cl = LiquidityClusterer(symbol="MNQ", timeframe="M5", side="high",
                            zone_type="eqh", tolerance=1.0, min_touches=2)
    cl.add(pivot(0, 100.0), bar_index=3)
    cl.add(pivot(5, 100.5), bar_index=8)
    zid = cl.zones()[0].zone_id
    avail = cl.zones()[0].available_at
    cl.add(pivot(10, 101.0), bar_index=13)  # mismo cluster (cadena greedy)
    zones = cl.zones()
    assert len(zones) == 1
    assert zones[0].zone_id == zid  # id estable
    assert zones[0].available_at == avail  # sin backdating
    assert zones[0].upper == 101.0 + 1.0  # crecio
    assert zones[0].metadata["n_pivots"] == 3


def test_lifecycle_touched_swept_broken():
    z = Zone(zone_id="z", zone_type="liquidity", symbol="MNQ", timeframe="M5",
             lower=100.0, upper=101.0, midpoint=100.5, direction="long",
             pattern_time=ts(0), available_at=ts(3),
             metadata={"available_bar_index": 3})
    z1 = transition_liquidity(z, bar(4, 100.6, 100.9, 100.5, 100.7), 4)  # mecha entra sin cruzar
    assert z1.state == "touched" and z1.touches == 1
    # mecha cruza el extremo lejano y cuerpo cierra de vuelta dentro -> swept
    z2 = transition_liquidity(z1, bar(5, 100.7, 102.0, 100.5, 100.9), 5)
    assert z2.state == "swept"
    # cierre cruza completo -> broken
    z3 = transition_liquidity(z2, bar(6, 101.2, 102.5, 101.3, 102.0), 6)
    assert z3.state == "broken"


def test_sellside_mirror():
    z = Zone(zone_id="z", zone_type="liquidity", symbol="MNQ", timeframe="M5",
             lower=99.0, upper=100.0, midpoint=99.5, direction="short",
             pattern_time=ts(0), available_at=ts(3),
             metadata={"available_bar_index": 3})
    z1 = transition_liquidity(z, bar(4, 99.2, 99.8, 99.1, 99.5), 4)
    assert z1.state == "touched"
    z2 = transition_liquidity(z1, bar(5, 99.0, 99.5, 98.0, 99.3), 5)
    assert z2.state == "swept"
    z3 = transition_liquidity(z2, bar(6, 99.2, 99.4, 99.0, 98.6), 6)
    assert z3.state == "broken"  # close < lower


def test_expiry_liquidity():
    z = Zone(zone_id="z", zone_type="liquidity", symbol="MNQ", timeframe="M5",
             lower=100.0, upper=101.0, midpoint=100.5, direction="long",
             pattern_time=ts(0), available_at=ts(3),
             metadata={"available_bar_index": 3})
    z1 = transition_liquidity(z, bar(30, 99.0, 99.8, 98.5, 99.4), 30, max_age_bars=20)
    assert z1.state == "expired"

"""Liquidity pools (Z3): EQH/EQL y pools buy/sell-side por clustering 1D determinista.

Pieza NUEVA (EQH/EQL no existe en FARS ni en Kai — spec seccion 5.2). Los pools
se construyen SOLO con swings ya confirmados (``find_swings`` canonico).

Clustering determinista (spec 5.3 Phase A):
1. ordenar niveles confirmados por precio;
2. agrupar vecinos con distancia <= tolerance (greedy);
3. exigir ``min_touches`` (default 2);
4. bounds = [min(levels) - tolerance, max(levels) + tolerance], midpoint = mediana.

Causalidad: la zona no existe antes de confirmarse el ``min_touches``-esimo
pivot. Al unirse pivotes nuevos la zona CRECE sin retroceder ``available_at``
(su zone_id se ancla al primer pivot y es estable).

Tipos de ancla de sesion (PREV_DAY_*, D20, overnight) quedan declarados como
Z3-b: requieren integracion con ``src/session_calendar.py`` y se implementan en
la siguiente iteracion — NO se fingen en V1.

Lifecycle: active -> touched (mecha entra) -> swept (mecha cruza el extremo
lejano y el cuerpo cierra de vuelta dentro) | broken (cierre cruza completo).
expired: caducidad causal por edad en barras (opcional).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Any, Sequence

from src.zones.models import TERMINAL_STATES, Zone, make_zone_id

POOL_SIDE = {"eqh": ("high", "long"), "buyside": ("high", "long"),
              "eql": ("low", "short"), "sellside": ("low", "short")}

DEFAULTS = dict(min_touches=2, tick_tolerance=4, atr_tolerance=0.10)


@dataclass(frozen=True)
class ConfirmedPivot:
    index: int
    level: float
    side: str  # "high" | "low"
    confirmed_at_index: int
    timestamp: datetime


def liquidity_tolerance(
    tick_size: float,
    atr: float,
    tick_tolerance: int = DEFAULTS["tick_tolerance"],
    atr_tolerance: float = DEFAULTS["atr_tolerance"],
) -> float:
    """tolerance = max(tick_size * tick_tolerance, atr * atr_tolerance) — spec 5.2."""
    return max(tick_size * tick_tolerance, atr * atr_tolerance)


def _cluster_levels(levels: Sequence[float], tolerance: float, min_touches: int) -> list[list[float]]:
    """Clustering greedy 1D: ordena y agrupa vecinos a distancia <= tolerance."""
    if not levels:
        return []
    clusters: list[list[float]] = []
    for lvl in sorted(levels):
        if clusters and lvl - max(clusters[-1]) <= tolerance:
            clusters[-1].append(lvl)
        else:
            clusters.append([lvl])
    return [c for c in clusters if len(c) >= min_touches]


class LiquidityClusterer:
    """Estado incremental: pivotes confirmados + zonas resultantes (append-only)."""

    def __init__(
        self,
        *,
        symbol: str,
        timeframe: str,
        side: str,  # "high" | "low"
        zone_type: str,  # "eqh" | "eql" | "buyside" | "sellside"
        tolerance: float,
        min_touches: int = DEFAULTS["min_touches"],
        lookback_bars: int = 4000,
    ) -> None:
        if (side, zone_type) not in {("high", "eqh"), ("high", "buyside"),
                                     ("low", "eql"), ("low", "sellside")}:
            raise ValueError(f"side/zone_type invalidos: {side}/{zone_type}")
        self.symbol, self.timeframe, self.side, self.zone_type = symbol, timeframe, side, zone_type
        self.tolerance, self.min_touches, self.lookback_bars = tolerance, min_touches, lookback_bars
        self._pivots: list[ConfirmedPivot] = []
        self._zones: dict[str, Zone] = {}

    def add(self, pivot: ConfirmedPivot, bar_index: int) -> None:
        self._pivots.append(pivot)
        cutoff = bar_index - self.lookback_bars
        self._pivots = [p for p in self._pivots if p.index >= cutoff]
        levels = [p.level for p in self._pivots]
        clusters = _cluster_levels(levels, self.tolerance, self.min_touches)
        rebuilt: dict[str, Zone] = {}
        direction = POOL_SIDE[self.zone_type][1]
        for cl in clusters:
            pivot_list = [p for p in self._pivots if p.level in cl]
            pivot_list.sort(key=lambda p: p.index)
            anchor = (pivot_list[0].index, pivot_list[0].level)
            zone_id = make_zone_id("liquidity", self.symbol, self.timeframe,
                                   direction, pivot_list[0].timestamp, anchor)
            lo, hi = min(cl) - self.tolerance, max(cl) + self.tolerance
            prev = self._zones.get(zone_id)
            pattern_time = pivot_list[0].timestamp  # primera evidencia (<= available_at siempre)
            if prev is None:
                available_at = pivot_list[-1].timestamp  # min_touches-esimo confirmado
                avail_bar = pivot_list[-1].confirmed_at_index
            else:
                available_at = prev.available_at
                avail_bar = prev.metadata["available_bar_index"]
            rebuilt[zone_id] = Zone(
                zone_id=zone_id, zone_type="liquidity",
                symbol=self.symbol, timeframe=self.timeframe,
                lower=lo, upper=hi, midpoint=median(cl),
                direction=direction,
                pattern_time=pattern_time, available_at=available_at,
                state=prev.state if prev else "active",
                touches=prev.touches if prev else 0,
                strength=len(pivot_list),
                source_bar_ids=tuple(p.index for p in pivot_list),
                metadata={"n_pivots": len(pivot_list),
                          "first_confirmed": pivot_list[0].timestamp.isoformat(),
                          "available_bar_index": avail_bar,
                          "tolerance": self.tolerance, "zone_width": hi - lo},
            )
        self._zones = rebuilt

    def zones(self) -> tuple[Zone, ...]:
        return tuple(self._zones.values())


def transition_liquidity(
    zone: Zone,
    bar: Any,
    bar_index: int,
    *,
    max_age_bars: int | None = None,
) -> Zone:
    """touched -> swept | broken, con determinismo causal. (bar = barra cerrada)."""
    if zone.is_terminal() or zone.zone_type != "liquidity":
        return zone
    if isinstance(bar, dict):
        lo, hi, cl = bar["low"], bar["high"], bar["close"]
    else:
        lo, hi, cl = bar.low, bar.high, bar.close
    in_zone = lo <= zone.upper and hi >= zone.lower
    new_state, touches = zone.state, zone.touches
    if in_zone:
        touches += 1
        if zone.state == "active":
            new_state = "touched"
    if new_state != "broken":
        if zone.direction == "long":  # pool de highs: extremo lejano = upper
            if hi > zone.upper and lo <= zone.upper:
                new_state = "swept" if zone.lower <= cl <= zone.upper else new_state
            if cl > zone.upper:
                new_state = "broken"
        else:  # pool de lows: extremo lejano = lower
            if lo < zone.lower and hi >= zone.lower:
                new_state = "swept" if zone.lower <= cl <= zone.upper else new_state
            if cl < zone.lower:
                new_state = "broken"
    if max_age_bars is not None and new_state not in TERMINAL_STATES:
        born = zone.metadata.get("available_bar_index", bar_index)
        if bar_index - born > max_age_bars:
            new_state = "expired"
    return Zone(
        zone_id=zone.zone_id, zone_type=zone.zone_type, symbol=zone.symbol,
        timeframe=zone.timeframe, lower=zone.lower, upper=zone.upper,
        midpoint=zone.midpoint, direction=zone.direction,
        pattern_time=zone.pattern_time, available_at=zone.available_at,
        state=new_state, touches=touches, strength=zone.strength,
        source_bar_ids=zone.source_bar_ids, metadata=zone.metadata,
    )

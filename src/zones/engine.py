"""ZoneEngine incremental (Z4) — motor causal de zonas para estrategias.

Consume UNA barra cerrada a la vez (append-only, rechaza historia divergente
igual que ``SmcFvgStrategy``). Replay incremental y backtest producen las
mismas zonas por construccion: cada barra nueva solo puede crear/actualizar
zonas con ``available_at`` >= su propio cierre.

Expone FVG, liquidity, S/R y order blocks. Volume voids (Z7) y las features
de Fibonacci que requieren un rango causal siguen en ``None``.

Features crudas, sin score magico (spec seccion 8): cada componente visible.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Sequence

from src.backtest.markets import get_market_spec
from src.detectors.pivots import find_swings
from src.zones.fvg import DEFAULT_MITIGATION, fvg_zones_from_bars, transition_fvg
from src.zones.liquidity import ConfirmedPivot, LiquidityClusterer, transition_liquidity
from src.zones.levels import SrPivot, SupportResistanceClusterer, transition_sr
from src.zones.models import Zone
from src.zones.order_blocks import OrderBlockBuilder
from src.zones.session_levels import SessionLevelsBuilder


def _fld(bar: Any, name: str) -> float:
    if isinstance(bar, dict):
        return bar[name]
    return getattr(bar, name)


def _ts(bar: Any) -> datetime:
    if isinstance(bar, dict):
        ts = bar["timestamp"]
        return datetime.fromisoformat(ts) if isinstance(ts, str) else ts
    return bar.timestamp


def _bar_sig(bar: Any) -> tuple:
    return (_ts(bar), _fld(bar, "open"), _fld(bar, "high"), _fld(bar, "low"), _fld(bar, "close"))


def _zone_dist(price: float, z: Zone) -> float:
    """Distancia al borde mas cercano (0 si el precio esta dentro)."""
    if price < z.lower:
        return z.lower - price
    if price > z.upper:
        return price - z.upper
    return 0.0


class ZoneEngine:
    def __init__(
        self,
        *,
        symbol: str = "",
        timeframe: str = "",
        tick_size: float = 0.25,
        pivot_left: int = 3,
        pivot_right: int = 3,
        min_gap_pct: float = 0.001,
        fvg_mitigation: str = DEFAULT_MITIGATION,
        liquidity_min_touches: int = 2,
        liquidity_tick_tol: int = 4,
        liquidity_atr_tol: float = 0.10,
        liquidity_lookback_bars: int = 4000,
        max_age_bars: int | None = None,
        atr_series: Sequence[float] | None = None,
        session_pools: bool = False,
        session_tick_tolerance: int = 4,
        session_atr_tolerance: float = 0.10,
        d20_sessions: int = 20,
    ) -> None:
        self.symbol, self.timeframe = symbol, timeframe
        self.tick_size, self.pivot_left, self.pivot_right = tick_size, pivot_left, pivot_right
        self.min_gap_pct, self.fvg_mitigation = min_gap_pct, fvg_mitigation
        self.max_age_bars = max_age_bars
        self.session_pools = session_pools
        self.session_tick_tolerance = session_tick_tolerance
        self.session_atr_tolerance = session_atr_tolerance
        self.d20_sessions = d20_sessions

        self._atr_series = list(atr_series) if atr_series is not None else None
        self._liquidity_cfg = dict(min_touches=liquidity_min_touches,
                                   tick_tolerance=liquidity_tick_tol,
                                   atr_tolerance=liquidity_atr_tol,
                                   lookback_bars=liquidity_lookback_bars)

        self._session_builder: SessionLevelsBuilder | None = None
        if self.session_pools:
            market_spec = get_market_spec(self.symbol)
            self._session_builder = SessionLevelsBuilder(
                symbol=self.symbol,
                timeframe=self.timeframe,
                market_spec=market_spec,
                tick_size=self.tick_size,
                tick_tolerance=self.session_tick_tolerance,
                atr_tolerance=self.session_atr_tolerance,
                d20_sessions=self.d20_sessions,
            )

        self._high: list[float] = []
        self._low: list[float] = []
        self._ts: list[datetime] = []
        self._seen = 0
        self._last_sig: tuple | None = None

        self._zones: dict[str, Zone] = {}        # zonas NO terminales
        self._all_zones: dict[str, Zone] = {}    # historial completo (estados finales)
        self._fvg_since: dict[str, list] = {}    # [min_low, max_high] desde creacion
        self._clusterers: dict[str, LiquidityClusterer] = {}
        for ztype in ("eqh", "buyside", "eql", "sellside"):
            self._clusterers[ztype] = self._make_clusterer(ztype)
        self._sr_clusterers = {
            side: SupportResistanceClusterer(
                symbol=self.symbol, timeframe=self.timeframe, side=side,
                tolerance=self._current_tolerance(),
                min_samples=self._liquidity_cfg["min_touches"],
                lookback_bars=self._liquidity_cfg["lookback_bars"],
            )
            for side in ("high", "low")
        }
        self._ob_builder = OrderBlockBuilder(symbol=self.symbol, timeframe=self.timeframe)

    def _make_clusterer(self, ztype: str) -> LiquidityClusterer:
        side = "high" if ztype in ("eqh", "buyside") else "low"
        return LiquidityClusterer(
            symbol=self.symbol, timeframe=self.timeframe, side=side, zone_type=ztype,
            tolerance=self._current_tolerance(), min_touches=self._liquidity_cfg["min_touches"],
            lookback_bars=self._liquidity_cfg["lookback_bars"],
        )

    def _current_tolerance(self) -> float:
        from src.zones.liquidity import liquidity_tolerance
        atr = self._atr_series[-1] if self._atr_series else self.tick_size * 10
        return liquidity_tolerance(self.tick_size, atr, self._liquidity_cfg["tick_tolerance"],
                                   self._liquidity_cfg["atr_tolerance"])

    def _get_atr(self, i: int) -> float | None:
        if self._atr_series:
            if i < len(self._atr_series):
                return self._atr_series[i]
            return self._atr_series[-1]
        return None

    # ---------------------------------------------------------------- update
    def update(self, history: Sequence[Any]) -> None:
        if len(history) < self._seen:
            raise ValueError("history must be append-only (shorter than processed prefix)")
        if self._seen and len(history) > self._seen - 1:
            sig = _bar_sig(history[self._seen - 1])
            if sig != self._last_sig:
                raise ValueError("history diverges from processed prefix (append-only enforced)")
        for i in range(self._seen, len(history)):
            self._on_bar(history, i)
        self._seen = len(history)

    def _on_bar(self, history: Sequence[Any], i: int) -> None:
        bar = history[i]
        hi, lo = _fld(bar, "high"), _fld(bar, "low")
        self._high.append(hi)
        self._low.append(lo)
        self._ts.append(_ts(bar))
        self._last_sig = _bar_sig(bar)

        # 0. Order blocks: misma barra de disponibilidad y geometria que SMC-OB.
        for z in self._ob_builder.on_bar(bar, i):
            self._zones[z.zone_id] = z
            self._all_zones[z.zone_id] = z

        # 1. FVG (barra 3 cerrada): reutiliza el detector consolidado
        if i >= 2:
            for z in fvg_zones_from_bars(history[i - 2:i + 1], symbol=self.symbol,
                                         timeframe=self.timeframe, min_gap_pct=self.min_gap_pct,
                                         base_index=i - 2):
                if z.zone_id not in self._zones:
                    self._zones[z.zone_id] = z
                    self._all_zones[z.zone_id] = z
                    self._fvg_since[z.zone_id] = [lo, hi]

        # 2. Pivotes confirmados (left/right) — find_swings canonico sobre ventana
        if i >= self.pivot_left + self.pivot_right:
            j = i - self.pivot_right
            win_lo = j - self.pivot_left
            hs, ls = find_swings(self._high[win_lo:i + 1], self._low[win_lo:i + 1],
                                 self.pivot_left, self.pivot_right)
            for p in hs:
                if p.index == self.pivot_left:
                    piv = ConfirmedPivot(j, p.level, "high", i, self._ts[j])
                    self._clusterers["eqh"].add(piv, i)
                    self._clusterers["buyside"].add(piv, i)
                    self._sr_clusterers["high"].add(SrPivot(
                        j, p.level, self._low[j], self._high[j], "high", i,
                        self._ts[j], self._ts[i],
                    ), i)
            for p in ls:
                if p.index == self.pivot_left:
                    piv = ConfirmedPivot(j, p.level, "low", i, self._ts[j])
                    self._clusterers["eql"].add(piv, i)
                    self._clusterers["sellside"].add(piv, i)
                    self._sr_clusterers["low"].add(SrPivot(
                        j, p.level, self._low[j], self._high[j], "low", i,
                        self._ts[j], self._ts[i],
                    ), i)

        # 2b. Anclajes de sesión (Z3-b)
        if self._session_builder is not None:
            curr_atr = self._get_atr(i)
            new_s_zones, retired_s_zones = self._session_builder.on_bar(bar, i, curr_atr)
            for rz in retired_s_zones:
                self._all_zones[rz.zone_id] = rz
                if rz.zone_id in self._zones:
                    del self._zones[rz.zone_id]
            for sz in new_s_zones:
                self._zones[sz.zone_id] = sz
                self._all_zones[sz.zone_id] = sz

        # 3. Lifecycle con la barra cerrada — solo zonas disponibles ANTES de i
        new_zones: dict[str, Zone] = {}
        for zid, z in self._zones.items():
            if z.is_terminal():
                self._all_zones[zid] = z
                continue
            if z.metadata.get("available_bar_index", -1) >= i:
                new_zones[zid] = z  # nace con esta barra: aun no evaluar
                continue
            if z.zone_type == "fvg":
                nz = transition_fvg(z, bar, i, mitigation=self.fvg_mitigation,
                                    max_age_bars=self.max_age_bars)
                self._fvg_since[zid][0] = min(self._fvg_since[zid][0], lo)
                self._fvg_since[zid][1] = max(self._fvg_since[zid][1], hi)
            elif z.zone_type in ("support", "resistance"):
                nz = transition_sr(z, bar)
            else:
                nz = transition_liquidity(z, bar, i, max_age_bars=self.max_age_bars)
            self._all_zones[zid] = nz
            if not nz.is_terminal():
                new_zones[zid] = nz
        self._zones = new_zones

        # 4. Sincronizar geometria de liquidez (el clusterer es la fuente de bounds)
        for cl in self._clusterers.values():
            for z in cl.zones():
                zid = z.zone_id
                live = self._zones.get(zid)
                if live is None:
                    if z.metadata.get("available_bar_index", i) < i:
                        self._zones[zid] = z
                        self._all_zones[zid] = z
                else:
                    # la geometria crece con pivotes nuevos; el lifecycle lo manda el engine
                    merged = Zone(
                        zone_id=live.zone_id, zone_type=live.zone_type,
                        symbol=live.symbol, timeframe=live.timeframe,
                        lower=z.lower, upper=z.upper, midpoint=z.midpoint,
                        direction=live.direction, pattern_time=live.pattern_time,
                        available_at=live.available_at,
                        state=live.state, touches=live.touches, strength=z.strength,
                        source_bar_ids=z.source_bar_ids, metadata=z.metadata,
                    )
                    self._zones[zid] = merged
                    self._all_zones[zid] = merged

        # 5. Sincronizar S/R despues del lifecycle, preservando estado y nacimiento.
        for clusterer in self._sr_clusterers.values():
            for z in clusterer.zones():
                zid = z.zone_id
                live = self._zones.get(zid)
                if live is None:
                    if z.metadata["available_bar_index"] <= i:
                        self._zones[zid] = z
                        self._all_zones[zid] = z
                else:
                    merged = Zone(
                        zone_id=live.zone_id, zone_type=live.zone_type,
                        symbol=live.symbol, timeframe=live.timeframe,
                        lower=z.lower, upper=z.upper, midpoint=z.midpoint,
                        direction=live.direction, pattern_time=live.pattern_time,
                        available_at=live.available_at, state=live.state,
                        touches=live.touches, strength=z.strength,
                        source_bar_ids=z.source_bar_ids, metadata=z.metadata,
                    )
                    self._zones[zid] = merged
                    self._all_zones[zid] = merged

    # ---------------------------------------------------------------- queries
    def active_zones(self, *, zone_type: str | None = None, direction: str | None = None) -> tuple[Zone, ...]:
        out = [z for z in self._zones.values() if not z.is_terminal()]
        if zone_type is not None:
            out = [z for z in out if z.zone_type == zone_type]
        if direction is not None:
            out = [z for z in out if z.direction == direction]
        return tuple(sorted(out, key=lambda z: (z.available_at, z.zone_id)))

    def nearest_zones(self, price: float, *, limit: int = 5, zone_type: str | None = None) -> tuple[Zone, ...]:
        cands = self.active_zones(zone_type=zone_type)
        cands = sorted(cands, key=lambda z: (_zone_dist(price, z), z.zone_id))
        return tuple(cands[:limit])

    def context(self, price: float, *, atr: float | None = None) -> dict:
        fvgs = self.active_zones(zone_type="fvg")
        liqs = self.active_zones(zone_type="liquidity")
        sr_zones = [
            *self.active_zones(zone_type="support"),
            *self.active_zones(zone_type="resistance"),
        ]
        buy = [z for z in liqs if z.direction == "long"]
        sell = [z for z in liqs if z.direction == "short"]

        inside_bull = any(z.direction == "long" and z.lower <= price <= z.upper for z in fvgs)
        inside_bear = any(z.direction == "short" and z.lower <= price <= z.upper for z in fvgs)

        near_fvg = None
        if fvgs:
            near_fvg = min(fvgs, key=lambda z: _zone_dist(price, z))
        fvg_age = None
        fvg_fill = None
        if near_fvg is not None:
            if self._seen:
                born = near_fvg.metadata.get("available_bar_index")
                if born is not None:
                    fvg_age = self._seen - 1 - born
            rng = near_fvg.upper - near_fvg.lower
            if rng > 0:
                since = self._fvg_since.get(near_fvg.zone_id)
                if since:
                    if near_fvg.direction == "long":
                        fill = (near_fvg.upper - max(near_fvg.lower, since[0])) / rng
                    else:
                        fill = (min(near_fvg.upper, since[1]) - near_fvg.lower) / rng
                    fvg_fill = max(0.0, min(1.0, fill))

        def dpts(zs: list[Zone]) -> float | None:
            if not zs:
                return None
            z = min(zs, key=lambda z: _zone_dist(price, z))
            return round(_zone_dist(price, z), 4)

        dbuy, dsell = dpts(buy), dpts(sell)
        nearest_liq = min(liqs, key=lambda z: _zone_dist(price, z)) if liqs else None
        touched = nearest_liq.touches if nearest_liq else None
        swept = bool(nearest_liq.state == "swept") if nearest_liq else None
        inside_sr = [z for z in sr_zones if z.lower <= price <= z.upper]
        inside_sr_zone = bool(inside_sr)
        support_inside = [z for z in inside_sr if z.zone_type == "support"]
        support_strength = (
            max(z.strength for z in support_inside) if support_inside else None
        )

        overlap_pairs = 0
        fvg_liq_overlap = False
        for fz in fvgs:
            for lz in liqs:
                if max(fz.lower, lz.lower) <= min(fz.upper, lz.upper):
                    overlap_pairs += 1
                    fvg_liq_overlap = True

        nearest_type = None
        all_z = self.active_zones()
        if all_z:
            nearest_type = min(all_z, key=lambda z: _zone_dist(price, z)).zone_type

        # Features de sesión (Z3-b, 16 keys; None si session_pools=False o sin evidencia)
        d_pdh_pts = None
        d_pdh_atr = None
        d_pdl_pts = None
        d_pdl_atr = None
        d_d20h_pts = None
        d_d20h_atr = None
        d_d20l_pts = None
        d_d20l_atr = None
        d_onh_pts = None
        d_onh_atr = None
        d_onl_pts = None
        d_onl_atr = None
        inside_pdr = None
        pdr_pos = None
        on_swept_pdh = None
        on_swept_pdl = None

        if self._session_builder is not None:
            def _zone_dist_pair(z: Zone | None) -> tuple[float | None, float | None]:
                if z is None:
                    return None, None
                live_z = self._all_zones.get(z.zone_id, z)
                d = round(_zone_dist(price, live_z), 4)
                d_a = round(d / atr, 4) if (atr is not None and atr > 0) else None
                return d, d_a

            pdh_z = self._session_builder.get_current_zone("prev_day_high")
            pdl_z = self._session_builder.get_current_zone("prev_day_low")
            d20h_z = self._session_builder.get_current_zone("d20_high")
            d20l_z = self._session_builder.get_current_zone("d20_low")
            onh_z = self._session_builder.get_current_zone("overnight_high")
            onl_z = self._session_builder.get_current_zone("overnight_low")

            d_pdh_pts, d_pdh_atr = _zone_dist_pair(pdh_z)
            d_pdl_pts, d_pdl_atr = _zone_dist_pair(pdl_z)
            d_d20h_pts, d_d20h_atr = _zone_dist_pair(d20h_z)
            d_d20l_pts, d_d20l_atr = _zone_dist_pair(d20l_z)
            d_onh_pts, d_onh_atr = _zone_dist_pair(onh_z)
            d_onl_pts, d_onl_atr = _zone_dist_pair(onl_z)

            if pdh_z is not None and pdl_z is not None:
                inside_pdr = bool(pdl_z.midpoint <= price <= pdh_z.midpoint)
                rng = pdh_z.midpoint - pdl_z.midpoint
                if rng > 0:
                    pos = (price - pdl_z.midpoint) / rng
                    pdr_pos = round(max(0.0, min(1.0, pos)), 4)

            on_swept_pdh = self._session_builder.overnight_swept_pdh
            on_swept_pdl = self._session_builder.overnight_swept_pdl

        out = {
            "inside_bullish_fvg": inside_bull,
            "inside_bearish_fvg": inside_bear,
            "fvg_age_bars": fvg_age,
            "fvg_fill_pct": round(fvg_fill, 4) if fvg_fill is not None else None,
            "distance_to_buyside_liquidity_pts": dbuy,
            "distance_to_sellside_liquidity_pts": dsell,
            "distance_to_buyside_liquidity_atr": round(dbuy / atr, 4) if (dbuy is not None and atr) else None,
            "distance_to_sellside_liquidity_atr": round(dsell / atr, 4) if (dsell is not None and atr) else None,
            "liquidity_touch_count": touched,
            "liquidity_swept": swept,
            "inside_sr_zone": inside_sr_zone,
            "support_zone_strength": support_strength,
            "inside_ote_zone": None,        # reversio n V1 (paso 8 de la spec)
            "premium_discount_state": None,  # requiere rango causal (reversio n V1)
            "fvg_liquidity_overlap": fvg_liq_overlap,
            "zone_overlap_count": overlap_pairs,
            "nearest_zone_type": nearest_type,
        }

        if self.session_pools:
            out.update({
                "distance_to_prev_day_high_pts": d_pdh_pts,
                "distance_to_prev_day_high_atr": d_pdh_atr,
                "distance_to_prev_day_low_pts": d_pdl_pts,
                "distance_to_prev_day_low_atr": d_pdl_atr,
                "distance_to_d20_high_pts": d_d20h_pts,
                "distance_to_d20_high_atr": d_d20h_atr,
                "distance_to_d20_low_pts": d_d20l_pts,
                "distance_to_d20_low_atr": d_d20l_atr,
                "distance_to_overnight_high_pts": d_onh_pts,
                "distance_to_overnight_high_atr": d_onh_atr,
                "distance_to_overnight_low_pts": d_onl_pts,
                "distance_to_overnight_low_atr": d_onl_atr,
                "inside_prev_day_range": inside_pdr,
                "prev_day_range_position": pdr_pos,
                "overnight_swept_prev_day_high": on_swept_pdh,
                "overnight_swept_prev_day_low": on_swept_pdl,
            })

        return out

    @property
    def n_zones(self) -> int:
        return len(self._all_zones)

    def all_zones(self) -> tuple[Zone, ...]:
        return tuple(sorted(self._all_zones.values(), key=lambda z: (z.available_at, z.zone_id)))

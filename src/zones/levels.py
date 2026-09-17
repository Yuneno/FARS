"""Causal support/resistance zones from confirmed pivot clusters (Z6).

The price clustering primitive is deliberately imported from liquidity: Z6
must not acquire a subtly different clustering rule.  High pivots produce
resistance and low pivots produce support.  Cluster membership uses the pivot
level, while zone width covers the complete candles that supplied the pivots.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.zones.liquidity import _cluster_levels
from src.zones.models import Zone, make_zone_id


@dataclass(frozen=True)
class SrPivot:
    index: int
    level: float
    low: float
    high: float
    side: str
    confirmed_at_index: int
    pattern_time: datetime
    confirmed_at: datetime


class SupportResistanceClusterer:
    """Incremental, deterministic clusters for one pivot side."""

    def __init__(self, *, symbol: str, timeframe: str, side: str,
                 tolerance: float, min_samples: int = 2,
                 lookback_bars: int = 4000) -> None:
        if side not in ("high", "low"):
            raise ValueError("side must be 'high' or 'low'")
        if min_samples < 1:
            raise ValueError("min_samples must be >= 1")
        self.symbol, self.timeframe, self.side = symbol, timeframe, side
        self.tolerance, self.min_samples = float(tolerance), int(min_samples)
        self.lookback_bars = int(lookback_bars)
        self._pivots: list[SrPivot] = []
        self._zones: dict[str, Zone] = {}
        self._history: dict[str, Zone] = {}

    def add(self, pivot: SrPivot, bar_index: int) -> None:
        if pivot.side != self.side:
            raise ValueError("pivot side does not match clusterer")
        self._pivots.append(pivot)
        cutoff = bar_index - self.lookback_bars
        self._pivots = [p for p in self._pivots if p.index >= cutoff]
        clusters = _cluster_levels(
            [p.level for p in self._pivots], self.tolerance, self.min_samples
        )
        by_level: dict[float, list[SrPivot]] = {}
        for item in self._pivots:
            by_level.setdefault(item.level, []).append(item)
        rebuilt: dict[str, Zone] = {}
        zone_type = "resistance" if self.side == "high" else "support"
        direction = "short" if self.side == "high" else "long"
        for levels in clusters:
            members = [p for level in levels for p in by_level[level]]
            members.sort(key=lambda p: (p.index, p.level))
            first = members[0]
            zone_id = make_zone_id(
                zone_type, self.symbol, self.timeframe, direction,
                first.pattern_time, (first.index, first.level),
            )
            previous = self._history.get(zone_id)
            evidence = members[self.min_samples - 1]
            available_at = previous.available_at if previous else evidence.confirmed_at
            available_bar = (
                previous.metadata["available_bar_index"]
                if previous else evidence.confirmed_at_index
            )
            lower = min(p.low for p in members)
            upper = max(p.high for p in members)
            source_ids = tuple(p.index for p in members)
            if previous is not None:
                lower = min(lower, previous.lower)
                upper = max(upper, previous.upper)
                source_ids = tuple(sorted(set(previous.source_bar_ids) | set(source_ids)))
            zone = Zone(
                zone_id=zone_id, zone_type=zone_type,
                symbol=self.symbol, timeframe=self.timeframe,
                lower=lower, upper=upper, midpoint=(lower + upper) / 2.0,
                direction=direction, pattern_time=first.pattern_time,
                available_at=available_at,
                state=previous.state if previous else "active",
                touches=previous.touches if previous else 0,
                strength=max(previous.strength if previous else 0.0, float(len(source_ids))),
                source_bar_ids=source_ids,
                metadata={
                    "n_pivots": len(source_ids),
                    "available_bar_index": available_bar,
                    "tolerance": self.tolerance,
                    "pivot_side": self.side,
                    "zone_width": upper - lower,
                },
            )
            rebuilt[zone_id] = zone
            self._history[zone_id] = zone
        self._zones = rebuilt

    def zones(self) -> tuple[Zone, ...]:
        return tuple(sorted(self._zones.values(), key=lambda z: (z.available_at, z.zone_id)))


def transition_sr(zone: Zone, bar: object) -> Zone:
    """Minimal auditable lifecycle: active -> touched -> broken."""
    if zone.is_terminal() or zone.zone_type not in ("support", "resistance"):
        return zone
    get = bar.__getitem__ if isinstance(bar, dict) else lambda name: getattr(bar, name)
    low, high, close = float(get("low")), float(get("high")), float(get("close"))
    state, touches = zone.state, zone.touches
    if low <= zone.upper and high >= zone.lower:
        touches += 1
        if state == "active":
            state = "touched"
    if (zone.zone_type == "support" and close < zone.lower) or (
        zone.zone_type == "resistance" and close > zone.upper
    ):
        state = "broken"
    return Zone(
        zone_id=zone.zone_id, zone_type=zone.zone_type, symbol=zone.symbol,
        timeframe=zone.timeframe, lower=zone.lower, upper=zone.upper,
        midpoint=zone.midpoint, direction=zone.direction,
        pattern_time=zone.pattern_time, available_at=zone.available_at,
        state=state, touches=touches, strength=zone.strength,
        source_bar_ids=zone.source_bar_ids, metadata=zone.metadata,
    )

"""Modelo de dominio de zona (Z1) — spec de GPT, seccion 2.

Invariantes duros (validados en ``__post_init__``):
- ``lower <= midpoint <= upper``
- ``available_at >= pattern_time``
- precios positivos

IDs deterministas: ``make_zone_id`` hashea (zone_type, symbol, timeframe,
direction, pattern_time, anchor). El ``anchor`` lo elige el emisor y DEBE ser
estable ante el crecimiento de la zona (p.ej. FVG: source_bar_ids; liquidez:
primer pivot). Nunca incluir bounds cambiantes en el anchor.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

ZoneType = Literal[
    "fvg", "liquidity", "support", "resistance", "order_block",
    "volume_void", "session_level",
]
ZoneState = Literal["active", "touched", "partial", "swept", "mitigated", "broken", "expired"]
ZoneDirection = Literal["long", "short", "neutral"]

TERMINAL_STATES: frozenset[str] = frozenset({"mitigated", "broken", "expired"})


def make_zone_id(
    zone_type: str,
    symbol: str,
    timeframe: str,
    direction: str,
    pattern_time: datetime,
    anchor: tuple,
) -> str:
    raw = json.dumps(
        [zone_type, symbol, timeframe, direction, pattern_time.isoformat(), list(anchor)],
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


@dataclass(frozen=True)
class Zone:
    zone_id: str
    zone_type: ZoneType
    symbol: str
    timeframe: str

    lower: float
    upper: float
    midpoint: float

    direction: ZoneDirection

    pattern_time: datetime
    available_at: datetime

    state: ZoneState = "active"
    touches: int = 0
    strength: float = 0.0

    source_bar_ids: tuple[int, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (self.lower <= self.midpoint <= self.upper):
            raise ValueError(f"invariante roto: lower<=mid<=upper ({self.lower}, {self.midpoint}, {self.upper})")
        if self.available_at < self.pattern_time:
            raise ValueError("invariante roto: available_at < pattern_time")
        if self.lower <= 0 or self.upper <= 0:
            raise ValueError(f"precios invalidos: {self.lower}, {self.upper}")

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def to_dict(self) -> dict:
        return {
            "zone_id": self.zone_id, "zone_type": self.zone_type,
            "symbol": self.symbol, "timeframe": self.timeframe,
            "lower": self.lower, "upper": self.upper, "midpoint": self.midpoint,
            "direction": self.direction,
            "pattern_time": self.pattern_time.isoformat(),
            "available_at": self.available_at.isoformat(),
            "state": self.state, "touches": self.touches, "strength": self.strength,
            "source_bar_ids": list(self.source_bar_ids), "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Zone":
        return cls(
            zone_id=d["zone_id"], zone_type=d["zone_type"],
            symbol=d["symbol"], timeframe=d["timeframe"],
            lower=d["lower"], upper=d["upper"], midpoint=d["midpoint"],
            direction=d["direction"],
            pattern_time=datetime.fromisoformat(d["pattern_time"]),
            available_at=datetime.fromisoformat(d["available_at"]),
            state=d["state"], touches=d["touches"], strength=d["strength"],
            source_bar_ids=tuple(d["source_bar_ids"]), metadata=dict(d["metadata"]),
        )

"""Diagnostic event schema for JITA-inspired detectors (P4)."""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

EventKind = Literal["crt", "fvg", "cisd", "sweep", "liquidity", "swing"]

@dataclass(frozen=True)
class DiagnosticEvent:
    detector: str
    symbol: str
    timeframe: str
    source_bar_ids: tuple[int, ...]
    pattern_time: datetime
    available_at: datetime
    direction: str | None
    levels: dict[str, float] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    event_id: str = ""

    def __post_init__(self):
        if not self.event_id:
            obj = {
                "detector": self.detector, "symbol": self.symbol,
                "tf": self.timeframe, "bars": list(self.source_bar_ids),
                "t": self.pattern_time.isoformat(), "a": self.available_at.isoformat(),
            }
            raw = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
            object.__setattr__(self, "event_id", hashlib.sha256(raw).hexdigest())

"""Intrabar ambiguity resolution using M1 bars for M5 execution (D4 / A2.4).

Resolves bars where both Stop Loss and Take Profit levels fall within the
high-low range of the same M5 candle. Maintains signals strictly in M5.

Rules:
1. When an M5 candle touches both SL and TP:
   - Consult constituent M1 bars in strictly chronological order.
   - If an M1 bar touches SL first -> resolve as "stop_loss".
   - If an M1 bar touches TP first -> resolve as "take_profit".
   - If BOTH SL and TP are touched within the SAME M1 bar (or M1 data is missing),
     apply the conservative bound -> resolve as "stop_loss".
2. Report percentage of ambiguous bars and classification breakdown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Sequence

from src.backtest.history import Bar


def is_m5_ambiguous(
    direction: str,
    stop: float,
    target: float,
    high: float,
    low: float,
) -> bool:
    """True when both stop and target are within the high/low range of the bar."""
    if direction == "long":
        hit_target = target <= high
        hit_stop = stop >= low
    else:
        hit_target = target >= low
        hit_stop = stop <= high
    return bool(hit_target and hit_stop)


def resolve_intrabar_with_m1(
    direction: str,
    stop: float,
    target: float,
    m1_bars: Sequence[Bar],
) -> tuple[str, str]:
    """Resolve an ambiguous M5 bar using constituent M1 bars.

    Returns:
        (exit_reason, resolution_status)
        exit_reason in {"stop_loss", "take_profit"}
        resolution_status in {
            "resolved_by_m1_stop",
            "resolved_by_m1_target",
            "m1_residual_ambiguity_conservative_stop",
            "no_m1_data_conservative_stop",
        }
    """
    if not m1_bars:
        return "stop_loss", "no_m1_data_conservative_stop"

    for m1 in m1_bars:
        if direction == "long":
            m1_stop = stop >= m1.low
            m1_target = target <= m1.high
        else:
            m1_stop = stop <= m1.high
            m1_target = target >= m1.low

        if m1_stop and m1_target:
            # Ambiguity persists at 1-minute granularity; apply conservative bound
            return "stop_loss", "m1_residual_ambiguity_conservative_stop"
        if m1_stop:
            return "stop_loss", "resolved_by_m1_stop"
        if m1_target:
            return "take_profit", "resolved_by_m1_target"

    # Fallback if M1 bars did not touch either level (e.g. truncated M1 slice)
    return "stop_loss", "no_m1_data_conservative_stop"


@dataclass
class IntrabarAudit:
    """Audit metrics for M5 intrabar ambiguity and M1 resolution."""

    total_bars_evaluated: int = 0
    ambiguous_bars_count: int = 0
    resolved_by_m1_target: int = 0
    resolved_by_m1_stop: int = 0
    m1_residual_ambiguity: int = 0
    no_m1_data_fallback: int = 0

    @property
    def ambiguous_bars_pct(self) -> float:
        if self.total_bars_evaluated == 0:
            return 0.0
        return (self.ambiguous_bars_count / self.total_bars_evaluated) * 100.0

    @property
    def resolution_rate_pct(self) -> float:
        """Percentage of ambiguous bars successfully resolved by M1."""
        if self.ambiguous_bars_count == 0:
            return 100.0
        resolved = self.resolved_by_m1_target + self.resolved_by_m1_stop
        return (resolved / self.ambiguous_bars_count) * 100.0


def index_m1_bars(m1_bars: Sequence[Bar]) -> dict[datetime, list[Bar]]:
    """Index M1 bars by their parent M5 candle timestamp (5-minute floor)."""
    indexed: dict[datetime, list[Bar]] = {}
    for bar in m1_bars:
        ts = bar.timestamp
        m5_minute = (ts.minute // 5) * 5
        m5_ts = ts.replace(minute=m5_minute, second=0, microsecond=0)
        if m5_ts not in indexed:
            indexed[m5_ts] = []
        indexed[m5_ts].append(bar)
    return indexed


__all__ = [
    "is_m5_ambiguous",
    "resolve_intrabar_with_m1",
    "IntrabarAudit",
    "index_m1_bars",
]

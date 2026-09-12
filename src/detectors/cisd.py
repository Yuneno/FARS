"""CISD (Close-Invalidation Sell/Demand) detector for FARS (P4).

CISD is dated at the close that confirms it.
"""
from __future__ import annotations
from datetime import datetime
from src.detectors.events import DiagnosticEvent

def detect_cisd(
    bars: list[dict],
    *,
    symbol: str = "",
    timeframe: str = "",
    lookback: int = 10,
) -> list[DiagnosticEvent]:
    events = []
    if len(bars) < lookback + 1:
        return events
    for i in range(lookback, len(bars)):
        window = bars[i - lookback:i]
        highs = [b["high"] for b in window]
        lows = [b["low"] for b in window]
        resistance = max(highs)
        support = min(lows)
        close = bars[i]["close"]
        # CISD long: close breaks above resistance
        if close > resistance:
            ts = datetime.fromisoformat(bars[i]["timestamp"]) if isinstance(bars[i]["timestamp"], str) else bars[i]["timestamp"]
            events.append(DiagnosticEvent(
                detector="cisd_v1", symbol=symbol, timeframe=timeframe,
                source_bar_ids=(i,), pattern_time=ts, available_at=ts,
                direction="long",
                levels={"resistance": resistance, "support": support, "close": close},
                params={"lookback": lookback},
            ))
        # CISD short: close breaks below support
        if close < support:
            ts = datetime.fromisoformat(bars[i]["timestamp"]) if isinstance(bars[i]["timestamp"], str) else bars[i]["timestamp"]
            events.append(DiagnosticEvent(
                detector="cisd_v1", symbol=symbol, timeframe=timeframe,
                source_bar_ids=(i,), pattern_time=ts, available_at=ts,
                direction="short",
                levels={"resistance": resistance, "support": support, "close": close},
                params={"lookback": lookback},
            ))
    return events

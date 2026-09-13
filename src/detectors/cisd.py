"""CISD (Close-Invalidation Sell/Demand) detector for FARS (P4).

CISD is dated at the close that confirms it. Baseline-first, opt-in diagnostic.

JITA comparison (patterns/cisd.py):
- JITA CISD uses confluence with other patterns for trade decisions; FARS is event-only.
- JITA identifies support/resistance from swing structure; FARS uses simple lookback extremes.
- FARS CISD available_at == pattern_time (confirmed at bar close).
- JITA may require multi-bar confirmation; FARS emits at single-bar close.
- Differences documented; no forced equivalences.

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

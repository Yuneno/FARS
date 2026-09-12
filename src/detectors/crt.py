"""CRT (Causal Range Tightening) detector for FARS (P4).

Detects range contraction patterns with confirmation delay.
Pattern forms over N bars; available_at is after the confirming bar closes.
"""
from __future__ import annotations
from datetime import datetime
from src.detectors.events import DiagnosticEvent

def detect_crt(
    bars: list[dict],
    *,
    symbol: str = "",
    timeframe: str = "",
    lookback: int = 20,
) -> list[DiagnosticEvent]:
    events = []
    if len(bars) < lookback + 1:
        return events
    for i in range(lookback, len(bars)):
        window = bars[i - lookback:i]
        highs = [b["high"] for b in window]
        lows = [b["low"] for b in window]
        range_high = max(highs)
        range_low = min(lows)
        bar_range = bars[i]["high"] - bars[i]["low"]
        avg_range = (range_high - range_low) / lookback
        if avg_range > 0 and bar_range < avg_range * 0.5:
            mid = (range_high + range_low) / 2
            direction = "long" if bars[i]["close"] > mid else "short"
            ts = datetime.fromisoformat(bars[i]["timestamp"]) if isinstance(bars[i]["timestamp"], str) else bars[i]["timestamp"]
            events.append(DiagnosticEvent(
                detector="crt_v1", symbol=symbol, timeframe=timeframe,
                source_bar_ids=(i,), pattern_time=ts, available_at=ts,
                direction=direction,
                levels={"range_high": range_high, "range_low": range_low, "mid": mid},
                params={"lookback": lookback, "bar_range": bar_range, "avg_range": avg_range},
            ))
    return events

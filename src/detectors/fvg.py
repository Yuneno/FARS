"""FVG (Fair Value Gap) detector for FARS (P4).

Emits after the third bar closes. available_at = third bar close.
"""
from __future__ import annotations
from datetime import datetime
from src.detectors.events import DiagnosticEvent

def detect_fvg(
    bars: list[dict],
    *,
    symbol: str = "",
    timeframe: str = "",
    min_gap_pct: float = 0.001,
) -> list[DiagnosticEvent]:
    events = []
    if len(bars) < 3:
        return events
    for i in range(2, len(bars)):
        b1, b2, b3 = bars[i-2], bars[i-1], bars[i]
        # Bullish FVG: gap between b1.high and b3.low (b2 moved up through)
        if b3["low"] > b1["high"] and b2["close"] > b1["high"]:
            gap_size = b3["low"] - b1["high"]
            avg_price = (b1["high"] + b3["low"]) / 2
            if avg_price > 0 and gap_size / avg_price >= min_gap_pct:
                ts = datetime.fromisoformat(b3["timestamp"]) if isinstance(b3["timestamp"], str) else b3["timestamp"]
                events.append(DiagnosticEvent(
                    detector="fvg_v1", symbol=symbol, timeframe=timeframe,
                    source_bar_ids=(i-2, i-1, i), pattern_time=ts, available_at=ts,
                    direction="long",
                    levels={"gap_low": b1["high"], "gap_high": b3["low"]},
                    params={"gap_size": gap_size},
                ))
        # Bearish FVG: gap between b3.high and b1.low
        if b3["high"] < b1["low"] and b2["close"] < b1["low"]:
            gap_size = b1["low"] - b3["high"]
            avg_price = (b1["low"] + b3["high"]) / 2
            if avg_price > 0 and gap_size / avg_price >= min_gap_pct:
                ts = datetime.fromisoformat(b3["timestamp"]) if isinstance(b3["timestamp"], str) else b3["timestamp"]
                events.append(DiagnosticEvent(
                    detector="fvg_v1", symbol=symbol, timeframe=timeframe,
                    source_bar_ids=(i-2, i-1, i), pattern_time=ts, available_at=ts,
                    direction="short",
                    levels={"gap_low": b3["high"], "gap_high": b1["low"]},
                    params={"gap_size": gap_size},
                ))
    return events

"""Swing/sweep detector for FARS (P4).

Detects liquidity sweeps: price exceeds a swing high/low and reverses.
available_at includes confirmation bars.
"""
from __future__ import annotations
from datetime import datetime
from src.detectors.events import DiagnosticEvent

def _find_swing_highs(bars: list[dict], left: int = 3, right: int = 3) -> list[tuple[int, float]]:
    results = []
    for i in range(left, len(bars) - right):
        h = bars[i]["high"]
        is_high = all(bars[i-j]["high"] <= h for j in range(1, left+1)) and \
                  all(bars[i+j]["high"] <= h for j in range(1, right+1))
        if is_high:
            results.append((i, h))
    return results

def _find_swing_lows(bars: list[dict], left: int = 3, right: int = 3) -> list[tuple[int, float]]:
    results = []
    for i in range(left, len(bars) - right):
        l = bars[i]["low"]
        is_low = all(bars[i-j]["low"] >= l for j in range(1, left+1)) and \
                 all(bars[i+j]["low"] >= l for j in range(1, right+1))
        if is_low:
            results.append((i, l))
    return results

def detect_sweeps(
    bars: list[dict],
    *,
    symbol: str = "",
    timeframe: str = "",
    left: int = 3,
    right: int = 3,
) -> list[DiagnosticEvent]:
    events = []
    if len(bars) < left + right + 1:
        return events
    swing_highs = _find_swing_highs(bars, left, right)
    swing_lows = _find_swing_lows(bars, left, right)
    # Check for sweeps: price exceeds swing level then reverses
    for sh_idx, sh_val in swing_highs:
        for i in range(sh_idx + right + 1, len(bars)):
            if bars[i]["high"] > sh_val and bars[i]["close"] < sh_val:
                ts = datetime.fromisoformat(bars[i]["timestamp"]) if isinstance(bars[i]["timestamp"], str) else bars[i]["timestamp"]
                events.append(DiagnosticEvent(
                    detector="sweep_v1", symbol=symbol, timeframe=timeframe,
                    source_bar_ids=(sh_idx, i), pattern_time=ts, available_at=ts,
                    direction="short",
                    levels={"sweep_level": sh_val, "sweep_high": bars[i]["high"]},
                    params={"swing_bar": sh_idx},
                ))
                break
    for sl_idx, sl_val in swing_lows:
        for i in range(sl_idx + right + 1, len(bars)):
            if bars[i]["low"] < sl_val and bars[i]["close"] > sl_val:
                ts = datetime.fromisoformat(bars[i]["timestamp"]) if isinstance(bars[i]["timestamp"], str) else bars[i]["timestamp"]
                events.append(DiagnosticEvent(
                    detector="sweep_v1", symbol=symbol, timeframe=timeframe,
                    source_bar_ids=(sl_idx, i), pattern_time=ts, available_at=ts,
                    direction="long",
                    levels={"sweep_level": sl_val, "sweep_low": bars[i]["low"]},
                    params={"swing_bar": sl_idx},
                ))
                break
    return events

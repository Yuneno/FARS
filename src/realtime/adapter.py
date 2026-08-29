"""RT-5 boundary between realtime events and FARS Core analytics.

Market prints, quotes, signals, and execution reports are not completed
analytical trades. Core analyses run only on Core ``Trade`` values that
already carry a finite ``r_result``.
"""

from __future__ import annotations

from collections.abc import Iterable
from math import isfinite

from src.metrics import Metrics, compute_metrics
from src.realtime.events import CanonicalEvent
from src.types import Trade


class AdapterError(ValueError):
    """Realtime data is not eligible for Core analytical types or analyses."""


def require_core_trades(items: Iterable[object]) -> tuple[Trade, ...]:
    """Accept only completed Core trades. Reject every canonical realtime event."""
    trades: list[Trade] = []
    for item in items:
        if isinstance(item, CanonicalEvent):
            raise AdapterError(
                f"cannot convert realtime {type(item).__name__} into Core Trade"
            )
        if not isinstance(item, Trade):
            raise AdapterError(
                f"Core adapter accepts Trade only, got {type(item).__name__}"
            )
        if isinstance(item.r_result, bool) or not isinstance(item.r_result, (int, float)):
            raise AdapterError(f"Trade.r_result must be a finite number, got {item.r_result!r}")
        if not isfinite(item.r_result):
            raise AdapterError(f"Trade.r_result must be finite, got {item.r_result!r}")
        trades.append(item)
    if not trades:
        raise AdapterError("Core analysis requires at least one completed trade")
    return tuple(trades)


def run_core_metrics(items: Iterable[object]) -> Metrics:
    """Invoke Core metrics only after the realtime/Core gate accepts the data."""
    trades = require_core_trades(items)
    return compute_metrics([trade.r_result for trade in trades])

"""Causal Maximum Adverse Excursion (MAE) computation using M1 resolution (Block D3).

Calculates the exact intra-trade adverse and favorable price excursions
for executed trades strictly within [entry_time, exit_time].
Prevents look-ahead bias by ignoring M1 bars outside the trade's active lifetime.
Applies conservative bounds when M1 granularity is missing or ambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from src.backtest.executor import ExecutedTrade
from src.backtest.history import Bar


@dataclass(frozen=True)
class TradeMaeResult:
    """Intratrade excursion audit for a single trade."""

    trade_id: str
    mae_points: float
    mae_dollars_per_contract: float
    mae_timestamp: datetime | None
    mfe_points: float
    mfe_timestamp: datetime | None
    m1_bars_evaluated: int
    resolution_mode: str  # "m1_causal", "m5_fallback_conservative", "stop_bound_conservative"


def compute_trade_mae(
    trade: ExecutedTrade,
    m1_bars: Sequence[Bar] | None = None,
    dollar_per_point: float = 2.0,
    m5_fallback_bars: Sequence[Bar] | None = None,
) -> TradeMaeResult:
    """Compute causal Maximum Adverse Excursion (MAE) for an executed trade.

    Parameters:
        trade: The ExecutedTrade with entry_time, exit_time, entry_price, direction.
        m1_bars: Optional sequence of 1-minute bars covering the trade's date/span.
        dollar_per_point: Multiplier for instrument (default 2.0 for MNQ).
        m5_fallback_bars: Optional parent M5 bars for conservative fallback if M1 is absent.

    Returns:
        TradeMaeResult with mae_points, mae_dollars_per_contract, and causal timestamps.
    """
    entry_time = trade.entry_time
    exit_time = trade.exit_time
    entry_price = trade.entry_price
    is_long = trade.direction == "long"

    # 1. Primary path: strictly causal M1 bars within [entry_time, exit_time]
    if m1_bars:
        causal_m1 = [
            bar for bar in m1_bars
            if entry_time <= bar.timestamp <= exit_time
        ]
        if causal_m1:
            max_adverse = 0.0
            max_favorable = 0.0
            adverse_ts: datetime | None = None
            favorable_ts: datetime | None = None

            for bar in causal_m1:
                if is_long:
                    adverse = max(0.0, entry_price - bar.low)
                    favorable = max(0.0, bar.high - entry_price)
                else:
                    adverse = max(0.0, bar.high - entry_price)
                    favorable = max(0.0, entry_price - bar.low)

                if adverse > max_adverse:
                    max_adverse = adverse
                    adverse_ts = bar.timestamp
                if favorable > max_favorable:
                    max_favorable = favorable
                    favorable_ts = bar.timestamp

            return TradeMaeResult(
                trade_id=trade.trade_id,
                mae_points=round(max_adverse, 4),
                mae_dollars_per_contract=round(max_adverse * dollar_per_point, 2),
                mae_timestamp=adverse_ts,
                mfe_points=round(max_favorable, 4),
                mfe_timestamp=favorable_ts,
                m1_bars_evaluated=len(causal_m1),
                resolution_mode="m1_causal",
            )

    # 2. Secondary fallback: parent M5 bars covering [entry_time, exit_time]
    if m5_fallback_bars:
        causal_m5 = [
            bar for bar in m5_fallback_bars
            if entry_time <= bar.timestamp <= exit_time
        ]
        if causal_m5:
            max_adverse = 0.0
            max_favorable = 0.0
            adverse_ts = None
            favorable_ts = None

            for bar in causal_m5:
                if is_long:
                    adverse = max(0.0, entry_price - bar.low)
                    favorable = max(0.0, bar.high - entry_price)
                else:
                    adverse = max(0.0, bar.high - entry_price)
                    favorable = max(0.0, entry_price - bar.low)

                if adverse > max_adverse:
                    max_adverse = adverse
                    adverse_ts = bar.timestamp
                if favorable > max_favorable:
                    max_favorable = favorable
                    favorable_ts = bar.timestamp

            return TradeMaeResult(
                trade_id=trade.trade_id,
                mae_points=round(max_adverse, 4),
                mae_dollars_per_contract=round(max_adverse * dollar_per_point, 2),
                mae_timestamp=adverse_ts,
                mfe_points=round(max_favorable, 4),
                mfe_timestamp=favorable_ts,
                m1_bars_evaluated=0,
                resolution_mode="m5_fallback_conservative",
            )

    # 3. Ultimate conservative fallback: distance to stop loss
    stop_distance = abs(trade.entry_price - trade.stop_price)
    return TradeMaeResult(
        trade_id=trade.trade_id,
        mae_points=round(stop_distance, 4),
        mae_dollars_per_contract=round(stop_distance * dollar_per_point, 2),
        mae_timestamp=trade.entry_time,
        mfe_points=0.0,
        mfe_timestamp=None,
        m1_bars_evaluated=0,
        resolution_mode="stop_bound_conservative",
    )


__all__ = ["TradeMaeResult", "compute_trade_mae"]

"""Causal Opening Range Breakout (ORB) strategy for the FARS backtest.

Port of Juanca's momentum breakout strategy from tsfm_trading_bench-main/orb_strategy.py.

Core rules:
- Opening range: 09:30–10:00 America/New_York (30 minutes).
- Intraday levels: orh = max(high), orl = min(low), orm = (orh + orl) / 2.0.
- Triggers on close breaking range (close > orh for long, close < orl for short).
- Stop mode 'opposite': stop at opposite extreme of opening range.
- Target: 2R (rr = 2.0).
- Midpoint rearm: price returning through orm re-arms that side for a subsequent breakout.
- Session end: no new entries at or after 16:00 ET.
- Max hold: 192 bars (M5 bars = 16 hours) with flat time exit.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from src.backtest.executor import BacktestConfig
from src.backtest.history import Bar
from src.backtest.markets import MNQ, MarketSpec
from src.backtest.strategy import Signal

NY_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class OrbConfig:
    """Explicit parameters for Opening Range Breakout."""

    ticker: str = "MNQ"
    or_minutes: int = 30
    stop_mode: str = "opposite"  # "opposite" | "mid"
    rr: float = 2.0
    use_bias: bool = False
    fade: bool = False
    end_hour: int = 16
    max_hold_bars: int = 192
    time_exit_mode: str = "flat"


class OrbStrategy:
    """Stateful, causal Opening Range Breakout strategy consumed by FARS executor."""

    def __init__(
        self,
        config: OrbConfig | None = None,
        *,
        market: MarketSpec = MNQ,
    ) -> None:
        self.config = config or OrbConfig()
        self.market = market
        self.session_tz = market.session_timezone or NY_TZ

        self._current_date: date | None = None
        self._in_or = False
        self._or_ready = False
        self._orh = -float("inf")
        self._orl = float("inf")
        self._orm = float("nan")

        self._done_up = False
        self._done_dn = False

        self._pending_active = False
        self._seen_bars = 0

    def set_execution_state(self, *, pending: bool, position: bool, cooldown: int) -> None:
        del position, cooldown
        self._pending_active = pending

    def fresh(self) -> OrbStrategy:
        return OrbStrategy(self.config, market=self.market)

    def parameters(self) -> dict[str, object]:
        return {
            "strategy": "OrbStrategy",
            "ticker": self.config.ticker,
            "or_minutes": self.config.or_minutes,
            "stop_mode": self.config.stop_mode,
            "rr": self.config.rr,
            "use_bias": self.config.use_bias,
            "fade": self.config.fade,
            "end_hour": self.config.end_hour,
            "max_hold_bars": self.config.max_hold_bars,
            "time_exit_mode": self.config.time_exit_mode,
            "timezone": str(self.session_tz),
        }

    def _reset_day(self, d: date) -> None:
        self._current_date = d
        self._in_or = False
        self._or_ready = False
        self._orh = -float("inf")
        self._orl = float("inf")
        self._orm = float("nan")
        self._done_up = False
        self._done_dn = False

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        if not history:
            return None

        # Synchronize through all newly presented bars
        signal: Signal | None = None
        start_idx = self._seen_bars
        self._seen_bars = len(history)

        for idx in range(start_idx, len(history)):
            bar = history[idx]
            bar_ny = bar.timestamp.astimezone(self.session_tz)
            d = bar_ny.date()
            minute_of_day = bar_ny.hour * 60 + bar_ny.minute

            if self._current_date is None or d != self._current_date:
                self._reset_day(d)

            open_m = 9 * 60 + 30  # 09:30 ET
            or_end_m = open_m + self.config.or_minutes  # 10:00 ET

            # 1. Opening range formation [09:30, 10:00)
            if open_m <= minute_of_day < or_end_m:
                self._in_or = True
                if bar.high > self._orh:
                    self._orh = bar.high
                if bar.low < self._orl:
                    self._orl = bar.low
                continue

            # 2. Finalize opening range when time >= 10:00
            if self._in_or and not self._or_ready and minute_of_day >= or_end_m:
                if self._orh > self._orl and math.isfinite(self._orh) and math.isfinite(self._orl):
                    self._orm = (self._orh + self._orl) / 2.0
                    self._or_ready = True
                self._in_or = False

            if not self._or_ready:
                continue

            # 3. After opening range, up to end_hour (16:00 ET)
            if bar_ny.hour >= self.config.end_hour or minute_of_day < or_end_m:
                continue

            price = bar.close

            # Midpoint rearm
            if price < self._orm:
                self._done_up = False
            if price > self._orm:
                self._done_dn = False

            # Check breakouts
            # Long breakout
            broke_up = price > self._orh
            broke_dn = price < self._orl

            # Evaluate only on the latest closed bar of the history sequence
            is_latest_bar = (idx == len(history) - 1)

            if broke_up and not self._done_up:
                self._done_up = True
                if is_latest_bar and not self._pending_active:
                    stop = self._orl if self.config.stop_mode == "opposite" else self._orm
                    risk = abs(price - stop)
                    if risk > 0:
                        take = 1 if not self.config.fade else -1
                        if take == 1:
                            target = price + self.config.rr * risk
                            signal = Signal(direction="long", entry=price, stop=stop, target=target)
                        else:
                            target = price - self.config.rr * risk
                            stop_fade = price + risk
                            signal = Signal(direction="short", entry=price, stop=stop_fade, target=target)

            elif broke_dn and not self._done_dn:
                self._done_dn = True
                if is_latest_bar and not self._pending_active:
                    stop = self._orh if self.config.stop_mode == "opposite" else self._orm
                    risk = abs(price - stop)
                    if risk > 0:
                        take = -1 if not self.config.fade else 1
                        if take == -1:
                            target = price - self.config.rr * risk
                            signal = Signal(direction="short", entry=price, stop=stop, target=target)
                        else:
                            target = price + self.config.rr * risk
                            stop_fade = price - risk
                            signal = Signal(direction="long", entry=price, stop=stop_fade, target=target)

        return signal

    def observe(self, history: Sequence[Bar]) -> None:
        self.evaluate(history)


def orb_config(
    *,
    market: MarketSpec = MNQ,
    max_bars_held: int = 192,
    time_exit_mode: str = "flat",
    **overrides,
) -> BacktestConfig:
    """Execution config for Opening Range Breakout."""
    params = {
        "dollar_per_point": market.dollar_per_point,
        "tick_size": market.tick_size,
        "commission_per_side": 0.0,
        "slippage_points": 0.0,
        "bar_interval_seconds": 300,
        "max_contracts": 1_000_000,
        "max_bars_held": max_bars_held,
        "partial_take_profit_fraction": 0.0,
        "move_stop_to_break_even": False,
        "pending_limit_entry": False,
        "pending_order_wait_bars": 0,
        "cooldown_bars": 0,
        "discrete_partial_contracts": False,
        "time_exit_mode": time_exit_mode,
    }
    params.update(overrides)
    return BacktestConfig(**params)


__all__ = ["OrbConfig", "OrbStrategy", "orb_config"]

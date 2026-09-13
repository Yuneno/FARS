"""Causal top-down CRT + TBS strategy for the FARS backtest.

Port of Juanca's top-down CRT + TBS strategy from tsfm_trading_bench-main/crt_tbs_strategy.py.

Top-down structure:
- 4H: Context direction (EMA20/EMA50 + slope) and supply/demand zones.
- 1H: CRT range, liquidity purge, and displacement back through range.
- M5: TBS/CISD or engulfing confirmation inside the 1H CRT range.

Strict causality guarantee:
- Higher timeframe bars (H1 and H4) are ONLY available after they have fully closed.
- At timestamp T, only H1 bars that finished at or before T and H4 bars that finished
  at or before T are consulted.
- No future bar open/high/low/close is ever leaked.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from src.backtest.executor import BacktestConfig
from src.backtest.history import Bar
from src.backtest.markets import MNQ, MarketSpec
from src.backtest.strategy import Signal


@dataclass(frozen=True)
class CrtTbsConfig:
    """Configuration matching Juanca's CrtTbsConfig with causal execution parameters."""

    ticker: str = "MNQ"
    cost_points_roundtrip: float = 1.0
    h1_sweep_lookback: int = 6
    h1_body_ratio: float = 0.50
    m5_sweep_lookback: int = 6
    m5_confirm_bars: int = 48
    min_rr: float = 1.50
    max_risk_points: float = 180.0
    min_risk_points: float = 2.0
    session_start: str = "09:30"
    session_end: str = "16:00"
    require_4h_bias: bool = True
    require_half_zone: bool = True
    target_mode: str = "crt"  # "crt" | "fixed_rr"
    fixed_rr: float = 2.0
    time_exit_mode: str = "flat"  # "flat" | "market"
    cooldown_hours: float = 4.0


@dataclass
class _OhlcBucket:
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def update(self, bar: Bar) -> None:
        if bar.high > self.high:
            self.high = bar.high
        if bar.low < self.low:
            self.low = bar.low
        self.close = bar.close
        self.volume += bar.volume


class _BarAggregator:
    """Causal higher timeframe bar aggregator from M5 bars."""

    def __init__(self, interval_seconds: int) -> None:
        self.interval = timedelta(seconds=interval_seconds)
        self.current: _OhlcBucket | None = None
        self.closed: list[_OhlcBucket] = []

    def add(self, bar: Bar) -> bool:
        """Add an M5 bar. Returns True if a new higher timeframe bar just closed."""
        ts = bar.timestamp
        # Align bucket to UTC boundary
        epoch = int(ts.timestamp())
        bucket_start_epoch = (epoch // int(self.interval.total_seconds())) * int(self.interval.total_seconds())
        bucket_start = datetime.fromtimestamp(bucket_start_epoch, tz=ts.tzinfo)
        bucket_end = bucket_start + self.interval

        newly_closed = False
        if self.current is None:
            self.current = _OhlcBucket(bucket_start, bucket_end, bar.open, bar.high, bar.low, bar.close, bar.volume)
        elif self.current.open_time == bucket_start:
            self.current.update(bar)
        else:
            # Current bucket closed
            self.closed.append(self.current)
            newly_closed = True
            self.current = _OhlcBucket(bucket_start, bucket_end, bar.open, bar.high, bar.low, bar.close, bar.volume)

        return newly_closed


class CrtTbsStrategy:
    """Stateful, causal CRT-TBS signal layer consumed by FARS executor."""

    def __init__(
        self,
        config: CrtTbsConfig | None = None,
        *,
        market: MarketSpec = MNQ,
    ) -> None:
        self.config = config or CrtTbsConfig()
        self.market = market
        self.session_tz = market.session_timezone

        # Aggregators
        self._h1 = _BarAggregator(3600)
        self._h4 = _BarAggregator(14400)

        # M5 history
        self._m5_bars: list[Bar] = []

        # H4 bias caches
        self._h4_closes: list[float] = []
        self._h4_ema20: list[float] = []
        self._h4_ema50: list[float] = []
        self._h4_bias: list[int] = []
        self._h4_supply: list[float] = []
        self._h4_demand: list[float] = []

        # Active CRT event
        self._active_event: dict | None = None
        self._event_m5_count = 0

        # Cooldown / execution state
        self._busy_until: datetime | None = None
        self._pending_active = False

    def set_execution_state(self, *, pending: bool, position: bool, cooldown: int) -> None:
        del position, cooldown
        self._pending_active = pending

    def fresh(self) -> CrtTbsStrategy:
        return CrtTbsStrategy(self.config, market=self.market)

    def parameters(self) -> dict[str, object]:
        return {
            "strategy": "CrtTbsStrategy",
            "ticker": self.config.ticker,
            "h1_sweep_lookback": self.config.h1_sweep_lookback,
            "h1_body_ratio": self.config.h1_body_ratio,
            "m5_sweep_lookback": self.config.m5_sweep_lookback,
            "m5_confirm_bars": self.config.m5_confirm_bars,
            "min_rr": self.config.min_rr,
            "max_risk_points": self.config.max_risk_points,
            "min_risk_points": self.config.min_risk_points,
            "session_start": self.config.session_start,
            "session_end": self.config.session_end,
            "require_4h_bias": self.config.require_4h_bias,
            "require_half_zone": self.config.require_half_zone,
            "target_mode": self.config.target_mode,
            "fixed_rr": self.config.fixed_rr,
            "time_exit_mode": self.config.time_exit_mode,
            "cooldown_hours": self.config.cooldown_hours,
        }

    def _update_h4(self) -> None:
        """Update H4 indicators on closed H4 bars."""
        k20 = 2.0 / (20.0 + 1.0)
        k50 = 2.0 / (50.0 + 1.0)
        start_idx = len(self._h4_closes)
        for i in range(start_idx, len(self._h4.closed)):
            bar = self._h4.closed[i]
            c = bar.close
            self._h4_closes.append(c)

            # EMA20
            if not self._h4_ema20:
                ema20 = c
            else:
                ema20 = c * k20 + self._h4_ema20[-1] * (1.0 - k20)
            self._h4_ema20.append(ema20)

            # EMA50
            if not self._h4_ema50:
                ema50 = c
            else:
                ema50 = c * k50 + self._h4_ema50[-1] * (1.0 - k50)
            self._h4_ema50.append(ema50)

            # Slope of EMA20 (diff 3)
            slope = (self._h4_ema20[-1] - self._h4_ema20[-4]) if len(self._h4_ema20) >= 4 else 0.0

            # Bias
            if c > ema50 and ema20 > ema50 and slope > 0:
                bias = 1
            elif c < ema50 and ema20 < ema50 and slope < 0:
                bias = -1
            else:
                bias = 0
            self._h4_bias.append(bias)

            # Supply: max high of prior 10 closed H4 bars (excluding current bar)
            if i >= 10:
                prior_highs = [b.high for b in self._h4.closed[i - 10 : i]]
                prior_lows = [b.low for b in self._h4.closed[i - 10 : i]]
                self._h4_supply.append(max(prior_highs))
                self._h4_demand.append(min(prior_lows))
            elif i >= 1:
                prior_highs = [b.high for b in self._h4.closed[:i]]
                prior_lows = [b.low for b in self._h4.closed[:i]]
                self._h4_supply.append(max(prior_highs))
                self._h4_demand.append(min(prior_lows))
            else:
                self._h4_supply.append(float("nan"))
                self._h4_demand.append(float("nan"))

    def _h4_state_at(self, t: datetime) -> tuple[int, float, float]:
        """Return (bias, supply, demand) from completed H4 bars at or before t."""
        # Find latest H4 bar whose close_time <= t
        idx = -1
        for i in range(len(self._h4.closed) - 1, -1, -1):
            if self._h4.closed[i].close_time <= t:
                idx = i
                break
        if idx < 0 or idx >= len(self._h4_bias):
            return 0, float("nan"), float("nan")
        return self._h4_bias[idx], self._h4_supply[idx], self._h4_demand[idx]

    def _check_h1_crt(self) -> None:
        """Check if the latest closed H1 bar formed a valid CRT setup."""
        h1_closed = self._h1.closed
        if len(h1_closed) < max(60, self.config.h1_sweep_lookback + 2):
            return

        row = h1_closed[-1]
        prev = h1_closed[-2]
        look = h1_closed[-1 - self.config.h1_sweep_lookback : -1]

        rng = row.high - row.low
        if rng <= 0:
            return
        body_ratio = abs(row.close - row.open) / rng
        if body_ratio < self.config.h1_body_ratio:
            return

        crh = row.high
        crl = row.low
        ce = (crh + crl) / 2.0
        direction = 0
        purged_level = float("nan")

        look_high = max(b.high for b in look)
        look_low = min(b.low for b in look)

        if crh > look_high and row.close < prev.low:
            direction = -1
            purged_level = look_high
        elif crl < look_low and row.close > prev.high:
            direction = 1
            purged_level = look_low

        if direction == 0:
            return

        # H4 filter at the moment H1 closes
        bias, supply, demand = self._h4_state_at(row.close_time)
        in_zone = False
        if direction == -1 and math.isfinite(supply):
            in_zone = crh >= supply - 0.25 * (crh - crl)
        if direction == 1 and math.isfinite(demand):
            in_zone = crl <= demand + 0.25 * (crh - crl)

        if self.config.require_4h_bias and not (bias == direction or in_zone):
            return

        # New CRT setup armed
        self._active_event = {
            "time": row.close_time,
            "direction": direction,
            "crh": crh,
            "crl": crl,
            "ce": ce,
            "target": crl if direction == -1 else crh,
            "purged_level": purged_level,
            "h4_bias": bias,
            "in_4h_zone": in_zone,
        }
        self._event_m5_count = 0

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        if not history:
            return None

        # Synchronize bar history
        while len(self._m5_bars) < len(history):
            new_bar = history[len(self._m5_bars)]
            self._m5_bars.append(new_bar)

            h1_closed = self._h1.add(new_bar)
            h4_closed = self._h4.add(new_bar)

            if h4_closed:
                self._update_h4()
            if h1_closed:
                self._check_h1_crt()

            if self._active_event is not None:
                self._event_m5_count += 1
                if self._event_m5_count > self.config.m5_confirm_bars:
                    self._active_event = None
                    self._event_m5_count = 0

        bar = self._m5_bars[-1]
        if self._pending_active:
            return None

        if self._busy_until is not None and bar.timestamp <= self._busy_until:
            return None

        if self._active_event is None:
            return None

        # M5 confirmation checks
        if len(self._m5_bars) < self.config.m5_sweep_lookback + 2:
            return None

        # Session time check in ET
        bar_et = bar.timestamp.astimezone(self.session_tz)
        bar_time_str = bar_et.strftime("%H:%M")
        if not (self.config.session_start <= bar_time_str <= self.config.session_end):
            return None

        ev = self._active_event
        direction = ev["direction"]
        close = bar.close
        prev_bar = self._m5_bars[-2]
        look_m5 = self._m5_bars[-1 - self.config.m5_sweep_lookback : -1]

        signal = None
        if direction == -1:
            if self.config.require_half_zone and close > ev["ce"]:
                return None
            swept = bar.high > max(b.high for b in look_m5)
            cisd = close < prev_bar.low
            engulf = close < bar.open and close < prev_bar.open
            if swept and (cisd or engulf):
                entry = close
                stop = max(bar.high, ev["crh"])
                risk = stop - entry
                if self.config.target_mode == "fixed_rr":
                    target = entry - self.config.fixed_rr * risk
                else:
                    target = float(ev["target"])
                reward = entry - target
                if (
                    risk >= self.config.min_risk_points
                    and risk <= self.config.max_risk_points
                    and reward > 0
                    and (reward / risk) >= self.config.min_rr
                ):
                    signal = Signal(direction="short", entry=entry, stop=stop, target=target)
        else:
            if self.config.require_half_zone and close < ev["ce"]:
                return None
            swept = bar.low < min(b.low for b in look_m5)
            cisd = close > prev_bar.high
            engulf = close > bar.open and close > prev_bar.open
            if swept and (cisd or engulf):
                entry = close
                stop = min(bar.low, ev["crl"])
                risk = entry - stop
                if self.config.target_mode == "fixed_rr":
                    target = entry + self.config.fixed_rr * risk
                else:
                    target = float(ev["target"])
                reward = target - entry
                if (
                    risk >= self.config.min_risk_points
                    and risk <= self.config.max_risk_points
                    and reward > 0
                    and (reward / risk) >= self.config.min_rr
                ):
                    signal = Signal(direction="long", entry=entry, stop=stop, target=target)

        if signal is not None:
            self._busy_until = bar.timestamp + timedelta(hours=self.config.cooldown_hours)
            self._active_event = None
            self._event_m5_count = 0

        return signal

    def observe(self, history: Sequence[Bar]) -> None:
        """Advance aggregators without emitting new signals."""
        self.evaluate(history)


def crt_tbs_config(
    *,
    market: MarketSpec = MNQ,
    time_exit_mode: str = "flat",
    max_bars_held: int = 192,
    **overrides,
) -> BacktestConfig:
    """Execution config for CRT-TBS."""
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
        "time_exit_mode": time_exit_mode,
    }
    params.update(overrides)
    return BacktestConfig(**params)


__all__ = ["CrtTbsConfig", "CrtTbsStrategy", "crt_tbs_config"]

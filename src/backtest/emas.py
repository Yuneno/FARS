"""Causal M5 port of kai's deployed EMA trend strategy.

The port keeps the production/default branch: EMA 10/20/55/200 bias and
breakout, ET session gates, volume/distance vetoes, closed M15/H1 confirmation,
ATR-SMA(14) stop, next-bar market entry, and TP1 partial + break-even handling.
Reference branches whose defaults are off (counter-trend and video presets) are
intentionally outside this pilot.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal

from src.backtest.executor import BacktestConfig
from src.backtest.history import Bar
from src.backtest.markets import MNQ, MarketSpec
from src.backtest.strategy import Signal

BIAS_THRESHOLD = 0.0005
ATR_PERIOD = 14

EmasReason = Literal[
    "volume_climax",
    "too_far",
    "chop",
    "htf_against",
    "accepted",
    "closed",
]


@dataclass(frozen=True)
class EmasDecision:
    """Observational snapshot of a breakout candidate and its outcome."""

    timestamp: datetime
    direction: Literal["long", "short"]
    entry: float
    risk: float | None
    session_hour_et: int
    decision: EmasReason
    r_result: float | None = None
    exit_time: datetime | None = None


class _Ema:
    def __init__(self, period: int) -> None:
        self.period = period
        self.count = 0
        self.seed_sum = 0.0
        self.value = math.nan

    def add(self, value: float) -> float:
        self.count += 1
        if self.count <= self.period:
            self.seed_sum += value
            if self.count == self.period:
                self.value = self.seed_sum / self.period
        else:
            k = 2.0 / (self.period + 1.0)
            self.value = value * k + self.value * (1.0 - k)
        return self.value


class _HigherTimeframeEma:
    """UTC-aligned OHLC bucket closes with point-in-time completion lookup."""

    def __init__(self, seconds: int, ema_period: int) -> None:
        self.seconds = seconds
        self.ema = _Ema(ema_period)
        self.key: int | None = None
        self.first_timestamp: datetime | None = None
        self.close = 0.0
        self.finalized: list[tuple[datetime, float]] = []

    def add(self, bar: Bar) -> None:
        key = int(bar.timestamp.timestamp()) // self.seconds
        if self.key is None:
            self.key = key
            self.first_timestamp = bar.timestamp
            self.close = bar.close
            return
        if key == self.key:
            self.close = bar.close
            return
        assert self.first_timestamp is not None
        self.finalized.append((self.first_timestamp, self.ema.add(self.close)))
        self.key = key
        self.first_timestamp = bar.timestamp
        self.close = bar.close

    def latest_completed(self, timestamp: datetime) -> float | None:
        duration = timedelta(seconds=self.seconds)
        for first, value in reversed(self.finalized):
            if first + duration <= timestamp:
                return value if math.isfinite(value) else None
        return None


def _parse_hours(raw: str) -> frozenset[int]:
    values: set[int] = set()
    for token in str(raw or "").split(","):
        try:
            hour = int(token.strip())
        except ValueError:
            continue
        if 0 <= hour <= 23:
            values.add(hour)
    return frozenset(values)


def _parse_windows(raw: str) -> tuple[tuple[int, int], ...]:
    values: list[tuple[int, int]] = []
    for token in str(raw or "").split(","):
        try:
            start, end = token.strip().split("-")
            sh, sm = (int(value) for value in start.split(":"))
            eh, em = (int(value) for value in end.split(":"))
        except ValueError:
            continue
        first, last = sh * 60 + sm, eh * 60 + em
        if first < last:
            values.append((first, last))
    return tuple(values)


class EmasStrategy:
    """Incremental closed-bar implementation of the kai EMAS default branch."""

    def __init__(
        self,
        *,
        market: MarketSpec = MNQ,
        f: float = 0.5,
        target_rr: float = 3.0,
        confirm_closes: int = 3,
        atr_mult: float = 2.0,
        stop_min: float = 5.0,
        stop_max: float = 50.0,
        hivol_atr: float = 30.0,
        hivol_mult: float = 1.6,
        max_dist_atr: float = 0.5,
        vol3_max: float = 1.8,
        chop_atr: float = 0.0,
        htf_filter: bool = True,
        max_trades_day: int = 3,
        session_block: str = "18,21,22,23",
        blackout: str = "09:15-09:45",
        p_entry: int = 10,
        p_second: int = 20,
        p_pullback: int = 55,
        p_bias: int = 200,
        entry_on_20: bool = False,
        log_decisions: bool = True,
    ) -> None:
        if not isinstance(market, MarketSpec):
            raise TypeError("market must be a MarketSpec")
        finite_positive = {
            "target_rr": target_rr,
            "atr_mult": atr_mult,
            "stop_min": stop_min,
            "stop_max": stop_max,
            "hivol_mult": hivol_mult,
            "max_dist_atr": max_dist_atr,
        }
        if not 0.0 < f < 1.0:
            raise ValueError("f must be in (0, 1)")
        for name, value in finite_positive.items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and > 0")
        if stop_min > stop_max:
            raise ValueError("stop_min must be <= stop_max")
        if not math.isfinite(hivol_atr) or hivol_atr < 0:
            raise ValueError("hivol_atr must be finite and >= 0")
        if not math.isfinite(vol3_max) or vol3_max < 0:
            raise ValueError("vol3_max must be finite and >= 0")
        if not math.isfinite(chop_atr) or chop_atr < 0:
            raise ValueError("chop_atr must be finite and >= 0")
        if confirm_closes < 1 or max_trades_day < 0:
            raise ValueError("confirm_closes must be >= 1 and max_trades_day >= 0")
        periods = (p_entry, p_second, p_pullback, p_bias)
        if any(period < 1 for period in periods):
            raise ValueError("EMA periods must be >= 1")

        self.market = market
        self.session_tz = market.session_timezone
        self.f = float(f)
        self.target_rr = float(target_rr)
        self.confirm_closes = int(confirm_closes)
        self.atr_mult = float(atr_mult)
        self.stop_min = float(stop_min)
        self.stop_max = float(stop_max)
        self.hivol_atr = float(hivol_atr)
        self.hivol_mult = float(hivol_mult)
        self.max_dist_atr = float(max_dist_atr)
        self.vol3_max = float(vol3_max)
        self.chop_atr = float(chop_atr)
        self.htf_filter = bool(htf_filter)
        self.max_trades_day = int(max_trades_day)
        self.session_block = session_block
        self.blackout = blackout
        self.p_entry = int(p_entry)
        self.p_second = int(p_second)
        self.p_pullback = int(p_pullback)
        self.p_bias = int(p_bias)
        self.entry_on_20 = bool(entry_on_20)
        self.log_decisions = bool(log_decisions)
        self.decisions: list[EmasDecision] = []

        self._blocked_hours = _parse_hours(session_block)
        self._blackout_windows = _parse_windows(blackout)
        self._emas = {period: _Ema(period) for period in periods}
        self._m15 = _HigherTimeframeEma(15 * 60, self.p_bias)
        self._h1 = _HigherTimeframeEma(60 * 60, self.p_pullback)
        self._open: list[float] = []
        self._high: list[float] = []
        self._low: list[float] = []
        self._close: list[float] = []
        self._volume: list[float] = []
        self._timestamps: list[datetime] = []
        self._seen = 0
        self._ema_now: dict[int, float] = {}
        self._atr = math.nan
        self._true_ranges: list[float] = []
        self._day_key: tuple[int, int, int] | None = None
        self._day_count = 0
        self._active_decision: int | None = None

    def _append(self, bar: Bar) -> None:
        previous_close = self._close[-1] if self._close else bar.close
        true_range = max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        )
        self._true_ranges.append(true_range)
        if len(self._true_ranges) >= ATR_PERIOD:
            self._atr = sum(self._true_ranges[-ATR_PERIOD:]) / ATR_PERIOD
        self._open.append(bar.open)
        self._high.append(bar.high)
        self._low.append(bar.low)
        self._close.append(bar.close)
        self._volume.append(bar.volume)
        self._timestamps.append(bar.timestamp)
        for period, ema in self._emas.items():
            self._ema_now[period] = ema.add(bar.close)
        self._m15.add(bar)
        self._h1.add(bar)
        self._seen += 1

    def _sync(self, history: Sequence[Bar]) -> bool:
        if self._seen and (
            len(history) < self._seen
            or history[self._seen - 1].timestamp != self._timestamps[-1]
        ):
            raise ValueError("history must be an append-only chronological sequence")
        changed = self._seen < len(history)
        for index in range(self._seen, len(history)):
            self._append(history[index])
        return changed

    def observe(self, history: Sequence[Bar]) -> None:
        self._sync(history)

    def _record(
        self,
        t: int,
        direction: Literal["long", "short"],
        reason: EmasReason,
        risk: float | None,
        close_hour: int,
    ) -> None:
        if not self.log_decisions:
            return
        self.decisions.append(
            EmasDecision(
                timestamp=self._timestamps[t],
                direction=direction,
                entry=self._close[t],
                risk=risk,
                session_hour_et=close_hour,
                decision=reason,
            )
        )
        if reason == "accepted":
            self._active_decision = len(self.decisions) - 1

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        if not self._sync(history) or not history:
            return None
        t = self._seen - 1
        start = max(self.p_bias, self.confirm_closes + 2, 21, ATR_PERIOD)
        if t < start:
            return None

        close_time = (self._timestamps[t] + timedelta(minutes=5)).astimezone(
            self.session_tz
        )
        minute_of_day = close_time.hour * 60 + close_time.minute
        if close_time.hour in self._blocked_hours or any(
            first <= minute_of_day < last for first, last in self._blackout_windows
        ):
            return None
        day_key = (close_time.year, close_time.month, close_time.day)
        if day_key != self._day_key:
            self._day_key = day_key
            self._day_count = 0
        if self.max_trades_day > 0 and self._day_count >= self.max_trades_day:
            return None

        a10 = self._ema_now[self.p_entry]
        a20 = self._ema_now[self.p_second]
        a55 = self._ema_now[self.p_pullback]
        a200 = self._ema_now[self.p_bias]
        atr = self._atr
        if not math.isfinite(a200) or not math.isfinite(atr) or atr <= 0:
            return None
        price = self._close[t]
        threshold = a200 * BIAS_THRESHOLD
        if price > a200 + threshold:
            side: Literal["long", "short"] = "long"
        elif price < a200 - threshold:
            side = "short"
        else:
            return None

        aligned = (
            a10 > a20 > a55 > a200
            if side == "long"
            else a10 < a20 < a55 < a200
        )
        nwin = min(self.confirm_closes, t)
        break_index = t - nwin + 1

        def long_ok(required: float) -> bool:
            if min(self._close[break_index : t + 1]) <= required:
                return False
            return self._low[break_index] <= required or (
                break_index > 0 and self._close[break_index - 1] <= required
            )

        def short_ok(required: float) -> bool:
            if max(self._close[break_index : t + 1]) >= required:
                return False
            return self._high[break_index] >= required or (
                break_index > 0 and self._close[break_index - 1] >= required
            )

        if side == "long":
            required = a10 if aligned else max(a10, a55)
            breakout = long_ok(required) or (self.entry_on_20 and long_ok(a20))
        else:
            required = a10 if aligned else min(a10, a55)
            breakout = short_ok(required) or (self.entry_on_20 and short_ok(a20))
        if not breakout:
            return None

        if self.chop_atr > 0 and abs(a20 - a55) < self.chop_atr * atr:
            self._record(t, side, "chop", None, close_time.hour)
            return None
        if self.vol3_max > 0 and t >= 20:
            average_previous = sum(self._volume[t - 20 : t]) / 20.0
            volume_three = sum(self._volume[t - 2 : t + 1])
            if average_previous > 0 and volume_three / (3.0 * average_previous) > self.vol3_max:
                self._record(t, side, "volume_climax", None, close_time.hour)
                return None
        distance = abs(price - a10)
        if self.entry_on_20:
            distance = min(distance, abs(price - a20))
        if distance > self.max_dist_atr * atr:
            self._record(t, side, "too_far", None, close_time.hour)
            return None

        if self.htf_filter:
            m15_bias = self._m15.latest_completed(self._timestamps[t])
            if m15_bias is not None and ((price > m15_bias) != (side == "long")):
                self._record(t, side, "htf_against", None, close_time.hour)
                return None
            h1_bias = self._h1.latest_completed(self._timestamps[t])
            if h1_bias is not None and ((price > h1_bias) != (side == "long")):
                self._record(t, side, "htf_against", None, close_time.hour)
                return None

        stop = atr * self.atr_mult
        if atr >= self.hivol_atr:
            stop *= self.hivol_mult
        stop = min(max(stop, self.stop_min), self.stop_max)
        if stop <= 0:
            return None
        self._record(t, side, "accepted", stop, close_time.hour)
        self._day_count += 1
        return Signal(
            direction=side,
            entry=price,
            stop=stop,
            target=self.target_rr * stop,
            stop_target_as_points=True,
        )

    def note_trade(self, exit_time: datetime, r_result: float) -> None:
        if self._active_decision is not None:
            index = self._active_decision
            self.decisions[index] = replace(
                self.decisions[index],
                decision="closed",
                r_result=float(r_result),
                exit_time=exit_time,
            )
            self._active_decision = None

    def clear_decisions(self) -> None:
        self.decisions.clear()
        self._active_decision = None

    def parameters(self) -> dict[str, object]:
        return {
            **({"market": self.market.to_dict()} if self.market != MNQ else {}),
            "f": self.f,
            "target_rr": self.target_rr,
            "confirm_closes": self.confirm_closes,
            "atr_mult": self.atr_mult,
            "stop_min": self.stop_min,
            "stop_max": self.stop_max,
            "hivol_atr": self.hivol_atr,
            "hivol_mult": self.hivol_mult,
            "max_dist_atr": self.max_dist_atr,
            "vol3_max": self.vol3_max,
            "chop_atr": self.chop_atr,
            "htf_filter": self.htf_filter,
            "max_trades_day": self.max_trades_day,
            "session_block": self.session_block,
            "blackout": self.blackout,
            "p_entry": self.p_entry,
            "p_second": self.p_second,
            "p_pullback": self.p_pullback,
            "p_bias": self.p_bias,
            "entry_on_20": self.entry_on_20,
            "log_decisions": self.log_decisions,
            "timeframe": "M5",
            "atr": "SMA(14)",
            "ema_seed": "SMA then recursive full-series EMA",
        }

    def fresh(self) -> EmasStrategy:
        values = self.parameters()
        values.pop("timeframe")
        values.pop("atr")
        values.pop("ema_seed")
        values.pop("market", None)
        return EmasStrategy(market=self.market, **values)


def emas_config(
    *,
    market: MarketSpec = MNQ,
    f: float = 0.5,
    discrete_partial_contracts: bool = False,
    **overrides,
) -> BacktestConfig:
    """Gross-reference execution config for EMAS."""
    params = {
        "dollar_per_point": market.dollar_per_point,
        "tick_size": market.tick_size,
        "commission_per_side": 0.0,
        "slippage_points": 0.0,
        "bar_interval_seconds": 300,
        "max_contracts": 1_000_000,
        "max_bars_held": 1_000_000_000,
        "partial_take_profit_fraction": f,
        "move_stop_to_break_even": True,
        "discrete_partial_contracts": discrete_partial_contracts,
    }
    params.update(overrides)
    return BacktestConfig(**params)


__all__ = ["EmasDecision", "EmasStrategy", "emas_config"]

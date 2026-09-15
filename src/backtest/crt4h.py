"""Causal FARS port of Kai's CRT 4H + M5 entry specification.

The higher-timeframe buckets are anchored to the 18:00 America/New_York CME
session.  Only closed M5 bars are consumed; a signal emitted from bar ``t`` is
therefore eligible for a pending limit fill from bar ``t + 1`` onward.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from src.backtest.executor import BacktestConfig
from src.backtest.history import Bar
from src.backtest.markets import MNQ, MarketSpec
from src.backtest.strategy import Signal
from src.session_calendar import session_date


DEFAULT_MAX_FOLLOW = 3
DEFAULT_MIN_RR = 1.5
DEFAULT_SL_PAD_FRAC = 0.1
DEFAULT_DISP_MULT = 1.2
DEFAULT_MAX_HOLD_BARS = 96
DEFAULT_REQUIRE_BIAS = 1


@dataclass
class _Bucket:
    key: object
    open: float
    high: float
    low: float
    close: float
    start: int
    end: int

    def update(self, bar: Bar, end: int) -> None:
        self.high = max(self.high, bar.high)
        self.low = min(self.low, bar.low)
        self.close = bar.close
        self.end = end


def _session_key(ts: datetime) -> date:
    key = session_date(ts, reset_hour=18, skip_weekends=False)
    assert key is not None
    return key


def _h4_key(ts: datetime, timezone) -> tuple[date, int]:
    local = ts.astimezone(timezone)
    key = _session_key(ts)
    minutes_from_open = ((local.hour - 18) % 24) * 60 + local.minute
    return key, minutes_from_open // 240


class _SessionAggregator:
    """Incremental OHLC aggregation with Kai's 18:00 ET wall-clock anchors."""

    def __init__(self, market: MarketSpec) -> None:
        self.market = market
        self.days: list[_Bucket] = []
        self.h4: list[_Bucket] = []
        self.day_of: list[int] = []
        self.h4_of: list[int] = []

    @staticmethod
    def _add(bucket_list: list[_Bucket], key: object, bar: Bar, index: int) -> int:
        if not bucket_list or bucket_list[-1].key != key:
            bucket_list.append(
                _Bucket(key, bar.open, bar.high, bar.low, bar.close, index, index + 1)
            )
        else:
            bucket_list[-1].update(bar, index + 1)
        return len(bucket_list) - 1

    def add(self, bar: Bar, index: int) -> None:
        self.day_of.append(self._add(self.days, _session_key(bar.timestamp), bar, index))
        self.h4_of.append(
            self._add(self.h4, _h4_key(bar.timestamp, self.market.session_timezone), bar, index)
        )


def daily_bias(ph: float, pl: float, pc: float, h: float, l: float, c: float) -> int:
    """Kai Daily Bias Guide: +1 bullish, -1 bearish, 0 avoid."""
    del pc
    swept_h = h > ph
    swept_l = l < pl
    inside = pl <= c <= ph
    if c < pl:
        return -1
    if c > ph:
        return 1
    if swept_h and not swept_l and inside:
        return -1
    if swept_l and not swept_h and inside:
        return 1
    if swept_h and swept_l:
        return 0
    rng = ph - pl
    if rng <= 0:
        return 0
    pos = (c - pl) / rng
    if pos >= 0.66:
        return 1
    if pos <= 0.34:
        return -1
    return 0


class Crt4hStrategy:
    """Stateful implementation preserving the reference decision order."""

    def __init__(
        self,
        *,
        market: MarketSpec = MNQ,
        max_follow: int = DEFAULT_MAX_FOLLOW,
        min_rr: float = DEFAULT_MIN_RR,
        sl_pad_frac: float = DEFAULT_SL_PAD_FRAC,
        disp_mult: float = DEFAULT_DISP_MULT,
        max_hold_bars: int = DEFAULT_MAX_HOLD_BARS,
        require_bias: int | bool = DEFAULT_REQUIRE_BIAS,
    ) -> None:
        if max_follow < 1 or max_hold_bars < 1:
            raise ValueError("max_follow and max_hold_bars must be >= 1")
        if min_rr <= 0 or disp_mult <= 0 or sl_pad_frac < 0:
            raise ValueError("min_rr/disp_mult must be > 0 and sl_pad_frac >= 0")
        if not all(math.isfinite(x) for x in (min_rr, sl_pad_frac, disp_mult)):
            raise ValueError("floating parameters must be finite")
        self.market = market
        self.max_follow = int(max_follow)
        self.min_rr = float(min_rr)
        self.sl_pad_frac = float(sl_pad_frac)
        self.disp_mult = float(disp_mult)
        self.max_hold_bars = int(max_hold_bars)
        self.require_bias = bool(int(require_bias))

        self._bars: list[Bar] = []
        self._agg = _SessionAggregator(market)
        self._armed: dict[str, object] | None = None
        self._pending_active = False
        self._position_active = False
        self._open_until = -1
        self._suppress_through: datetime | None = None

    def fresh(self) -> Crt4hStrategy:
        return Crt4hStrategy(
            market=self.market,
            max_follow=self.max_follow,
            min_rr=self.min_rr,
            sl_pad_frac=self.sl_pad_frac,
            disp_mult=self.disp_mult,
            max_hold_bars=self.max_hold_bars,
            require_bias=self.require_bias,
        )

    def parameters(self) -> dict[str, object]:
        return {
            **({"market": self.market.to_dict()} if self.market != MNQ else {}),
            "max_follow": self.max_follow,
            "min_rr": self.min_rr,
            "sl_pad_frac": self.sl_pad_frac,
            "disp_mult": self.disp_mult,
            "max_hold_bars": self.max_hold_bars,
            "require_bias": int(self.require_bias),
            "timeframe": "M5",
            "session_anchor": "18:00 America/New_York",
        }

    def set_execution_state(self, *, pending: bool, position: bool, cooldown: int) -> None:
        del cooldown
        self._pending_active = pending
        self._position_active = position

    def note_order_filled(self, entry_time: datetime) -> None:
        self._suppress_through = entry_time

    def note_order_expired(self, expiry_time: datetime) -> None:
        self._suppress_through = expiry_time

    def note_trade(self, exit_time: datetime, r_result: float) -> None:
        del r_result
        self._suppress_through = exit_time
        self._open_until = len(self._bars) - 1

    def _append(self, bar: Bar) -> int:
        if self._bars and bar.timestamp <= self._bars[-1].timestamp:
            raise ValueError("history must be an append-only chronological sequence")
        index = len(self._bars)
        self._bars.append(bar)
        self._agg.add(bar, index)
        return index

    def _last_swing(self, end_index: int, side: str) -> float | None:
        for i in range(end_index - 1, 0, -1):
            if i + 1 >= len(self._bars):
                continue
            if side == "HIGH" and self._bars[i].high > self._bars[i - 1].high and self._bars[i].high > self._bars[i + 1].high:
                return self._bars[i].high
            if side == "LOW" and self._bars[i].low < self._bars[i - 1].low and self._bars[i].low < self._bars[i + 1].low:
                return self._bars[i].low
        return None

    def _detect_mss(self, raid_index: int, direction: str, hi: int) -> dict[str, object] | None:
        swing = self._last_swing(raid_index, "HIGH" if direction == "LONG" else "LOW")
        if swing is None:
            return None
        for i in range(raid_index + 1, hi):
            broke = self._bars[i].close > swing if direction == "LONG" else self._bars[i].close < swing
            if not broke:
                continue
            w0 = max(0, i - 20)
            if i <= w0:
                continue
            avg = sum(bar.high - bar.low for bar in self._bars[w0:i]) / (i - w0)
            ratio = (self._bars[i].high - self._bars[i].low) / avg if avg > 0 else 0.0
            if ratio < self.disp_mult:
                continue
            return {"index": i, "level": swing}
        return None

    def _detect_fvg(self, raid_index: int, direction: str, mss_index: int) -> dict[str, float] | None:
        zone = None
        end = min(mss_index, len(self._bars) - 3)
        for i in range(raid_index, end + 1):
            if i + 2 >= len(self._bars):
                break
            if direction == "LONG" and self._bars[i + 2].low > self._bars[i].high:
                zone = {"entry_edge": self._bars[i].high, "near_edge": self._bars[i + 2].low}
            if direction == "SHORT" and self._bars[i + 2].high < self._bars[i].low:
                zone = {"entry_edge": self._bars[i].low, "near_edge": self._bars[i + 2].high}
        return zone

    def _process_armed(self, t: int) -> Signal | None:
        assert self._armed is not None
        armed = self._armed
        raid_i = int(armed["raid_i"])
        window_end = raid_i + self.max_hold_bars + 30
        hi_scan = min(len(self._bars), window_end, t + 1)
        if hi_scan > raid_i + 1:
            direction = str(armed["direction"])
            mss = self._detect_mss(raid_i, direction, hi_scan)
            if mss is not None and int(mss["index"]) == t:
                fvg = self._detect_fvg(raid_i, direction, int(mss["index"]))
                if fvg is not None:
                    entry = fvg["entry_edge"]
                    extreme = float(armed["extreme"])
                    pad = abs(extreme - float(armed["c1_close"])) * self.sl_pad_frac
                    stop = extreme - pad if direction == "LONG" else extreme + pad
                    risk = abs(entry - stop)
                    if risk > 0:
                        target = float(armed["target"])
                        rr = (target - entry) / risk if direction == "LONG" else (entry - target) / risk
                        if rr >= self.min_rr:
                            self._armed = None
                            return Signal(direction.lower(), entry, stop, target)
                self._armed = None
                return None
        if t >= window_end - 1:
            self._armed = None
        return None

    def _process_setup(self, t: int) -> Signal | None:
        if self._armed is not None:
            return self._process_armed(t)
        h = self._agg.h4_of[t] - 1
        if h < 0:
            return None
        c1 = self._agg.h4[h]
        if c1.end > t - 8 or c1.end <= self._open_until:
            return None
        i0 = c1.end
        last_h = min(h + self.max_follow, len(self._agg.h4) - 1)
        i1 = min(self._agg.h4[last_h].end, t + 1)
        sweep_lo_i = sweep_hi_i = None
        sweep_lo_x = sweep_hi_x = None
        broke_up_i = broke_dn_i = None
        for i in range(i0, i1):
            bar = self._bars[i]
            if sweep_lo_i is None and bar.low < c1.low:
                sweep_lo_i, sweep_lo_x = i, bar.low
            elif sweep_lo_i is not None and bar.low < float(sweep_lo_x):
                sweep_lo_i, sweep_lo_x = i, bar.low
            if sweep_hi_i is None and bar.high > c1.high:
                sweep_hi_i, sweep_hi_x = i, bar.high
            elif sweep_hi_i is not None and bar.high > float(sweep_hi_x):
                sweep_hi_i, sweep_hi_x = i, bar.high
            if broke_up_i is None and sweep_lo_i is not None and i > sweep_lo_i and bar.close > c1.close:
                broke_up_i = i
            if broke_dn_i is None and sweep_hi_i is not None and i > sweep_hi_i and bar.close < c1.close:
                broke_dn_i = i

        next_h = min(h + 1, len(self._agg.h4) - 1)
        day_index = self._agg.day_of[self._agg.h4[next_h].start]
        bias = 0
        if day_index >= 2:
            previous2 = self._agg.days[day_index - 2]
            previous1 = self._agg.days[day_index - 1]
            bias = daily_bias(previous2.high, previous2.low, previous2.close, previous1.high, previous1.low, previous1.close)
        setups: list[tuple[str, int, float, float, int]] = []
        if broke_up_i is not None and (not self.require_bias or bias == 1):
            setups.append(("LONG", int(sweep_lo_i), float(sweep_lo_x), c1.high, broke_up_i))
        if broke_dn_i is not None and (not self.require_bias or bias == -1):
            setups.append(("SHORT", int(sweep_hi_i), float(sweep_hi_x), c1.low, broke_dn_i))
        if len(setups) != 1:
            return None
        direction, raid_i, extreme, target, broke_i = setups[0]
        raid_i = max(raid_i, broke_i)
        if raid_i != t and broke_i != t:
            return None
        self._armed = {
            "direction": direction,
            "raid_i": raid_i,
            "extreme": extreme,
            "target": target,
            "c1_close": c1.close,
        }
        return None

    def _sync(self, history: Sequence[Bar], *, emit: bool) -> Signal | None:
        if self._bars and (len(history) < len(self._bars) or history[len(self._bars) - 1].timestamp != self._bars[-1].timestamp):
            raise ValueError("history must be an append-only chronological sequence")
        signal = None
        final = len(history) - 1
        for source_index in range(len(self._bars), len(history)):
            t = self._append(history[source_index])
            execution_suppressed = (
                not emit
                or self._pending_active
                or self._position_active
                or (self._suppress_through is not None and self._bars[t].timestamp <= self._suppress_through)
            )
            if not execution_suppressed:
                candidate = self._process_setup(t)
                if source_index == final:
                    signal = candidate
        return signal

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        return self._sync(history, emit=True)

    def observe(self, history: Sequence[Bar]) -> None:
        self._sync(history, emit=False)


def crt4h_config(
    *,
    market: MarketSpec = MNQ,
    max_hold_bars: int = DEFAULT_MAX_HOLD_BARS,
    **overrides,
) -> BacktestConfig:
    """Execution mechanics matching the reference limit and holding windows."""
    # The shared walk-forward runner requests discrete partial accounting for
    # every family; CRT 4H has no partial exit in the authoritative spec.
    overrides.pop("discrete_partial_contracts", None)
    params = {
        "dollar_per_point": market.dollar_per_point,
        "tick_size": market.tick_size,
        "commission_per_side": 0.0,
        "slippage_points": 0.0,
        "bar_interval_seconds": 300,
        "max_contracts": 1_000_000,
        # Executor checks exits before its close-based bar limit.  95 therefore
        # checks fill..fill+95 and exits at that last close, as Kai's 96-bar window.
        "max_bars_held": max_hold_bars - 1,
        "partial_take_profit_fraction": 0.0,
        "move_stop_to_break_even": False,
        "pending_limit_entry": True,
        "pending_order_wait_bars": max_hold_bars,
        "cooldown_bars": 0,
        "time_exit_mode": "market",
    }
    params.update(overrides)
    return BacktestConfig(**params)


__all__ = ["Crt4hStrategy", "crt4h_config", "daily_bias"]

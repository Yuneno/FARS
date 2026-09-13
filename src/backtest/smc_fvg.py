"""Causal port of kai's swing-structure + fair-value-gap strategy.

Signals are formed from closed M5 bars.  A bullish imbalance is
``high[t-2] < low[t]`` (the bearish rule is mirrored); the executor holds the
proximal FVG edge as a pending limit for ``wait`` bars.  Swing pivots are only
confirmed after ``swing_w`` later bars, preserving the reference's point-in-time
structure state.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from src.backtest.executor import BacktestConfig
from src.backtest.history import Bar
from src.backtest.markets import MNQ, MarketSpec
from src.backtest.strategy import Signal

DEFAULT_FRACTION = 0.5
DEFAULT_SWING_W = 5
DEFAULT_TARGET_RR = 1.5
DEFAULT_WAIT = 48
DEFAULT_MIN_RISK_PTS = 8.0
DEFAULT_COOLDOWN = 6

SmcFvgReason = Literal["accepted", "risk_below_min", "expired", "closed"]


@dataclass(frozen=True)
class SmcFvgDecision:
    """Observational snapshot of an FVG order candidate and its outcome."""

    timestamp: datetime
    direction: Literal["long", "short"]
    entry: float
    stop: float
    target: float
    risk: float
    decision: SmcFvgReason
    r_result: float | None = None
    exit_time: datetime | None = None


class SmcFvgStrategy:
    """Stateful, causal SMC-FVG signal layer consumed by the FARS executor."""

    def __init__(
        self,
        *,
        market: MarketSpec = MNQ,
        f: float = DEFAULT_FRACTION,
        swing_w: int = DEFAULT_SWING_W,
        target_rr: float = DEFAULT_TARGET_RR,
        wait: int = DEFAULT_WAIT,
        min_risk_pts: float = DEFAULT_MIN_RISK_PTS,
        cooldown: int = DEFAULT_COOLDOWN,
        log_decisions: bool = True,
    ) -> None:
        if not isinstance(market, MarketSpec):
            raise TypeError("market must be a MarketSpec")
        if not 0.0 < f < 1.0:
            raise ValueError("f must be in (0, 1)")
        if swing_w < 1:
            raise ValueError("swing_w must be >= 1")
        if target_rr <= 0 or not math.isfinite(target_rr):
            raise ValueError("target_rr must be finite and > 0")
        if wait < 1:
            raise ValueError("wait must be >= 1")
        if min_risk_pts < 0 or not math.isfinite(min_risk_pts):
            raise ValueError("min_risk_pts must be finite and >= 0")
        if cooldown < 0:
            raise ValueError("cooldown must be >= 0")
        self.market = market
        self.f = float(f)
        self.swing_w = int(swing_w)
        self.target_rr = float(target_rr)
        self.wait = int(wait)
        self.min_risk_pts = float(min_risk_pts)
        self.cooldown = int(cooldown)
        self.log_decisions = bool(log_decisions)
        self.session_tz = market.session_timezone
        self.decisions: list[SmcFvgDecision] = []

        self._high: list[float] = []
        self._low: list[float] = []
        self._close: list[float] = []
        self._timestamps: list[datetime] = []
        self._seen = 0
        self._sh_p: float | None = None
        self._sh_i: int | None = None
        self._sl_p: float | None = None
        self._sl_i: int | None = None
        self._trend = 0
        self._pending_active = False
        self._active_decision: int | None = None

    def set_execution_state(self, *, pending: bool, position: bool, cooldown: int) -> None:
        del position, cooldown
        self._pending_active = pending

    def _append(self, bar: Bar) -> int:
        self._high.append(bar.high)
        self._low.append(bar.low)
        self._close.append(bar.close)
        self._timestamps.append(bar.timestamp)
        self._seen += 1
        return self._seen - 1

    def _update_pivots(self, t: int) -> None:
        w = self.swing_w
        i = t - w
        if i - w < 0:
            return
        if self._high[i] == max(self._high[i - w : i + w + 1]):
            self._sh_p, self._sh_i = self._high[i], i
        if self._low[i] == min(self._low[i - w : i + w + 1]):
            self._sl_p, self._sl_i = self._low[i], i

    def _structure_and_signal(self, t: int, *, emit: bool) -> Signal | None:
        # The reference gates the whole structure/FVG block on both most-recent
        # pivots being available. A consumed BOS pivot therefore pauses further
        # structure changes until that side is confirmed again.
        if self._sh_p is None or self._sl_p is None:
            return None
        bullish_break = (
            self._sl_i is not None
            and self._sh_i is not None
            and self._close[t] > self._sh_p
            and self._sl_i < self._sh_i
        )
        bearish_break = (
            self._sh_i is not None
            and self._sl_i is not None
            and self._close[t] < self._sl_p
            and self._sh_i < self._sl_i
        )
        if bullish_break:
            self._trend = 1
            self._sh_p = None
        elif bearish_break:
            self._trend = -1
            self._sl_p = None
        if not emit or self._pending_active or t < 3:
            return None

        direction: Literal["long", "short"]
        if self._trend == 1 and self._high[t - 2] < self._low[t]:
            direction = "long"
            entry = self._low[t]
            stop = self._high[t - 2] - abs(entry) * 1e-4
            risk = entry - stop
            target = entry + self.target_rr * risk
        elif self._trend == -1 and self._low[t - 2] > self._high[t]:
            direction = "short"
            entry = self._high[t]
            stop = self._low[t - 2] + abs(entry) * 1e-4
            risk = stop - entry
            target = entry - self.target_rr * risk
        else:
            return None
        if risk <= 0:
            return None

        decision: SmcFvgReason = (
            "accepted" if risk >= self.min_risk_pts else "risk_below_min"
        )
        if self.log_decisions:
            self.decisions.append(
                SmcFvgDecision(
                    timestamp=self._timestamps[t],
                    direction=direction,
                    entry=entry,
                    stop=stop,
                    target=target,
                    risk=risk,
                    decision=decision,
                )
            )
            if decision == "accepted":
                self._active_decision = len(self.decisions) - 1
        if decision != "accepted":
            return None
        return Signal(direction, entry, stop, target)

    def _sync(self, history: Sequence[Bar], *, emit_last: bool, pivots_only: bool) -> Signal | None:
        if self._seen and (
            len(history) < self._seen
            or history[self._seen - 1].timestamp != self._timestamps[-1]
        ):
            raise ValueError("history must be an append-only chronological sequence")
        signal = None
        final = len(history) - 1
        for index in range(self._seen, len(history)):
            t = self._append(history[index])
            self._update_pivots(t)
            if not pivots_only and t >= 2 * self.swing_w + 2:
                signal = self._structure_and_signal(
                    t, emit=emit_last and index == final
                )
        return signal

    def observe(self, history: Sequence[Bar]) -> None:
        """Update delayed pivots on bars where execution suppresses new setups."""
        self._sync(history, emit_last=False, pivots_only=True)

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        return self._sync(history, emit_last=True, pivots_only=False)

    def note_order_filled(self, entry_time: datetime) -> None:
        del entry_time

    def note_order_expired(self, expiry_time: datetime) -> None:
        if self._active_decision is not None:
            index = self._active_decision
            self.decisions[index] = replace(
                self.decisions[index], decision="expired", exit_time=expiry_time
            )
            self._active_decision = None

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
            "swing_w": self.swing_w,
            "target_rr": self.target_rr,
            "wait": self.wait,
            "min_risk_pts": self.min_risk_pts,
            "cooldown": self.cooldown,
            "log_decisions": self.log_decisions,
            "timeframe": "M5",
            "pivot_confirmation": "point_in_time",
        }

    def fresh(self) -> SmcFvgStrategy:
        return SmcFvgStrategy(
            market=self.market,
            f=self.f,
            swing_w=self.swing_w,
            target_rr=self.target_rr,
            wait=self.wait,
            min_risk_pts=self.min_risk_pts,
            cooldown=self.cooldown,
            log_decisions=self.log_decisions,
        )


def smc_fvg_config(
    *,
    market: MarketSpec = MNQ,
    f: float = DEFAULT_FRACTION,
    wait: int = DEFAULT_WAIT,
    cooldown: int = DEFAULT_COOLDOWN,
    **overrides,
) -> BacktestConfig:
    """Gross-reference execution config for SMC-FVG."""
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
        "pending_limit_entry": True,
        "pending_order_wait_bars": wait,
        "cooldown_bars": cooldown,
    }
    params.update(overrides)
    return BacktestConfig(**params)


__all__ = ["SmcFvgDecision", "SmcFvgStrategy", "smc_fvg_config"]

"""Executable backtest strategy for the FARS MVP (FASE B).

The FARS repository does not currently define SMC-FVG, SMC-OB, SRT, or any
"Juan Carlos" trading rules as executable code. To satisfy the MVP's
end-to-end path without inventing rules attributed to someone else, this module
provides a small, deterministic, look-ahead-free placeholder strategy whose
parameters are fully explicit and marked PROVISIONAL.

This is NOT a validated strategy and MUST NOT be presented as such. It exists
only so the backtest pipeline has a real signal source to exercise the
entry/TP/SL/commission/slippage and risk-rule machinery.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from src.backtest.history import Bar


@dataclass(frozen=True)
class Signal:
    """A proposed entry. Prices are floats; entry fills on the NEXT bar open.

    ``stop``/``target`` are absolute prices by default. When
    ``stop_target_as_points`` is True they are POSITIVE distances in points
    from the entry fill — the executor resolves them against the actual fill,
    so a distance signal can never be silently interpreted as an absolute price.
    """

    direction: Literal["long", "short"]
    entry: float
    stop: float
    target: float
    stop_target_as_points: bool = False


class Strategy(Protocol):
    """Minimal bar strategy: evaluate only CLOSED bars, propose next-bar entry."""

    def evaluate(self, history: Sequence[Bar]) -> Signal | None: ...


def _true_range(prev: Bar, bar: Bar) -> float:
    return max(
        bar.high - bar.low,
        abs(bar.high - prev.close),
        abs(bar.low - prev.close),
    )


def _average_true_range(history: Sequence[Bar], lookback: int) -> float:
    if len(history) < 2:
        return 0.0
    window = history[-lookback:] if lookback > 0 else list(history)
    ranges = [_true_range(window[i - 1], window[i]) for i in range(1, len(window))]
    if not ranges:
        return 0.0
    return sum(ranges) / len(ranges)


@dataclass(frozen=True)
class BreakoutStrategy:
    """Donchian-style channel breakout on closed bars (PROVISIONAL placeholder).

    A long fires when the last closed close exceeds the prior ``lookback`` bar
    high; a short fires when it falls below the prior ``lookback`` bar low.
    Stop and target are ATR multiples off the signal bar close.
    """

    lookback: int = 20
    stop_atr_mult: float = 1.5
    target_atr_mult: float = 3.0
    min_atr: float = 0.25  # MNQ: 0.25 point floor keeps stop/target sane on flat ATR

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        if len(history) < self.lookback + 1:
            return None
        prior = list(history[-self.lookback - 1 : -1])  # the N bars BEFORE the last
        last = history[-1]
        highest = max(bar.high for bar in prior)
        lowest = min(bar.low for bar in prior)
        atr = max(_average_true_range(history, self.lookback), self.min_atr)

        if last.close > highest:
            entry = last.close
            return Signal(
                direction="long",
                entry=entry,
                stop=entry - self.stop_atr_mult * atr,
                target=entry + self.target_atr_mult * atr,
            )
        if last.close < lowest:
            entry = last.close
            return Signal(
                direction="short",
                entry=entry,
                stop=entry + self.stop_atr_mult * atr,
                target=entry - self.target_atr_mult * atr,
            )
        return None


__all__ = ["BreakoutStrategy", "Signal", "Strategy", "_average_true_range"]

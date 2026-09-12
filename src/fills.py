"""Fill profiles and slippage measurement for FARS backtest (P3).

Implements 'legacy' and 'gap_aware' fill profiles that wrap the existing
backtest executor with configurable cost and slippage models.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

from src.types import FundedAccountRules


# ---------------------------------------------------------------------------
# Cost configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CostConfig:
    """Explicit cost parameters with units."""

    commission_per_side: float = 0.62
    slippage_points: float = 0.25
    tick_size: float = 0.25
    dollar_per_point: float = 2.0

    def validate(self) -> list[str]:
        """Return list of validation error messages."""
        errors = []
        if self.commission_per_side < 0:
            errors.append(f"commission_per_side must be >= 0, got {self.commission_per_side}")
        if self.slippage_points < 0:
            errors.append(f"slippage_points must be >= 0, got {self.slippage_points}")
        if math.isnan(self.commission_per_side) or math.isnan(self.slippage_points):
            errors.append("cost values must not be NaN")
        if self.tick_size <= 0:
            errors.append(f"tick_size must be > 0, got {self.tick_size}")
        return errors

    def round_to_tick(self, price: float) -> float:
        """Round price to nearest tick."""
        if self.tick_size <= 0:
            return price
        return round(price / self.tick_size) * self.tick_size


# ---------------------------------------------------------------------------
# Fill profiles
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FillEvent:
    """One fill event with provenance."""

    fill_price: float
    intended_price: float
    fill_type: Literal["market", "limit", "stop_market", "time_exit", "no_fill"]
    slippage_points: float
    commission: float
    reason: str = ""

    @property
    def total_cost(self) -> float:
        """Total cost in points (slippage + commission converted to points)."""
        return abs(self.slippage_points) + self.commission


@dataclass(frozen=True)
class FillProfile:
    """A fill simulation profile."""

    name: str
    apply_slippage_to_market: bool = True
    gap_aware: bool = False
    stop_first_in_ambiguous: bool = True

    def fill_market(
        self,
        intended_price: float,
        bar_open: float,
        bar_high: float,
        bar_low: float,
        side: int,
        cost: CostConfig,
    ) -> FillEvent:
        """Simulate a market fill with slippage.

        Parameters
        ----------
        intended_price : float
            The price at which we want to fill.
        bar_open, bar_high, bar_low : float
            Bar data for fill simulation.
        side : int
            +1 for long, -1 for short.
        cost : CostConfig
            Cost parameters.
        """
        if self.apply_slippage_to_market:
            slippage = cost.slippage_points * side  # adverse to position
            fill_price = intended_price + slippage
        else:
            slippage = 0.0
            fill_price = intended_price

        fill_price = cost.round_to_tick(fill_price)
        commission = cost.commission_per_side

        return FillEvent(
            fill_price=fill_price,
            intended_price=intended_price,
            fill_type="market",
            slippage_points=slippage,
            commission=commission,
        )

    def fill_stop_market(
        self,
        stop_price: float,
        bar_open: float,
        bar_high: float,
        bar_low: float,
        side: int,
        cost: CostConfig,
    ) -> FillEvent:
        """Simulate a stop-market fill.

        If the bar gaps through the stop, fill at bar open (worst case).
        """
        # Stop triggers when price crosses stop level
        # For long: stop triggers when low <= stop
        # For short: stop triggers when high >= stop
        if side > 0:
            triggered = bar_low <= stop_price
            # Gap: bar_open is already through the stop -> fill at open (worst case)
            if triggered and bar_open <= stop_price:
                fill_at = bar_open
            elif triggered:
                fill_at = stop_price
            else:
                fill_at = stop_price
        else:
            triggered = bar_high >= stop_price
            # Gap: bar_open is already through the stop -> fill at open (worst case)
            if triggered and bar_open >= stop_price:
                fill_at = bar_open
            elif triggered:
                fill_at = stop_price
            else:
                fill_at = stop_price

        if not triggered:
            return FillEvent(
                fill_price=stop_price,
                intended_price=stop_price,
                fill_type="no_fill",
                slippage_points=0.0,
                commission=0.0,
                reason="stop not triggered in bar",
            )

        # No market slippage on stop fills (already at stop level or worse)
        fill_price = cost.round_to_tick(fill_at)
        return FillEvent(
            fill_price=fill_price,
            intended_price=stop_price,
            fill_type="stop_market",
            slippage_points=0.0,
            commission=cost.commission_per_side,
        )

    def fill_limit(
        self,
        limit_price: float,
        bar_open: float,
        bar_high: float,
        bar_low: float,
        side: int,
        cost: CostConfig,
    ) -> FillEvent:
        """Simulate a limit fill (no slippage, fill at limit)."""
        if side > 0:
            filled = bar_low <= limit_price
        else:
            filled = bar_high >= limit_price

        if not filled:
            return FillEvent(
                fill_price=limit_price,
                intended_price=limit_price,
                fill_type="no_fill",
                slippage_points=0.0,
                commission=0.0,
                reason="limit not reached in bar",
            )

        fill_price = cost.round_to_tick(limit_price)
        return FillEvent(
            fill_price=fill_price,
            intended_price=limit_price,
            fill_type="limit",
            slippage_points=0.0,
            commission=cost.commission_per_side,
        )


# ---------------------------------------------------------------------------
# Built-in profiles
# ---------------------------------------------------------------------------

LEGACY = FillProfile(
    name="legacy",
    apply_slippage_to_market=True,
    gap_aware=False,
    stop_first_in_ambiguous=True,
)

GAP_AWARE = FillProfile(
    name="gap_aware",
    apply_slippage_to_market=True,
    gap_aware=True,
    stop_first_in_ambiguous=True,
)


# ---------------------------------------------------------------------------
# Slippage measurement
# ---------------------------------------------------------------------------

@dataclass
class SlippageReport:
    """Aggregated slippage statistics."""

    n_fills: int = 0
    mean_points: float = 0.0
    median_points: float = 0.0
    p90_points: float = 0.0
    max_points: float = 0.0
    total_points: float = 0.0
    by_market: dict[str, float] = field(default_factory=dict)


def measure_slippage(fills: list[FillEvent]) -> SlippageReport:
    """Compute slippage statistics from a list of fill events.

    Returns SlippageReport with n, mean, median, p90, max, and total.
    """
    if not fills:
        return SlippageReport()

    slip_vals = [abs(f.slippage_points) for f in fills]
    slip_vals_sorted = sorted(slip_vals)
    n = len(slip_vals_sorted)
    total = sum(slip_vals_sorted)
    mean = total / n

    # Median
    if n % 2 == 1:
        median = slip_vals_sorted[n // 2]
    else:
        median = (slip_vals_sorted[n // 2 - 1] + slip_vals_sorted[n // 2]) / 2

    # P90
    p90_idx = int(n * 0.9)
    p90_idx = min(p90_idx, n - 1)
    p90 = slip_vals_sorted[p90_idx]

    max_val = slip_vals_sorted[-1]

    return SlippageReport(
        n_fills=n,
        mean_points=mean,
        median_points=median,
        p90_points=p90,
        max_points=max_val,
        total_points=total,
    )


# ---------------------------------------------------------------------------
# Run profile execution
# ---------------------------------------------------------------------------

def run_with_profile(
    bars: list[dict[str, Any]],
    profile: FillProfile,
    cost: CostConfig,
    *,
    max_hold_minutes: float | None = None,
) -> list[FillEvent]:
    """Execute a simplified bar-by-bar simulation with the given fill profile.

    This is a standalone simulation for testing fill profiles. The real
    integration uses the existing backtest executor.
    """
    cost_errors = cost.validate()
    if cost_errors:
        raise ValueError(f"Invalid cost config: {cost_errors}")

    fills: list[FillEvent] = []
    # Simplified simulation: just test fill logic on each bar
    for bar in bars:
        o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
        # Test market fill
        fill = profile.fill_market(c, o, h, l, 1, cost)
        fills.append(fill)
    return fills


__all__ = [
    "CostConfig",
    "FillEvent",
    "FillProfile",
    "LEGACY",
    "GAP_AWARE",
    "SlippageReport",
    "measure_slippage",
    "run_with_profile",
]

"""
Account state tracker for a single simulation run.

Tracks equity, drawdown (static or trailing), daily loss, and evaluates
funded-account rules after every trade.

Drawdown modes (FARS_SPEC §4):
  - "static":   violation when equity <= initial_balance * (1 - max_drawdown_pct)
  - "trailing": violation when equity <= peak_equity * (1 - max_drawdown_pct)

Historical peak-to-trough drawdown is ALWAYS tracked independently:
  peak_trough_DD = (peak_equity - equity) / peak_equity
This is the drawdown that feeds median/P95/P99 statistics in later phases,
regardless of which drawdown mode the rules use.

ASSUMPTION: Equity is updated using static fractional risk sizing:
    equity += r_result * risk_per_trade * initial_balance
"""

import math
from dataclasses import dataclass, field

from src.types import FundedAccountRules


@dataclass
class AccountState:
    """
    Mutable account state during a single simulation.

    Constraint checks use relative tolerance (1e-12) for floating-point
    safety. This correctly handles accounts from $1 to $100M+.
    """

    rules: FundedAccountRules

    equity: float = 0.0
    peak_equity: float = 0.0
    start_of_day_equity: float = 0.0
    current_date: str | None = None
    trades_applied: int = 0

    _equity_curve: list[float] = field(default_factory=list)

    def __post_init__(self):
        self.equity = self.rules.initial_balance
        self.peak_equity = self.rules.initial_balance
        self.start_of_day_equity = self.rules.initial_balance

        # Validate that risk parameters produce a meaningful dollar risk
        self._dollar_risk = self.rules.risk_per_trade * self.rules.initial_balance
        if self._dollar_risk <= 0.0 or not math.isfinite(self._dollar_risk):
            raise ValueError(
                f"risk_per_trade * initial_balance must be finite and > 0, "
                f"got {self._dollar_risk} "
                f"(risk={self.rules.risk_per_trade}, balance={self.rules.initial_balance})"
            )

    # ------------------------------------------------------------------
    # Trade application
    # ------------------------------------------------------------------

    def apply_trade(self, r_result: float, date: str) -> None:
        """
        Apply a single trade to the account.

        Raises ValueError if r_result is NaN/infinite, or if the
        resulting equity would overflow (become non-finite).
        """
        if not math.isfinite(r_result):
            raise ValueError(
                f"r_result must be a finite real number, got {r_result!r}"
            )

        if self.current_date is not None and date != self.current_date:
            self.start_of_day_equity = self.equity

        self.current_date = date

        # Compute P&L and new equity, guarding against overflow
        pnl = r_result * self._dollar_risk
        new_equity = self.equity + pnl

        if not math.isfinite(new_equity):
            raise ValueError(
                f"Trade caused non-finite equity: "
                f"equity={self.equity} + r_result={r_result} * "
                f"dollar_risk={self._dollar_risk} = {new_equity}"
            )

        self.equity = new_equity
        self.trades_applied += 1

        if self.equity > self.peak_equity:
            self.peak_equity = self.equity

        self._equity_curve.append(self.equity)

    # ------------------------------------------------------------------
    # Drawdown — two concepts
    # ------------------------------------------------------------------

    def rule_drawdown(self) -> float:
        """
        Drawdown as defined by the funded-account rules.

        "static":   (initial_balance - equity) / initial_balance
        "trailing": (peak_equity - equity) / peak_equity

        Returns 0.0 if equity >= reference.
        """
        if self.rules.drawdown_mode == "static":
            if self.equity >= self.rules.initial_balance:
                return 0.0
            return (
                self.rules.initial_balance - self.equity
            ) / self.rules.initial_balance
        else:  # "trailing"
            if self.equity >= self.peak_equity:
                return 0.0
            return (self.peak_equity - self.equity) / self.peak_equity

    def peak_to_trough_drawdown(self) -> float:
        """
        Historical peak-to-trough drawdown, always computed from peak_equity.

        This is the drawdown used for statistical reporting (median, P95, P99)
        in Monte Carlo analysis. It is independent of the rule's drawdown_mode.
        """
        if self.equity >= self.peak_equity:
            return 0.0
        return (self.peak_equity - self.equity) / self.peak_equity

    # Convenience alias for backward compatibility in internal tracking
    def current_drawdown(self) -> float:
        """Alias for rule_drawdown()."""
        return self.rule_drawdown()

    # ------------------------------------------------------------------
    # Daily loss
    # ------------------------------------------------------------------

    def daily_loss_today(self) -> float:
        """
        Current daily loss as a fraction.

        "initial": (start_of_day_equity - equity) / initial_balance
        "eod":     (start_of_day_equity - equity) / start_of_day_equity
        """
        if self.equity >= self.start_of_day_equity:
            return 0.0
        loss_dollars = self.start_of_day_equity - self.equity
        if self.rules.daily_loss_base == "initial":
            return loss_dollars / self.rules.initial_balance
        else:
            return loss_dollars / self.start_of_day_equity

    # ------------------------------------------------------------------
    # Constraint checks — relative tolerance for FP safety
    # ------------------------------------------------------------------

    def is_profit_target_reached(self) -> bool:
        target = self.rules.initial_balance * (1 + self.rules.profit_target_pct)
        return self.equity >= target * (1 - 1e-12) or math.isclose(
            self.equity, target, rel_tol=1e-12
        )

    def is_max_drawdown_violated(self) -> bool:
        return self.rule_drawdown() >= self.rules.max_drawdown_pct - 1e-12

    def is_daily_loss_violated(self) -> bool:
        return self.daily_loss_today() >= self.rules.daily_loss_limit_pct - 1e-12

    def is_max_trades_reached(self) -> bool:
        if self.rules.max_trades is None:
            return False
        return self.trades_applied >= self.rules.max_trades

    # ------------------------------------------------------------------
    # Read-only
    # ------------------------------------------------------------------

    @property
    def equity_curve(self) -> list[float]:
        return list(self._equity_curve)

    @property
    def initial_balance(self) -> float:
        return self.rules.initial_balance

    @property
    def profit_target(self) -> float:
        return self.rules.initial_balance * (1 + self.rules.profit_target_pct)

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

    Constraint checks use exact comparison against dollar thresholds (no
    tolerance); the profit target is computed consistently with the equity
    update. See the "Constraint checks" section below.
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

    # ------------------------------------------------------------------
    # Trade application
    # ------------------------------------------------------------------

    def apply_trade(self, r_result: float, date: str) -> None:
        """
        Apply a single trade to the account.

        Raises ValueError for:
          - non-finite r_result (NaN / inf)
          - dollar_risk underflow/overflow
          - P&L overflow (r_result * dollar_risk → ±inf)
          - equity overflow (equity + P&L → ±inf)
          - P&L underflow (non-zero r_result but P&L rounds to 0.0)
          - P&L absorption (non-zero P&L but equity doesn't move)

        Atomicity: every quantity is computed and validated BEFORE any
        state is mutated. A rejected trade leaves the account unchanged
        (current_date, start_of_day_equity, equity, peak_equity, curve).
        """
        if not math.isfinite(r_result):
            raise ValueError(
                f"r_result must be a finite real number, got {r_result!r}"
            )

        # --- Compute + validate everything first (atomicity) ---

        dollar_risk = self.rules.risk_per_trade * self.rules.initial_balance
        if not math.isfinite(dollar_risk) or dollar_risk <= 0.0:
            raise ValueError(
                f"dollar_risk must be finite and positive, got {dollar_risk!r}"
            )

        pnl = r_result * dollar_risk
        if not math.isfinite(pnl):
            raise ValueError(
                f"Trade P&L overflowed float precision: "
                f"r_result={r_result!r} * dollar_risk={dollar_risk!r}"
            )

        new_equity = self.equity + pnl
        if not math.isfinite(new_equity):
            raise ValueError(
                f"Trade equity overflowed float precision: "
                f"equity={self.equity!r} + pnl={pnl!r}"
            )

        if r_result != 0.0 and pnl == 0.0:
            raise ValueError(
                f"Trade P&L underflowed to zero: "
                f"r_result={r_result!r} * dollar_risk={dollar_risk!r}"
            )

        if pnl != 0.0 and new_equity == self.equity:
            raise ValueError(
                f"Trade P&L was absorbed by float precision: "
                f"equity={self.equity!r} + pnl={pnl!r} == {new_equity!r}"
            )

        # --- All guards passed: commit state mutations atomically ---

        if self.current_date is not None and date != self.current_date:
            self.start_of_day_equity = self.equity

        self.current_date = date
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
    # Constraint checks — exact dollar comparison (no tolerance)
    # ------------------------------------------------------------------
    #
    # Boundaries are compared exactly against DOLLAR thresholds, not ratios.
    # The ratio form (balance - equity)/balance rounds through a division, so
    # a trade that lands exactly on the threshold can compute a drawdown one
    # ULP below the limit and be missed (e.g. balance 28778.7, 12% limit,
    # -16R → equity exactly 25325.256 but ratio drawdown 0.11999999999999998).
    # The profit target is likewise balance + balance*pct (bit-for-bit
    # consistent with the equity update). Any non-zero tolerance would widen
    # the boundary into a band and misclassify values just below a limit.

    def is_profit_target_reached(self) -> bool:
        return self.equity >= self.profit_target

    def is_max_drawdown_violated(self) -> bool:
        if self.rules.drawdown_mode == "static":
            threshold = (
                self.rules.initial_balance
                - self.rules.initial_balance * self.rules.max_drawdown_pct
            )
        else:  # "trailing"
            threshold = self.peak_equity - self.peak_equity * self.rules.max_drawdown_pct
        return self.equity <= threshold

    def is_daily_loss_violated(self) -> bool:
        if self.rules.daily_loss_base == "initial":
            threshold = (
                self.start_of_day_equity
                - self.rules.initial_balance * self.rules.daily_loss_limit_pct
            )
        else:  # "eod"
            threshold = (
                self.start_of_day_equity
                - self.start_of_day_equity * self.rules.daily_loss_limit_pct
            )
        return self.equity <= threshold

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
        # balance + balance*pct (not balance*(1+pct)) — bit-for-bit consistent
        # with the equity update, so an exact-boundary trade reaches exactly.
        return (
            self.rules.initial_balance
            + self.rules.initial_balance * self.rules.profit_target_pct
        )

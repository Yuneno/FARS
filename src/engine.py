"""
Single simulation engine.

Runs one complete funded-account evaluation: apply trades sequentially,
check all constraints after each trade, stop on first terminal condition.

Produces an immutable SimulationResult with final state, reason for
termination, and both rule-based and historical drawdown metrics.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime

from src.account import AccountState
from src.types import FundedAccountRules, Trade


@dataclass(frozen=True)
class SimulationResult:
    """
    Result of a single simulation run.

    All scalar fields are immutable. equity_curve is a list (mutable by
    Python semantics despite frozen=True) — treat as read-only.

    terminal_condition:
      - "profit_target"  — reached profit target
      - "max_drawdown"   — violated max drawdown limit (rule-based)
      - "daily_loss"     — violated daily loss limit
      - "max_trades"     — reached max trades without passing or failing
      - "completed"      — all trades applied, none of the above

    Conditions are checked in priority order (profit > DD > DL > max_trades).
    A trade that violates multiple conditions is attributed to the first one
    hit. However, max_drawdown_hit and daily_loss_hit still record the worst
    values seen during the entire simulation, including conditions that
    weren't the primary stop reason.

    max_drawdown_hit: worst rule-based DD seen (mode-dependent).
    max_drawdown_historical: worst peak-to-trough DD seen (always from
      peak_equity, regardless of drawdown_mode). This feeds statistical
      reporting (median, P95, P99) in Monte Carlo analysis.
    """

    final_equity: float
    terminal_condition: str
    trades_executed: int
    max_drawdown_hit: float
    max_drawdown_historical: float
    daily_loss_hit: float
    equity_curve: list[float] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.terminal_condition == "profit_target"

    def __post_init__(self):
        valid = {
            "profit_target", "max_drawdown", "daily_loss",
            "max_trades", "completed",
        }
        if self.terminal_condition not in valid:
            raise ValueError(
                f"terminal_condition must be one of {valid}, "
                f"got {self.terminal_condition!r}"
            )


def _parse_date(date_str: str, trade_id: str) -> str:
    """Validate and normalize a date string to YYYY-MM-DD."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError(
            f"Trade {trade_id} has invalid date {date_str!r}. "
            f"Expected format: YYYY-MM-DD."
        )


def run_simulation(
    trades: list[Trade],
    rules: FundedAccountRules,
) -> SimulationResult:
    """
    Run a single funded-account simulation.

    Applies trades in chronological order. After each trade, checks:
      1. profit target  → PASS (stop)
      2. max drawdown   → FAIL (stop)
      3. daily loss     → FAIL (stop)
      4. max trades     → TIMEOUT (stop)
      5. continue

    Returns SimulationResult with final equity, terminal condition,
    both rule-based and historical max drawdown, and equity curve.

    Raises ValueError for: NaN/inf r_result, non-chronological dates,
    or invalid date formats.
    """
    account = AccountState(rules=rules)
    max_rule_dd = 0.0
    max_hist_dd = 0.0
    max_dl = 0.0
    prev_date: str | None = None

    for trade in trades:
        if not math.isfinite(trade.r_result):
            raise ValueError(
                f"Trade {trade.trade_id} has non-finite r_result: {trade.r_result}"
            )

        # Validate and normalize date
        normalized = _parse_date(trade.date, trade.trade_id)

        if prev_date is not None and normalized < prev_date:
            raise ValueError(
                f"Trade dates must be non-decreasing. "
                f"Got {normalized!r} after {prev_date!r} "
                f"(trade {trade.trade_id})"
            )
        prev_date = normalized

        account.apply_trade(trade.r_result, normalized)

        # Track worst-case metrics
        rule_dd = account.rule_drawdown()
        if rule_dd > max_rule_dd:
            max_rule_dd = rule_dd

        hist_dd = account.peak_to_trough_drawdown()
        if hist_dd > max_hist_dd:
            max_hist_dd = hist_dd

        dl = account.daily_loss_today()
        if dl > max_dl:
            max_dl = dl

        # --- Terminal condition checks ---

        if account.is_profit_target_reached():
            return SimulationResult(
                final_equity=account.equity,
                terminal_condition="profit_target",
                trades_executed=account.trades_applied,
                max_drawdown_hit=max_rule_dd,
                max_drawdown_historical=max_hist_dd,
                daily_loss_hit=max_dl,
                equity_curve=account.equity_curve,
            )

        if account.is_max_drawdown_violated():
            return SimulationResult(
                final_equity=account.equity,
                terminal_condition="max_drawdown",
                trades_executed=account.trades_applied,
                max_drawdown_hit=max_rule_dd,
                max_drawdown_historical=max_hist_dd,
                daily_loss_hit=max_dl,
                equity_curve=account.equity_curve,
            )

        if account.is_daily_loss_violated():
            return SimulationResult(
                final_equity=account.equity,
                terminal_condition="daily_loss",
                trades_executed=account.trades_applied,
                max_drawdown_hit=max_rule_dd,
                max_drawdown_historical=max_hist_dd,
                daily_loss_hit=max_dl,
                equity_curve=account.equity_curve,
            )

        if account.is_max_trades_reached():
            return SimulationResult(
                final_equity=account.equity,
                terminal_condition="max_trades",
                trades_executed=account.trades_applied,
                max_drawdown_hit=max_rule_dd,
                max_drawdown_historical=max_hist_dd,
                daily_loss_hit=max_dl,
                equity_curve=account.equity_curve,
            )

    return SimulationResult(
        final_equity=account.equity,
        terminal_condition="completed",
        trades_executed=account.trades_applied,
        max_drawdown_hit=max_rule_dd,
        max_drawdown_historical=max_hist_dd,
        daily_loss_hit=max_dl,
        equity_curve=account.equity_curve,
    )

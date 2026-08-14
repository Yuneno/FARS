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

    max_drawdown_hit: worst rule-based DD seen (mode-dependent).
    max_drawdown_historical: worst peak-to-trough DD seen (always from
      peak_equity, regardless of drawdown_mode). This feeds statistical
      reporting (median, P95, P99) in Monte Carlo analysis.

    violated_conditions: tuple of ALL rule violations triggered by the
      terminal trade, in priority order (max_drawdown, daily_loss,
      max_trades). For any failure, terminal_condition == violated_conditions[0].
      Empty for profit_target/completed. Normalized to an immutable tuple
      in __post_init__.
    """

    final_equity: float
    terminal_condition: str
    trades_executed: int
    max_drawdown_hit: float
    max_drawdown_historical: float
    daily_loss_hit: float
    equity_curve: list[float] = field(default_factory=list)
    violated_conditions: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.terminal_condition == "profit_target"

    _VALID_TERMINAL = {
        "profit_target", "max_drawdown", "daily_loss", "max_trades", "completed",
    }
    _VIOLATION_PRIORITY = {"max_drawdown": 0, "daily_loss": 1, "max_trades": 2}

    def __post_init__(self):
        # Normalize violated_conditions to an immutable tuple up front. The
        # field is declared tuple[str, ...], but a caller can still pass a
        # mutable list; freezing it here preserves this frozen dataclass's
        # immutability contract.
        object.__setattr__(self, "violated_conditions", tuple(self.violated_conditions))

        if self.terminal_condition not in self._VALID_TERMINAL:
            raise ValueError(
                f"terminal_condition must be one of {self._VALID_TERMINAL}, "
                f"got {self.terminal_condition!r}"
            )

        vc = self.violated_conditions

        # Entries must be valid failure conditions
        for cond in vc:
            if cond not in self._VIOLATION_PRIORITY:
                raise ValueError(
                    f"violated_conditions must contain only "
                    f"{sorted(self._VIOLATION_PRIORITY)}, got {cond!r}"
                )

        # No duplicates
        if len(vc) != len(set(vc)):
            raise ValueError(
                f"violated_conditions must not contain duplicates, got {vc!r}"
            )

        # Priority order: max_drawdown < daily_loss < max_trades
        for i in range(len(vc) - 1):
            if self._VIOLATION_PRIORITY[vc[i]] > self._VIOLATION_PRIORITY[vc[i + 1]]:
                raise ValueError(
                    f"violated_conditions out of priority order, got {vc!r}"
                )

        # Coherence with terminal_condition. For PASS/completed, vc must be
        # empty. For any failure, terminal_condition must equal the
        # highest-priority violation (vc[0]).
        if self.terminal_condition in ("profit_target", "completed"):
            if vc:
                raise ValueError(
                    f"{self.terminal_condition} result must have empty "
                    f"violated_conditions, got {vc!r}"
                )
        else:
            if not vc or vc[0] != self.terminal_condition:
                raise ValueError(
                    f"failure terminal_condition {self.terminal_condition!r} "
                    f"must equal violated_conditions[0], got {vc!r}"
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
        #
        # PASS (profit target) is checked first and alone: a trade that
        # simultaneously reaches the target AND would violate a rule is a
        # PASS. (FARS_SPEC §7 lists the terminal conditions but does not
        # specify precedence; PASS-first is this implementation's chosen
        # policy.)
        #
        # Failures are collected together: a single trade can violate
        # multiple rules (e.g. -10R blows through both max_drawdown AND
        # daily_loss). We record ALL of them in violated_conditions, in
        # priority order, and use the highest-priority one as the primary
        # terminal_condition. Recording only the first would understate
        # per-condition failure probabilities in Monte Carlo analysis.
        #
        # NOTE: this makes failure attribution INCLUSIVE, not mutually
        # exclusive — one simulation can count toward multiple
        # failure_probability_* buckets. Phase 5/6 must either sum them with
        # this in mind or define an explicit primary-cause attribution.

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

        violated: list[str] = []
        if account.is_max_drawdown_violated():
            violated.append("max_drawdown")
        if account.is_daily_loss_violated():
            violated.append("daily_loss")
        if account.is_max_trades_reached():
            violated.append("max_trades")

        if violated:
            return SimulationResult(
                final_equity=account.equity,
                terminal_condition=violated[0],
                trades_executed=account.trades_applied,
                max_drawdown_hit=max_rule_dd,
                max_drawdown_historical=max_hist_dd,
                daily_loss_hit=max_dl,
                equity_curve=account.equity_curve,
                violated_conditions=tuple(violated),
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

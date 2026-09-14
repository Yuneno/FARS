"""Generic offline account simulator for FARS (P6).

Consumes ordered fills/events and applies account rules (balance, drawdown,
daily loss, profit target, trailing) deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from src.types import FundedAccountRules

NY_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")


def get_session_date(
    ts: str | datetime | None,
    session_reset_hour: int = 17,
    skip_weekends: bool = True,
) -> str | None:
    """Determine the trading session date based on America/New_York clock and session_reset_hour.

    CME futures trading sessions roll over at session_reset_hour (default 17:00 NY).
    Trades occurring at or after session_reset_hour belong to the NEXT trading session.
    When skip_weekends is True (default, Option B), rolls from Friday after reset (or weekend)
    to Monday's trading session.
    Trades occurring before session_reset_hour belong to the current day's session.
    DST transitions are handled automatically via ZoneInfo("America/New_York").
    """
    if not ts:
        return None
    if isinstance(ts, str):
        cleaned = ts.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(cleaned)
        except ValueError:
            return ts[:10]
    else:
        dt = ts

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC_TZ)

    dt_ny = dt.astimezone(NY_TZ)
    if dt_ny.hour >= session_reset_hour:
        session_dt = dt_ny.date() + timedelta(days=1)
    else:
        session_dt = dt_ny.date()

    if skip_weekends:
        if session_dt.weekday() == 5:  # Saturday -> Monday
            session_dt += timedelta(days=2)
        elif session_dt.weekday() == 6:  # Sunday -> Monday
            session_dt += timedelta(days=1)

    return session_dt.isoformat()


@dataclass(frozen=True)
class AccountRules:
    """Explicit versioned account rules."""

    currency: str = "USD"
    initial_balance: float = 50_000.0
    daily_loss_limit: float = 1_500.0
    max_drawdown: float = 3_000.0
    trailing_type: Literal["static", "eod", "intraday"] = "static"
    profit_target: float = 3_000.0
    consistency_limit: float | None = None
    session_reset_hour: int = 17  # NY close
    included_costs: bool = True
    max_concurrent_positions: int = 1
    tick_value: float = 0.50  # MNQ per tick
    tick_size: float = 0.25
    check_broker_margin: bool = False

    def validate(self) -> list[str]:
        errors = []
        if self.initial_balance <= 0:
            errors.append(f"initial_balance must be > 0, got {self.initial_balance}")
        if self.daily_loss_limit < 0:
            errors.append(f"daily_loss_limit must be >= 0")
        if self.max_drawdown < 0:
            errors.append(f"max_drawdown must be >= 0")
        if self.profit_target < 0:
            errors.append(f"profit_target must be >= 0")
        return errors


@dataclass(frozen=True)
class FillEvent:
    """A single fill event for the account simulator."""

    trade_id: str
    direction: Literal["long", "short"]
    entry_price: float
    exit_price: float
    quantity: int
    points_pnl: float
    timestamp: str
    cost_points: float = 0.0


@dataclass
class AccountState:
    """Running account state."""

    balance: float
    high_water_mark: float
    daily_start_balance: float
    daily_pnl: float
    realized_pnl: float
    n_trades: int
    n_breaches: int
    current_date: str | None = None


@dataclass(frozen=True)
class SimulationResult:
    """Outcome of running fills through account rules."""

    final_balance: float
    status: Literal["PASSED_SIMULATION", "FAILED_RULES", "TIMEOUT", "INSUFFICIENT_DATA", "NO_EVALUABLE"]
    breaches: tuple[dict[str, Any], ...]
    equity_curve: tuple[float, ...]
    n_trades: int
    total_pnl: float
    max_drawdown_seen: float
    daily_results: tuple[dict[str, Any], ...]
    broker_margin_status: str = "NO_EVALUABLE"
    broker_margin_evaluable: bool = False


def simulate_account(
    fills: list[FillEvent],
    rules: AccountRules,
) -> SimulationResult:
    """Run fills through account rules and return simulation result.

    Rules checked at trade close:
    - Daily loss limit (with NY session reset)
    - Max drawdown (from high-water mark, with trailing support)
    - Profit target (EOD trailing updates HWM without converting to intraday)
    """
    errors = rules.validate()
    if errors:
        raise ValueError(f"Invalid account rules: {errors}")

    balance = rules.initial_balance
    hwm = balance
    trailing_hwm = balance  # for intraday trailing
    daily_start = balance
    daily_pnl = 0.0
    realized = 0.0
    breaches: list[dict[str, Any]] = []
    equity: list[float] = []
    daily_results: list[dict[str, Any]] = []
    current_date: str | None = None
    target_reached = False

    for fill in fills:
        # Session reset detection (NY close at session_reset_hour, DST-aware)
        fill_date = get_session_date(fill.timestamp, rules.session_reset_hour)
        if fill_date and fill_date != current_date:
            if current_date is not None:
                daily_results.append({
                    "date": current_date,
                    "daily_pnl": daily_pnl,
                    "end_balance": balance,
                })
                # EOD trailing: update HWM at session end
                if rules.trailing_type == "eod":
                    hwm = max(hwm, balance)
            current_date = fill_date
            daily_start = balance
            daily_pnl = 0.0
            # Intraday trailing: reset trailing HWM at session start
            if rules.trailing_type == "intraday":
                trailing_hwm = balance

        # Compute P&L (points -> monetary)
        pnl = fill.points_pnl * rules.tick_value / rules.tick_size
        if rules.included_costs:
            pnl -= fill.cost_points * rules.tick_value / rules.tick_size

        balance += pnl
        daily_pnl += pnl
        realized += pnl

        # Update HWM based on trailing type
        if rules.trailing_type == "intraday":
            trailing_hwm = max(trailing_hwm, balance)
            hwm = max(hwm, balance)
        else:
            hwm = max(hwm, balance)

        equity.append(balance)

        # Check max drawdown (using appropriate HWM for trailing type)
        effective_hwm = trailing_hwm if rules.trailing_type == "intraday" else hwm
        dd = effective_hwm - balance
        if rules.max_drawdown > 0 and dd >= rules.max_drawdown:
            breaches.append({
                "rule": "max_drawdown",
                "trailing_type": rules.trailing_type,
                "balance": balance,
                "drawdown": dd,
                "threshold": rules.max_drawdown,
                "trade_id": fill.trade_id,
            })
            return SimulationResult(
                final_balance=balance,
                status="FAILED_RULES",
                breaches=tuple(breaches),
                equity_curve=tuple(equity),
                n_trades=len(equity),
                total_pnl=realized,
                max_drawdown_seen=dd,
                daily_results=tuple(daily_results),
            )

        if rules.daily_loss_limit > 0 and daily_pnl <= -rules.daily_loss_limit:
            breaches.append({
                "rule": "daily_loss_limit",
                "daily_pnl": daily_pnl,
                "threshold": rules.daily_loss_limit,
                "trade_id": fill.trade_id,
            })
            return SimulationResult(
                final_balance=balance,
                status="FAILED_RULES",
                breaches=tuple(breaches),
                equity_curve=tuple(equity),
                n_trades=len(equity),
                total_pnl=realized,
                max_drawdown_seen=dd,
                daily_results=tuple(daily_results),
            )

        if rules.profit_target > 0 and realized >= rules.profit_target:
            target_reached = True

    # Record last day
    if current_date is not None:
        daily_results.append({
            "date": current_date,
            "daily_pnl": daily_pnl,
            "end_balance": balance,
        })

    final_status = "PASSED_SIMULATION" if target_reached else "PASSED_SIMULATION"
    if rules.check_broker_margin:
        breaches.append({
            "rule": "broker_margin",
            "status": "NO_EVALUABLE",
            "reason": "Intrabar broker margin data not available in offline fill events",
        })
        final_status = "NO_EVALUABLE"

    return SimulationResult(
        final_balance=balance,
        status=final_status,
        breaches=tuple(breaches),
        equity_curve=tuple(equity),
        n_trades=len(equity),
        total_pnl=realized,
        max_drawdown_seen=hwm - balance if hwm > balance else 0.0,
        daily_results=tuple(daily_results),
    )


__all__ = [
    "AccountRules",
    "FillEvent",
    "AccountState",
    "SimulationResult",
    "simulate_account",
]

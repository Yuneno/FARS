"""Bar-by-bar backtest executor (FASE C) feeding the Core engine.

Turns a bar sequence + strategy into simulated trades, then runs the existing
:func:`src.engine.run_simulation` for funded-account rules.

Look-ahead and fill rules honored:
* signals use only CLOSED bars; entry fills at the NEXT bar's open (with
  slippage against the position). If the immediate next bar is missing (a gap),
  the entry is REJECTED and counted as a gap rejection — never filled on a
  later bar;
* the entry bar IS checked for TP/SL; if both TP and SL are reachable within
  one bar, the stop is assumed to fill first (conservative);
* stop (market) fills get slippage against the position; limit (TP) fills do
  not; if a bar GAPS through the stop, the fill is the bar's open (the stop
  price is NOT guaranteed) and no market slippage is added;
* when a timestamp-based hold (``max_hold_minutes``) expires at a bar's OPEN,
  the position closes at that open BEFORE the new bar's high/low/close are
  consulted;
* prices are rounded to the instrument tick, and levels are validated AFTER
  rounding (a collapsed stop/target rejects the signal);
* a distance-mode position still open at end-of-data is reported as UNRESOLVED;
  legacy absolute-price signals retain close-at-last-close compatibility.

Two notions of equity are kept distinct:
* ``BacktestResult.equity_curve`` / ``net_pnl`` / ``trades`` = the RAW strategy
  path (every simulated trade);
* ``BacktestResult.simulation`` = the Core rule-limited path (trades processed
  until the engine stops on a profit target / drawdown / daily-loss / max-trades
  rule). The engine checks these rules at trade CLOSE, not intrabar.

``Signal.stop_target_as_points`` (on the signal, not the config) selects the
stop/target convention, so a distance signal can never be silently read as an
absolute price.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from src.backtest.history import Bar
from src.backtest.intrabar import (
    IntrabarAudit,
    index_m1_bars,
    is_m5_ambiguous,
    resolve_intrabar_with_m1,
)
from src.backtest.markets import MNQ
from src.backtest.strategy import Strategy
from src.engine import SimulationResult, run_simulation
from src.session_calendar import session_date
from src.types import FundedAccountRules, Trade


@dataclass(frozen=True)
class BacktestConfig:
    """Explicit, reproducible backtest + funded-rule configuration.

    Dollar-per-point and tick are MNQ defaults; commission and slippage are
    PROVISIONAL values chosen only to run the MVP, not provider-quoted.
    """

    initial_balance: float = 50_000.0
    risk_per_trade: float = 0.01
    dollar_per_point: float = MNQ.dollar_per_point
    tick_size: float = MNQ.tick_size
    max_contracts: int = 10
    commission_per_side: float = 0.62  # per contract per side (PROVISIONAL)
    slippage_points: float = 0.25  # per market fill (PROVISIONAL)
    profit_target_pct: float = 0.06
    max_drawdown_pct: float = 0.06
    daily_loss_limit_pct: float = 0.03
    max_trades: int | None = None
    max_bars_held: int = 100  # bar-count time exit (PROVISIONAL, legacy mode)
    # timestamp-based time exit (minutes since fill); when set, closes at the
    # first bar open at/after the limit (and overrides max_bars_held).
    max_hold_minutes: float | None = None
    # fixed contract count; None → risk-based sizing (AMD+CRT uses 1 micro)
    fixed_quantity: int | None = None
    # expected bar interval (seconds) for entry-gap detection; None → disabled
    bar_interval_seconds: int | None = None
    # Additive execution mechanics used by the ported kai strategies. All are
    # disabled by default so the legacy AMD+CRT path remains unchanged.
    partial_take_profit_fraction: float = 0.0
    move_stop_to_break_even: bool = False
    pending_limit_entry: bool = False
    pending_order_wait_bars: int = 0
    cooldown_bars: int = 0
    discrete_partial_contracts: bool = False
    time_exit_mode: str = "market"
    end_of_data_policy: str = "unresolved"  # "unresolved" or "close"
    time_exit_slippage_points: float = 0.0
    end_of_data_slippage_points: float = 0.0
    session_date_for_ledger: bool = False

    def __post_init__(self) -> None:
        if self.time_exit_mode not in {"market", "flat"}:
            raise ValueError("time_exit_mode must be 'market' or 'flat'")
        if self.end_of_data_policy not in {"unresolved", "close"}:
            raise ValueError("end_of_data_policy must be 'unresolved' or 'close'")
        float_fields = (
            "initial_balance",
            "risk_per_trade",
            "dollar_per_point",
            "tick_size",
            "commission_per_side",
            "slippage_points",
            "time_exit_slippage_points",
            "end_of_data_slippage_points",
            "profit_target_pct",
            "max_drawdown_pct",
            "daily_loss_limit_pct",
            "max_hold_minutes",
            "partial_take_profit_fraction",
        )
        for name in float_fields:
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")

        integer_fields = (
            "max_contracts",
            "max_trades",
            "max_bars_held",
            "fixed_quantity",
            "bar_interval_seconds",
            "pending_order_wait_bars",
            "cooldown_bars",
        )
        for name in integer_fields:
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
                raise ValueError(f"{name} must be an integer")

        positive_economic_fields = (
            "initial_balance",
            "dollar_per_point",
            "tick_size",
        )
        for name in positive_economic_fields:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0")

        nonnegative_economic_fields = (
            "commission_per_side",
            "slippage_points",
            "time_exit_slippage_points",
            "end_of_data_slippage_points",
        )
        for name in nonnegative_economic_fields:
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")
        percentage_fields = (
            "risk_per_trade",
            "profit_target_pct",
            "max_drawdown_pct",
            "daily_loss_limit_pct",
        )
        for name in percentage_fields:
            if not 0 < getattr(self, name) < 1:
                raise ValueError(f"{name} must be strictly between 0 and 1")
        if self.max_contracts <= 0:
            raise ValueError("max_contracts must be > 0")
        if self.max_trades is not None and self.max_trades <= 0:
            raise ValueError("max_trades must be > 0 when set")
        if self.max_bars_held <= 0:
            raise ValueError("max_bars_held must be > 0")
        if self.fixed_quantity is not None and self.fixed_quantity <= 0:
            raise ValueError("fixed_quantity must be > 0 when set")
        if self.max_hold_minutes is not None and self.max_hold_minutes <= 0:
            raise ValueError("max_hold_minutes must be > 0 when set")
        if self.bar_interval_seconds is not None and self.bar_interval_seconds <= 0:
            raise ValueError("bar_interval_seconds must be > 0 when set")
        if not 0.0 <= self.partial_take_profit_fraction < 1.0:
            raise ValueError("partial_take_profit_fraction must be in [0, 1)")
        if self.pending_order_wait_bars < 0:
            raise ValueError("pending_order_wait_bars must be >= 0")
        if self.cooldown_bars < 0:
            raise ValueError("cooldown_bars must be >= 0")
        if self.move_stop_to_break_even and self.partial_take_profit_fraction <= 0:
            raise ValueError(
                "move_stop_to_break_even requires partial_take_profit_fraction > 0"
            )
        if self.pending_limit_entry and self.pending_order_wait_bars <= 0:
            raise ValueError("pending_limit_entry requires pending_order_wait_bars > 0")
        if not isinstance(self.discrete_partial_contracts, bool):
            raise ValueError("discrete_partial_contracts must be a bool")
        if self.discrete_partial_contracts and self.partial_take_profit_fraction <= 0:
            raise ValueError(
                "discrete_partial_contracts requires partial_take_profit_fraction > 0"
            )
        if not isinstance(self.session_date_for_ledger, bool):
            raise ValueError("session_date_for_ledger must be a bool")


@dataclass(frozen=True)
class ExecutedTrade:
    trade_id: str
    direction: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    quantity: int
    gross_pnl: float
    commission: float
    net_pnl: float
    r_result: float
    exit_reason: str
    stop_risk_dollars: float = 0.0  # |entry - stop| * dollar_per_point * quantity
    slippage_cost: float = 0.0  # slippage actually applied, in dollars
    budgeted_risk_dollars: float = 0.0  # Requested fixed risk budget ($500)
    effective_risk_dollars: float = 0.0  # Actual effective risk at stop (same as stop_risk_dollars)
    budgeted_r: float = 0.0  # net_pnl / budgeted_risk_dollars (identical to r_result)
    effective_r: float = 0.0  # net_pnl / effective_risk_dollars


@dataclass(frozen=True)
class BacktestResult:
    config: BacktestConfig
    trades: tuple[ExecutedTrade, ...]
    simulation: SimulationResult | None
    net_pnl: float
    n_trades: int
    win_rate: float
    profit_factor: float
    expectancy: float
    max_drawdown_pct: float
    total_commission: float
    total_slippage_cost: float
    equity_curve: tuple[float, ...] = field(default_factory=tuple)
    gap_rejections: int = 0  # entries rejected because the next bar was missing
    unresolved_positions: int = 0  # positions still open at end-of-data
    open_position: dict | None = None  # marked price & state when open at end of data
    intrabar_audit: Any | None = None  # Intrabar ambiguity audit metrics


def _round_tick(price: float, tick_size: float) -> float:
    if tick_size <= 0:
        return price
    return round(price / tick_size) * tick_size


def _quantity(config: BacktestConfig, stop_distance: float) -> int:
    dollar_risk = config.risk_per_trade * config.initial_balance
    if stop_distance <= 0:
        return 1
    raw = dollar_risk / (stop_distance * config.dollar_per_point)
    qty = max(1, round(raw))
    return min(qty, config.max_contracts)


def _entry_price(config: BacktestConfig, direction: str, open_price: float) -> float:
    slip = config.slippage_points
    return open_price + slip if direction == "long" else open_price - slip


def _stop_fill(
    config: BacktestConfig, direction: str, stop: float, bar_open: float | None = None
) -> float:
    """Stop fill price. If the bar gaps through the stop, fill at the (worse) open."""
    if bar_open is not None:
        if direction == "long" and bar_open < stop:
            return _round_tick(bar_open, config.tick_size)
        if direction == "short" and bar_open > stop:
            return _round_tick(bar_open, config.tick_size)
    slip = config.slippage_points
    fill = stop - slip if direction == "long" else stop + slip
    return _round_tick(fill, config.tick_size)


def _time_exit_fill(
    config: BacktestConfig, direction: str, price: float, entry: float
) -> float:
    """Time exit fill price. Returns entry if flat, adverse slippage if market."""
    if config.time_exit_mode == "flat":
        return entry
    slip = config.time_exit_slippage_points
    fill = price - slip if direction == "long" else price + slip
    return _round_tick(fill, config.tick_size)


def _resolve_stop_target(
    direction: str, entry: float, stop: float, target: float, as_points: bool, tick_size: float
) -> tuple[float, float]:
    """Resolve stop/target to absolute prices, honoring the distance mode."""
    if as_points:
        if direction == "long":
            stop_px = entry - stop
            target_px = entry + target
        else:
            stop_px = entry + stop
            target_px = entry - target
    else:
        stop_px = stop
        target_px = target
    return _round_tick(stop_px, tick_size), _round_tick(target_px, tick_size)


def _valid_levels(direction: str, entry: float, stop: float, target: float) -> bool:
    """True when stop/target are on the correct side of the entry (positive risk)."""
    if direction == "long":
        return stop < entry < target
    return stop > entry > target


def _entry_gap(config: BacktestConfig, history: list[Bar], bar: Bar) -> bool:
    """True when the entry bar is not the immediate bar after the last closed bar."""
    if config.bar_interval_seconds is None or not history:
        return False
    step = config.bar_interval_seconds
    expected = history[-1].timestamp + timedelta(seconds=step)
    return bar.timestamp > expected + timedelta(seconds=step * 0.5)


def _time_exit_open(config: BacktestConfig, position: dict, bar: Bar) -> bool:
    """True when the hold limit expires at/ before this bar's open."""
    if config.max_hold_minutes is None:
        return False
    limit = position["entry_time"] + timedelta(minutes=config.max_hold_minutes)
    return bar.timestamp >= limit


def _time_exit_close(config: BacktestConfig, position: dict, bar: Bar, bars_held: int) -> bool:
    """Bar-count time exit (legacy), only when max_hold_minutes is unset."""
    if config.max_hold_minutes is not None:
        return False
    return bars_held >= config.max_bars_held


def _empty_result(config: BacktestConfig) -> BacktestResult:
    return BacktestResult(
        config=config,
        trades=(),
        simulation=None,
        net_pnl=0.0,
        n_trades=0,
        win_rate=0.0,
        profit_factor=0.0,
        expectancy=0.0,
        max_drawdown_pct=0.0,
        total_commission=0.0,
        total_slippage_cost=0.0,
        equity_curve=(config.initial_balance,),
        open_position=None,
        intrabar_audit=IntrabarAudit(),
    )


def _run_backtest_legacy(
    bars: list[Bar],
    strategy: Strategy,
    config: BacktestConfig,
    *,
    calibration_bars: Sequence[Bar] = (),
    m1_index: dict[datetime, list[Bar]] | None = None,
) -> BacktestResult:
    """Simulate a contiguous run, optionally seeded with prior calibration bars."""
    if len(bars) < 2:
        return _empty_result(config)
    if calibration_bars and calibration_bars[-1].timestamp >= bars[0].timestamp:
        raise ValueError("calibration_bars must end before the simulated bars")

    strategy_name = strategy.__class__.__name__
    dollar_risk = config.risk_per_trade * config.initial_balance
    trades: list[ExecutedTrade] = []
    position: dict | None = None
    history = list(calibration_bars)
    gap_rejections = 0
    intrabar_audit = IntrabarAudit(total_bars_evaluated=len(bars))

    i = 0
    while i < len(bars):
        bar = bars[i]
        if position is None:
            signal = strategy.evaluate(history) if history else None
            if signal is not None:
                if _entry_gap(config, history, bar):
                    # the immediate next bar is missing: reject, do not fill later
                    gap_rejections += 1
                    history.append(bar)
                    i += 1
                    continue
                entry = _round_tick(
                    _entry_price(config, signal.direction, bar.open), config.tick_size
                )
                stop, target = _resolve_stop_target(
                    signal.direction,
                    entry,
                    signal.stop,
                    signal.target,
                    signal.stop_target_as_points,
                    config.tick_size,
                )
                if not _valid_levels(signal.direction, entry, stop, target):
                    history.append(bar)
                    i += 1
                    continue
                stop_distance = abs(entry - stop)
                qty = (
                    config.fixed_quantity
                    if config.fixed_quantity is not None
                    else _quantity(config, stop_distance)
                )
                position = {
                    "direction": signal.direction,
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                    "qty": qty,
                    "entry_index": i,
                    "entry_time": bar.timestamp,
                    "entry_open": bar.open,
                    "distance_mode": signal.stop_target_as_points,
                }
            else:
                history.append(bar)
                i += 1
                continue

        direction = position["direction"]
        stop = position["stop"]
        target = position["target"]
        bars_held = i - position["entry_index"]
        intrabar_audit.bars_in_position += 1

        reason: str | None = None
        exit_price: float | None = None
        if _time_exit_open(config, position, bar):
            # hold limit reached at this bar's open: close at the open, before
            # consulting the new bar's high/low/close.
            reason = "time_exit"
            exit_price = _time_exit_fill(
                config, direction, bar.open, position["entry"]
            )
        else:
            hit_target = target <= bar.high if direction == "long" else target >= bar.low
            hit_stop = stop >= bar.low if direction == "long" else stop <= bar.high
            if hit_target and hit_stop:
                intrabar_audit.ambiguous_bars_count += 1
                intrabar_audit.ambiguous_timestamps.append(bar.timestamp)
                if m1_index and bar.timestamp in m1_index:
                    m1_slice = m1_index[bar.timestamp]
                    m1_reason, status = resolve_intrabar_with_m1(direction, stop, target, m1_slice)
                    if status == "resolved_by_m1_target":
                        intrabar_audit.resolved_by_m1_target += 1
                        reason, exit_price = "take_profit", target
                    elif status == "resolved_by_m1_stop":
                        intrabar_audit.resolved_by_m1_stop += 1
                        reason, exit_price = "stop_loss", _stop_fill(config, direction, stop, bar.open)
                    elif status == "m1_residual_ambiguity_conservative_stop":
                        intrabar_audit.m1_residual_ambiguity += 1
                        reason, exit_price = "stop_loss", _stop_fill(config, direction, stop, bar.open)
                    else:
                        intrabar_audit.no_m1_data_fallback += 1
                        reason, exit_price = "stop_loss", _stop_fill(config, direction, stop, bar.open)
                else:
                    intrabar_audit.no_m1_data_fallback += 1
                    reason, exit_price = "stop_loss", _stop_fill(config, direction, stop, bar.open)
            elif hit_target:
                reason, exit_price = "take_profit", target
            elif hit_stop:
                reason, exit_price = "stop_loss", _stop_fill(config, direction, stop, bar.open)
            elif _time_exit_close(config, position, bar, bars_held):
                reason = "time_exit"
                exit_price = _time_exit_fill(
                    config, direction, bar.close, position["entry"]
                )

        if reason is not None and exit_price is not None:
            entry = position["entry"]
            stop_distance = abs(entry - position["stop"])
            move = exit_price - entry if direction == "long" else entry - exit_price
            gross_pnl = move * config.dollar_per_point * position["qty"]
            commission = config.commission_per_side * 2 * position["qty"]
            net_pnl = gross_pnl - commission
            r_result = net_pnl / dollar_risk if dollar_risk > 0 else 0.0
            stop_risk_dollars = stop_distance * config.dollar_per_point * position["qty"]
            entry_slip_pts = (
                max(entry - position["entry_open"], 0.0)
                if direction == "long"
                else max(position["entry_open"] - entry, 0.0)
            )
            if reason == "stop_loss":
                gap = (direction == "long" and bar.open < stop) or (
                    direction == "short" and bar.open > stop
                )
                exit_slip_pts = 0.0 if gap else abs(exit_price - stop)
            elif reason == "time_exit" and config.time_exit_mode == "market":
                exit_slip_pts = config.time_exit_slippage_points
            elif reason == "end_of_data":
                exit_slip_pts = config.end_of_data_slippage_points
            else:
                exit_slip_pts = 0.0
            slippage_cost = (
                entry_slip_pts + exit_slip_pts
            ) * config.dollar_per_point * position["qty"]
            executed = ExecutedTrade(
                trade_id=f"bt-{len(trades) + 1}",
                direction=direction,
                entry_time=position["entry_time"],
                exit_time=bar.timestamp,
                entry_price=entry,
                exit_price=exit_price,
                stop_price=stop,
                target_price=target,
                quantity=position["qty"],
                gross_pnl=gross_pnl,
                commission=commission,
                net_pnl=net_pnl,
                r_result=r_result,
                exit_reason=reason,
                stop_risk_dollars=stop_risk_dollars,
                slippage_cost=slippage_cost,
                budgeted_risk_dollars=dollar_risk,
                effective_risk_dollars=stop_risk_dollars,
                budgeted_r=r_result,
                effective_r=net_pnl / stop_risk_dollars if stop_risk_dollars > 0 else 0.0,
            )
            trades.append(executed)
            # Optional strategy hook: notify a closed trade (e.g. causal edge
            # gate). Duck-typed so strategies without note_trade are unaffected.
            note = getattr(strategy, "note_trade", None)
            if callable(note):
                note(executed.exit_time, executed.r_result)
            position = None

        history.append(bar)
        i += 1

    open_pos = None
    if position is not None:
        last = bars[-1]
        direction = position["direction"]
        entry = position["entry"]
        qty = position["qty"]
        raw_exit_price = _round_tick(last.close, config.tick_size)
        slip = config.end_of_data_slippage_points
        exit_price = _round_tick(
            raw_exit_price - slip if direction == "long" else raw_exit_price + slip,
            config.tick_size,
        )
        move = exit_price - entry if direction == "long" else entry - exit_price
        gross_pnl = move * config.dollar_per_point * qty
        commission = config.commission_per_side * 2 * qty
        net_pnl = gross_pnl - commission
        stop_distance = abs(entry - position["stop"])
        stop_risk_dollars = stop_distance * config.dollar_per_point * qty

        should_close = (config.end_of_data_policy == "close") or (
            config.end_of_data_policy == "unresolved" and not position["distance_mode"]
        )
        if should_close:
            entry_slip_pts = (
                max(entry - position["entry_open"], 0.0)
                if direction == "long"
                else max(position["entry_open"] - entry, 0.0)
            )
            exit_slip_pts = slip
            trades.append(
                ExecutedTrade(
                    trade_id=f"bt-{len(trades) + 1}",
                    direction=direction,
                    entry_time=position["entry_time"],
                    exit_time=last.timestamp,
                    entry_price=entry,
                    exit_price=exit_price,
                    stop_price=position["stop"],
                    target_price=position["target"],
                    quantity=qty,
                    gross_pnl=gross_pnl,
                    commission=commission,
                    net_pnl=net_pnl,
                    r_result=net_pnl / dollar_risk if dollar_risk > 0 else 0.0,
                    exit_reason="end_of_data",
                    stop_risk_dollars=stop_risk_dollars,
                    slippage_cost=(
                        (entry_slip_pts + exit_slip_pts) * config.dollar_per_point * qty
                    ),
                    budgeted_risk_dollars=dollar_risk,
                    effective_risk_dollars=stop_risk_dollars,
                    budgeted_r=net_pnl / dollar_risk if dollar_risk > 0 else 0.0,
                    effective_r=net_pnl / stop_risk_dollars if stop_risk_dollars > 0 else 0.0,
                )
            )
            note = getattr(strategy, "note_trade", None)
            if callable(note):
                note(last.timestamp, net_pnl / dollar_risk if dollar_risk > 0 else 0.0)
            position = None
        else:
            open_pos = {
                "direction": direction,
                "entry_price": entry,
                "entry_time": position["entry_time"],
                "last_price": exit_price,
                "last_time": last.timestamp,
                "stop_price": position["stop"],
                "target_price": position["target"],
                "quantity": qty,
                "unrealized_gross_pnl": gross_pnl,
                "unrealized_net_pnl": net_pnl,
                "state": "open",
            }

    unresolved = 1 if position is not None else 0
    symbol = getattr(strategy, "market", MNQ).symbol
    return _build_result(
        trades,
        config,
        strategy_name,
        gap_rejections,
        unresolved,
        symbol,
        open_position=open_pos,
        intrabar_audit=intrabar_audit,
    )


def _uses_enhanced_execution(config: BacktestConfig) -> bool:
    """Whether any opt-in kai execution mechanic is enabled."""
    return bool(
        config.partial_take_profit_fraction
        or config.move_stop_to_break_even
        or config.pending_limit_entry
        or config.pending_order_wait_bars
        or config.cooldown_bars
        or config.discrete_partial_contracts
    )


def _strategy_execution_state(
    strategy: Strategy,
    *,
    pending: bool,
    position: bool,
    cooldown: int,
) -> None:
    hook = getattr(strategy, "set_execution_state", None)
    if callable(hook):
        hook(pending=pending, position=position, cooldown=cooldown)


def _observe_closed_bar(strategy: Strategy, history: list[Bar]) -> None:
    """Advance optional stateful indicators without asking for a signal."""
    hook = getattr(strategy, "observe", None)
    if callable(hook):
        hook(history)


def _run_backtest_enhanced(
    bars: list[Bar],
    strategy: Strategy,
    config: BacktestConfig,
    *,
    calibration_bars: Sequence[Bar] = (),
    m1_index: dict[datetime, list[Bar]] | None = None,
) -> BacktestResult:
    """Opt-in executor for partial/BE, pending limits, and cooldown.

    The event order mirrors the kai loops: a signal is formed from the last
    closed bar, its order is eligible on the next bar, stops are checked before
    targets, and a bar which merely reaches TP1 cannot also hit the newly moved
    break-even stop retroactively.
    """
    if len(bars) < 2:
        return _empty_result(config)
    if calibration_bars and calibration_bars[-1].timestamp >= bars[0].timestamp:
        raise ValueError("calibration_bars must end before the simulated bars")

    strategy_name = strategy.__class__.__name__
    dollar_risk = config.risk_per_trade * config.initial_balance
    trades: list[ExecutedTrade] = []
    position: dict | None = None
    pending: dict | None = None
    history = list(calibration_bars)
    gap_rejections = 0
    cooldown = 0
    partial_fraction = config.partial_take_profit_fraction
    intrabar_audit = IntrabarAudit(total_bars_evaluated=len(bars))

    def notify(name: str, *args) -> None:
        hook = getattr(strategy, name, None)
        if callable(hook):
            hook(*args)

    def make_order(signal, bar: Bar, index: int, *, limit: bool) -> dict | None:
        nonlocal gap_rejections
        if not limit and _entry_gap(config, history, bar):
            gap_rejections += 1
            return None
        entry = _round_tick(
            signal.entry if limit else _entry_price(config, signal.direction, bar.open),
            config.tick_size,
        )
        if config.fixed_quantity is not None:
            qty = config.fixed_quantity
        elif signal.stop_target_as_points:
            qty = _quantity(config, signal.stop)
        else:
            qty = _quantity(config, abs(entry - signal.stop))
        stop, target = _resolve_stop_target(
            signal.direction,
            entry,
            signal.stop,
            signal.target,
            signal.stop_target_as_points,
            config.tick_size,
        )
        if not _valid_levels(signal.direction, entry, stop, target):
            return None
        return {
            "direction": signal.direction,
            "entry": entry,
            "stop": stop,
            "initial_stop": stop,
            "target": target,
            "qty": qty,
            "entry_index": index,
            "entry_time": bar.timestamp,
            "entry_open": entry if limit else bar.open,
            "distance_mode": signal.stop_target_as_points,
            "risk_points": abs(entry - stop),
            "tp1": (
                entry + abs(entry - stop)
                if signal.direction == "long"
                else entry - abs(entry - stop)
            ),
            "partial_taken": False,
            "booked_move_points": 0.0,
            "limit_entry": limit,
            "q_tp1": 0,
            "q_rem": qty,
            "booked_pnl": 0.0,
        }

    def close_position(reason: str, fill: float, bar: Bar) -> None:
        nonlocal position, cooldown
        if position is None:
            return

        direction = position["direction"]
        entry = position["entry"]
        qty = position["qty"]
        if config.discrete_partial_contracts:
            if position["partial_taken"]:
                q_tp1 = position.get("q_tp1", math.floor(qty * partial_fraction))
                q_rem = position.get("q_rem", qty - q_tp1)
                booked_pnl = position.get(
                    "booked_pnl",
                    q_tp1 * position["risk_points"] * config.dollar_per_point,
                )
            else:
                q_tp1 = 0
                q_rem = qty
                booked_pnl = 0.0
            signed_move = fill - entry if direction == "long" else entry - fill
            rem_pnl = q_rem * signed_move * config.dollar_per_point
            gross_pnl = booked_pnl + rem_pnl
            total_move = (
                gross_pnl / (config.dollar_per_point * qty) if qty > 0 else 0.0
            )
            equivalent_exit = (
                entry + total_move if direction == "long" else entry - total_move
            )
            remaining_frac = q_rem / qty if qty > 0 else 1.0
        else:
            remaining = 1.0 - partial_fraction if position["partial_taken"] else 1.0
            signed_move = fill - entry if direction == "long" else entry - fill
            total_move = position["booked_move_points"] + remaining * signed_move
            equivalent_exit = (
                entry + total_move if direction == "long" else entry - total_move
            )
            gross_pnl = total_move * config.dollar_per_point * qty
            remaining_frac = remaining

        commission = config.commission_per_side * 2 * qty
        net_pnl = gross_pnl - commission
        initial_risk = position["risk_points"]
        stop_risk_dollars = initial_risk * config.dollar_per_point * qty
        entry_slip_pts = (
            0.0
            if position["limit_entry"]
            else (
                max(entry - position["entry_open"], 0.0)
                if direction == "long"
                else max(position["entry_open"] - entry, 0.0)
            )
        )
        exit_slip_pts = 0.0
        if reason in {"stop_loss", "break_even_stop"}:
            gap = (direction == "long" and bar.open < position["stop"]) or (
                direction == "short" and bar.open > position["stop"]
            )
            if not gap:
                exit_slip_pts = abs(fill - position["stop"]) * remaining_frac
        elif reason == "time_exit" and config.time_exit_mode == "market":
            exit_slip_pts = config.time_exit_slippage_points * remaining_frac
        elif reason == "end_of_data":
            exit_slip_pts = config.end_of_data_slippage_points * remaining_frac
        slippage_cost = (
            entry_slip_pts + exit_slip_pts
        ) * config.dollar_per_point * qty
        executed = ExecutedTrade(
            trade_id=f"bt-{len(trades) + 1}",
            direction=direction,
            entry_time=position["entry_time"],
            exit_time=bar.timestamp,
            entry_price=entry,
            exit_price=equivalent_exit,
            stop_price=position["stop"],
            target_price=position["target"],
            quantity=position["qty"],
            gross_pnl=gross_pnl,
            commission=commission,
            net_pnl=net_pnl,
            r_result=net_pnl / dollar_risk if dollar_risk > 0 else 0.0,
            exit_reason=reason,
            stop_risk_dollars=stop_risk_dollars,
            slippage_cost=slippage_cost,
            budgeted_risk_dollars=dollar_risk,
            effective_risk_dollars=stop_risk_dollars,
            budgeted_r=net_pnl / dollar_risk if dollar_risk > 0 else 0.0,
            effective_r=net_pnl / stop_risk_dollars if stop_risk_dollars > 0 else 0.0,
        )
        trades.append(executed)
        notify("note_trade", executed.exit_time, executed.r_result)
        position = None
        cooldown = config.cooldown_bars

    i = 0
    while i < len(bars):
        bar = bars[i]

        if position is None and cooldown > 0:
            history.append(bar)
            # On the final cooldown bar kai decrements to zero and then allows
            # structure evaluation. Defer that closed bar to the next loop so
            # evaluate(history) can form an order for the following bar.
            if cooldown > 1:
                _observe_closed_bar(strategy, history)
            cooldown -= 1
            i += 1
            continue

        if position is None:
            _strategy_execution_state(
                strategy, pending=pending is not None, position=False, cooldown=0
            )
            signal = strategy.evaluate(history) if history else None
            if pending is None and signal is not None:
                if config.pending_limit_entry:
                    order = make_order(signal, bar, i, limit=True)
                    if order is not None:
                        order["remaining_wait"] = config.pending_order_wait_bars
                        pending = order
                else:
                    position = make_order(signal, bar, i, limit=False)

            if pending is not None:
                # E5: a resting limit only fills when the bar actually TRADES
                # through the level (low <= entry <= high). A bar that opens
                # entirely beyond the level (gap-through) never negotiates it;
                # filling at the stale limit price and checking the stop on that
                # same bar fabricates losses that cannot occur in live execution
                # (observed: -23.5R). Gap-through orders do not fill: they keep
                # waiting and expire normally.
                filled = bar.low <= pending["entry"] <= bar.high
                if filled:
                    position = pending
                    position["entry_index"] = i
                    position["entry_time"] = bar.timestamp
                    pending = None
                    notify("note_order_filled", bar.timestamp)
                else:
                    pending["remaining_wait"] -= 1
                    if pending["remaining_wait"] <= 0:
                        pending = None
                        notify("note_order_expired", bar.timestamp)
                    history.append(bar)
                    i += 1
                    continue

            if position is None:
                history.append(bar)
                i += 1
                continue

        direction = position["direction"]
        stop = position["stop"]
        target = position["target"]
        tp1 = position["tp1"]
        bars_held = i - position["entry_index"]
        intrabar_audit.bars_in_position += 1

        reason: str | None = None
        fill: float | None = None
        if _time_exit_open(config, position, bar):
            reason = "time_exit"
            fill = _time_exit_fill(
                config, direction, bar.open, position["entry"]
            )
        else:
            hit_stop = stop >= bar.low if direction == "long" else stop <= bar.high
            hit_target = target <= bar.high if direction == "long" else target >= bar.low
            hit_tp1 = tp1 <= bar.high if direction == "long" else tp1 >= bar.low
            if hit_target and hit_stop:
                intrabar_audit.ambiguous_bars_count += 1
                intrabar_audit.ambiguous_timestamps.append(bar.timestamp)
                if m1_index and bar.timestamp in m1_index:
                    m1_slice = m1_index[bar.timestamp]
                    m1_reason, status = resolve_intrabar_with_m1(direction, stop, target, m1_slice)
                    if status == "resolved_by_m1_target":
                        intrabar_audit.resolved_by_m1_target += 1
                        if not position["partial_taken"] and hit_tp1 and partial_fraction > 0:
                            position["partial_taken"] = True
                            if config.discrete_partial_contracts:
                                q_tp1 = math.floor(position["qty"] * partial_fraction)
                                position["q_tp1"] = q_tp1
                                position["q_rem"] = position["qty"] - q_tp1
                                position["booked_pnl"] = (
                                    q_tp1 * position["risk_points"] * config.dollar_per_point
                                )
                            else:
                                position["booked_move_points"] = (
                                    partial_fraction * position["risk_points"]
                                )
                        reason, fill = "take_profit", target
                    elif status == "resolved_by_m1_stop":
                        intrabar_audit.resolved_by_m1_stop += 1
                        reason = "break_even_stop" if position["partial_taken"] else "stop_loss"
                        fill = _stop_fill(config, direction, stop, bar.open)
                    elif status == "m1_residual_ambiguity_conservative_stop":
                        intrabar_audit.m1_residual_ambiguity += 1
                        reason = "break_even_stop" if position["partial_taken"] else "stop_loss"
                        fill = _stop_fill(config, direction, stop, bar.open)
                    else:
                        intrabar_audit.no_m1_data_fallback += 1
                        reason = "break_even_stop" if position["partial_taken"] else "stop_loss"
                        fill = _stop_fill(config, direction, stop, bar.open)
                else:
                    intrabar_audit.no_m1_data_fallback += 1
                    reason = "break_even_stop" if position["partial_taken"] else "stop_loss"
                    fill = _stop_fill(config, direction, stop, bar.open)
            elif hit_stop:
                reason = "break_even_stop" if position["partial_taken"] else "stop_loss"
                fill = _stop_fill(config, direction, stop, bar.open)
            elif hit_target:
                if not position["partial_taken"] and hit_tp1 and partial_fraction > 0:
                    position["partial_taken"] = True
                    if config.discrete_partial_contracts:
                        q_tp1 = math.floor(position["qty"] * partial_fraction)
                        position["q_tp1"] = q_tp1
                        position["q_rem"] = position["qty"] - q_tp1
                        position["booked_pnl"] = (
                            q_tp1 * position["risk_points"] * config.dollar_per_point
                        )
                    else:
                        position["booked_move_points"] = (
                            partial_fraction * position["risk_points"]
                        )
                reason, fill = "take_profit", target
            elif (
                hit_tp1
                and partial_fraction > 0
                and not position["partial_taken"]
            ):
                position["partial_taken"] = True
                if config.discrete_partial_contracts:
                    q_tp1 = math.floor(position["qty"] * partial_fraction)
                    position["q_tp1"] = q_tp1
                    position["q_rem"] = position["qty"] - q_tp1
                    position["booked_pnl"] = (
                        q_tp1 * position["risk_points"] * config.dollar_per_point
                    )
                else:
                    position["booked_move_points"] = (
                        partial_fraction * position["risk_points"]
                    )
                if config.move_stop_to_break_even:
                    position["stop"] = position["entry"]
            elif _time_exit_close(config, position, bar, bars_held):
                reason = "time_exit"
                fill = _time_exit_fill(
                    config, direction, bar.close, position["entry"]
                )

        if reason is not None and fill is not None:
            close_position(reason, fill, bar)

        history.append(bar)
        _observe_closed_bar(strategy, history)
        i += 1

    open_pos = None
    if position is not None:
        last = bars[-1]
        raw_exit_price = _round_tick(last.close, config.tick_size)
        slip = config.end_of_data_slippage_points
        direction = position["direction"]
        exit_price = _round_tick(
            raw_exit_price - slip if direction == "long" else raw_exit_price + slip,
            config.tick_size,
        )
        if config.end_of_data_policy == "close":
            close_position("end_of_data", exit_price, last)
        else:
            direction = position["direction"]
            entry = position["entry"]
            qty = position["qty"]
            remaining = 1.0 - partial_fraction if position["partial_taken"] else 1.0
            signed_move = exit_price - entry if direction == "long" else entry - exit_price
            if config.discrete_partial_contracts and position["partial_taken"]:
                q_rem = position.get("q_rem", qty)
                unrealized_gross = position.get("booked_pnl", 0.0) + (
                    signed_move * config.dollar_per_point * q_rem
                )
            else:
                total_move = position.get("booked_move_points", 0.0) + remaining * signed_move
                unrealized_gross = total_move * config.dollar_per_point * qty
            commission = config.commission_per_side * 2 * qty
            unrealized_net = unrealized_gross - commission
            open_pos = {
                "direction": direction,
                "entry_price": entry,
                "entry_time": position["entry_time"],
                "last_price": exit_price,
                "last_time": last.timestamp,
                "stop_price": position["stop"],
                "target_price": position["target"],
                "quantity": qty,
                "unrealized_gross_pnl": unrealized_gross,
                "unrealized_net_pnl": unrealized_net,
                "state": "open",
            }

    unresolved = int(position is not None)
    if pending is not None:
        notify("note_order_expired", bars[-1].timestamp)
    symbol = getattr(strategy, "market", MNQ).symbol
    return _build_result(
        trades,
        config,
        strategy_name,
        gap_rejections,
        unresolved,
        symbol,
        open_position=open_pos,
        intrabar_audit=intrabar_audit,
    )


def run_backtest(
    bars: list[Bar],
    strategy: Strategy,
    config: BacktestConfig,
    *,
    calibration_bars: Sequence[Bar] = (),
    m1_bars: Sequence[Bar] | None = None,
) -> BacktestResult:
    """Run the legacy executor or the additive opt-in execution path."""
    m1_idx = index_m1_bars(m1_bars) if m1_bars else None
    if not _uses_enhanced_execution(config):
        return _run_backtest_legacy(
            bars, strategy, config, calibration_bars=calibration_bars, m1_index=m1_idx
        )
    return _run_backtest_enhanced(
        bars, strategy, config, calibration_bars=calibration_bars, m1_index=m1_idx
    )


def executed_to_core_trades(
    trades: tuple[ExecutedTrade, ...],
    config: BacktestConfig | None = None,
    strategy_name: str = "unknown",
    *,
    symbol: str = MNQ.symbol,
) -> list[Trade]:
    """Map executed backtest trades to Core ``Trade`` (R-multiples) for the engine.

    ``r_result`` uses the dollar-accurate normalization
    ``net_pnl / (risk_per_trade * initial_balance)`` so the engine's equity update
    reproduces actual dollar P&L. The per-trade stop risk (``|entry-stop| * $/pt
    * qty``) and the stop-relative R are stored separately in ``metadata``, so
    the two notions of "R" are never conflated. ``strategy_name`` is the real
    strategy identity (never hardcoded).
    """
    dollar_risk = (
        config.risk_per_trade * config.initial_balance if config is not None else None
    )
    use_session_date = config.session_date_for_ledger if config is not None else False
    core_trades: list[Trade] = []
    for t in trades:
        if use_session_date:
            sd = session_date(t.exit_time)
            trade_date = sd.strftime("%Y-%m-%d") if sd is not None else t.exit_time.strftime("%Y-%m-%d")
        else:
            trade_date = t.exit_time.strftime("%Y-%m-%d")
        core_trades.append(
            Trade(
                r_result=t.r_result,
                trade_id=t.trade_id,
                timestamp=t.exit_time,
                date=trade_date,
                asset=symbol,
                direction=t.direction,  # type: ignore[arg-type]
                entry_price=t.entry_price,
                stop_price=t.stop_price,
                exit_price=t.exit_price,
                strategy=strategy_name,
                metadata={
                    "quantity": t.quantity,
                    "gross_pnl": t.gross_pnl,
                    "commission": t.commission,
                    "net_pnl": t.net_pnl,
                    "exit_reason": t.exit_reason,
                    "risk_budget_dollars": dollar_risk,
                    "stop_risk_dollars": t.stop_risk_dollars,
                    "r_vs_stop": (
                        t.net_pnl / t.stop_risk_dollars if t.stop_risk_dollars > 0 else None
                    ),
                    "normalization": "r_result = net_pnl / (risk_per_trade * initial_balance)",
                },
            )
        )
    return core_trades


to_engine_trades = executed_to_core_trades


def _build_result(
    trades: list[ExecutedTrade],
    config: BacktestConfig,
    strategy_name: str,
    gap_rejections: int,
    unresolved: int,
    symbol: str = MNQ.symbol,
    *,
    open_position: dict | None = None,
    intrabar_audit: Any | None = None,
) -> BacktestResult:
    core_trades = executed_to_core_trades(tuple(trades), config, strategy_name, symbol=symbol)
    simulation = (
        run_simulation(
            core_trades,
            FundedAccountRules(
                initial_balance=config.initial_balance,
                profit_target_pct=config.profit_target_pct,
                max_drawdown_pct=config.max_drawdown_pct,
                daily_loss_limit_pct=config.daily_loss_limit_pct,
                risk_per_trade=config.risk_per_trade,
                max_trades=config.max_trades,
            ),
        )
        if core_trades
        else None
    )

    net_pnl = sum(t.net_pnl for t in trades)
    n = len(trades)
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl < 0]
    gross_profit = sum(t.net_pnl for t in wins)
    gross_loss = abs(sum(t.net_pnl for t in losses))
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0
    win_rate = (len(wins) / n) if n else 0.0
    expectancy = (net_pnl / n) if n else 0.0
    total_commission = sum(t.commission for t in trades)
    total_slippage = sum(t.slippage_cost for t in trades)

    # RAW strategy equity curve (all simulated trades, no rule stops).
    equity = config.initial_balance
    peak = equity
    max_dd = 0.0
    curve = [equity]
    for t in trades:
        equity += t.net_pnl
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        curve.append(equity)

    return BacktestResult(
        config=config,
        trades=tuple(trades),
        simulation=simulation,
        net_pnl=net_pnl,
        n_trades=n,
        win_rate=win_rate,
        profit_factor=profit_factor,
        expectancy=expectancy,
        max_drawdown_pct=max_dd,
        total_commission=total_commission,
        total_slippage_cost=total_slippage,
        equity_curve=tuple(curve),
        gap_rejections=gap_rejections,
        unresolved_positions=unresolved,
        open_position=open_position,
        intrabar_audit=intrabar_audit,
    )


__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "ExecutedTrade",
    "executed_to_core_trades",
    "run_backtest",
    "to_engine_trades",
]

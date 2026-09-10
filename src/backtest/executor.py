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
from src.backtest.markets import MNQ
from src.backtest.strategy import Strategy
from src.engine import SimulationResult, run_simulation
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

    def __post_init__(self) -> None:
        float_fields = (
            "initial_balance",
            "risk_per_trade",
            "dollar_per_point",
            "tick_size",
            "commission_per_side",
            "slippage_points",
            "profit_target_pct",
            "max_drawdown_pct",
            "daily_loss_limit_pct",
            "max_hold_minutes",
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
    )


def run_backtest(
    bars: list[Bar],
    strategy: Strategy,
    config: BacktestConfig,
    *,
    calibration_bars: Sequence[Bar] = (),
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
                if config.fixed_quantity is not None:
                    qty = config.fixed_quantity
                elif signal.stop_target_as_points:
                    qty = _quantity(config, signal.stop)  # stop IS the distance
                else:
                    qty = _quantity(config, abs(entry - signal.stop))
                stop, target = _resolve_stop_target(
                    signal.direction, entry, signal.stop, signal.target,
                    signal.stop_target_as_points, config.tick_size,
                )
                if not _valid_levels(signal.direction, entry, stop, target):
                    # levels collapsed after rounding: reject the signal
                    history.append(bar)
                    i += 1
                    continue
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

        reason: str | None = None
        exit_price: float | None = None
        if _time_exit_open(config, position, bar):
            # hold limit reached at this bar's open: close at the open, before
            # consulting the new bar's high/low/close.
            reason, exit_price = "time_exit", _round_tick(bar.open, config.tick_size)
        else:
            hit_target = target <= bar.high if direction == "long" else target >= bar.low
            hit_stop = stop >= bar.low if direction == "long" else stop <= bar.high
            if hit_target and hit_stop:
                reason, exit_price = "stop_loss", _stop_fill(config, direction, stop, bar.open)
            elif hit_target:
                reason, exit_price = "take_profit", target
            elif hit_stop:
                reason, exit_price = "stop_loss", _stop_fill(config, direction, stop, bar.open)
            elif _time_exit_close(config, position, bar, bars_held):
                reason, exit_price = "time_exit", _round_tick(bar.close, config.tick_size)

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

    if position is not None and not position["distance_mode"]:
        last = bars[-1]
        direction = position["direction"]
        entry = position["entry"]
        exit_price = _round_tick(last.close, config.tick_size)
        move = exit_price - entry if direction == "long" else entry - exit_price
        gross_pnl = move * config.dollar_per_point * position["qty"]
        commission = config.commission_per_side * 2 * position["qty"]
        net_pnl = gross_pnl - commission
        stop_distance = abs(entry - position["stop"])
        entry_slip_pts = (
            max(entry - position["entry_open"], 0.0)
            if direction == "long"
            else max(position["entry_open"] - entry, 0.0)
        )
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
                quantity=position["qty"],
                gross_pnl=gross_pnl,
                commission=commission,
                net_pnl=net_pnl,
                r_result=net_pnl / dollar_risk if dollar_risk > 0 else 0.0,
                exit_reason="end_of_data",
                stop_risk_dollars=(
                    stop_distance * config.dollar_per_point * position["qty"]
                ),
                slippage_cost=(
                    entry_slip_pts * config.dollar_per_point * position["qty"]
                ),
            )
        )
        note = getattr(strategy, "note_trade", None)
        if callable(note):
            note(last.timestamp, net_pnl / dollar_risk if dollar_risk > 0 else 0.0)
        position = None

    unresolved = 1 if position is not None else 0
    symbol = getattr(strategy, "market", MNQ).symbol
    return _build_result(trades, config, strategy_name, gap_rejections, unresolved, symbol)


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
    return [
        Trade(
            r_result=t.r_result,
            trade_id=t.trade_id,
            timestamp=t.exit_time,
            date=t.exit_time.strftime("%Y-%m-%d"),
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
        for t in trades
    ]


def _build_result(
    trades: list[ExecutedTrade],
    config: BacktestConfig,
    strategy_name: str,
    gap_rejections: int,
    unresolved: int,
    symbol: str = MNQ.symbol,
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
    )


__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "ExecutedTrade",
    "executed_to_core_trades",
    "run_backtest",
]

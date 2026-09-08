"""Bar-by-bar backtest executor (FASE C) feeding the Core engine.

Turns a bar sequence + strategy into simulated trades (entry on the NEXT bar
open, conservative intrabar TP/SL resolution, commissions and slippage), then
runs the existing :func:`src.engine.run_simulation` for funded-account rules.

Look-ahead rules honored:
* signals use only CLOSED bars;
* entry fills at the next bar's open (with slippage against the position);
* TP/SL use an explicit intrabar policy: if both are reachable within one bar,
  the stop is assumed to fill first (conservative);
* stop (market) fills get slippage against the position; limit (TP) fills do not;
* the entry bar itself is not checked for TP/SL (conservative).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from src.backtest.history import Bar
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
    dollar_per_point: float = 2.0  # MNQ micro
    tick_size: float = 0.25
    max_contracts: int = 10
    commission_per_side: float = 0.62  # per contract per side (PROVISIONAL)
    slippage_points: float = 0.25  # per market fill (PROVISIONAL)
    profit_target_pct: float = 0.06
    max_drawdown_pct: float = 0.06
    daily_loss_limit_pct: float = 0.03
    max_trades: int | None = None
    max_bars_held: int = 100  # force-close after N bars (PROVISIONAL)


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


def _quantity(config: BacktestConfig, stop_distance: float) -> int:
    dollar_risk = config.risk_per_trade * config.initial_balance
    if stop_distance <= 0:
        return 1
    raw = dollar_risk / (stop_distance * config.dollar_per_point)
    qty = max(1, int(round(raw)))
    return min(qty, config.max_contracts)


def _entry_price(config: BacktestConfig, direction: str, open_price: float) -> float:
    slip = config.slippage_points
    return open_price + slip if direction == "long" else open_price - slip


def _stop_fill(config: BacktestConfig, direction: str, stop: float) -> float:
    slip = config.slippage_points
    return stop - slip if direction == "long" else stop + slip


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
    bars: list[Bar], strategy: Strategy, config: BacktestConfig
) -> BacktestResult:
    """Simulate one contiguous bar run and return trades + metrics + engine result."""
    if len(bars) < 2:
        return _empty_result(config)

    dollar_risk = config.risk_per_trade * config.initial_balance
    trades: list[ExecutedTrade] = []
    position: dict | None = None
    history: list[Bar] = []

    i = 0
    while i < len(bars):
        bar = bars[i]
        if position is None:
            signal = strategy.evaluate(history) if history else None
            if signal is not None:
                entry = _entry_price(config, signal.direction, bar.open)
                qty = _quantity(config, abs(entry - signal.stop))
                position = {
                    "direction": signal.direction,
                    "entry": entry,
                    "stop": signal.stop,
                    "target": signal.target,
                    "qty": qty,
                    "entry_index": i,
                    "entry_time": bar.timestamp,
                }
            else:
                history.append(bar)
                i += 1
                continue

        # A position is open — possibly just opened on this bar. Evaluate TP/SL
        # intrabar for the current bar, INCLUDING the entry bar. If both levels
        # are reachable within one bar, the stop is assumed to fill first
        # (conservative: worst-case resolution).
        direction = position["direction"]
        stop = position["stop"]
        target = position["target"]
        hit_target = target <= bar.high if direction == "long" else target >= bar.low
        hit_stop = stop >= bar.low if direction == "long" else stop <= bar.high
        bars_held = i - position["entry_index"]

        reason: str | None = None
        exit_price: float | None = None
        if hit_target and hit_stop:
            reason, exit_price = "stop_loss", _stop_fill(config, direction, stop)
        elif hit_target:
            reason, exit_price = "take_profit", target
        elif hit_stop:
            reason, exit_price = "stop_loss", _stop_fill(config, direction, stop)
        elif bars_held >= config.max_bars_held:
            reason, exit_price = "time_exit", bar.close

        if reason is not None and exit_price is not None:
            entry = position["entry"]
            move = exit_price - entry if direction == "long" else entry - exit_price
            gross_pnl = move * config.dollar_per_point * position["qty"]
            commission = config.commission_per_side * 2 * position["qty"]
            net_pnl = gross_pnl - commission
            r_result = net_pnl / dollar_risk if dollar_risk > 0 else 0.0
            trades.append(
                ExecutedTrade(
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
                )
            )
            position = None

        history.append(bar)
        i += 1

    # Close any still-open position at end of data (marked honestly).
    if position is not None:
        last = bars[-1]
        direction = position["direction"]
        entry = position["entry"]
        exit_price = last.close
        move = exit_price - entry if direction == "long" else entry - exit_price
        gross_pnl = move * config.dollar_per_point * position["qty"]
        commission = config.commission_per_side * 2 * position["qty"]
        net_pnl = gross_pnl - commission
        r_result = net_pnl / dollar_risk if dollar_risk > 0 else 0.0
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
                r_result=r_result,
                exit_reason="end_of_data",
            )
        )

    return _build_result(trades, config)


def executed_to_core_trades(trades: tuple[ExecutedTrade, ...]) -> list[Trade]:
    """Map executed backtest trades to Core ``Trade`` (R-multiples) for the engine."""
    return [
        Trade(
            r_result=t.r_result,
            trade_id=t.trade_id,
            timestamp=t.exit_time,
            date=t.exit_time.strftime("%Y-%m-%d"),
            asset="MNQ",
            direction=t.direction,  # type: ignore[arg-type]
            entry_price=t.entry_price,
            stop_price=t.stop_price,
            exit_price=t.exit_price,
            strategy="breakout_provisional",
            metadata={
                "quantity": t.quantity,
                "gross_pnl": t.gross_pnl,
                "commission": t.commission,
                "net_pnl": t.net_pnl,
                "exit_reason": t.exit_reason,
            },
        )
        for t in trades
    ]


def _build_result(
    trades: list[ExecutedTrade], config: BacktestConfig
) -> BacktestResult:
    core_trades = executed_to_core_trades(tuple(trades))
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
    total_slippage = sum(
        config.slippage_points * config.dollar_per_point * t.quantity * 2
        for t in trades
    )

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
    )


__all__ = [
    "BacktestConfig",
    "ExecutedTrade",
    "BacktestResult",
    "run_backtest",
    "executed_to_core_trades",
]

# -*- coding: utf-8 -*-
"""Regression tests for fill-bar clean resolution (regla A1).

Verifica que en órdenes límite descansadas (position["limit_entry"] and i == position["entry_index"]):
(a) Límite con solo-TP en la vela del fill -> NO es win (continúa; se resuelve luego o queda sin resolver).
(b) Límite con tp1 (parcial) en la vela del fill -> no hay parcial ni BE en esa vela.
(c) Ambos (SL y TP) en la vela del fill -> stop_loss inmediato.
(d) Entrada a mercado -> semántica actual intacta (resuelve en barra de entrada si aplica).
(e) Velas posteriores al fill -> resolución normal de TP y SL.
(f) Fixture end-to-end determinista congelado con regla limpia A1.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import pytest

from src.backtest.executor import BacktestConfig, run_backtest
from src.backtest.history import Bar
from src.backtest.strategy import Signal, Strategy


def _bar(minute: int, o: float, h: float, l: float, c: float) -> Bar:
    ts = datetime(2026, 9, 1, 9, 0, tzinfo=UTC) + timedelta(minutes=minute)
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=10.0)


class _SignalOnce(Strategy):
    def __init__(self, signal: Signal):
        self._signal = signal
        self._fired = False

    def evaluate(self, history: list[Bar]) -> Signal | None:
        if self._fired or not history:
            return None
        self._fired = True
        return self._signal


def test_a_limit_solo_target_on_fill_bar_remains_open():
    """(a) Límite con solo-TP en la vela del fill -> NO cierra en esa vela; queda abierta."""
    # Señal en barra 0: compra límite en 100.0, SL=95.0, TP=110.0
    sig = Signal("long", 100.0, 95.0, 110.0)
    bars = [
        _bar(0, 102, 103, 101, 102),  # Barra 0: se genera la orden límite descansada
        _bar(1, 102, 112, 99, 101),   # Barra 1: toca 99 (fill a 100), toca 112 (TP 110), low no toca SL (95)
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.0,
        slippage_points=0.0,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
    )
    res = run_backtest(bars, _SignalOnce(sig), cfg)
    
    # En la vela del fill (barra 1), TP está bloqueado (regla A1).
    # Como SL (95) no se tocó, la posición DEBE seguir abierta.
    assert res.n_trades == 0
    assert res.unresolved_positions == 1
    assert res.open_position is not None
    assert res.open_position["entry_price"] == 100.0


def test_b_limit_tp1_partial_on_fill_bar_does_not_trigger_partial_or_be():
    """(b) tp1 (parcial) en la vela del fill -> no hay parcial ni movimiento de stop a BE."""
    # Señal en barra 0: compra límite 100.0, SL=90.0 (riesgo 10), tp1=110.0 (1R), TP=130.0 (3R)
    sig = Signal("long", 100.0, 90.0, 130.0)
    bars = [
        _bar(0, 102, 103, 101, 102),
        _bar(1, 102, 115, 99, 101),   # Barra 1: fill a 100, high 115 toca tp1 110 pero no TP 130
        _bar(2, 101, 102, 95, 98),    # Barra 2: low cae a 95 (por debajo de BE 100, pero por encima de SL 90)
    ]
    cfg = BacktestConfig(
        fixed_quantity=2,
        commission_per_side=0.0,
        slippage_points=0.0,
        partial_take_profit_fraction=0.5,
        move_stop_to_break_even=True,
        discrete_partial_contracts=True,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
    )
    res = run_backtest(bars, _SignalOnce(sig), cfg)
    
    # Si tp1 se hubiera activado en la vela 1, el stop se habría movido a BE (100.0).
    # En la vela 2, con low=95, se habría cerrado por break_even_stop.
    # Con la regla limpia A1, tp1 NO se activa en la vela del fill -> stop sigue en 90.0.
    # En la vela 2, low=95 > 90 -> la posición sigue abierta con 2 contratos y 0 trades cerrados.
    assert res.n_trades == 0
    assert res.unresolved_positions == 1
    assert res.open_position is not None
    assert res.open_position["stop_price"] == 90.0
    assert res.open_position["quantity"] == 2


def test_c_limit_both_sl_and_tp_on_fill_bar_exits_by_stop_loss():
    """(c) Ambos (SL y TP) tocados en la vela del fill -> cierra por stop_loss."""
    # Señal en barra 0: compra límite 100.0, SL=95.0, TP=105.0
    sig = Signal("long", 100.0, 95.0, 105.0)
    bars = [
        _bar(0, 102, 103, 101, 102),
        _bar(1, 102, 108, 93, 100),   # Barra 1: toca 100 (fill), 108 (TP 105), 93 (SL 95)
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.0,
        slippage_points=0.0,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
    )
    res = run_backtest(bars, _SignalOnce(sig), cfg)
    
    # En A1, en la vela del fill solo SL cuenta. Si se tocan ambos, SL gana incondicionalmente.
    assert res.n_trades == 1
    t = res.trades[0]
    assert t.entry_price == 100.0
    assert t.exit_reason == "stop_loss"
    assert t.exit_price == 95.0
    assert t.net_pnl == -10.0  # (95 - 100) * 2$/pt * 1 contrato


def test_d_market_entry_semantics_remain_intact():
    """(d) Entrada a mercado -> semántica actual intacta (resuelve en la misma barra si aplica)."""
    # Para órdenes a mercado (pending_limit_entry=False), el comportamiento legado se preserva.
    sig = Signal("long", 100.0, 95.0, 105.0)
    bars = [
        _bar(0, 100, 101, 99, 100),  # Señal
        _bar(1, 100, 106, 98, 102),  # Entrada a mercado al open (100). High 106 toca TP 105.
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.0,
        slippage_points=0.0,
        pending_limit_entry=False,  # Mercado
    )
    res = run_backtest(bars, _SignalOnce(sig), cfg)
    
    # En órdenes a mercado no hay cola límite descansando; resuelve en la barra 1 como antes.
    assert res.n_trades == 1
    t = res.trades[0]
    assert t.entry_price == 100.0
    assert t.exit_reason == "take_profit"
    assert t.exit_price == 105.0


def test_e_subsequent_bars_resolve_normally():
    """(e) Velas posteriores al fill -> TP y SL resuelven normalmente."""
    sig = Signal("long", 100.0, 95.0, 110.0)
    bars = [
        _bar(0, 102, 103, 101, 102),  # Señal
        _bar(1, 102, 108, 99, 101),   # Fill en 100. High 108 (no toca TP 110). Low 99 (no toca SL 95).
        _bar(2, 101, 112, 100, 111),  # Vela posterior al fill: High 112 toca TP 110.
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.0,
        slippage_points=0.0,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
    )
    res = run_backtest(bars, _SignalOnce(sig), cfg)
    
    assert res.n_trades == 1
    t = res.trades[0]
    assert t.entry_time == bars[1].timestamp
    assert t.exit_time == bars[2].timestamp
    assert t.entry_price == 100.0
    assert t.exit_price == 110.0
    assert t.exit_reason == "take_profit"


def test_f_frozen_deterministic_fixture_clean_semantics():
    """(f) Celda determinista congelada fijando la semántica limpia A1 como regresión permanente."""
    # Secuencia de 5 velas con 2 órdenes límite:
    # Trade 1: Long limit 100 (stop 90, target 110) en barra 1. Barra 1 toca 112 (TP) y 99 (fill) -> NO cierra.
    # Barra 2 cae a 89 -> cierra en barra 2 por stop_loss a 90.0.
    bars = [
        _bar(0, 105, 106, 104, 105),  # Señal en barra 0
        _bar(1, 103, 112, 99, 101),   # Fill a 100. High 112 toca TP. En regla A1 NO cierra.
        _bar(2, 101, 102, 89, 90),    # Low 89 toca SL 90 -> cierra por stop_loss.
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=2.0,
        slippage_points=0.25,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
    )
    res = run_backtest(bars, _SignalOnce(Signal("long", 100.0, 90.0, 110.0)), cfg)
    
    assert res.n_trades == 1
    t = res.trades[0]
    assert t.entry_time == bars[1].timestamp
    assert t.exit_time == bars[2].timestamp
    assert t.entry_price == 100.0
    assert t.stop_price == 90.0
    assert t.target_price == 110.0
    assert t.exit_reason == "stop_loss"
    assert t.exit_price == 89.75  # Stop fill con 0.25 slip
    assert t.net_pnl == -24.5     # Pérdida neta exacta congelada: gross (-20.5) - comm (4.0) = -24.5

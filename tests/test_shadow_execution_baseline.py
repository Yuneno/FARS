# -*- coding: utf-8 -*-
"""SIM-01A: Caracterizacion y regresion congelada del executor enhanced existente.

Este modulo define los fixtures, casos de prueba y baseline determinista
para las 10 familias (A-J) requeridas antes de la extraccion del nucleo incremental.

Permisos de escritura estrictos:
- tests/test_shadow_execution_baseline.py
- tests/fixtures/shadow_execution_baseline_v1.json
- lab_artifacts/shadow_simulation/SIM01A_REPORT.md
- lab_artifacts/shadow_simulation/SIM01A_TEST_OUTPUT.txt
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Calcular raiz del repositorio dinamicamente sin hardcodear home
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.backtest.executor import (
    BacktestConfig,
    BacktestResult,
    ExecutedTrade,
    run_backtest,
)
from src.backtest.history import Bar
from src.backtest.strategy import Signal, Strategy
from src.backtest.markets import MNQ

BASELINE_PATH = REPO_ROOT / "tests" / "fixtures" / "shadow_execution_baseline_v1.json"
BASE_TIME = datetime(2026, 9, 23, 13, 30, tzinfo=timezone.utc)


def compute_file_sha256(filepath: Path) -> str:
    """Calcula SHA256 de un archivo para registrar procedencia."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_git_head(repo_root: Path) -> str:
    """Obtiene el hash del commit HEAD sin invocar shell adicional."""
    git_head_file = repo_root / ".git" / "HEAD"
    if git_head_file.exists():
        content = git_head_file.read_text(encoding="utf-8").strip()
        if content.startswith("ref:"):
            ref_path = repo_root / ".git" / content.split(":", 1)[1].strip()
            if ref_path.exists():
                return ref_path.read_text(encoding="utf-8").strip()
        return content
    return "unknown"


def make_bar(
    minute: int,
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    volume: float = 10.0,
    base_time: datetime = BASE_TIME,
) -> Bar:
    """Helper determinista para construir barras con timestamps UTC."""
    ts = base_time + timedelta(minutes=minute)
    return Bar(
        timestamp=ts,
        open=float(open_p),
        high=float(high_p),
        low=float(low_p),
        close=float(close_p),
        volume=float(volume),
    )


class RecordingStrategy(Strategy):
    """Estrategia envoltorio que captura todos los hooks observables del executor."""

    def __init__(self, inner: Strategy, market=MNQ):
        self.inner = inner
        self.market = market
        self.events: list[dict[str, Any]] = []

    def evaluate(self, history: list[Bar]) -> Signal | None:
        sig = self.inner.evaluate(history)
        if sig is not None:
            self.events.append(
                {
                    "type": "evaluate_signal",
                    "history_len": len(history),
                    "last_bar_time": history[-1].timestamp.isoformat()
                    if history
                    else None,
                    "direction": sig.direction,
                    "entry": sig.entry,
                    "stop": sig.stop,
                    "target": sig.target,
                }
            )
        return sig

    def set_execution_state(
        self, *, pending: bool, position: bool, cooldown: int
    ) -> None:
        self.events.append(
            {
                "type": "set_execution_state",
                "pending": pending,
                "position": position,
                "cooldown": cooldown,
            }
        )
        hook = getattr(self.inner, "set_execution_state", None)
        if callable(hook):
            hook(pending=pending, position=position, cooldown=cooldown)

    def observe(self, history: list[Bar]) -> None:
        self.events.append(
            {
                "type": "observe",
                "history_len": len(history),
                "last_bar_time": history[-1].timestamp.isoformat()
                if history
                else None,
            }
        )
        hook = getattr(self.inner, "observe", None)
        if callable(hook):
            hook(history)

    def note_order_filled(self, timestamp: datetime) -> None:
        self.events.append(
            {
                "type": "note_order_filled",
                "timestamp": timestamp.isoformat(),
            }
        )
        hook = getattr(self.inner, "note_order_filled", None)
        if callable(hook):
            hook(timestamp)

    def note_order_expired(self, timestamp: datetime) -> None:
        self.events.append(
            {
                "type": "note_order_expired",
                "timestamp": timestamp.isoformat(),
            }
        )
        hook = getattr(self.inner, "note_order_expired", None)
        if callable(hook):
            hook(timestamp)

    def note_trade(self, timestamp: datetime, r_result: float) -> None:
        self.events.append(
            {
                "type": "note_trade",
                "timestamp": timestamp.isoformat(),
                "r_result": r_result,
            }
        )
        hook = getattr(self.inner, "note_trade", None)
        if callable(hook):
            hook(timestamp, r_result)


class SingleSignalStrategy(Strategy):
    """Genera una sola senal cuando se cierra la primera barra."""

    def __init__(self, signal: Signal, market=MNQ):
        self.signal = signal
        self.market = market
        self.fired = False

    def evaluate(self, history: list[Bar]) -> Signal | None:
        if self.fired or not history:
            return None
        self.fired = True
        return self.signal


class CooldownProbingStrategy(Strategy):
    """Estrategia para sondear el ciclo causal de cooldown."""

    def __init__(self, signal: Signal, market=MNQ):
        self.signal = signal
        self.market = market
        self.signals_fired = 0

    def evaluate(self, history: list[Bar]) -> Signal | None:
        if self.signals_fired == 0 and len(history) >= 1:
            self.signals_fired += 1
            return self.signal
        if self.signals_fired == 1 and len(history) >= 5:
            self.signals_fired += 1
            return self.signal
        return None


def serialize_trade(t: ExecutedTrade) -> dict[str, Any]:
    return {
        "trade_id": t.trade_id,
        "direction": t.direction,
        "entry_time": t.entry_time.isoformat(),
        "exit_time": t.exit_time.isoformat(),
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "stop_price": t.stop_price,
        "target_price": t.target_price,
        "quantity": t.quantity,
        "gross_pnl": t.gross_pnl,
        "commission": t.commission,
        "net_pnl": t.net_pnl,
        "r_result": t.r_result,
        "exit_reason": t.exit_reason,
        "stop_risk_dollars": t.stop_risk_dollars,
        "slippage_cost": t.slippage_cost,
        "budgeted_risk_dollars": t.budgeted_risk_dollars,
        "effective_risk_dollars": t.effective_risk_dollars,
        "budgeted_r": t.budgeted_r,
        "effective_r": t.effective_r,
    }


def serialize_open_position(open_pos: dict[str, Any] | None) -> dict[str, Any] | None:
    if open_pos is None:
        return None
    return {
        "direction": open_pos["direction"],
        "entry_price": open_pos["entry_price"],
        "entry_time": open_pos["entry_time"].isoformat(),
        "last_price": open_pos["last_price"],
        "last_time": open_pos["last_time"].isoformat(),
        "stop_price": open_pos["stop_price"],
        "target_price": open_pos["target_price"],
        "quantity": open_pos["quantity"],
        "unrealized_gross_pnl": open_pos["unrealized_gross_pnl"],
        "unrealized_net_pnl": open_pos["unrealized_net_pnl"],
        "state": open_pos["state"],
    }


def serialize_bar(b: Bar) -> dict[str, Any]:
    return {
        "timestamp": b.timestamp.isoformat(),
        "open": b.open,
        "high": b.high,
        "low": b.low,
        "close": b.close,
        "volume": b.volume,
    }


def serialize_config(cfg: BacktestConfig) -> dict[str, Any]:
    return {
        "initial_balance": cfg.initial_balance,
        "risk_per_trade": cfg.risk_per_trade,
        "fixed_quantity": cfg.fixed_quantity,
        "dollar_per_point": cfg.dollar_per_point,
        "tick_size": cfg.tick_size,
        "commission_per_side": cfg.commission_per_side,
        "slippage_points": cfg.slippage_points,
        "pending_limit_entry": cfg.pending_limit_entry,
        "pending_order_wait_bars": cfg.pending_order_wait_bars,
        "cooldown_bars": cfg.cooldown_bars,
        "partial_take_profit_fraction": cfg.partial_take_profit_fraction,
        "move_stop_to_break_even": cfg.move_stop_to_break_even,
        "discrete_partial_contracts": cfg.discrete_partial_contracts,
        "end_of_data_policy": cfg.end_of_data_policy,
        "end_of_data_slippage_points": cfg.end_of_data_slippage_points,
        "max_hold_minutes": cfg.max_hold_minutes,
        "max_bars_held": cfg.max_bars_held,
        "time_exit_mode": cfg.time_exit_mode,
        "time_exit_slippage_points": cfg.time_exit_slippage_points,
    }


# =============================================================================
# Definiciones de los casos para las 10 familias A-J
# =============================================================================


def build_case_a_long() -> dict[str, Any]:
    """Familia A (LONG): limite toca nivel y ejecuta a 100.0, nunca a next-open market (102.0)."""
    bars = [
        make_bar(0, 102, 103, 101, 102),
        make_bar(1, 102, 103, 99, 101),  # Low 99 cruza limite 100.0
        make_bar(2, 101, 112, 100, 111),  # High 112 toca TP 110.0
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.62,
        slippage_points=0.0,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
    )
    strat = RecordingStrategy(
        SingleSignalStrategy(Signal("long", 100.0, 95.0, 110.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_a_long",
        "family": "A",
        "description": "Limite Long toca nivel y ejecuta exacto a 100.0 (no next-open)",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


def build_case_a_short() -> dict[str, Any]:
    """Familia A (SHORT): limite toca nivel y ejecuta a 100.0, nunca a next-open market (98.0)."""
    bars = [
        make_bar(0, 98, 99, 97, 98),
        make_bar(1, 98, 101, 97, 99),  # High 101 cruza limite 100.0
        make_bar(2, 99, 100, 88, 89),  # Low 88 toca TP 90.0
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.62,
        slippage_points=0.0,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
    )
    strat = RecordingStrategy(
        SingleSignalStrategy(Signal("short", 100.0, 105.0, 90.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_a_short",
        "family": "A",
        "description": "Limite Short toca nivel y ejecuta exacto a 100.0 (no next-open)",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


def build_case_b_long_fill_later() -> dict[str, Any]:
    """Familia B (LONG): gap-down completo mas alla del limite no llena en barra 1; llena en barra 2."""
    bars = [
        make_bar(0, 102, 103, 101, 102),
        make_bar(1, 98, 99, 97, 98),  # Gap-through total bajo 100.0 -> NO llena
        make_bar(2, 98, 101, 97, 100),  # Rango [97, 101] cruza 100.0 -> llena
        make_bar(3, 100, 112, 99, 111),  # TP 110.0
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.62,
        slippage_points=0.0,
        pending_limit_entry=True,
        pending_order_wait_bars=3,
    )
    strat = RecordingStrategy(
        SingleSignalStrategy(Signal("long", 100.0, 95.0, 110.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_b_long_fill_later",
        "family": "B",
        "description": "Gap-through Long no llena hasta que barra posterior negocia el nivel",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


def build_case_b_short_fill_later() -> dict[str, Any]:
    """Familia B (SHORT): gap-up completo mas alla del limite no llena en barra 1; llena en barra 2."""
    bars = [
        make_bar(0, 98, 99, 97, 98),
        make_bar(1, 102, 103, 101, 102),  # Gap-up total sobre 100.0 -> NO llena
        make_bar(2, 102, 103, 99, 100),  # Rango [99, 103] cruza 100.0 -> llena
        make_bar(3, 100, 101, 88, 89),  # TP 90.0
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.62,
        slippage_points=0.0,
        pending_limit_entry=True,
        pending_order_wait_bars=3,
    )
    strat = RecordingStrategy(
        SingleSignalStrategy(Signal("short", 100.0, 105.0, 90.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_b_short_fill_later",
        "family": "B",
        "description": "Gap-through Short no llena hasta que barra posterior negocia el nivel",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


def build_case_b_gap_expires() -> dict[str, Any]:
    """Familia B: gap constante mas alla del limite expira normalmente sin ejecucion ficticia."""
    bars = [
        make_bar(0, 102, 103, 101, 102),
        make_bar(1, 98, 99, 97, 98),  # Gap (wait 2->1)
        make_bar(2, 97, 98, 96, 97),  # Gap (wait 1->0: expira)
        make_bar(3, 98, 101, 97, 100),  # Negocia 100 luego, pero ya expiro
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.0,
        slippage_points=0.0,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
    )
    strat = RecordingStrategy(
        SingleSignalStrategy(Signal("long", 100.0, 95.0, 110.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_b_gap_expires",
        "family": "B",
        "description": "Gap-through no negocia nivel y expira sin orden ficticia",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


def build_case_c_expiration_exact() -> dict[str, Any]:
    """Familia C: expiracion exacta con notificacion observable en hook note_order_expired."""
    bars = [
        make_bar(0, 105, 106, 104, 105),  # Senal long 100.0
        make_bar(1, 104, 105, 102, 103),  # wait 2 -> 1
        make_bar(2, 103, 104, 101, 102),  # wait 1 -> 0: expira exactamente en barra 2
        make_bar(3, 102, 103, 99, 101),  # Toca 100 en barra 3 pero orden ya expiro
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.0,
        slippage_points=0.0,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
    )
    strat = RecordingStrategy(
        SingleSignalStrategy(Signal("long", 100.0, 95.0, 110.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_c_expiration_exact",
        "family": "C",
        "description": "Expiracion exacta a wait_bars=2 con notificacion de hook observable",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


def build_case_d_long_fillbar_tp() -> dict[str, Any]:
    """Familia D (LONG): solo-TP tocado en vela de fill queda bloqueado; posicion permanece abierta."""
    bars = [
        make_bar(0, 102, 103, 101, 102),
        make_bar(1, 102, 112, 99, 101),  # Fill en 100, High 112 toca TP 110; TP bloqueado por regla A1
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.0,
        slippage_points=0.0,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
        partial_take_profit_fraction=0.5,
        move_stop_to_break_even=True,
    )
    strat = RecordingStrategy(
        SingleSignalStrategy(Signal("long", 100.0, 90.0, 110.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_d_long_fillbar_tp",
        "family": "D",
        "description": "Fill-bar solo TP bloqueado en Long; tp1 y BE bloqueados; posicion queda abierta",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


def build_case_d_short_fillbar_tp() -> dict[str, Any]:
    """Familia D (SHORT): solo-TP tocado en vela de fill queda bloqueado; posicion permanece abierta."""
    bars = [
        make_bar(0, 98, 99, 97, 98),
        make_bar(1, 98, 101, 88, 99),  # Fill en 100, Low 88 toca TP 90; TP bloqueado por regla A1
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.0,
        slippage_points=0.0,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
        partial_take_profit_fraction=0.5,
        move_stop_to_break_even=True,
    )
    strat = RecordingStrategy(
        SingleSignalStrategy(Signal("short", 100.0, 110.0, 90.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_d_short_fillbar_tp",
        "family": "D",
        "description": "Fill-bar solo TP bloqueado en Short; posicion queda abierta",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


def build_case_e_long_sl_tp() -> dict[str, Any]:
    """Familia E (LONG): vela de fill toca SL y TP -> SL gana incondicionalmente."""
    bars = [
        make_bar(0, 102, 103, 101, 102),
        make_bar(1, 102, 108, 93, 100),  # Fill en 100, toca TP (105) y SL (95)
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.0,
        slippage_points=0.0,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
    )
    strat = RecordingStrategy(
        SingleSignalStrategy(Signal("long", 100.0, 95.0, 105.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_e_long_sl_tp",
        "family": "E",
        "description": "Fill-bar Long toca SL y TP simultaneos; resuelve por stop_loss",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


def build_case_e_short_sl_tp() -> dict[str, Any]:
    """Familia E (SHORT): vela de fill toca SL y TP -> SL gana incondicionalmente."""
    bars = [
        make_bar(0, 98, 99, 97, 98),
        make_bar(1, 98, 107, 92, 100),  # Fill en 100, toca TP (95) y SL (105)
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.0,
        slippage_points=0.0,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
    )
    strat = RecordingStrategy(
        SingleSignalStrategy(Signal("short", 100.0, 105.0, 95.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_e_short_sl_tp",
        "family": "E",
        "description": "Fill-bar Short toca SL y TP simultaneos; resuelve por stop_loss",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


def build_case_e_stop_gap_convention() -> dict[str, Any]:
    """Familia E: barra abre con gap mas alla del stop; fill ocurre al open y slippage_cost es 0.0."""
    bars = [
        make_bar(0, 102, 103, 101, 102),
        make_bar(1, 102, 103, 99, 101),  # Fill en 100.0
        make_bar(2, 92, 93, 91, 92),  # Abre con gap bajo stop 95.0 -> fill al open (92.0)
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.0,
        slippage_points=0.25,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
    )
    strat = RecordingStrategy(
        SingleSignalStrategy(Signal("long", 100.0, 95.0, 110.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_e_stop_gap_convention",
        "family": "E",
        "description": "Stop gap convention: fill al open adverso y slippage_cost nulo (absorbido)",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


def build_case_f_long_partial_be() -> dict[str, Any]:
    """Familia F (LONG): parcial posterior + BE + salida target final con costes no nulos."""
    bars = [
        make_bar(0, 102, 103, 101, 102),
        make_bar(1, 102, 105, 99, 101),  # Fill en 100.0, High 105 no toca tp1 (110.0)
        make_bar(2, 101, 115, 101, 112),  # Toca tp1 (110.0), toma parcial y stop a BE (100.0)
        make_bar(3, 112, 132, 108, 131),  # Toca target final (130.0)
    ]
    cfg = BacktestConfig(
        fixed_quantity=2,
        commission_per_side=0.62,
        slippage_points=0.25,
        partial_take_profit_fraction=0.5,
        move_stop_to_break_even=True,
        discrete_partial_contracts=True,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
    )
    strat = RecordingStrategy(
        SingleSignalStrategy(Signal("long", 100.0, 90.0, 130.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_f_long_partial_be",
        "family": "F",
        "description": "Parcial posterior + BE + salida final con comisiones en Long",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


def build_case_f_short_partial_be() -> dict[str, Any]:
    """Familia F (SHORT): parcial posterior + BE + salida target final con costes no nulos."""
    bars = [
        make_bar(0, 98, 99, 97, 98),
        make_bar(1, 98, 101, 95, 99),  # Fill en 100.0, Low 95 no toca tp1 (90.0)
        make_bar(2, 99, 99, 85, 88),  # Toca tp1 (90.0), toma parcial y stop a BE (100.0)
        make_bar(3, 88, 92, 68, 69),  # Toca target final (70.0)
    ]
    cfg = BacktestConfig(
        fixed_quantity=2,
        commission_per_side=0.62,
        slippage_points=0.25,
        partial_take_profit_fraction=0.5,
        move_stop_to_break_even=True,
        discrete_partial_contracts=True,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
    )
    strat = RecordingStrategy(
        SingleSignalStrategy(Signal("short", 100.0, 110.0, 70.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_f_short_partial_be",
        "family": "F",
        "description": "Parcial posterior + BE + salida final con comisiones en Short",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


def build_case_g_cooldown_causal_hooks() -> dict[str, Any]:
    """Familia G: cooldown_bars=2 y hooks causales sin reevaluacion artificial."""
    bars = [
        make_bar(0, 102, 103, 101, 102),  # Senal inicial
        make_bar(1, 102, 103, 99, 101),  # Fill en 100.0
        make_bar(2, 101, 112, 100, 111),  # TP en 110.0 -> cierra trade, inicia cooldown=2
        make_bar(3, 111, 112, 110, 111),  # Cooldown 2->1: observe() ejecutado, evaluate() bloqueado
        make_bar(4, 111, 112, 110, 111),  # Cooldown 1->0: decrece a 0
        make_bar(5, 111, 113, 110, 112),  # Cooldown=0: evaluate() llamado con historial causal completo
        make_bar(6, 112, 113, 99, 105),  # Fill segunda senal si fuera emitida
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.0,
        slippage_points=0.0,
        cooldown_bars=2,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
    )
    strat = RecordingStrategy(
        CooldownProbingStrategy(Signal("long", 100.0, 95.0, 110.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_g_cooldown_causal_hooks",
        "family": "G",
        "description": "Cooldown de 2 barras con observacion causal de historial sin fuga temporal",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


def build_case_h_unresolved() -> dict[str, Any]:
    """Familia H (unresolved): posicion abierta al fin de datos queda como unresolved_positions=1."""
    bars = [
        make_bar(0, 102, 103, 101, 102),
        make_bar(1, 102, 103, 99, 101),  # Fill en 100.0
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.62,
        slippage_points=0.25,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
        end_of_data_policy="unresolved",
    )
    strat = RecordingStrategy(
        SingleSignalStrategy(Signal("long", 100.0, 95.0, 110.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_h_unresolved",
        "family": "H",
        "description": "Politica unresolved: posicion no cerrada, marcada a mercado en open_position",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


def build_case_h_close() -> dict[str, Any]:
    """Familia H (close): posicion abierta al fin de datos se liquida con end_of_data."""
    bars = [
        make_bar(0, 102, 103, 101, 102),
        make_bar(1, 102, 103, 99, 101),  # Fill en 100.0, cierra a close (101) con slippage
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.62,
        slippage_points=0.0,
        end_of_data_slippage_points=0.25,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
        end_of_data_policy="close",
    )
    strat = RecordingStrategy(
        SingleSignalStrategy(Signal("long", 100.0, 95.0, 110.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_h_close",
        "family": "H",
        "description": "Politica close: liquidacion forzada a fin de datos por end_of_data",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


def build_case_i_qty1_fractional() -> dict[str, Any]:
    """Familia I: qty1 en modo fraccional toma 0.5 contratos en tp1 y 0.5 en TP final."""
    bars = [
        make_bar(0, 104, 105, 103, 104),
        make_bar(1, 102, 112, 99, 101),  # Fill en 100.0
        make_bar(2, 101, 111, 100, 110),  # TP final en 110.0 (tp1 en 105.0)
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.62,
        slippage_points=0.25,
        partial_take_profit_fraction=0.5,
        move_stop_to_break_even=True,
        discrete_partial_contracts=False,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
        end_of_data_policy="unresolved",
    )
    strat = RecordingStrategy(
        SingleSignalStrategy(Signal("long", 100.0, 95.0, 110.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_i_qty1_fractional",
        "family": "I",
        "description": "Qty=1 fraccional: liquida medio contrato en tp1 y medio en target",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


def build_case_i_qty1_discrete() -> dict[str, Any]:
    """Familia I: qty1 en modo discreto no puede fraccionar (floor(1*0.5)=0); liquida 1 en TP final."""
    bars = [
        make_bar(0, 104, 105, 103, 104),
        make_bar(1, 102, 112, 99, 101),  # Fill en 100.0
        make_bar(2, 101, 111, 100, 110),  # TP final en 110.0
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.62,
        slippage_points=0.25,
        partial_take_profit_fraction=0.5,
        move_stop_to_break_even=True,
        discrete_partial_contracts=True,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
        end_of_data_policy="unresolved",
    )
    strat = RecordingStrategy(
        SingleSignalStrategy(Signal("long", 100.0, 95.0, 110.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_i_qty1_discrete",
        "family": "I",
        "description": "Qty=1 discreto: contratos enteros indivisibles (q_tp1=0, q_rem=1)",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


def build_case_j_time_exit_minutes() -> dict[str, Any]:
    """Familia J: time-exit por max_hold_minutes al open de la barra correspondiente."""
    bars = [
        make_bar(0, 102, 103, 101, 102),  # Senal inicial a T+0m
        make_bar(5, 102, 103, 99, 101),  # Fill en 100.0 (T+5m)
        make_bar(10, 101, 102, 100, 101),  # T+10m (5m de permanencia)
        make_bar(15, 101, 102, 100, 101),  # T+15m (10m cumplidos >= max_hold_minutes) -> time_exit al open
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.0,
        slippage_points=0.0,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
        max_hold_minutes=10,
        time_exit_mode="market",
        time_exit_slippage_points=0.25,
    )
    strat = RecordingStrategy(
        SingleSignalStrategy(Signal("long", 100.0, 95.0, 110.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_j_time_exit_minutes",
        "family": "J",
        "description": "Time-exit por minutos cumplidos; resuelve al open de la barra",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


def build_case_j_time_exit_bars() -> dict[str, Any]:
    """Familia J: time-exit por max_bars_held al close de la barra cumplida."""
    bars = [
        make_bar(0, 102, 103, 101, 102),
        make_bar(1, 102, 103, 99, 101),  # Fill barra 1 (bars_held=0)
        make_bar(2, 101, 102, 100, 101),  # Barra 2 (bars_held=1)
        make_bar(3, 101, 102, 100, 101),  # Barra 3 (bars_held=2 >= max_bars_held) -> time_exit al close
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.0,
        slippage_points=0.0,
        pending_limit_entry=True,
        pending_order_wait_bars=2,
        max_bars_held=2,
        max_hold_minutes=None,
        time_exit_mode="market",
        time_exit_slippage_points=0.25,
    )
    strat = RecordingStrategy(
        SingleSignalStrategy(Signal("long", 100.0, 95.0, 110.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_j_time_exit_bars",
        "family": "J",
        "description": "Time-exit por conteo de barras held; resuelve al close",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


def build_case_j_legacy_dispatch() -> dict[str, Any]:
    """Familia J: control legacy sin ejecucion opt-in ejecuta via _run_backtest_legacy."""
    bars = [
        make_bar(0, 100, 101, 99, 100),  # Senal a mercado
        make_bar(1, 100, 112, 99, 102),  # Entra al open (100.0) y toca TP (105.0) en misma barra
    ]
    cfg = BacktestConfig(
        fixed_quantity=1,
        commission_per_side=0.0,
        slippage_points=0.0,
        pending_limit_entry=False,
        pending_order_wait_bars=0,
        cooldown_bars=0,
        partial_take_profit_fraction=0.0,
        move_stop_to_break_even=False,
        discrete_partial_contracts=False,
    )
    strat = RecordingStrategy(
        SingleSignalStrategy(Signal("long", 100.0, 95.0, 105.0))
    )
    res = run_backtest(bars, strat, cfg)
    return {
        "case_id": "case_j_legacy_dispatch",
        "family": "J",
        "description": "Control legacy market sin opt-in resuelve via dispatch clasico",
        "config": serialize_config(cfg),
        "bars": [serialize_bar(b) for b in bars],
        "events": strat.events,
        "n_trades": res.n_trades,
        "net_pnl": res.net_pnl,
        "unresolved_positions": res.unresolved_positions,
        "trades": [serialize_trade(t) for t in res.trades],
        "open_position": serialize_open_position(res.open_position),
    }


ALL_CASE_BUILDERS = [
    build_case_a_long,
    build_case_a_short,
    build_case_b_long_fill_later,
    build_case_b_short_fill_later,
    build_case_b_gap_expires,
    build_case_c_expiration_exact,
    build_case_d_long_fillbar_tp,
    build_case_d_short_fillbar_tp,
    build_case_e_long_sl_tp,
    build_case_e_short_sl_tp,
    build_case_e_stop_gap_convention,
    build_case_f_long_partial_be,
    build_case_f_short_partial_be,
    build_case_g_cooldown_causal_hooks,
    build_case_h_unresolved,
    build_case_h_close,
    build_case_i_qty1_fractional,
    build_case_i_qty1_discrete,
    build_case_j_time_exit_minutes,
    build_case_j_time_exit_bars,
    build_case_j_legacy_dispatch,
]


def run_all_cases() -> dict[str, Any]:
    """Ejecuta todos los constructores de casos deterministas."""
    return {fn.__name__.replace("build_", ""): fn() for fn in ALL_CASE_BUILDERS}


def compare_case_dictionaries(
    actual: dict[str, Any], expected: dict[str, Any], path: str = ""
) -> list[str]:
    """Comparador profundo y sensible que detecta discrepancias en cualquier campo."""
    diffs = []
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        if actual != expected:
            diffs.append(f"{path}: actual {actual!r} != expected {expected!r}")
        return diffs

    all_keys = sorted(set(actual.keys()) | set(expected.keys()))
    for k in all_keys:
        curr_path = f"{path}.{k}" if path else k
        if k not in actual:
            diffs.append(f"{curr_path}: missing in actual")
            continue
        if k not in expected:
            diffs.append(f"{curr_path}: unexpected in actual")
            continue
        v_act = actual[k]
        v_exp = expected[k]
        if isinstance(v_act, dict) and isinstance(v_exp, dict):
            diffs.extend(compare_case_dictionaries(v_act, v_exp, curr_path))
        elif isinstance(v_act, list) and isinstance(v_exp, list):
            if len(v_act) != len(v_exp):
                diffs.append(
                    f"{curr_path}: len mismatch actual={len(v_act)} != expected={len(v_exp)}"
                )
            else:
                for idx, (item_a, item_e) in enumerate(zip(v_act, v_exp)):
                    item_path = f"{curr_path}[{idx}]"
                    if isinstance(item_a, dict) and isinstance(item_e, dict):
                        diffs.extend(
                            compare_case_dictionaries(item_a, item_e, item_path)
                        )
                    elif isinstance(item_a, (float, int)) and isinstance(
                        item_e, (float, int)
                    ):
                        if not math.isclose(
                            float(item_a),
                            float(item_e),
                            rel_tol=1e-12,
                            abs_tol=1e-12,
                        ):
                            diffs.append(
                                f"{item_path}: numeric mismatch {item_a} != {item_e}"
                            )
                    else:
                        if item_a != item_e:
                            diffs.append(f"{item_path}: {item_a!r} != {item_e!r}")
        elif isinstance(v_act, (float, int)) and isinstance(
            v_exp, (float, int)
        ):
            if not math.isclose(
                float(v_act), float(v_exp), rel_tol=1e-12, abs_tol=1e-12
            ):
                diffs.append(
                    f"{curr_path}: numeric mismatch {v_act} != {v_exp}"
                )
        else:
            if v_act != v_exp:
                diffs.append(f"{curr_path}: {v_act!r} != {v_exp!r}")
    return diffs


def write_baseline_fixture() -> None:
    """Modo explicito (--write-baseline) que genera el archivo baseline congelado."""
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    git_head = get_git_head(REPO_ROOT)
    provenance = {
        "git_head": git_head,
        "files_sha256": {
            "src/backtest/executor.py": compute_file_sha256(
                REPO_ROOT / "src" / "backtest" / "executor.py"
            ),
            "src/backtest/strategy.py": compute_file_sha256(
                REPO_ROOT / "src" / "backtest" / "strategy.py"
            ),
            "src/backtest/smc_fvg.py": compute_file_sha256(
                REPO_ROOT / "src" / "backtest" / "smc_fvg.py"
            ),
        },
    }
    cases = run_all_cases()
    baseline_payload = {
        "version": "v1",
        "description": "SIM-01A deterministic shadow execution baseline",
        "provenance": provenance,
        "cases": cases,
    }
    with open(BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(baseline_payload, f, indent=2, sort_keys=True)
    print(f"Baseline fixture successfully frozen at: {BASELINE_PATH}")


# =============================================================================
# Suite de Pruebas Unittest
# =============================================================================


class TestShadowExecutionBaseline(unittest.TestCase):
    """Pruebas unitarias de caracterizacion independiente y comparacion de baseline."""

    def test_00_baseline_fixture_congelado_match(self):
        """Verifica igualdad bit-a-bit con el baseline congelado. Falla en RED si no existe."""
        if not BASELINE_PATH.exists():
            self.fail(
                f"RED: Archivo baseline inexistente en {BASELINE_PATH}. "
                f"Ejecute con --write-baseline para congelar la referencia."
            )
        with open(BASELINE_PATH, "r", encoding="utf-8") as f:
            baseline_data = json.load(f)

        current_cases = run_all_cases()
        expected_cases = baseline_data.get("cases", {})
        diffs = compare_case_dictionaries(current_cases, expected_cases)
        if diffs:
            self.fail(
                f"Discrepancia detectada contra baseline congelado ({len(diffs)} diferencias):\n"
                + "\n".join(diffs[:10])
            )

    def test_01_baseline_comparator_sensitivity(self):
        """Prueba de sensibilidad: mutar un campo economico en memoria debe ser detectado."""
        current_cases = run_all_cases()
        mutated = copy.deepcopy(current_cases)
        # Mutar un trade economico real
        trade = mutated["case_a_long"]["trades"][0]
        trade["net_pnl"] += 5.0
        diffs = compare_case_dictionaries(current_cases, mutated)
        self.assertTrue(
            len(diffs) > 0, "El comparador no detecto la mutacion de net_pnl!"
        )
        self.assertTrue(
            any("net_pnl" in d for d in diffs),
            f"Se esperaba net_pnl en las diferencias reportadas: {diffs}",
        )

    def test_family_a_pending_limit_execution_long_and_short(self):
        """Familia A: el limite ejecuta exacto a su nivel y jamas como next-open market."""
        # Long
        case_a_l = build_case_a_long()
        self.assertEqual(case_a_l["n_trades"], 1)
        trade_l = case_a_l["trades"][0]
        self.assertEqual(trade_l["entry_price"], 100.0)
        self.assertEqual(trade_l["exit_price"], 110.0)
        self.assertEqual(trade_l["exit_reason"], "take_profit")
        self.assertAlmostEqual(trade_l["net_pnl"], (110 - 100) * 2 - 2 * 0.62)

        # Short
        case_a_s = build_case_a_short()
        self.assertEqual(case_a_s["n_trades"], 1)
        trade_s = case_a_s["trades"][0]
        self.assertEqual(trade_s["entry_price"], 100.0)
        self.assertEqual(trade_s["exit_price"], 90.0)
        self.assertEqual(trade_s["exit_reason"], "take_profit")
        self.assertAlmostEqual(trade_s["net_pnl"], (100 - 90) * 2 - 2 * 0.62)

    def test_family_b_gap_through_and_fill_or_expire(self):
        """Familia B: gap enteramente mas alla del limite no llena; llena luego o expira."""
        # Long llena en barra 2 tras gap en barra 1
        case_b_l = build_case_b_long_fill_later()
        self.assertEqual(case_b_l["n_trades"], 1)
        trade_l = case_b_l["trades"][0]
        self.assertEqual(trade_l["entry_price"], 100.0)
        self.assertEqual(trade_l["entry_time"], case_b_l["bars"][2]["timestamp"])

        # Short llena en barra 2 tras gap en barra 1
        case_b_s = build_case_b_short_fill_later()
        self.assertEqual(case_b_s["n_trades"], 1)
        trade_s = case_b_s["trades"][0]
        self.assertEqual(trade_s["entry_price"], 100.0)
        self.assertEqual(trade_s["entry_time"], case_b_s["bars"][2]["timestamp"])

        # Gap permanente expira sin llenar
        case_b_exp = build_case_b_gap_expires()
        self.assertEqual(case_b_exp["n_trades"], 0)
        self.assertEqual(case_b_exp["unresolved_positions"], 0)
        expired_events = [
            e for e in case_b_exp["events"] if e["type"] == "note_order_expired"
        ]
        self.assertEqual(len(expired_events), 1)

    def test_family_c_exact_expiration_and_observable_hook(self):
        """Familia C: expiracion exacta al cumplir wait_bars y emision de hook."""
        case_c = build_case_c_expiration_exact()
        self.assertEqual(case_c["n_trades"], 0)
        expired = [
            e for e in case_c["events"] if e["type"] == "note_order_expired"
        ]
        self.assertEqual(len(expired), 1)
        # Debe haber expirado en la barra 2 (indice 2)
        self.assertEqual(
            expired[0]["timestamp"], case_c["bars"][2]["timestamp"]
        )

    def test_family_d_fillbar_tp_blocked_open_position(self):
        """Familia D: fill-bar solo TP bloqueado; tp1 no genera parcial/BE; posicion queda abierta."""
        # Long
        case_d_l = build_case_d_long_fillbar_tp()
        self.assertEqual(case_d_l["n_trades"], 0)
        self.assertEqual(case_d_l["unresolved_positions"], 1)
        self.assertIsNotNone(case_d_l["open_position"])
        self.assertEqual(case_d_l["open_position"]["entry_price"], 100.0)
        self.assertEqual(case_d_l["open_position"]["stop_price"], 90.0)

        # Short
        case_d_s = build_case_d_short_fillbar_tp()
        self.assertEqual(case_d_s["n_trades"], 0)
        self.assertEqual(case_d_s["unresolved_positions"], 1)
        self.assertIsNotNone(case_d_s["open_position"])
        self.assertEqual(case_d_s["open_position"]["entry_price"], 100.0)
        self.assertEqual(case_d_s["open_position"]["stop_price"], 110.0)

    def test_family_e_fillbar_sl_priority_and_stop_gap(self):
        """Familia E: vela de fill con SL+TP resuelve stop; gap de stop usa convencion documentada."""
        # Long SL+TP
        case_e_l = build_case_e_long_sl_tp()
        self.assertEqual(case_e_l["n_trades"], 1)
        self.assertEqual(case_e_l["trades"][0]["exit_reason"], "stop_loss")
        self.assertEqual(case_e_l["trades"][0]["exit_price"], 95.0)

        # Short SL+TP
        case_e_s = build_case_e_short_sl_tp()
        self.assertEqual(case_e_s["n_trades"], 1)
        self.assertEqual(case_e_s["trades"][0]["exit_reason"], "stop_loss")
        self.assertEqual(case_e_s["trades"][0]["exit_price"], 105.0)

        # Stop gap convention
        case_e_gap = build_case_e_stop_gap_convention()
        self.assertEqual(case_e_gap["n_trades"], 1)
        t_gap = case_e_gap["trades"][0]
        self.assertEqual(t_gap["exit_reason"], "stop_loss")
        self.assertEqual(t_gap["exit_price"], 92.0)  # Open adverso
        self.assertEqual(t_gap["slippage_cost"], 0.0)  # Convencion documentada

    def test_family_f_posterior_partial_be_and_costs(self):
        """Familia F: parcial posterior + BE + salida final con comisiones no nulas."""
        case_f_l = build_case_f_long_partial_be()
        self.assertEqual(case_f_l["n_trades"], 1)
        t_l = case_f_l["trades"][0]
        self.assertEqual(t_l["exit_reason"], "take_profit")
        self.assertEqual(t_l["quantity"], 2)
        # tp1 tomo 1 contrato (+10 pts = $20), target tomo 1 contrato (+30 pts = $60)
        # gross = $80. comision = 2 * 2 * 0.62 = $2.48. net = $77.52
        self.assertAlmostEqual(t_l["gross_pnl"], 80.0)
        self.assertAlmostEqual(t_l["commission"], 2.48)
        self.assertAlmostEqual(t_l["net_pnl"], 77.52)
        self.assertEqual(t_l["exit_price"], 120.0)  # Precio equivalente

        case_f_s = build_case_f_short_partial_be()
        self.assertEqual(case_f_s["n_trades"], 1)
        t_s = case_f_s["trades"][0]
        self.assertEqual(t_s["exit_reason"], "take_profit")
        self.assertAlmostEqual(t_s["net_pnl"], 77.52)
        self.assertEqual(t_s["exit_price"], 80.0)

    def test_family_g_cooldown_and_causal_observation_hooks(self):
        """Familia G: cooldown_bars respeta ciclo causal y no evalua antes de tiempo."""
        case_g = build_case_g_cooldown_causal_hooks()
        self.assertEqual(case_g["n_trades"], 1)
        events = case_g["events"]
        # Filtrar llamadas a observe y evaluate_signal
        observe_events = [e for e in events if e["type"] == "observe"]
        sig_events = [e for e in events if e["type"] == "evaluate_signal"]
        self.assertTrue(len(observe_events) >= 1)
        # Evaluacion causal: 1era senal con history_len=1, 2da senal con history_len=5 tras agotar cooldown
        self.assertEqual(len(sig_events), 2)
        self.assertEqual(sig_events[0]["history_len"], 1)
        self.assertEqual(sig_events[1]["history_len"], 5)

    def test_family_h_end_of_data_unresolved_vs_close_policy(self):
        """Familia H: fin de datos con politica unresolved vs close."""
        case_h_un = build_case_h_unresolved()
        self.assertEqual(case_h_un["n_trades"], 0)
        self.assertEqual(case_h_un["unresolved_positions"], 1)
        self.assertIsNotNone(case_h_un["open_position"])

        case_h_cl = build_case_h_close()
        self.assertEqual(case_h_cl["n_trades"], 1)
        self.assertEqual(case_h_cl["unresolved_positions"], 0)
        self.assertEqual(case_h_cl["trades"][0]["exit_reason"], "end_of_data")

    def test_family_i_qty1_fractional_vs_discrete_divergence(self):
        """Familia I: qty1 en fraccional vs discreto produce diferencia declarada."""
        case_frac = build_case_i_qty1_fractional()
        case_disc = build_case_i_qty1_discrete()
        pnl_frac = case_frac["trades"][0]["net_pnl"]
        pnl_disc = case_disc["trades"][0]["net_pnl"]
        # Declarar explicitamente que NO son iguales
        self.assertNotEqual(pnl_frac, pnl_disc)
        self.assertAlmostEqual(pnl_frac, 13.76)
        self.assertAlmostEqual(pnl_disc, 18.76)

    def test_family_j_time_exit_modes_and_legacy_dispatch_control(self):
        """Familia J: time-exit por minutos, por barras, y despacho a legacy."""
        case_j_min = build_case_j_time_exit_minutes()
        self.assertEqual(case_j_min["n_trades"], 1)
        self.assertEqual(case_j_min["trades"][0]["exit_reason"], "time_exit")

        case_j_bar = build_case_j_time_exit_bars()
        self.assertEqual(case_j_bar["n_trades"], 1)
        self.assertEqual(case_j_bar["trades"][0]["exit_reason"], "time_exit")

        case_j_leg = build_case_j_legacy_dispatch()
        self.assertEqual(case_j_leg["n_trades"], 1)
        self.assertEqual(case_j_leg["trades"][0]["exit_reason"], "take_profit")


if __name__ == "__main__":
    if "--write-baseline" in sys.argv:
        write_baseline_fixture()
        sys.exit(0)
    unittest.main()

"""Kai SMC-OB detector and causal FARS execution adapter.

Detector bodies preserve strat_smc_ob_signal.py; only its MAX_WAIT import is
resolved locally. Integration follows smc_fvg.py, with Kai OB defaults, no
cooldown and no added minimum-risk/session filter. Parsed prices are cached
incrementally with the exact ATR recurrence; the detector remains unchanged.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

import numpy as np

from src.backtest.executor import BacktestConfig
from src.backtest.history import Bar
from src.backtest.markets import MNQ, MarketSpec
from src.backtest.strategy import Signal

DEFAULT_FRACTION = 0.5
DEFAULT_SWING_W = 10
DEFAULT_TARGET_RR = 3.0
MAX_WAIT = DEFAULT_WAIT = 36
DEFAULT_MIN_RISK_PTS = 0.0
DEFAULT_COOLDOWN = 0

def _atr(h, l, c, n=200):
    """Copia EXACTA de strategies.strat_smc._atr (la misma que usa live_smc_bridge vía
    `from strategies.strat_smc import _atr, MAX_WAIT`)."""
    tr = np.empty(len(c))
    tr[0] = h[0] - l[0]
    tr[1:] = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    a = np.empty(len(c)); acc = 0.0
    for i in range(len(c)):
        acc += (tr[i] - acc) / n if i else tr[i]
        a[i] = acc
    return a

class _SmcObSignal:
    """Señal OB (order-block) candle-driven. Interfaz idéntica a SmcFvgSignal:
    update_pivots / ready / structure_and_arm. Sin fill ni gestión de posición
    (eso lo hace el driver broker-confirmado). Paridad decisiva con SmcObBridgeLive."""

    def __init__(self, swing_w, target_rr, choch_only, ob_lookback):
        self.w = int(swing_w); self.rr = float(target_rr)
        self.choch_only = bool(choch_only); self.oblook = int(ob_lookback)
        self.sh_p = self.sh_i = self.sl_p = self.sl_i = None
        self.sh_cross = self.sl_cross = True
        self.trend = 0
        self._plen = -1; self._p_hi = self._p_lo = None

    def _ensure_parsed(self, h5, l5, c5):
        """Recalcula p_hi/p_lo cuando crece el array. ATR es causal (solo pasado) → p_hi/p_lo
        en un índice cerrado NO cambian al añadir velas: incremental == pasada completa."""
        if len(c5) != self._plen:
            atr = _atr(h5, l5, c5)
            hv = (h5 - l5) >= 2 * atr
            self._p_hi = np.where(hv, l5, h5)
            self._p_lo = np.where(hv, h5, l5)
            self._plen = len(c5)

    def update_pivots(self, t, h5, l5, c5):
        """Confirma pivote swing en el índice i=t-w (== live_smc_bridge.on_bar:76-84).
        Corre en CADA barra, incluso con posición abierta — igual que el original."""
        self._ensure_parsed(h5, l5, c5)
        w = self.w; i = t - w
        if i - w >= 0:
            seg_h = h5[i - w:i + w + 1]; seg_l = l5[i - w:i + w + 1]
            if h5[i] == seg_h.max():
                self.sh_p, self.sh_i, self.sh_cross = h5[i], i, False
            if l5[i] == seg_l.min():
                self.sl_p, self.sl_i, self.sl_cross = l5[i], i, False

    def ready(self):
        return self.sh_p is not None and self.sl_p is not None

    def structure_and_arm(self, t, h5, l5, c5, pend_active):
        """Ruptura de estructura (BOS/CHoCH) + order block. Evalúa SIEMPRE alcista Y
        bajista en la misma llamada (SIN early-return entre ambas), exactamente como
        live_smc_bridge.on_bar:111-144: son DOS `if` independientes, no if/elif, así
        que ambas mutaciones de sh_cross/sl_cross/trend corren siempre que su
        condición se cumpla, sin importar si la otra rama también dispara. Si ambas
        disparan en la misma barra (requiere sh_p < c[t] < sl_p), la bajista corre
        DESPUÉS y su spec SOBRESCRIBE al de la alcista — igual que el bridge, donde
        `_arm()` alcista fija `self.pend` y el `_arm()` bajista subsiguiente lo
        vuelve a fijar (sobrescribe). Devuelve el spec del ÚLTIMO lado que disparó
        (bajista si ambos dispararon; si no, el único que disparó), o None si
        ninguno disparó. `pend_active` se ignora: el driver ya gatea por estado (no
        re-arma si ya hay pend/pos); esta capa solo produce el spec cuando la
        estructura rompe."""
        spec = None
        # ── RUPTURA ALCISTA ── (portado EXACTO de live_smc_bridge.py:112-127)
        if not self.sh_cross and c5[t] > self.sh_p and self.sl_i is not None:
            self.sh_cross = True; tag_choch = self.trend == -1; self.trend = 1
            if not (self.choch_only and not tag_choch):
                a = max(self.sh_i, t - self.oblook); seg = self._p_lo[a:t + 1]
                if len(seg):
                    j = a + int(np.argmin(seg)); ob_hi, ob_lo = self._p_hi[j], self._p_lo[j]
                    if ob_hi > ob_lo:
                        entry = ob_hi; buf = max((ob_hi - ob_lo) * 0.05, entry * 1e-4)
                        sl = ob_lo - buf; risk = entry - sl
                        if risk > 0:
                            spec = {"side": 1, "entry": entry, "sl": sl,
                                    "tp": entry + self.rr * risk, "tp1": entry + risk,
                                    "risk": risk, "w": MAX_WAIT}
        # ── RUPTURA BAJISTA ── (portado EXACTO de live_smc_bridge.py:129-144)
        if not self.sl_cross and c5[t] < self.sl_p and self.sh_i is not None:
            self.sl_cross = True; tag_choch = self.trend == 1; self.trend = -1
            if not (self.choch_only and not tag_choch):
                a = max(self.sl_i, t - self.oblook); seg = self._p_hi[a:t + 1]
                if len(seg):
                    j = a + int(np.argmax(seg)); ob_hi, ob_lo = self._p_hi[j], self._p_lo[j]
                    if ob_hi > ob_lo:
                        entry = ob_lo; buf = max((ob_hi - ob_lo) * 0.05, entry * 1e-4)
                        sl = ob_hi + buf; risk = sl - entry
                        if risk > 0:
                            spec = {"side": -1, "entry": entry, "sl": sl,
                                    "tp": entry - self.rr * risk, "tp1": entry - risk,
                                    "risk": risk, "w": MAX_WAIT}
        return spec


SmcObReason = Literal["accepted", "risk_below_min", "expired", "closed"]


@dataclass(frozen=True)
class SmcObDecision:
    """Observational snapshot of an OB order candidate and its outcome."""

    timestamp: datetime
    direction: Literal["long", "short"]
    entry: float
    stop: float
    target: float
    risk: float
    decision: SmcObReason
    r_result: float | None = None
    exit_time: datetime | None = None


class SmcObStrategy:
    """Stateful, causal SMC-OB signal layer consumed by the FARS executor."""

    def __init__(
        self,
        *,
        market: MarketSpec = MNQ,
        f: float = DEFAULT_FRACTION,
        swing_w: int = DEFAULT_SWING_W,
        choch_only: bool = True,
        ob_lookback: int = 60,
        target_rr: float = DEFAULT_TARGET_RR,
        wait: int = DEFAULT_WAIT,
        min_risk_pts: float = DEFAULT_MIN_RISK_PTS,
        cooldown: int = DEFAULT_COOLDOWN,
        log_decisions: bool = True,
    ) -> None:
        if not isinstance(market, MarketSpec):
            raise TypeError("market must be a MarketSpec")
        if not 0.0 < f < 1.0:
            raise ValueError("f must be in (0, 1)")
        if swing_w < 1:
            raise ValueError("swing_w must be >= 1")
        if target_rr <= 0 or not math.isfinite(target_rr):
            raise ValueError("target_rr must be finite and > 0")
        if wait < 1:
            raise ValueError("wait must be >= 1")
        if min_risk_pts < 0 or not math.isfinite(min_risk_pts):
            raise ValueError("min_risk_pts must be finite and >= 0")
        if cooldown < 0:
            raise ValueError("cooldown must be >= 0")
        if ob_lookback < 1:
            raise ValueError("ob_lookback must be >= 1")
        self.choch_only = bool(choch_only)
        self.ob_lookback = int(ob_lookback)
        self._detector = _SmcObSignal(swing_w, target_rr, choch_only, ob_lookback)
        self._arrays = np.empty((5, 1024), dtype=float)
        self._atr_acc = 0.0
        self._execution_blocked = False
        self.market = market
        self.f = float(f)
        self.swing_w = int(swing_w)
        self.target_rr = float(target_rr)
        self.wait = int(wait)
        self.min_risk_pts = float(min_risk_pts)
        self.cooldown = int(cooldown)
        self.log_decisions = bool(log_decisions)
        self.session_tz = market.session_timezone
        self.decisions: list[SmcObDecision] = []

        self._high: list[float] = []
        self._low: list[float] = []
        self._close: list[float] = []
        self._timestamps: list[datetime] = []
        self._seen = 0
        self._pending_active = False
        self._active_decision: int | None = None

    def set_execution_state(self, *, pending: bool, position: bool, cooldown: int) -> None:
        self._execution_blocked = position or cooldown > 0
        self._pending_active = pending

    def _append(self, bar: Bar) -> int:
        self._high.append(bar.high)
        self._low.append(bar.low)
        self._close.append(bar.close)
        self._timestamps.append(bar.timestamp)
        self._seen += 1
        return self._seen - 1

    def _update_pivots(self, t: int) -> None:
        # Populate the reference cache with the SAME causal recurrence as _atr.
        # Capacity doubles; prefix views avoid quadratic full-history parsing.
        if t == self._arrays.shape[1]:
            grown = np.empty((5, 2 * self._arrays.shape[1]), dtype=float)
            grown[:, :t] = self._arrays
            self._arrays = grown
        h, l, c = self._high[t], self._low[t], self._close[t]
        tr = h - l if t == 0 else max(h - l, abs(h - self._close[t-1]), abs(l - self._close[t-1]))
        self._atr_acc += (tr - self._atr_acc) / 200 if t else tr
        hv = h - l >= 2 * self._atr_acc
        self._arrays[:, t] = h, l, c, l if hv else h, h if hv else l
        h5, l5, c5, phi, plo = self._arrays[:, :t + 1]
        detector = self._detector
        detector._p_hi, detector._p_lo, detector._plen = phi, plo, t + 1
        detector.update_pivots(t, h5, l5, c5)

    def _structure_and_signal(self, t: int, *, emit: bool) -> Signal | None:
        if not emit or self._pending_active or self._execution_blocked or not self._detector.ready():
            return None
        h5, l5, c5 = self._arrays[:3, :t + 1]
        spec = self._detector.structure_and_arm(t, h5, l5, c5, False)
        if spec is None:
            return None
        direction = "long" if spec["side"] == 1 else "short"
        entry, stop, target, risk = spec["entry"], spec["sl"], spec["tp"], spec["risk"]

        decision: SmcObReason = (
            "accepted" if risk >= self.min_risk_pts else "risk_below_min"
        )
        if self.log_decisions:
            self.decisions.append(
                SmcObDecision(
                    timestamp=self._timestamps[t],
                    direction=direction,
                    entry=entry,
                    stop=stop,
                    target=target,
                    risk=risk,
                    decision=decision,
                )
            )
            if decision == "accepted":
                self._active_decision = len(self.decisions) - 1
        if decision != "accepted":
            return None
        return Signal(direction, entry, stop, target)

    def _sync(self, history: Sequence[Bar], *, emit_last: bool, pivots_only: bool) -> Signal | None:
        if self._seen and (
            len(history) < self._seen
            or history[self._seen - 1].timestamp != self._timestamps[-1]
        ):
            raise ValueError("history must be an append-only chronological sequence")
        signal = None
        final = len(history) - 1
        for index in range(self._seen, len(history)):
            t = self._append(history[index])
            self._update_pivots(t)
            if not pivots_only:
                signal = self._structure_and_signal(
                    t, emit=emit_last and index == final
                )
        return signal

    def observe(self, history: Sequence[Bar]) -> None:
        """Update delayed pivots on bars where execution suppresses new setups."""
        self._sync(history, emit_last=False, pivots_only=True)

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        return self._sync(history, emit_last=True, pivots_only=False)

    def note_order_filled(self, entry_time: datetime) -> None:
        del entry_time

    def note_order_expired(self, expiry_time: datetime) -> None:
        if self._active_decision is not None:
            index = self._active_decision
            self.decisions[index] = replace(
                self.decisions[index], decision="expired", exit_time=expiry_time
            )
            self._active_decision = None

    def note_trade(self, exit_time: datetime, r_result: float) -> None:
        if self._active_decision is not None:
            index = self._active_decision
            self.decisions[index] = replace(
                self.decisions[index],
                decision="closed",
                r_result=float(r_result),
                exit_time=exit_time,
            )
            self._active_decision = None

    def clear_decisions(self) -> None:
        self.decisions.clear()
        self._active_decision = None

    def parameters(self) -> dict[str, object]:
        return {
            **({"market": self.market.to_dict()} if self.market != MNQ else {}),
            "choch_only": self.choch_only,
            "ob_lookback": self.ob_lookback,
            "f": self.f,
            "swing_w": self.swing_w,
            "target_rr": self.target_rr,
            "wait": self.wait,
            "min_risk_pts": self.min_risk_pts,
            "cooldown": self.cooldown,
            "log_decisions": self.log_decisions,
            "timeframe": "M5",
            "pivot_confirmation": "point_in_time",
        }

    def fresh(self) -> SmcObStrategy:
        return SmcObStrategy(
            market=self.market,
            choch_only=self.choch_only,
            ob_lookback=self.ob_lookback,
            f=self.f,
            swing_w=self.swing_w,
            target_rr=self.target_rr,
            wait=self.wait,
            min_risk_pts=self.min_risk_pts,
            cooldown=self.cooldown,
            log_decisions=self.log_decisions,
        )


def smc_ob_config(
    *,
    market: MarketSpec = MNQ,
    f: float = DEFAULT_FRACTION,
    wait: int = DEFAULT_WAIT,
    cooldown: int = DEFAULT_COOLDOWN,
    discrete_partial_contracts: bool = True,
    **overrides,
) -> BacktestConfig:
    """Gross-reference execution config for SMC-OB."""
    params = {
        "dollar_per_point": market.dollar_per_point,
        "tick_size": market.tick_size,
        "commission_per_side": 0.0,
        "slippage_points": 0.0,
        "bar_interval_seconds": 300,
        "max_contracts": 1_000_000,
        "max_bars_held": 1_000_000_000,
        "partial_take_profit_fraction": f,
        "move_stop_to_break_even": True,
        "pending_limit_entry": True,
        "pending_order_wait_bars": wait,
        "cooldown_bars": cooldown,
        "discrete_partial_contracts": discrete_partial_contracts,
    }
    params.update(overrides)
    return BacktestConfig(**params)


__all__ = ["SmcObDecision", "SmcObStrategy", "smc_ob_config"]

"""Estrategia causal de Compresión Breakout para el Bloque O1 (DEV-only).

Hipótesis O1:
    En MNQ M5 real post-lanzamiento, una barra de compresión NR7 seguida por
    cierre fuera de un canal Donchian de longitud L in {20, 55} puede tener
    continuación suficiente para sobrevivir costes.

Semántica exacta:
    Para evaluar una barra cerrada t:
    1. La barra anterior cerrada t-1 es NR7 si su rango high-low es menor o igual
       al mínimo de los rangos de las siete barras cerradas que terminan en t-1
       (history[-8:-1]). Empates califican.
    2. Canal Donchian de longitud L en t: máximo high y mínimo low de las L barras
       cerradas inmediatamente anteriores a t (history[-L-1:-1]), excluyendo t.
    3. LONG si close[t] > donchian_high; SHORT si close[t] < donchian_low;
       si ninguno o ambos (datos corruptos), señal nula (fail-closed).
    4. Entrada market en el open de t+1 mediante el executor canónico; nunca llenar en t.
    5. ATR(14) calculado únicamente con barras cerradas hasta t;
       stop = 1.0 * ATR, target = 2.0 * ATR, tick-rounded mediante la ruta existente.
    6. Time exit = 48 barras M5, market. Cooldown = 6 barras.
    7. No más de una señal por barra; no short/long simultáneo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Sequence

from src.backtest.history import Bar
from src.backtest.markets import MarketSpec
from src.backtest.strategy import Signal


@dataclass(frozen=True)
class O1DecisionAudit:
    """Registro inmutable de una decisión auditada de O1."""

    timestamp: datetime
    length: int
    direction: Literal["long", "short"]
    bar_close: float
    donchian_high: float
    donchian_low: float
    nr7_range: float
    min_7_range: float
    atr_14: float
    stop_distance_pts: float
    target_distance_pts: float


class O1CompressionBreakoutStrategy:
    """Estrategia causal e incremental para la hipótesis O1."""

    def __init__(
        self,
        market_spec: MarketSpec,
        donchian_lookback: int = 20,
        atr_period: int = 14,
        stop_atr_mult: float = 1.0,
        target_atr_mult: float = 2.0,
        cooldown: int = 6,
        log_decisions: bool = False,
    ) -> None:
        if donchian_lookback not in (20, 55):
            raise ValueError(f"Longitud Donchian no permitida en O1: {donchian_lookback}. Solo L in {{20, 55}}.")
        self.market_spec = market_spec
        self.donchian_lookback = donchian_lookback
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.target_atr_mult = target_atr_mult
        self.cooldown = cooldown
        self.log_decisions = log_decisions

        self._seen = 0
        self._atr = 0.0
        self._cooldown_left = 0
        self.audits: list[O1DecisionAudit] = []

    def _sync(self, history: Sequence[Bar]) -> None:
        """Sincroniza incrementalmente el ATR(14) de Wilder y el contador de cooldown."""
        final = len(history) - 1
        for idx in range(self._seen, len(history)):
            bar = history[idx]
            if idx == 0:
                self._atr = bar.high - bar.low
            else:
                prev = history[idx - 1]
                tr = max(
                    bar.high - bar.low,
                    abs(bar.high - prev.close),
                    abs(bar.low - prev.close),
                )
                self._atr += (tr - self._atr) / float(self.atr_period)

            # Decrementar cooldown para barras intermedias consumidas fuera de evaluate
            if self._cooldown_left > 0 and idx != final:
                self._cooldown_left -= 1

        self._seen = len(history)

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        """Evalúa causalmente la barra cerrada t (history[-1])."""
        self._sync(history)

        min_required = max(self.donchian_lookback + 1, 8, self.atr_period + 1)
        if len(history) < min_required or self._cooldown_left > 0:
            if self._cooldown_left > 0:
                self._cooldown_left -= 1
            return None

        bar_t = history[-1]
        bar_prev = history[-2]

        # 1. Condición NR7 en la barra t-1:
        # Las 7 barras cerradas que terminan en t-1 son history[-8:-1]
        seven_bars = history[-8:-1]
        ranges_7 = [b.high - b.low for b in seven_bars]
        range_t_minus_1 = bar_prev.high - bar_prev.low
        min_range_7 = min(ranges_7)

        # Empates califican; tolerancia numérica float
        is_nr7 = range_t_minus_1 <= min_range_7 + 1e-9
        if not is_nr7:
            return None

        # 2. Canal Donchian de longitud L en t:
        # L barras cerradas inmediatamente anteriores a t: history[-L-1:-1]
        donchian_bars = history[-self.donchian_lookback - 1 : -1]
        donchian_high = max(b.high for b in donchian_bars)
        donchian_low = min(b.low for b in donchian_bars)

        # 3. Ruptura en t:
        is_long = bar_t.close > donchian_high
        is_short = bar_t.close < donchian_low

        # Fail-closed si ambos son ciertos (datos anómalos o invertidos) o ninguno
        if (is_long and is_short) or (not is_long and not is_short):
            return None

        direction: Literal["long", "short"] = "long" if is_long else "short"

        # 4 & 5. Dimensionamiento y brackets redondeados a ticks:
        tick_size = self.market_spec.tick_size
        raw_stop = self.stop_atr_mult * self._atr
        raw_target = self.target_atr_mult * self._atr

        stop_pts = max(round(raw_stop / tick_size) * tick_size, tick_size)
        target_pts = max(round(raw_target / tick_size) * tick_size, 2.0 * tick_size)

        # Activar cooldown
        self._cooldown_left = self.cooldown

        sig = Signal(
            direction=direction,
            entry=bar_t.close,
            stop=stop_pts,
            target=target_pts,
            stop_target_as_points=True,
        )

        if self.log_decisions:
            self.audits.append(
                O1DecisionAudit(
                    timestamp=bar_t.timestamp,
                    length=self.donchian_lookback,
                    direction=direction,
                    bar_close=bar_t.close,
                    donchian_high=donchian_high,
                    donchian_low=donchian_low,
                    nr7_range=range_t_minus_1,
                    min_7_range=min_range_7,
                    atr_14=self._atr,
                    stop_distance_pts=stop_pts,
                    target_distance_pts=target_pts,
                )
            )

        return sig

    def fresh(self) -> O1CompressionBreakoutStrategy:
        """Crea una nueva instancia idéntica sin estado previo."""
        return O1CompressionBreakoutStrategy(
            market_spec=self.market_spec,
            donchian_lookback=self.donchian_lookback,
            atr_period=self.atr_period,
            stop_atr_mult=self.stop_atr_mult,
            target_atr_mult=self.target_atr_mult,
            cooldown=self.cooldown,
            log_decisions=self.log_decisions,
        )


class TrivialWrapperStrategy:
    """Wrapper pasante para control anti-fraude (debe coincidir 100% bit-a-bit con la estrategia base)."""

    def __init__(self, inner: O1CompressionBreakoutStrategy) -> None:
        self.inner = inner
        self.market_spec = inner.market_spec
        self.donchian_lookback = inner.donchian_lookback

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        sig = self.inner.evaluate(history)
        if sig is None:
            return None
        return sig

    def fresh(self) -> TrivialWrapperStrategy:
        return TrivialWrapperStrategy(self.inner.fresh())


def build_o1_strategy(
    length: int,
    market_spec: MarketSpec,
    is_wrapper: bool = False,
    cooldown: int = 6,
    log_decisions: bool = False,
) -> O1CompressionBreakoutStrategy | TrivialWrapperStrategy:
    """Construye una instancia validada de la estrategia O1 o su wrapper."""
    base = O1CompressionBreakoutStrategy(
        market_spec=market_spec,
        donchian_lookback=length,
        cooldown=cooldown,
        log_decisions=log_decisions,
    )
    if is_wrapper:
        return TrivialWrapperStrategy(base)
    return base

"""Filtros causales y estrategia normalizada por volatilidad ATR para el Bloque M8.

Corrige el defecto metodológico de M7 (BLOCKERS.md §2: min_risk_pts=8.0 producía
asimetría monetaria severa entre mercados) normalizando el umbral de riesgo:
    min_risk_atr = k * ATR(14)
sobre barras M5 cerradas causales (available_at <= t).

Brazos implementados (7 arms):
1. control_m7: Baseline SMC-FVG con min_risk_pts=8.0 (régimen canónico M7, para delta de escala vs filtro).
2. atr_k050: SMC-FVG con min_risk_atr = 0.5 * ATR(14). Brazo de referencia declarado para combinaciones.
3. atr_k100: SMC-FVG con min_risk_atr = 1.0 * ATR(14). Sensibilidad standalone declarada antes de medir.
4. atr_k050_ote_band: atr_k050 + banda OTE [0.62, 0.705] sobre el impulso swing causal (responde §12 #5).
5. atr_k050_trend_kai: atr_k050 + alineación con sesgo diario D1 de Kai (responde §12 #7 con escala sana).
6. atr_k050_premium_discount: atr_k050 + premium/discount 0.5 (src/zones/fibonacci.py) (responde §12 #6).
7. wrapper_trivial: control anti-fraude (filtro siempre True sobre atr_k050 -> 100% bit-a-bit idéntico).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Sequence

import numpy as np

from lab_artifacts.m7_protocol.filters import (
    KaiDailyBiasIndex,
    OteCalculator,
)
from src.backtest.history import Bar
from src.backtest.markets import MarketSpec
from src.backtest.smc_fvg import SmcFvgStrategy
from src.backtest.strategy import Signal
from src.zones.fibonacci import premium_discount


@dataclass(frozen=True)
class M8DecisionAudit:
    """Registro inmutable de una señal evaluada bajo el protocolo M8."""

    timestamp: datetime
    direction: Literal["long", "short"]
    entry: float
    arm_id: str
    verdict: bool
    risk: float
    atr_14: float
    min_risk_threshold: float
    retrace_ratio: float | None = None
    d1_bias: int | None = None
    leg_low: float | None = None
    leg_high: float | None = None
    pd_status: str | None = None


class M8FilteredStrategy(SmcFvgStrategy):
    """Estrategia SMC-FVG con riesgo normalizado por ATR(14) y filtros causales de M8."""

    def __init__(
        self,
        *,
        market: MarketSpec,
        arm_id: str = "atr_k050",
        daily_bias_index: KaiDailyBiasIndex | None = None,
        min_risk_pts: float = 8.0,
        wait: int = 48,
        target_rr: float = 2.0,
        f: float = 0.5,
        swing_w: int = 5,
        cooldown: int = 6,
        log_decisions: bool = False,
    ) -> None:
        initial_min_pts = min_risk_pts if arm_id == "control_m7" else 0.0
        super().__init__(
            market=market,
            f=f,
            swing_w=swing_w,
            target_rr=target_rr,
            wait=wait,
            min_risk_pts=initial_min_pts,
            cooldown=cooldown,
            log_decisions=log_decisions,
        )
        self.arm_id = arm_id
        self.daily_bias_index = daily_bias_index

        if arm_id == "control_m7":
            self.k_atr: float | None = None
        elif arm_id == "atr_k100":
            self.k_atr = 1.0
        elif arm_id in (
            "atr_k050",
            "atr_k050_ote_band",
            "atr_k050_trend_kai",
            "atr_k050_premium_discount",
            "wrapper_trivial",
        ):
            self.k_atr = 0.5
        else:
            raise ValueError(f"arm_id desconocido en M8: {arm_id!r}")

        self._atr_val: float = 0.0
        self._atr_history: list[float] = []
        self.m8_audits: list[M8DecisionAudit] = []

    def _append(self, bar: Bar) -> int:
        t = super()._append(bar)
        # Cálculo causal point-in-time de True Range y Wilder ATR(14)
        if t == 0:
            tr = bar.high - bar.low
            self._atr_val = tr
        else:
            prev_close = self._close[t - 1]
            tr = max(
                bar.high - bar.low,
                abs(bar.high - prev_close),
                abs(bar.low - prev_close),
            )
            self._atr_val = self._atr_val + (tr - self._atr_val) / 14.0

        self._atr_history.append(self._atr_val)
        return t

    def _sync(
        self,
        history: Sequence[Bar],
        *,
        emit_last: bool,
        pivots_only: bool,
    ) -> Signal | None:
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

            # Si el brazo es normalizado por ATR, el umbral de riesgo de SmcFvgStrategy
            # se actualiza causalmente con el ATR(14) de la barra t
            if self.k_atr is not None:
                self.min_risk_pts = self.k_atr * self._atr_history[t]

            if not pivots_only and t >= 2 * self.swing_w + 2:
                signal = self._structure_and_signal(
                    t, emit=emit_last and index == final
                )
        return signal

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        sig = super().evaluate(history)
        if sig is None:
            return None

        t = len(history) - 1
        atr_now = self._atr_history[t] if t < len(self._atr_history) else self._atr_val
        thresh = (self.k_atr * atr_now) if self.k_atr is not None else self.min_risk_pts
        sig_risk = abs(sig.entry - sig.stop)

        # Brazos sin filtro secundario
        if self.arm_id in ("control_m7", "atr_k050", "atr_k100", "wrapper_trivial"):
            if self.log_decisions:
                self.m8_audits.append(
                    M8DecisionAudit(
                        timestamp=history[-1].timestamp,
                        direction=sig.direction,
                        entry=sig.entry,
                        arm_id=self.arm_id,
                        verdict=True,
                        risk=sig_risk,
                        atr_14=atr_now,
                        min_risk_threshold=thresh,
                    )
                )
            return sig

        retrace, leg_low, leg_high = OteCalculator.compute_retrace(
            direction=sig.direction,
            entry=sig.entry,
            sl_p=self._sl_p,
            sl_i=self._sl_i,
            sh_p=self._sh_p,
            sh_i=self._sh_i,
            highs=self._high,
            lows=self._low,
            t=t,
        )

        d1_bias = None
        if self.daily_bias_index is not None:
            d1_bias = self.daily_bias_index.get_bias_at(history[-1].timestamp)

        pd_status = None
        if leg_low is not None and leg_high is not None and leg_low > 0 and leg_high > leg_low:
            pd_status = premium_discount(sig.entry, leg_low, leg_high)

        passed = True

        if self.arm_id == "atr_k050_ote_band":
            # Retroceso en banda OTE [0.62, 0.705] sobre el impulso causal (§12 #5)
            if retrace is None or not (0.62 <= retrace <= 0.705):
                passed = False

        elif self.arm_id == "atr_k050_trend_kai":
            # Sesgo direccional D1 de Kai alineado (+1 Long, -1 Short) (§12 #7)
            if d1_bias is None:
                passed = False
            elif sig.direction == "long" and d1_bias != 1:
                passed = False
            elif sig.direction == "short" and d1_bias != -1:
                passed = False

        elif self.arm_id == "atr_k050_premium_discount":
            # Condición premium/discount 0.5 (fibonacci.py) (§12 #6):
            # Long debe comprar en descuento (entry < equilibrium, i.e. status == 'discount')
            # Short debe vender en prima (entry > equilibrium, i.e. status == 'premium')
            if pd_status is None:
                passed = False
            elif sig.direction == "long" and pd_status != "discount":
                passed = False
            elif sig.direction == "short" and pd_status != "premium":
                passed = False

        else:
            raise ValueError(f"arm_id desconocido: {self.arm_id!r}")

        if self.log_decisions:
            self.m8_audits.append(
                M8DecisionAudit(
                    timestamp=history[-1].timestamp,
                    direction=sig.direction,
                    entry=sig.entry,
                    arm_id=self.arm_id,
                    verdict=passed,
                    risk=sig_risk,
                    atr_14=atr_now,
                    min_risk_threshold=thresh,
                    retrace_ratio=round(retrace, 4) if retrace is not None else None,
                    d1_bias=d1_bias,
                    leg_low=round(leg_low, 4) if leg_low is not None else None,
                    leg_high=round(leg_high, 4) if leg_high is not None else None,
                    pd_status=pd_status,
                )
            )

        if not passed:
            return None

        return sig

    def fresh(self) -> M8FilteredStrategy:
        return M8FilteredStrategy(
            market=self.market,
            arm_id=self.arm_id,
            daily_bias_index=self.daily_bias_index,
            min_risk_pts=self.min_risk_pts,
            wait=self.wait,
            target_rr=self.target_rr,
            f=self.f,
            swing_w=self.swing_w,
            cooldown=self.cooldown,
            log_decisions=self.log_decisions,
        )

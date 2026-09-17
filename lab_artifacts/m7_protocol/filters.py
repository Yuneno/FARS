"""Filtros causales de investigación para el Bloque M7.

Implementa:
- KaiDailyBiasIndex: Cálculo causal point-in-time del sesgo diario (D1) de Kai
  (strat_rangos.py: sweep de low y cierre dentro -> +1; sweep de high y cierre dentro -> -1).
- OteHelper: Medición del ratio de retroceso Fibonacci sobre el rango de impulso causal
  de la estructura swing confirmada en SMC-FVG.
- M7FilteredStrategy: Decorador/subclase causal de SmcFvgStrategy que implementa los 7 arms:
  baseline, ote_062, ote_0705, ote_band, trend_kai, ote_band_plus_trend, wrapper_trivial.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Sequence

import numpy as np
import pandas as pd

from src.backtest.history import Bar
from src.backtest.markets import MarketSpec
from src.backtest.smc_fvg import SmcFvgStrategy
from src.backtest.strategy import Signal


@dataclass(frozen=True)
class M7DecisionAudit:
    """Registro inmutable de una señal evaluada bajo el protocolo M7."""

    timestamp: datetime
    direction: Literal["long", "short"]
    entry: float
    arm_id: str
    verdict: bool
    retrace_ratio: float | None = None
    d1_bias: int | None = None
    leg_low: float | None = None
    leg_high: float | None = None


class KaiDailyBiasIndex:
    """Índice point-in-time del sesgo diario (D1) de Kai.

    Agrega barras M5 a D1 por día calendario UTC, computa la regla de rango
    de strat_rangos.py, y expone una búsqueda point-in-time estricta:
    en el instante t solo se pueden consultar velas D1 que hayan cerrado antes
    del cierre de la vela base t.
    """

    def __init__(self, bars: Sequence[Bar], bar_duration_sec: int = 300) -> None:
        self.bar_duration_sec = bar_duration_sec
        # Agregación a velas D1
        ts_all = [b.timestamp for b in bars]
        df = pd.DataFrame(
            {
                "open": [b.open for b in bars],
                "high": [b.high for b in bars],
                "low": [b.low for b in bars],
                "close": [b.close for b in bars],
            },
            index=ts_all,
        ).sort_index()

        g = df.resample("1D", label="left", closed="left")
        d_df = g.agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}
        ).dropna()

        hd = d_df["high"].to_numpy(dtype=float)
        ld = d_df["low"].to_numpy(dtype=float)
        cd = d_df["close"].to_numpy(dtype=float)
        # Epoch segundos de inicio de cada vela D1
        self.tsd = pd.DatetimeIndex(d_df.index).asi8 // 10**9

        # Regla de sesgo D1 de Kai (strat_rangos.py:53-62)
        n_d = len(cd)
        self.bias_d = np.zeros(n_d, dtype=np.int8)
        cur_bias = 0
        for i in range(1, n_d):
            if ld[i] < ld[i - 1] and cd[i] > ld[i - 1]:
                cur_bias = 1
            elif hd[i] > hd[i - 1] and cd[i] < hd[i - 1]:
                cur_bias = -1
            self.bias_d[i] = cur_bias

        # Duración de vela diaria: 86400 segundos
        self.day_duration_sec = 86400
        self.d_close_sec = self.tsd + self.day_duration_sec

    def get_bias_at(self, ts: datetime) -> int:
        """Devuelve el sesgo D1 (+1, -1, 0) vigente y YA CERRADO al cerrar la barra ts."""
        base_close_sec = int(ts.timestamp()) + self.bar_duration_sec
        # Último índice cuya vela diaria cerró en o antes del cierre de esta barra
        di = np.searchsorted(self.d_close_sec, base_close_sec, side="right") - 1
        if di < 0 or di >= len(self.bias_d):
            return 0
        return int(self.bias_d[di])


class OteCalculator:
    """Calculador causal de retroceso OTE sobre el impulso de estructura swing."""

    @staticmethod
    def compute_retrace(
        direction: str,
        entry: float,
        sl_p: float | None,
        sl_i: int | None,
        sh_p: float | None,
        sh_i: int | None,
        highs: list[float],
        lows: list[float],
        t: int,
    ) -> tuple[float | None, float | None, float | None]:
        """Calcula (retrace_ratio, leg_low, leg_high).

        Para Long:
          L = sl_p (pivote swing low vigente que originó la ruptura)
          H = max(highs[sl_i : t + 1]) (extremo alcanzado por el impulso)
          retrace = (H - entry) / (H - L)

        Para Short:
          H = sh_p (pivote swing high vigente que originó la ruptura)
          L = min(lows[sh_i : t + 1]) (extremo alcanzado por el impulso)
          retrace = (entry - L) / (H - L)
        """
        if direction == "long":
            if sl_p is None or sl_i is None:
                return None, None, None
            L = float(sl_p)
            idx_start = max(0, min(sl_i, t))
            H = float(max(highs[idx_start : t + 1]))
            if H <= L:
                return None, L, H
            retrace = (H - entry) / (H - L)
            return float(retrace), L, H
        elif direction == "short":
            if sh_p is None or sh_i is None:
                return None, None, None
            H = float(sh_p)
            idx_start = max(0, min(sh_i, t))
            L = float(min(lows[idx_start : t + 1]))
            if H <= L:
                return None, L, H
            retrace = (entry - L) / (H - L)
            return float(retrace), L, H
        return None, None, None


class M7FilteredStrategy(SmcFvgStrategy):
    """Estrategia SMC-FVG filtrada causalmente por los brazos normativos de M7."""

    def __init__(
        self,
        *,
        market: MarketSpec,
        arm_id: str = "baseline",
        daily_bias_index: KaiDailyBiasIndex | None = None,
        min_risk_pts: float = 8.0,
        wait: int = 48,
        target_rr: float = 2.0,
        f: float = 0.5,
        swing_w: int = 5,
        cooldown: int = 6,
        log_decisions: bool = True,
    ) -> None:
        super().__init__(
            market=market,
            f=f,
            swing_w=swing_w,
            target_rr=target_rr,
            wait=wait,
            min_risk_pts=min_risk_pts,
            cooldown=cooldown,
            log_decisions=log_decisions,
        )
        self.arm_id = arm_id
        self.daily_bias_index = daily_bias_index
        self.m7_audits: list[M7DecisionAudit] = []

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        sig = super().evaluate(history)
        if sig is None:
            return None

        # Si el brazo es baseline o wrapper_trivial (filtro siempre True), pasa
        if self.arm_id in ("baseline", "wrapper_trivial"):
            if self.log_decisions:
                self.m7_audits.append(
                    M7DecisionAudit(
                        timestamp=history[-1].timestamp,
                        direction=sig.direction,
                        entry=sig.entry,
                        arm_id=self.arm_id,
                        verdict=True,
                    )
                )
            return sig

        t = len(history) - 1
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

        passed = True

        if self.arm_id == "ote_062":
            # Entrada si retrocede al menos al 62% del impulso (hasta el 100%)
            if retrace is None or not (0.62 <= retrace <= 1.0):
                passed = False

        elif self.arm_id == "ote_0705":
            # Entrada si retrocede al menos al 70.5% del impulso (hasta el 100%)
            if retrace is None or not (0.705 <= retrace <= 1.0):
                passed = False

        elif self.arm_id == "ote_band":
            # Entrada si retrocede dentro de la banda OTE [0.62, 0.705]
            if retrace is None or not (0.62 <= retrace <= 0.705):
                passed = False

        elif self.arm_id == "trend_kai":
            # Alineación obligatoria con sesgo D1 de Kai (+1 para Long, -1 para Short)
            if d1_bias is None:
                passed = False
            elif sig.direction == "long" and d1_bias != 1:
                passed = False
            elif sig.direction == "short" and d1_bias != -1:
                passed = False

        elif self.arm_id == "ote_band_plus_trend":
            # Confluencia: banda OTE [0.62, 0.705] Y sesgo D1 alineado
            if retrace is None or not (0.62 <= retrace <= 0.705):
                passed = False
            elif d1_bias is None:
                passed = False
            elif sig.direction == "long" and d1_bias != 1:
                passed = False
            elif sig.direction == "short" and d1_bias != -1:
                passed = False

        else:
            raise ValueError(f"arm_id desconocido: {self.arm_id!r}")

        if self.log_decisions:
            self.m7_audits.append(
                M7DecisionAudit(
                    timestamp=history[-1].timestamp,
                    direction=sig.direction,
                    entry=sig.entry,
                    arm_id=self.arm_id,
                    verdict=passed,
                    retrace_ratio=round(retrace, 4) if retrace is not None else None,
                    d1_bias=d1_bias,
                    leg_low=round(leg_low, 4) if leg_low is not None else None,
                    leg_high=round(leg_high, 4) if leg_high is not None else None,
                )
            )

        if not passed:
            return None

        return sig

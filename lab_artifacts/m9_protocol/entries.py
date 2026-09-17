"""Estrategias de entrada crudas y autónomas para el Bloque M9.

Implementa 5 entradas de investigación sin filtros adicionales:
- E1 · TS-D1: Turtle Soup del extremo D1 (prev_day_high / prev_day_low)
- E2 · TS-D20: Turtle Soup del extremo multi-sesión de 20 días (d20_high / d20_low)
- E3 · MOM-BREAK: Momentum de ruptura de rango Donchian de 20 días
- E4 · MR-LEVEL: Reversión a nivel de sesión sobre pools estructurales Z3-b (PDH, PDL, D20, ONH, ONL)
- E5 · VOL-BREAK: Ruptura de volatilidad del rango de la sesión previa (PDH / PDL, estilo Crabel)

Convención de riesgo unificada (M8):
    stop = 1.0 * ATR(14)
    target = 2.0 * ATR(14) (2R)
    cooldown = 6 barras M5
    time_exit = 48 barras M5 (4 horas)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Sequence

from src.backtest.history import Bar
from src.backtest.markets import MarketSpec
from src.backtest.strategy import Signal
from src.zones.session_levels import SessionLevelsBuilder


@dataclass(frozen=True)
class M9DecisionAudit:
    """Registro inmutable de una decisión auditada de M9."""

    timestamp: datetime
    entry_id: str
    direction: Literal["long", "short"]
    entry_price: float
    atr_14: float
    stop_distance: float
    target_distance: float
    level_type: str | None = None
    level_price: float | None = None


class BaseScreeningStrategy:
    """Clase base incremental y causal para las entradas del screening M9."""

    def __init__(
        self,
        market_spec: MarketSpec,
        entry_id: str,
        cooldown: int = 6,
        log_decisions: bool = False,
    ) -> None:
        self.market_spec = market_spec
        self.entry_id = entry_id
        self.cooldown = cooldown
        self.log_decisions = log_decisions
        self.builder = SessionLevelsBuilder(
            symbol=market_spec.symbol,
            timeframe="M5",
            market_spec=market_spec,
            tick_size=market_spec.tick_size,
        )
        self._seen = 0
        self._atr = 0.0
        self._cooldown_left = 0
        self.m9_audits: list[M9DecisionAudit] = []

    def _sync(self, history: Sequence[Bar]) -> None:
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
                self._atr += (tr - self._atr) / 14.0

            self.builder.on_bar(bar, idx, atr=self._atr)

            if self._cooldown_left > 0 and idx != final:
                self._cooldown_left -= 1

        self._seen = len(history)

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        raise NotImplementedError

    def fresh(self) -> BaseScreeningStrategy:
        raise NotImplementedError


class E1_TS_D1(BaseScreeningStrategy):
    """E1: Turtle Soup del extremo D1 (barrido de PDH/PDL con cierre dentro)."""

    def __init__(
        self,
        market_spec: MarketSpec,
        cooldown: int = 6,
        log_decisions: bool = False,
    ) -> None:
        super().__init__(market_spec, "e1_ts_d1", cooldown, log_decisions)

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        self._sync(history)
        if len(history) < 2 or self._cooldown_left > 0:
            if self._cooldown_left > 0:
                self._cooldown_left -= 1
            return None

        bar = history[-1]
        prev = history[-2]

        pdh_z = self.builder.get_current_zone("prev_day_high")
        pdl_z = self.builder.get_current_zone("prev_day_low")
        if not pdh_z or not pdl_z:
            return None

        pdh = pdh_z.midpoint
        pdl = pdl_z.midpoint

        # Long: mecha barre PDL y cierra de vuelta dentro
        if bar.low < pdl and bar.close > pdl and prev.close >= pdl:
            self._cooldown_left = self.cooldown
            sig = Signal(
                direction="long",
                entry=bar.close,
                stop=1.0 * self._atr,
                target=2.0 * self._atr,
                stop_target_as_points=True,
            )
            if self.log_decisions:
                self.m9_audits.append(
                    M9DecisionAudit(
                        timestamp=bar.timestamp,
                        entry_id=self.entry_id,
                        direction="long",
                        entry_price=bar.close,
                        atr_14=self._atr,
                        stop_distance=1.0 * self._atr,
                        target_distance=2.0 * self._atr,
                        level_type="prev_day_low",
                        level_price=pdl,
                    )
                )
            return sig

        # Short: mecha barre PDH y cierra de vuelta dentro
        if bar.high > pdh and bar.close < pdh and prev.close <= pdh:
            self._cooldown_left = self.cooldown
            sig = Signal(
                direction="short",
                entry=bar.close,
                stop=1.0 * self._atr,
                target=2.0 * self._atr,
                stop_target_as_points=True,
            )
            if self.log_decisions:
                self.m9_audits.append(
                    M9DecisionAudit(
                        timestamp=bar.timestamp,
                        entry_id=self.entry_id,
                        direction="short",
                        entry_price=bar.close,
                        atr_14=self._atr,
                        stop_distance=1.0 * self._atr,
                        target_distance=2.0 * self._atr,
                        level_type="prev_day_high",
                        level_price=pdh,
                    )
                )
            return sig

        return None

    def fresh(self) -> E1_TS_D1:
        return E1_TS_D1(self.market_spec, self.cooldown, self.log_decisions)


class E2_TS_D20(BaseScreeningStrategy):
    """E2: Turtle Soup del extremo multi-sesión de 20 días (d20_high / d20_low)."""

    def __init__(
        self,
        market_spec: MarketSpec,
        cooldown: int = 6,
        log_decisions: bool = False,
    ) -> None:
        super().__init__(market_spec, "e2_ts_d20", cooldown, log_decisions)

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        self._sync(history)
        if len(history) < 2 or self._cooldown_left > 0:
            if self._cooldown_left > 0:
                self._cooldown_left -= 1
            return None

        bar = history[-1]
        prev = history[-2]

        d20h_z = self.builder.get_current_zone("d20_high")
        d20l_z = self.builder.get_current_zone("d20_low")
        if not d20h_z or not d20l_z:
            return None

        d20h = d20h_z.midpoint
        d20l = d20l_z.midpoint

        # Long: barrido de d20_low y cierre dentro
        if bar.low < d20l and bar.close > d20l and prev.close >= d20l:
            self._cooldown_left = self.cooldown
            sig = Signal(
                direction="long",
                entry=bar.close,
                stop=1.0 * self._atr,
                target=2.0 * self._atr,
                stop_target_as_points=True,
            )
            if self.log_decisions:
                self.m9_audits.append(
                    M9DecisionAudit(
                        timestamp=bar.timestamp,
                        entry_id=self.entry_id,
                        direction="long",
                        entry_price=bar.close,
                        atr_14=self._atr,
                        stop_distance=1.0 * self._atr,
                        target_distance=2.0 * self._atr,
                        level_type="d20_low",
                        level_price=d20l,
                    )
                )
            return sig

        # Short: barrido de d20_high y cierre dentro
        if bar.high > d20h and bar.close < d20h and prev.close <= d20h:
            self._cooldown_left = self.cooldown
            sig = Signal(
                direction="short",
                entry=bar.close,
                stop=1.0 * self._atr,
                target=2.0 * self._atr,
                stop_target_as_points=True,
            )
            if self.log_decisions:
                self.m9_audits.append(
                    M9DecisionAudit(
                        timestamp=bar.timestamp,
                        entry_id=self.entry_id,
                        direction="short",
                        entry_price=bar.close,
                        atr_14=self._atr,
                        stop_distance=1.0 * self._atr,
                        target_distance=2.0 * self._atr,
                        level_type="d20_high",
                        level_price=d20h,
                    )
                )
            return sig

        return None

    def fresh(self) -> E2_TS_D20:
        return E2_TS_D20(self.market_spec, self.cooldown, self.log_decisions)


class E3_MOM_BREAK(BaseScreeningStrategy):
    """E3: Momentum de ruptura de rango multi-sesión de 20 días."""

    def __init__(
        self,
        market_spec: MarketSpec,
        cooldown: int = 6,
        log_decisions: bool = False,
    ) -> None:
        super().__init__(market_spec, "e3_mom_break", cooldown, log_decisions)

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        self._sync(history)
        if len(history) < 2 or self._cooldown_left > 0:
            if self._cooldown_left > 0:
                self._cooldown_left -= 1
            return None

        bar = history[-1]
        prev = history[-2]

        d20h_z = self.builder.get_current_zone("d20_high")
        d20l_z = self.builder.get_current_zone("d20_low")
        if not d20h_z or not d20l_z:
            return None

        d20h = d20h_z.midpoint
        d20l = d20l_z.midpoint

        # Long: confirmación de ruptura alcista por encima de d20_high
        if bar.close > d20h and prev.close <= d20h:
            self._cooldown_left = self.cooldown
            sig = Signal(
                direction="long",
                entry=bar.close,
                stop=1.0 * self._atr,
                target=2.0 * self._atr,
                stop_target_as_points=True,
            )
            if self.log_decisions:
                self.m9_audits.append(
                    M9DecisionAudit(
                        timestamp=bar.timestamp,
                        entry_id=self.entry_id,
                        direction="long",
                        entry_price=bar.close,
                        atr_14=self._atr,
                        stop_distance=1.0 * self._atr,
                        target_distance=2.0 * self._atr,
                        level_type="d20_high",
                        level_price=d20h,
                    )
                )
            return sig

        # Short: confirmación de ruptura bajista por debajo de d20_low
        if bar.close < d20l and prev.close >= d20l:
            self._cooldown_left = self.cooldown
            sig = Signal(
                direction="short",
                entry=bar.close,
                stop=1.0 * self._atr,
                target=2.0 * self._atr,
                stop_target_as_points=True,
            )
            if self.log_decisions:
                self.m9_audits.append(
                    M9DecisionAudit(
                        timestamp=bar.timestamp,
                        entry_id=self.entry_id,
                        direction="short",
                        entry_price=bar.close,
                        atr_14=self._atr,
                        stop_distance=1.0 * self._atr,
                        target_distance=2.0 * self._atr,
                        level_type="d20_low",
                        level_price=d20l,
                    )
                )
            return sig

        return None

    def fresh(self) -> E3_MOM_BREAK:
        return E3_MOM_BREAK(self.market_spec, self.cooldown, self.log_decisions)


class E4_MR_LEVEL(BaseScreeningStrategy):
    """E4: Reversión a nivel de sesión sobre pools estructurales Z3-b."""

    def __init__(
        self,
        market_spec: MarketSpec,
        cooldown: int = 6,
        log_decisions: bool = False,
    ) -> None:
        super().__init__(market_spec, "e4_mr_level", cooldown, log_decisions)

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        self._sync(history)
        if len(history) < 2 or self._cooldown_left > 0:
            if self._cooldown_left > 0:
                self._cooldown_left -= 1
            return None

        bar = history[-1]
        prev = history[-2]

        # 1. Niveles superiores (reversión bajista -> Short)
        # Prioridad: d20_high, prev_day_high, overnight_high
        for utype in ("d20_high", "prev_day_high", "overnight_high"):
            z = self.builder.get_current_zone(utype)
            if z:
                lvl = z.midpoint
                if bar.high > lvl and bar.close < lvl and prev.close <= lvl:
                    self._cooldown_left = self.cooldown
                    sig = Signal(
                        direction="short",
                        entry=bar.close,
                        stop=1.0 * self._atr,
                        target=2.0 * self._atr,
                        stop_target_as_points=True,
                    )
                    if self.log_decisions:
                        self.m9_audits.append(
                            M9DecisionAudit(
                                timestamp=bar.timestamp,
                                entry_id=self.entry_id,
                                direction="short",
                                entry_price=bar.close,
                                atr_14=self._atr,
                                stop_distance=1.0 * self._atr,
                                target_distance=2.0 * self._atr,
                                level_type=utype,
                                level_price=lvl,
                            )
                        )
                    return sig

        # 2. Niveles inferiores (reversión alcista -> Long)
        # Prioridad: d20_low, prev_day_low, overnight_low
        for ltype in ("d20_low", "prev_day_low", "overnight_low"):
            z = self.builder.get_current_zone(ltype)
            if z:
                lvl = z.midpoint
                if bar.low < lvl and bar.close > lvl and prev.close >= lvl:
                    self._cooldown_left = self.cooldown
                    sig = Signal(
                        direction="long",
                        entry=bar.close,
                        stop=1.0 * self._atr,
                        target=2.0 * self._atr,
                        stop_target_as_points=True,
                    )
                    if self.log_decisions:
                        self.m9_audits.append(
                            M9DecisionAudit(
                                timestamp=bar.timestamp,
                                entry_id=self.entry_id,
                                direction="long",
                                entry_price=bar.close,
                                atr_14=self._atr,
                                stop_distance=1.0 * self._atr,
                                target_distance=2.0 * self._atr,
                                level_type=ltype,
                                level_price=lvl,
                            )
                        )
                    return sig

        return None

    def fresh(self) -> E4_MR_LEVEL:
        return E4_MR_LEVEL(self.market_spec, self.cooldown, self.log_decisions)


class E5_VOL_BREAK(BaseScreeningStrategy):
    """E5: Ruptura de volatilidad del rango de la sesión previa (PDH/PDL - estilo Crabel)."""

    def __init__(
        self,
        market_spec: MarketSpec,
        cooldown: int = 6,
        log_decisions: bool = False,
    ) -> None:
        super().__init__(market_spec, "e5_vol_break", cooldown, log_decisions)

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        self._sync(history)
        if len(history) < 2 or self._cooldown_left > 0:
            if self._cooldown_left > 0:
                self._cooldown_left -= 1
            return None

        bar = history[-1]
        prev = history[-2]

        pdh_z = self.builder.get_current_zone("prev_day_high")
        pdl_z = self.builder.get_current_zone("prev_day_low")
        if not pdh_z or not pdl_z:
            return None

        pdh = pdh_z.midpoint
        pdl = pdl_z.midpoint

        # Long: confirmación de ruptura por encima de PDH
        if bar.close > pdh and prev.close <= pdh:
            self._cooldown_left = self.cooldown
            sig = Signal(
                direction="long",
                entry=bar.close,
                stop=1.0 * self._atr,
                target=2.0 * self._atr,
                stop_target_as_points=True,
            )
            if self.log_decisions:
                self.m9_audits.append(
                    M9DecisionAudit(
                        timestamp=bar.timestamp,
                        entry_id=self.entry_id,
                        direction="long",
                        entry_price=bar.close,
                        atr_14=self._atr,
                        stop_distance=1.0 * self._atr,
                        target_distance=2.0 * self._atr,
                        level_type="prev_day_high",
                        level_price=pdh,
                    )
                )
            return sig

        # Short: confirmación de ruptura por debajo de PDL
        if bar.close < pdl and prev.close >= pdl:
            self._cooldown_left = self.cooldown
            sig = Signal(
                direction="short",
                entry=bar.close,
                stop=1.0 * self._atr,
                target=2.0 * self._atr,
                stop_target_as_points=True,
            )
            if self.log_decisions:
                self.m9_audits.append(
                    M9DecisionAudit(
                        timestamp=bar.timestamp,
                        entry_id=self.entry_id,
                        direction="short",
                        entry_price=bar.close,
                        atr_14=self._atr,
                        stop_distance=1.0 * self._atr,
                        target_distance=2.0 * self._atr,
                        level_type="prev_day_low",
                        level_price=pdl,
                    )
                )
            return sig

        return None

    def fresh(self) -> E5_VOL_BREAK:
        return E5_VOL_BREAK(self.market_spec, self.cooldown, self.log_decisions)


class TrivialWrapperStrategy:
    """Wrapper pasante para control anti-fraude (debe coincidir 100% bit-a-bit con la entrada base)."""

    def __init__(self, inner_strategy: BaseScreeningStrategy) -> None:
        self.inner = inner_strategy
        self.market_spec = inner_strategy.market_spec
        self.entry_id = f"wrapper_{inner_strategy.entry_id}"

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        sig = self.inner.evaluate(history)
        # Filtro pasante trivial siempre True
        if sig is None:
            return None
        return sig

    def fresh(self) -> TrivialWrapperStrategy:
        return TrivialWrapperStrategy(self.inner.fresh())


STRATEGY_CLASSES: dict[str, type[BaseScreeningStrategy]] = {
    "e1_ts_d1": E1_TS_D1,
    "e2_ts_d20": E2_TS_D20,
    "e3_mom_break": E3_MOM_BREAK,
    "e4_mr_level": E4_MR_LEVEL,
    "e5_vol_break": E5_VOL_BREAK,
}


def build_strategy(
    entry_id: str,
    market_spec: MarketSpec,
    is_wrapper: bool = False,
    cooldown: int = 6,
    log_decisions: bool = False,
) -> BaseScreeningStrategy | TrivialWrapperStrategy:
    """Construye una instancia de la estrategia o su wrapper trivial."""
    if entry_id not in STRATEGY_CLASSES:
        raise ValueError(f"entry_id desconocido: {entry_id!r}")
    cls = STRATEGY_CLASSES[entry_id]
    base_instance = cls(market_spec=market_spec, cooldown=cooldown, log_decisions=log_decisions)
    if is_wrapper:
        return TrivialWrapperStrategy(base_instance)
    return base_instance

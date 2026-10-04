"""Módulo de Señales Externas — Port causal de jita-bot a FARS.

Implementa de forma estrictamente causal y reproducible las señales validadas
de la investigación de Juanca (repo jita-bot-review):
1. D2L: Ventana semanal domingo 18:05 ET -> lunes 16:00 ET (drift alcista semanal).
2. L0200: Entrada lunes anticipada 02:00 ET -> lunes 16:00 ET (variante LucidFlex).
3. ACT: Confluencia triple AMD + CRT + EMA50/4h en MNQ.
4. AC: Confluencia AMD + CRT sin filtro EMA (brazo de control metodológico).

Principios causales:
- Cero look-ahead: solo barras cerradas t <= t_eval.
- Entrada en el open de la barra siguiente a la confirmación de la señal.
- Prioridad SL-first en barras ambiguas (donde High >= TP y Low <= SL).
- Integración opcional de M1 mediante src.backtest.intrabar.resolve_intrabar_with_m1.
- Métricas separadas de Budget-R (riesgo teórico) y Stop-R (riesgo efectivo por stop).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import numpy as np

from src.backtest.history import Bar
from src.backtest.intrabar import resolve_intrabar_with_m1
from src.backtest.markets import MNQ, MarketSpec
from src.backtest.strategy import Signal, Strategy

ET = ZoneInfo("America/New_York")

# Horarios de referencia en ET
HORA_APERTURA_DOMINGO = time(18, 0)
HORA_ENTRADA_D2L = time(18, 5)
HORA_ENTRADA_L0200 = time(2, 0)
HORA_CIERRE_LUNES = time(16, 0)

# Parámetros validados D2L / L0200
D2L_SL_PTS = 372.9
D2L_TP_PTS = 150.0

# Parámetros validados ACT / AC
DEFAULT_ATR_PERIOD = 14
DEFAULT_SL_ATR_MULT = 2.0
DEFAULT_TP_ATR_RATIO = 2.0
DEFAULT_SL_CAP_PTS = 50.0
DEFAULT_EMA_PERIOD = 50
DEFAULT_EMA_TF_MINUTES = 240  # 4h
AMD_CONFIRM_MINUTES = 60
DEFAULT_MEDIAN_LOOKBACK = 260
DEFAULT_MIN_SAMPLES = 30


def _to_et(ts: datetime) -> datetime:
    """Garantiza timestamp aware en horario America/New_York (respetando DST)."""
    if ts.tzinfo is None:
        raise ValueError(f"Timestamp naive recibido: {ts!r}; se requiere timezone-aware.")
    return ts.astimezone(ET)


# =============================================================================
# 1. ESTRATEGIA D2L (Domingo 18:05 ET -> Lunes 16:00 ET)
# =============================================================================

class D2LStrategy(Strategy):
    """Estrategia D2L: Drift semanal domingo 18:05 ET -> lunes 16:00 ET (LONG MNQ).

    Entrada: Primer barra M5 disponible tras la reapertura dominical (18:05 ET por defecto).
    Salida: TP fijo 150 pts, SL de emergencia 372.9 pts, o salida obligatoria
    por tiempo a las 16:00 ET del lunes (configurable para variantes).
    """

    def __init__(
        self,
        market: MarketSpec = MNQ,
        sl_pts: float = D2L_SL_PTS,
        tp_pts: float = D2L_TP_PTS,
        entry_time_domingo: time = HORA_APERTURA_DOMINGO,
        exit_time_lunes: time = HORA_CIERRE_LUNES,
    ) -> None:
        self.market = market
        self.sl_pts = sl_pts
        self.tp_pts = tp_pts
        self.entry_time_domingo = entry_time_domingo
        self.exit_time_lunes = exit_time_lunes
        self._last_signal_week: str | None = None

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        if not history:
            return None
        last_bar = history[-1]
        t_et = _to_et(last_bar.timestamp)

        # La vela que acaba de cerrar define si disparamos
        dow = t_et.weekday()  # domingo = 6, lunes = 0
        cur_time = t_et.time()

        # Ventana de disparo dominical: barra cerrada tras la hora configurada
        if dow == 6 and cur_time >= self.entry_time_domingo:
            week_key = t_et.date().isoformat()
            if self._last_signal_week != week_key:
                self._last_signal_week = week_key
                return Signal(
                    direction="long",
                    entry=0.0,
                    stop=self.sl_pts,
                    target=self.tp_pts,
                    stop_target_as_points=True,
                )
        return None

    def should_exit_by_time(self, current_time: datetime) -> bool:
        """Determina si la posición abierta debe cerrarse por fin de ventana."""
        t_et = _to_et(current_time)
        return t_et.weekday() == 0 and t_et.time() >= self.exit_time_lunes


# =============================================================================
# 2. ESTRATEGIA L0200 (Lunes 02:00 ET -> Lunes 16:00 ET, LucidFlex)
# =============================================================================

class L0200Strategy(Strategy):
    """Estrategia L0200: Entrada lunes madrugada 02:00 ET (apertura europea).

    Variante optimizada de tiempo de exposición: entra el lunes a las 02:00 ET
    para capturar el drift europeo y RTH, cerrando a las 16:00 ET.
    """

    def __init__(
        self,
        market: MarketSpec = MNQ,
        sl_pts: float = D2L_SL_PTS,
        tp_pts: float = D2L_TP_PTS,
        exit_time_lunes: time = HORA_CIERRE_LUNES,
        use_time_exit_only: bool = False,
    ) -> None:
        self.market = market
        self.sl_pts = sl_pts
        self.tp_pts = 999999.0 if use_time_exit_only else tp_pts
        self.exit_time_lunes = exit_time_lunes
        self.use_time_exit_only = use_time_exit_only
        self._last_signal_week: str | None = None

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        if not history:
            return None
        last_bar = history[-1]
        t_et = _to_et(last_bar.timestamp)

        # Condición: lunes a las 01:55 cerrada -> disparo a las 02:00 ET
        dow = t_et.weekday()  # lunes = 0
        cur_time = t_et.time()

        if dow == 0 and cur_time >= time(1, 55) and cur_time < time(2, 5):
            week_key = t_et.date().isoformat()
            if self._last_signal_week != week_key:
                self._last_signal_week = week_key
                return Signal(
                    direction="long",
                    entry=0.0,
                    stop=self.sl_pts,
                    target=self.tp_pts,
                    stop_target_as_points=True,
                )
        return None

    def should_exit_by_time(self, current_time: datetime) -> bool:
        t_et = _to_et(current_time)
        return t_et.weekday() == 0 and t_et.time() >= self.exit_time_lunes


# =============================================================================
# 3. COMPONENTES Y ESTRATEGIA ACT / AC (Confluencia AMD + CRT ± EMA50/4h)
# =============================================================================

def _compute_h4_ema(
    history: Sequence[Bar],
    period: int = DEFAULT_EMA_PERIOD,
    tf_minutes: int = DEFAULT_EMA_TF_MINUTES,
    min_bars: int = 20,
) -> Literal["long", "short"] | None:
    """Calcula el régimen EMA sobre barras 4H completamente cerradas (100% causal).

    Descarta estrictamente la vela 4H en curso (in-progress bucket).
    """
    if period < 1 or tf_minutes < 1:
        return None
    buckets: dict[datetime, float] = {}
    for b in history:
        t_et = _to_et(b.timestamp)
        mins = t_et.hour * 60 + t_et.minute
        bucket_mins = (mins // tf_minutes) * tf_minutes
        b_key = t_et.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=bucket_mins)
        buckets[b_key] = b.close

    if len(buckets) < 2:
        return None

    # Excluir la última barra en curso
    closed_keys = sorted(buckets)[:-1]
    if len(closed_keys) < max(min_bars, period):
        return None

    closes = [buckets[k] for k in closed_keys]
    alpha = 2.0 / (period + 1.0)
    ema_val = closes[0]
    for c in closes[1:]:
        ema_val = ema_val + alpha * (c - ema_val)

    last_close = closes[-1]
    return "long" if last_close > ema_val else "short"


def _compute_rth_atr14(history: Sequence[Bar], period: int = DEFAULT_ATR_PERIOD) -> float | None:
    """Calcula ATR14 sobre barras RTH cerradas mediante media exponencial ponderada."""
    rth_bars = []
    cutoff = _to_et(history[-1].timestamp) - timedelta(days=5)
    for b in reversed(history):
        t_et = _to_et(b.timestamp)
        if t_et < cutoff:
            break
        if time(9, 30) <= t_et.time() <= time(16, 0):
            rth_bars.append(b)
    rth_bars.reverse()

    if len(rth_bars) < period + 2:
        return None

    trs = [rth_bars[0].high - rth_bars[0].low]
    prev_c = rth_bars[0].close
    for b in rth_bars[1:]:
        tr = max(b.high - b.low, abs(b.high - prev_c), abs(b.low - prev_c))
        trs.append(tr)
        prev_c = b.close

    alpha = 1.0 / period
    atr = trs[0]
    for tr in trs[1:]:
        atr = atr + alpha * (tr - atr)
    return atr if atr > 0 else None


class AmdCrtConfluenceStrategy(Strategy):
    """Estrategia de confluencia AMD + CRT con filtro opcional de EMA 4H.

    - use_ema_filter = True, ema_period = 50 -> ACT (Confluencia Triple)
    - use_ema_filter = False                 -> AC (Brazo de control sin EMA)
    - ny_session_only = True                 -> Filtro sesión NY mañana (09:30-12:00 ET)
    """

    def __init__(
        self,
        market: MarketSpec = MNQ,
        use_ema_filter: bool = True,
        ema_period: int = DEFAULT_EMA_PERIOD,
        ema_tf_minutes: int = DEFAULT_EMA_TF_MINUTES,
        sl_cap_pts: float = DEFAULT_SL_CAP_PTS,
        sl_atr_mult: float = DEFAULT_SL_ATR_MULT,
        tp_atr_ratio: float = DEFAULT_TP_ATR_RATIO,
        ny_session_only: bool = False,
        ny_session_end: time = time(12, 0),
    ) -> None:
        self.market = market
        self.use_ema_filter = use_ema_filter
        self.ema_period = ema_period
        self.ema_tf_minutes = ema_tf_minutes
        self.sl_cap_pts = sl_cap_pts
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_ratio = tp_atr_ratio
        self.ny_session_only = ny_session_only
        self.ny_session_end = ny_session_end

        # Estados de sesión
        self._current_day: date | None = None
        self._amd_confirmed: tuple[Literal["long", "short"], datetime] | None = None
        self._crt_confirmed: bool = False
        self._trade_taken_today: bool = False
        self._cached_medians: dict[int, float] = {}

    def _calc_weekday_medians(self, history: Sequence[Bar], today: date) -> dict[int, float]:
        """Calcula medianas históricas de amplitud D1 por día de la semana sin look-ahead."""
        session_amplitudes: dict[int, list[float]] = defaultdict(list)
        day_bars: dict[date, list[Bar]] = defaultdict(list)

        cutoff = today - timedelta(days=DEFAULT_MEDIAN_LOOKBACK * 2)
        for b in history:
            d = _to_et(b.timestamp).date()
            if cutoff <= d < today:
                day_bars[d].append(b)

        for d in sorted(day_bars.keys()):
            bars = day_bars[d]
            if len(bars) >= 30:  # sesión mínimamente representativa
                high = max(b.high for b in bars)
                low = min(b.low for b in bars)
                session_amplitudes[d.weekday()].append(high - low)

        medians = {}
        for wd in sorted(session_amplitudes.keys()):
            amps = session_amplitudes[wd]
            if len(amps) >= DEFAULT_MIN_SAMPLES:
                medians[wd] = float(np.median(amps))
        return medians

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        if not history:
            return None
        last_bar = history[-1]
        t_et = _to_et(last_bar.timestamp)
        today = t_et.date()
        cur_time = t_et.time()

        # Fin de semana o fuera de RTH no opera
        if today.weekday() >= 5 or cur_time >= time(16, 0):
            return None

        # Filtro de horario matutino NY si está activado (Variante V6: solo sesión NY mañana)
        if self.ny_session_only and cur_time >= self.ny_session_end:
            return None

        # Reset diario
        if self._current_day != today:
            self._current_day = today
            self._amd_confirmed = None
            self._crt_confirmed = False
            self._trade_taken_today = False
            self._cached_medians = self._calc_weekday_medians(history, today)

        if self._trade_taken_today:
            return None

        # 1. Detección Causal de AMD
        if self._amd_confirmed is None:
            # Ventana de confirmación: 09:30 a 10:30 ET
            if time(9, 30) <= cur_time < time(10, 30):
                day_bars = [b for b in history if _to_et(b.timestamp).date() == today]
                pre_ny = [b for b in day_bars if time(0, 0) <= _to_et(b.timestamp).time() < time(9, 30)]
                if pre_ny:
                    pre_high = max(b.high for b in pre_ny)
                    pre_low = min(b.low for b in pre_ny)
                    amp = pre_high - pre_low
                    median_amp = self._cached_medians.get(today.weekday(), math.inf)

                    if amp < median_amp:
                        # Rango comprimido, buscar barrido y cierre adentro
                        if last_bar.high > pre_high and last_bar.close < pre_high:
                            self._amd_confirmed = ("short", last_bar.timestamp)
                        elif last_bar.low < pre_low and last_bar.close > pre_low:
                            self._amd_confirmed = ("long", last_bar.timestamp)

        if self._amd_confirmed is None:
            return None

        direction, _ = self._amd_confirmed

        # 2. Detección Causal de CRT (PDH/PDL del RTH anterior)
        if not self._crt_confirmed:
            # Buscar el día anterior de RTH más reciente
            rth_prev = []
            prev_rth_date: date | None = None
            for b in reversed(history):
                bt = _to_et(b.timestamp)
                if bt.date() < today and time(9, 30) <= bt.time() <= time(16, 0):
                    if prev_rth_date is None:
                        prev_rth_date = bt.date()
                    if bt.date() == prev_rth_date:
                        rth_prev.append(b)
                    else:
                        break
                elif prev_rth_date is not None and bt.date() < prev_rth_date:
                    break
                elif bt.date() < today - timedelta(days=14):
                    break
            if rth_prev:
                pdh = max(b.high for b in rth_prev)
                pdl = min(b.low for b in rth_prev)

                # Barrido en RTH de hoy hasta la barra actual
                day_rth = [
                    b for b in history
                    if _to_et(b.timestamp).date() == today and time(9, 30) <= _to_et(b.timestamp).time() <= cur_time
                ]
                for b in day_rth:
                    if direction == "short" and b.high > pdh and b.close < pdh:
                        self._crt_confirmed = True
                        break
                    elif direction == "long" and b.low < pdl and b.close > pdl:
                        self._crt_confirmed = True
                        break

        if not self._crt_confirmed:
            return None

        # 3. Filtro EMA 4H (si aplica)
        if self.use_ema_filter:
            ema_regime = _compute_h4_ema(history, period=self.ema_period, tf_minutes=self.ema_tf_minutes)
            if ema_regime != direction:
                return None

        # 4. Dimensionamiento de Stop Loss y Take Profit
        atr = _compute_rth_atr14(history)
        if atr is None or atr <= 0:
            return None

        sl_pts = min(atr * self.sl_atr_mult, self.sl_cap_pts)
        tp_pts = sl_pts * self.tp_atr_ratio

        self._trade_taken_today = True
        return Signal(
            direction=direction,
            entry=0.0,
            stop=sl_pts,
            target=tp_pts,
            stop_target_as_points=True,
        )


# =============================================================================
# 4. SIMULADOR Y EJECUTOR DE SEÑALES CAUSALES
# =============================================================================

@dataclass
class TradeRecord:
    trade_id: str
    signal_name: str
    direction: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    sl_points: float
    tp_points: float
    pnl_points: float
    gross_pnl_usd: float
    net_pnl_usd: float
    commission_usd: float
    slippage_usd: float
    budget_r: float
    stop_r: float
    exit_reason: str
    year: int


def simulate_signal_causal(
    bars: list[Bar],
    strategy: Strategy,
    signal_name: str,
    commission_per_side: float = 0.62,
    slippage_points: float = 0.25,
    budget_risk_usd: float = 500.0,
    m1_bars: Sequence[Bar] | None = None,
) -> list[TradeRecord]:
    """Ejecuta una estrategia barra a barra con estricta causalidad y fill real."""
    trades: list[TradeRecord] = []
    position: dict[str, Any] | None = None
    dollar_per_point = 2.0  # MNQ
    tick_size = 0.25

    # Indexar M1 si está disponible
    m1_index: dict[datetime, list[Bar]] = defaultdict(list)
    if m1_bars:
        for b in m1_bars:
            m5_key = b.timestamp.replace(minute=(b.timestamp.minute // 5) * 5, second=0, microsecond=0)
            m1_index[m5_key].append(b)

    history: list[Bar] = []
    trade_idx = 0

    for i, bar in enumerate(bars):
        if position is None:
            # Evaluar señal sobre barras ya cerradas
            sig = strategy.evaluate(history) if history else None
            if sig is None:
                history.append(bar)
                continue

            trade_idx += 1
            direction = sig.direction
            # Entrada en la apertura de la barra actual con slippage adverso
            entry_fill = bar.open + slippage_points if direction == "long" else bar.open - slippage_points
            entry_fill = round(entry_fill / tick_size) * tick_size

            sl_pts = sig.stop
            tp_pts = sig.target

            if direction == "long":
                stop_px = entry_fill - sl_pts
                target_px = entry_fill + tp_pts
            else:
                stop_px = entry_fill + sl_pts
                target_px = entry_fill - tp_pts

            position = {
                "id": f"{signal_name}-{trade_idx}",
                "direction": direction,
                "entry_time": bar.timestamp,
                "entry_price": entry_fill,
                "stop_price": stop_px,
                "target_price": target_px,
                "sl_points": sl_pts,
                "tp_points": tp_pts,
                "entry_bar_idx": i,
            }

        # Posición activa (recién abierta o previa): verificar TP, SL, o Salida por Tiempo
        direction = position["direction"]
        stop_px = position["stop_price"]
        target_px = position["target_price"]
        exit_reason: str | None = None
        exit_price: float | None = None

        # Chequeo de salida por tiempo de la estrategia
        time_exit_hook = getattr(strategy, "should_exit_by_time", None)
        if callable(time_exit_hook) and time_exit_hook(bar.timestamp):
            exit_reason = "time_exit"
            exit_price = bar.open - slippage_points if direction == "long" else bar.open + slippage_points

        # Chequeo de TP / SL en la barra actual
        if exit_reason is None:
            hit_tp = bar.high >= target_px if direction == "long" else bar.low <= target_px
            hit_sl = bar.low <= stop_px if direction == "long" else bar.high >= stop_px

            if hit_tp and hit_sl:
                # Ambigüedad intrabarra: intentar resolver con M1
                m5_key = bar.timestamp.replace(minute=(bar.timestamp.minute // 5) * 5, second=0, microsecond=0)
                m1_slice = m1_index.get(m5_key)
                if m1_slice:
                    m1_res, _ = resolve_intrabar_with_m1(direction, stop_px, target_px, m1_slice)
                    if m1_res == "take_profit":
                        exit_reason = "take_profit"
                        exit_price = target_px
                    else:
                        exit_reason = "stop_loss"
                        # Stop fill con slippage si no gapea
                        exit_price = stop_px - slippage_points if direction == "long" else stop_px + slippage_points
                else:
                    # Fallback conservador obligatorio: SL-first
                    exit_reason = "stop_loss"
                    exit_price = stop_px - slippage_points if direction == "long" else stop_px + slippage_points

            elif hit_tp:
                exit_reason = "take_profit"
                exit_price = target_px  # orden límite no sufre slippage adverso
            elif hit_sl:
                exit_reason = "stop_loss"
                exit_price = stop_px - slippage_points if direction == "long" else stop_px + slippage_points

        if exit_reason is not None and exit_price is not None:
            exit_price = round(exit_price / tick_size) * tick_size
            entry_p = position["entry_price"]
            pnl_pts = (exit_price - entry_p) if direction == "long" else (entry_p - exit_price)

            gross_usd = pnl_pts * dollar_per_point
            comm_usd = commission_per_side * 2.0  # round-trip
            slip_usd = (slippage_points * 2.0) * dollar_per_point if exit_reason != "take_profit" else slippage_points * dollar_per_point
            net_usd = gross_usd - comm_usd

            stop_risk_usd = position["sl_points"] * dollar_per_point
            budget_r = net_usd / budget_risk_usd if budget_risk_usd > 0 else 0.0
            stop_r = net_usd / stop_risk_usd if stop_risk_usd > 0 else 0.0

            trades.append(
                TradeRecord(
                    trade_id=position["id"],
                    signal_name=signal_name,
                    direction=direction,
                    entry_time=position["entry_time"],
                    exit_time=bar.timestamp,
                    entry_price=entry_p,
                    exit_price=exit_price,
                    stop_price=stop_px,
                    target_price=target_px,
                    sl_points=position["sl_points"],
                    tp_points=position["tp_points"],
                    pnl_points=round(pnl_pts, 2),
                    gross_pnl_usd=round(gross_usd, 2),
                    net_pnl_usd=round(net_usd, 2),
                    commission_usd=round(comm_usd, 2),
                    slippage_usd=round(slip_usd, 2),
                    budget_r=round(budget_r, 4),
                    stop_r=round(stop_r, 4),
                    exit_reason=exit_reason,
                    year=position["entry_time"].year,
                )
            )
            position = None

        history.append(bar)

    return trades

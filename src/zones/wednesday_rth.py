"""Adaptador causal de barras MNQ M5 RTH hacia WednesdayBias (Bloque W2).

Este módulo procesa incrementalmente barras M5 de futuros MNQ y consolida
sesiones RTH completas [09:30, 16:00) America/New_York. Al recibir la barra
de apertura 09:25 del miércoles cerrada a las 09:30:00 exactas, invoca al
detector causal W1 (detect_wednesday_bias) y emite WednesdayBias si se
cumple la hipótesis semanal.

Garantías de causalidad e integridad:
- Construido exclusivamente para MNQ M5 RTH.
- Validación de grilla completa de 78 slots M5 (09:30 a 15:55 inclusive).
- Cero lookahead: rechazo de barras parciales (available_at < cierre de barra).
- Deduplicación semanal: máximo una emisión por semana ISO y convención session_basis.
- Memoria acotada: no almacena historial ilimitado de barras.
- Paridad exacta entre procesamiento incremental (on_bar) y por lotes (process_bars).
"""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from src.backtest.history import Bar
from src.backtest.markets import MNQ
from src.session_calendar import session_date
from src.zones.wednesday import (
    WeeklySessionSummary,
    WednesdayBias,
    detect_wednesday_bias,
)

NY_TZ = ZoneInfo("America/New_York")
UTC_TZ = timezone.utc

RTH_SESSION_BASIS: str = "MNQ_RTH_0930_1600_NY_M5_v1"

# Verificación de parámetros de mercado canónicos (MarketSpec MNQ)
if MNQ.regular_session_start != time(9, 30) or MNQ.regular_session_end != time(16, 0):
    raise RuntimeError(
        f"MarketSpec MNQ regular session window ({MNQ.regular_session_start}-{MNQ.regular_session_end}) "
        "no coincide con la ventana esperada 09:30-16:00"
    )

# Definición de la grilla canónica RTH (78 slots de 5 minutos: 09:30 a 15:55 inclusive)
RTH_SLOT_TIMES: frozenset[time] = frozenset(
    time(h, m)
    for h in range(9, 16)
    for m in range(0, 60, 5)
    if (h > 9 or (h == 9 and m >= 30)) and h < 16
)
assert len(RTH_SLOT_TIMES) == 78, "La grilla RTH M5 debe tener exactamente 78 slots"

FIRST_RTH_SLOT: time = time(9, 30)
LAST_RTH_SLOT: time = time(15, 55)
TRIGGER_BAR_SLOT: time = time(9, 25)


class WednesdayRthAdapter:
    """Adaptador causal e incremental de barras MNQ M5 RTH hacia WednesdayBias."""

    def __init__(self) -> None:
        # Estado de seguimiento de la última barra procesada
        self._last_bar_ts_utc: datetime | None = None
        self._last_bar_ohlcv: tuple[float, float, float, float, float] | None = None
        self._last_available_at_utc: datetime | None = None

        # Estado de la sesión diaria RTH en curso
        self._current_session_date: date | None = None
        self._current_rth_slots: set[time] = set()
        self._current_high: float | None = None
        self._current_low: float | None = None
        self._current_close: float | None = None

        # Almacenamiento acotado de resúmenes de sesión (máximo 14 días recientes)
        self._session_summaries: dict[date, WeeklySessionSummary] = {}

        # Claves de emisión para deduplicación (iso_year, iso_week, session_basis)
        self._emitted_keys: set[tuple[int, int, str]] = set()

    def get_session_summary(self, d: date) -> WeeklySessionSummary | None:
        """Devuelve el resumen de sesión consolidado para una fecha dada si existe."""
        return self._session_summaries.get(d)

    def on_bar(self, bar: Bar, *, available_at: datetime) -> WednesdayBias | None:
        """Procesa incrementalmente una barra M5 cerrada y evalúa la señal semanal el miércoles a las 09:30 ET.

        Parámetros:
        -----------
        bar : Bar
            Barra M5 con timestamp de APERTURA (debe ser aware y alineada a múltiplos de 5m).
        available_at : datetime
            Instante aware de recepción/consulta. Debe ser >= bar.timestamp + 5 minutos.
        """
        # =====================================================================
        # 1. Validación exhaustiva del input ANTES de mutar estado
        # =====================================================================
        if not isinstance(bar, Bar):
            raise TypeError(f"bar debe ser instancia de Bar (recibido {type(bar).__name__})")

        if bar.timestamp.tzinfo is None or bar.timestamp.tzinfo.utcoffset(bar.timestamp) is None:
            raise ValueError("bar.timestamp debe ser un datetime con zona horaria (aware)")

        if not isinstance(available_at, datetime):
            raise TypeError(f"available_at debe ser datetime (recibido {type(available_at).__name__})")

        if available_at.tzinfo is None or available_at.tzinfo.utcoffset(available_at) is None:
            raise ValueError("available_at debe ser un datetime con zona horaria (aware)")

        # Alineación a M5
        if bar.timestamp.second != 0 or bar.timestamp.microsecond != 0 or bar.timestamp.minute % 5 != 0:
            raise ValueError(
                f"bar.timestamp debe estar alineado a M5 con segundos y microsegundos a cero ({bar.timestamp})"
            )

        # Validación de tipos y valores numéricos en OHLCV (rechaza bool)
        for name, val in [
            ("open", bar.open),
            ("high", bar.high),
            ("low", bar.low),
            ("close", bar.close),
        ]:
            if type(val) is bool:
                raise TypeError(f"Campo {name} no puede ser bool")
            if not isinstance(val, (int, float)):
                raise TypeError(f"Campo {name} debe ser numérico (recibido {type(val).__name__})")
            if not math.isfinite(val):
                raise ValueError(f"Campo {name} debe ser finito (recibido {val})")
            if val <= 0.0:
                raise ValueError(f"Campo {name} debe ser positivo (recibido {val})")

        if type(bar.volume) is bool:
            raise TypeError("Campo volume no puede ser bool")
        if not isinstance(bar.volume, (int, float)):
            raise TypeError(f"Campo volume debe ser numérico (recibido {type(bar.volume).__name__})")
        if not math.isfinite(bar.volume) or bar.volume < 0.0:
            raise ValueError(f"Campo volume debe ser finito y >= 0.0 (recibido {bar.volume})")

        # Invariantes estructurales de precios
        if not (bar.low <= bar.open <= bar.high):
            raise ValueError(
                f"open fuera del rango [low, high]: open={bar.open}, high={bar.high}, low={bar.low}"
            )
        if not (bar.low <= bar.close <= bar.high):
            raise ValueError(
                f"close fuera del rango [low, high]: close={bar.close}, high={bar.high}, low={bar.low}"
            )

        # Causalidad de disponibilidad: la barra M5 cierra 5 minutos después de su timestamp
        bar_ts_utc = bar.timestamp.astimezone(UTC_TZ)
        avail_utc = available_at.astimezone(UTC_TZ)
        bar_close_utc = bar_ts_utc + timedelta(minutes=5)

        if avail_utc < bar_close_utc:
            raise ValueError(
                f"Barra no cerrada: available_at ({available_at}) es anterior al cierre ({bar_close_utc})"
            )

        # Validación de orden temporal y duplicados
        current_ohlcv = (bar.open, bar.high, bar.low, bar.close, bar.volume)
        if self._last_bar_ts_utc is not None:
            if bar_ts_utc < self._last_bar_ts_utc:
                raise ValueError(
                    f"Timestamp de barra decreciente (desorden temporal): actual={bar_ts_utc}, previo={self._last_bar_ts_utc}"
                )
            if self._last_available_at_utc is not None and avail_utc < self._last_available_at_utc:
                raise ValueError(
                    f"available_at decreciente (reloj regresivo): actual={avail_utc}, previo={self._last_available_at_utc}"
                )
            if bar_ts_utc == self._last_bar_ts_utc:
                if current_ohlcv == self._last_bar_ohlcv:
                    # Duplicado exacto idéntico -> no-op
                    return None
                else:
                    raise ValueError(
                        f"Barra duplicada conflictiva para el mismo timestamp {bar_ts_utc} con contenido distinto"
                    )

        # Conversión a tiempo NY para evaluación de ventana y sesión
        dt_ny = bar.timestamp.astimezone(NY_TZ)
        t_ny = dt_ny.time()
        bar_date_ny = dt_ny.date()

        is_rth_slot = t_ny in RTH_SLOT_TIMES

        # Verificación canónica con session_date para barras RTH ANTES de mutar estado
        if is_rth_slot:
            canonical_session = session_date(
                dt_ny,
                tz=str(MNQ.session_timezone),
                reset_hour=17,
            )
            if canonical_session is None or canonical_session != bar_date_ny:
                raise ValueError(
                    f"Barra RTH rechazada por discrepancia con session_date: fecha civil NY={bar_date_ny}, "
                    f"etiqueta canónica={canonical_session}"
                )

        # =====================================================================
        # 2. Mutación de estado de secuencia (validaciones superadas)
        # =====================================================================
        self._last_bar_ts_utc = bar_ts_utc
        self._last_bar_ohlcv = current_ohlcv
        self._last_available_at_utc = avail_utc

        # Rollover de día calendario para la sesión RTH
        if self._current_session_date is None or bar_date_ny != self._current_session_date:
            # Iniciamos seguimiento del nuevo día
            self._current_session_date = bar_date_ny
            self._current_rth_slots = set()
            self._current_high = None
            self._current_low = None
            self._current_close = None

        # =====================================================================
        # 3. Procesamiento de barra RTH
        # =====================================================================
        is_rth_slot = t_ny in RTH_SLOT_TIMES
        if is_rth_slot:
            self._current_rth_slots.add(t_ny)
            if self._current_high is None or bar.high > self._current_high:
                self._current_high = bar.high
            if self._current_low is None or bar.low < self._current_low:
                self._current_low = bar.low
            self._current_close = bar.close

            # ¿Es el último slot de RTH (15:55)?
            if t_ny == LAST_RTH_SLOT:
                # Comprobar si se recibieron los 78 slots exactos
                if len(self._current_rth_slots) == 78 and self._current_rth_slots == RTH_SLOT_TIMES:
                    assert self._current_high is not None and self._current_low is not None
                    summary = WeeklySessionSummary(
                        session_date=self._current_session_date,
                        session_basis=RTH_SESSION_BASIS,
                        high=self._current_high,
                        low=self._current_low,
                        close=bar.close,
                        completed_at=available_at,
                        complete=True,
                    )
                    self._session_summaries[self._current_session_date] = summary
                    # Mantener memoria acotada (conservar solo últimas 2 semanas)
                    cutoff_date = self._current_session_date - timedelta(days=14)
                    self._session_summaries = {
                        d: s for d, s in self._session_summaries.items() if d >= cutoff_date
                    }

        # =====================================================================
        # 4. Evaluación del sesgo el miércoles a las 09:30 ET
        # =====================================================================
        # El gatillo ocurre únicamente cuando se procesa la barra 09:25 disponible a las 09:30:00 exactas
        if dt_ny.weekday() == 2 and t_ny == TRIGGER_BAR_SLOT:
            avail_ny = available_at.astimezone(NY_TZ)
            # Debe estar disponible exactamente a las 09:30:00.000000 ET del mismo miércoles
            is_exact_0930 = (
                avail_ny.weekday() == 2
                and avail_ny.date() == bar_date_ny
                and avail_ny.hour == 9
                and avail_ny.minute == 30
                and avail_ny.second == 0
                and avail_ny.microsecond == 0
            )

            if is_exact_0930:
                iso_year, iso_week, _ = bar_date_ny.isocalendar()
                emission_key = (iso_year, iso_week, RTH_SESSION_BASIS)

                # Deduplicación: máximo una emisión por semana ISO
                if emission_key not in self._emitted_keys:
                    expected_mon = bar_date_ny - timedelta(days=2)
                    expected_tue = bar_date_ny - timedelta(days=1)

                    mon_summary = self._session_summaries.get(expected_mon)
                    tue_summary = self._session_summaries.get(expected_tue)

                    bias = detect_wednesday_bias(
                        monday=mon_summary,
                        tuesday=tue_summary,
                        decision_at=available_at,
                    )

                    if bias is not None:
                        self._emitted_keys.add(emission_key)
                        # Poda de claves emitidas: conserva claves de hasta 2 años ISO (año actual y anterior, <= 106 claves)
                        if len(self._emitted_keys) > 52:
                            self._emitted_keys = {
                                k for k in self._emitted_keys if k[0] >= iso_year - 1
                            }
                        return bias

        return None

    def process_bars(self, items: Iterable[tuple[Bar, datetime]]) -> list[WednesdayBias]:
        """Procesa una secuencia de pares (bar, available_at) con exactitud idéntica al streaming."""
        results: list[WednesdayBias] = []
        for bar, available_at in items:
            bias = self.on_bar(bar, available_at=available_at)
            if bias is not None:
                results.append(bias)
        return results

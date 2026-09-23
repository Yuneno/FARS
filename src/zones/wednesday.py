"""Detector causal del patrón semanal Wednesday (Bloque W1).

Este módulo implementa una función pura y desacoplada para la detección
matemática del sesgo direccional de ciclo semanal (Wednesday Model) a partir
de resúmenes de sesión RTH o D1 consolidados y cerrados.

Invariantes del contrato causal:
- Cero interacción con el motor de ejecución, señales (Signal), órdenes o fills.
- Cero lookahead: solo resúmenes con completed_at <= decision_at.
- Evaluación restringida estrictamente a los miércoles a las 09:30:00.000000 ET.
- Consistencia estricta de convención (session_basis) y semana ISO.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

DECISION_TIMEZONE = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class WeeklySessionSummary:
    """Resumen consolidado de una sesión diaria previo a la evaluación semanal.

    Representa la evidencia inmutable de una sesión completa (Lunes o Martes).
    No realiza agregación de barras M5 ni asume equivalencia entre RTH y D1;
    la convención debe ser declarada explícitamente en session_basis.
    """

    session_date: date
    session_basis: str
    high: float
    low: float
    close: float
    completed_at: datetime
    complete: bool

    def __post_init__(self) -> None:
        # Validación de tipo estricta para session_date (rechaza datetime)
        if not isinstance(self.session_date, date) or isinstance(self.session_date, datetime):
            raise TypeError(
                f"session_date debe ser datetime.date real, no datetime (recibido {type(self.session_date).__name__})"
            )

        # Validación de session_basis
        if not isinstance(self.session_basis, str):
            raise TypeError(
                f"session_basis debe ser str (recibido {type(self.session_basis).__name__})"
            )
        if not self.session_basis.strip():
            raise ValueError("session_basis no puede ser una cadena vacía o en blanco")

        # Validación estricta de bool para complete (rechaza int 1/0 o strings)
        if type(self.complete) is not bool:
            raise TypeError(
                f"complete debe ser un bool estricto (recibido {type(self.complete).__name__})"
            )

        # Validación de precios: rechaza bool, exige finitos y positivos
        for name, val in [("high", self.high), ("low", self.low), ("close", self.close)]:
            if type(val) is bool:
                raise TypeError(f"{name} no puede ser bool")
            if not isinstance(val, (int, float)):
                raise TypeError(f"{name} debe ser numérico (recibido {type(val).__name__})")
            if not math.isfinite(val):
                raise ValueError(f"{name} debe ser un número finito (recibido {val})")
            if val <= 0.0:
                raise ValueError(f"{name} debe ser estrictamente positivo (recibido {val})")

        # Invariantes estructurales de precios
        if self.high < self.low:
            raise ValueError(f"high debe ser >= low (high={self.high}, low={self.low})")
        if not (self.low <= self.close <= self.high):
            raise ValueError(
                f"close debe satisfacer low <= close <= high (low={self.low}, close={self.close}, high={self.high})"
            )

        # Validación de completed_at como datetime aware
        if not isinstance(self.completed_at, datetime):
            raise TypeError(
                f"completed_at debe ser datetime (recibido {type(self.completed_at).__name__})"
            )
        if self.completed_at.tzinfo is None or self.completed_at.tzinfo.utcoffset(self.completed_at) is None:
            raise ValueError("completed_at debe ser un datetime con zona horaria (aware)")


@dataclass(frozen=True)
class WednesdayBias:
    """Sesgo direccional observado para el ciclo semanal del miércoles.

    Contiene únicamente datos estructurales observados. No constituye una orden
    ni señal ejecutable de trading, no define precios con redondeo a tick ni
    autoriza límites de riesgo o tamaño de posición.
    """

    direction: Literal["long", "short"]
    iso_year: int
    iso_week: int
    monday_date: date
    tuesday_date: date
    session_basis: str
    decision_at: datetime
    structural_stop: float


def detect_wednesday_bias(
    monday: WeeklySessionSummary | None,
    tuesday: WeeklySessionSummary | None,
    decision_at: datetime,
) -> WednesdayBias | None:
    """Evalúa de forma pura y causal la presencia de sesgo semanal el miércoles a las 09:30 ET.

    Reglas causales:
    1. decision_at debe ser aware y coincidir exactamente con el miércoles a las
       09:30:00.000000 America/New_York.
    2. monday y tuesday deben existir, estar marcados como complete=True y sus
       marcas completed_at deben ser <= decision_at, con monday.completed_at < tuesday.completed_at.
    3. session_date de monday y tuesday deben ser exactamente el lunes y martes de la
       misma semana ISO que decision_at (según fecha local de Nueva York), y compartir
       el mismo session_basis.
    4. LONG: tuesday.low < monday.low Y monday.low < tuesday.close < monday.high.
       SHORT: tuesday.high > monday.high Y monday.low < tuesday.close < monday.high.
    5. Doble barrido o ningún barrido anulan el sesgo (retorna None).
    6. Igualdades en bordes no constituyen barrido ni cierre interior.
    """
    if not isinstance(decision_at, datetime):
        raise TypeError(f"decision_at debe ser datetime (recibido {type(decision_at).__name__})")
    if decision_at.tzinfo is None or decision_at.tzinfo.utcoffset(decision_at) is None:
        raise ValueError("decision_at debe ser un datetime con zona horaria (aware)")

    # Conversión a huso horario de decisión fijo America/New_York
    dt_ny = decision_at.astimezone(DECISION_TIMEZONE)

    # Solo elegible miércoles (weekday == 2) exactamente a las 09:30:00 sin microsegundos
    if dt_ny.weekday() != 2:
        return None
    if not (dt_ny.hour == 9 and dt_ny.minute == 30 and dt_ny.second == 0 and dt_ny.microsecond == 0):
        return None

    # Verificación de presencia y completitud de resúmenes
    if monday is None or tuesday is None:
        return None
    if not monday.complete or not tuesday.complete:
        return None

    # Causalidad temporal respecto a decision_at
    if monday.completed_at > decision_at or tuesday.completed_at > decision_at:
        return None

    # Orden temporal estricto entre sesiones completadas
    if monday.completed_at >= tuesday.completed_at:
        return None

    # Consistencia de convención de sesión
    if monday.session_basis != tuesday.session_basis:
        return None

    # Consistencia de fechas con la semana ISO de decision_at en NY
    dec_date = dt_ny.date()
    iso_year, iso_week, _ = dec_date.isocalendar()

    expected_mon_date = dec_date - timedelta(days=2)
    expected_tue_date = dec_date - timedelta(days=1)

    if monday.session_date != expected_mon_date or tuesday.session_date != expected_tue_date:
        return None

    # Lógica de barrido y cierre interior
    sweep_low = tuesday.low < monday.low
    sweep_high = tuesday.high > monday.high

    # Si hay doble sweep o ningún sweep -> None
    if (sweep_low and sweep_high) or (not sweep_low and not sweep_high):
        return None

    # Cierre estrictamente interior al rango del lunes
    close_interior = monday.low < tuesday.close < monday.high
    if not close_interior:
        return None

    if sweep_low:
        return WednesdayBias(
            direction="long",
            iso_year=iso_year,
            iso_week=iso_week,
            monday_date=monday.session_date,
            tuesday_date=tuesday.session_date,
            session_basis=monday.session_basis,
            decision_at=decision_at,
            structural_stop=tuesday.low,
        )
    else:
        return WednesdayBias(
            direction="short",
            iso_year=iso_year,
            iso_week=iso_week,
            monday_date=monday.session_date,
            tuesday_date=tuesday.session_date,
            session_basis=monday.session_basis,
            decision_at=decision_at,
            structural_stop=tuesday.high,
        )

"""Pruebas unitarias para el detector causal Wednesday (Bloque W1).

Cubre exhaustivamente:
- Deteccion LONG y SHORT con structural_stop exacto
- Doble sweep y no sweep
- Cierre en bordes o fuera de rango interior
- Igualdad de extremos (no es sweep) e igualdad en lado opuesto con sweep valido
- Resumenes ausentes, incompletos o futuros
- Fechas inconsistentes, semana previa o dias invertidos
- Convencion session_basis discordante
- Orden temporal de completed_at invalido
- Horario de decision fuera de miercoles 09:30:00 ET exacto o con microsegundos
- Datetimes naive en decision_at o completed_at
- Validacion estricta de campos: bool, NaN, inf, negativos, high < low, close fuera
- Fronteras de cambio de ano ISO
- Transiciones de DST (horario de verano / invierno ET)
- Determinismo, repeticion e inmutabilidad (FrozenInstanceError)
"""
import math
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from src.zones.wednesday import (
    WeeklySessionSummary,
    WednesdayBias,
    detect_wednesday_bias,
)

NY_TZ = ZoneInfo("America/New_York")


def _make_summary(
    session_date: date,
    session_basis: str = "RTH",
    high: float = 18100.0,
    low: float = 18000.0,
    close: float = 18050.0,
    completed_at: datetime | None = None,
    complete: bool = True,
) -> WeeklySessionSummary:
    """Helper para construir un WeeklySessionSummary valido por defecto."""
    if completed_at is None:
        completed_at = datetime(
            session_date.year, session_date.month, session_date.day, 16, 0, 0, tzinfo=NY_TZ
        )
    return WeeklySessionSummary(
        session_date=session_date,
        session_basis=session_basis,
        high=high,
        low=low,
        close=close,
        completed_at=completed_at,
        complete=complete,
    )


class TestWednesdayDetectorBasics:
    """Pruebas funcionales basicas de senales LONG, SHORT y neutralidad."""

    def test_valid_long_signal(self):
        """Martes barre Low del Lunes y cierra por dentro -> LONG con stop en low de Martes."""
        mon_date = date(2024, 6, 10)  # Lunes
        tue_date = date(2024, 6, 11)  # Martes
        wed_dt = datetime(2024, 6, 12, 9, 30, 0, tzinfo=NY_TZ)  # Miercoles 09:30:00 ET

        monday = _make_summary(mon_date, high=18100.0, low=18000.0, close=18050.0)
        # Low de martes (17950) < 18000, High de martes (18080) <= 18100, Close (18020) interior
        tuesday = _make_summary(tue_date, high=18080.0, low=17950.0, close=18020.0)

        bias = detect_wednesday_bias(monday, tuesday, decision_at=wed_dt)

        assert bias is not None
        assert bias.direction == "long"
        assert bias.iso_year == 2024
        assert bias.iso_week == 24
        assert bias.monday_date == mon_date
        assert bias.tuesday_date == tue_date
        assert bias.session_basis == "RTH"
        assert bias.decision_at == wed_dt
        assert bias.structural_stop == 17950.0

    def test_valid_short_signal(self):
        """Martes barre High del Lunes y cierra por dentro -> SHORT con stop en high de Martes."""
        mon_date = date(2024, 6, 10)
        tue_date = date(2024, 6, 11)
        wed_dt = datetime(2024, 6, 12, 9, 30, 0, tzinfo=NY_TZ)

        monday = _make_summary(mon_date, high=18100.0, low=18000.0, close=18050.0)
        # High de martes (18150) > 18100, Low de martes (18020) >= 18000, Close (18080) interior
        tuesday = _make_summary(tue_date, high=18150.0, low=18020.0, close=18080.0)

        bias = detect_wednesday_bias(monday, tuesday, decision_at=wed_dt)

        assert bias is not None
        assert bias.direction == "short"
        assert bias.iso_year == 2024
        assert bias.iso_week == 24
        assert bias.monday_date == mon_date
        assert bias.tuesday_date == tue_date
        assert bias.session_basis == "RTH"
        assert bias.decision_at == wed_dt
        assert bias.structural_stop == 18150.0

    def test_double_sweep_returns_none(self):
        """Si Martes barre tanto High como Low del Lunes, la senal se anula."""
        mon_date = date(2024, 6, 10)
        tue_date = date(2024, 6, 11)
        wed_dt = datetime(2024, 6, 12, 9, 30, 0, tzinfo=NY_TZ)

        monday = _make_summary(mon_date, high=18100.0, low=18000.0, close=18050.0)
        tuesday = _make_summary(tue_date, high=18150.0, low=17950.0, close=18050.0)

        bias = detect_wednesday_bias(monday, tuesday, decision_at=wed_dt)
        assert bias is None

    def test_no_sweep_returns_none(self):
        """Si Martes permanece dentro del rango de Lunes (inside day), no hay senal."""
        mon_date = date(2024, 6, 10)
        tue_date = date(2024, 6, 11)
        wed_dt = datetime(2024, 6, 12, 9, 30, 0, tzinfo=NY_TZ)

        monday = _make_summary(mon_date, high=18100.0, low=18000.0, close=18050.0)
        tuesday = _make_summary(tue_date, high=18080.0, low=18020.0, close=18050.0)

        bias = detect_wednesday_bias(monday, tuesday, decision_at=wed_dt)
        assert bias is None


class TestWednesdayBoundaryAndEquality:
    """Casos frontera de igualdad estricta y cierres interiores."""

    def test_equality_of_borders_is_not_sweep(self):
        """Igualdad de Low o High no cuenta como barrido."""
        mon_date = date(2024, 6, 10)
        tue_date = date(2024, 6, 11)
        wed_dt = datetime(2024, 6, 12, 9, 30, 0, tzinfo=NY_TZ)

        monday = _make_summary(mon_date, high=18100.0, low=18000.0, close=18050.0)
        # Igualdad exacta en low (18000.0 == 18000.0) no es sweep
        tuesday_eq_low = _make_summary(tue_date, high=18080.0, low=18000.0, close=18050.0)
        assert detect_wednesday_bias(monday, tuesday_eq_low, decision_at=wed_dt) is None

        # Igualdad exacta en high (18100.0 == 18100.0) no es sweep
        tuesday_eq_high = _make_summary(tue_date, high=18100.0, low=18020.0, close=18050.0)
        assert detect_wednesday_bias(monday, tuesday_eq_high, decision_at=wed_dt) is None

    def test_opposite_border_equality_does_not_veto_valid_sweep(self):
        """Igualdad en el extremo opuesto no barrido NO veta un barrido valido."""
        mon_date = date(2024, 6, 10)
        tue_date = date(2024, 6, 11)
        wed_dt = datetime(2024, 6, 12, 9, 30, 0, tzinfo=NY_TZ)

        monday = _make_summary(mon_date, high=18100.0, low=18000.0, close=18050.0)

        # Long: low barrido (17950 < 18000), high exactamente igual (18100 == 18100, no es sweep)
        tue_long = _make_summary(tue_date, high=18100.0, low=17950.0, close=18050.0)
        bias_long = detect_wednesday_bias(monday, tue_long, decision_at=wed_dt)
        assert bias_long is not None
        assert bias_long.direction == "long"

        # Short: high barrido (18150 > 18100), low exactamente igual (18000 == 18000, no es sweep)
        tue_short = _make_summary(tue_date, high=18150.0, low=18000.0, close=18050.0)
        bias_short = detect_wednesday_bias(monday, tue_short, decision_at=wed_dt)
        assert bias_short is not None
        assert bias_short.direction == "short"

    def test_close_on_borders_is_not_interior(self):
        """Cierre exactamente en el borde del lunes no es estrictamente interior."""
        mon_date = date(2024, 6, 10)
        tue_date = date(2024, 6, 11)
        wed_dt = datetime(2024, 6, 12, 9, 30, 0, tzinfo=NY_TZ)

        monday = _make_summary(mon_date, high=18100.0, low=18000.0, close=18050.0)

        # Cierre en monday.low
        tue_close_low = _make_summary(tue_date, high=18080.0, low=17950.0, close=18000.0)
        assert detect_wednesday_bias(monday, tue_close_low, decision_at=wed_dt) is None

        # Cierre en monday.high (18100.0) con low barrido (17950 < 18000) y high = 18100.0
        tue_close_high = _make_summary(tue_date, high=18100.0, low=17950.0, close=18100.0)
        assert detect_wednesday_bias(monday, tue_close_high, decision_at=wed_dt) is None

    def test_close_outside_monday_range_returns_none(self):
        """Cierre fuera del rango del lunes no valida la absorcion/manipulacion."""
        mon_date = date(2024, 6, 10)
        tue_date = date(2024, 6, 11)
        wed_dt = datetime(2024, 6, 12, 9, 30, 0, tzinfo=NY_TZ)

        monday = _make_summary(mon_date, high=18100.0, low=18000.0, close=18050.0)

        # Cierre por debajo de monday.low
        tue_close_below = _make_summary(tue_date, high=18080.0, low=17950.0, close=17980.0)
        assert detect_wednesday_bias(monday, tue_close_below, decision_at=wed_dt) is None

        # Cierre por encima de monday.high
        tue_close_above = _make_summary(tue_date, high=18150.0, low=18020.0, close=18120.0)
        assert detect_wednesday_bias(monday, tue_close_above, decision_at=wed_dt) is None


class TestTemporalAndSessionConstraints:
    """Verificacion de fechas, calendarios, orden temporal y festivos/ausencias."""

    def test_missing_summaries_return_none(self):
        """Si falta monday o tuesday, retorna None."""
        wed_dt = datetime(2024, 6, 12, 9, 30, 0, tzinfo=NY_TZ)
        summary = _make_summary(date(2024, 6, 10))

        assert detect_wednesday_bias(None, summary, decision_at=wed_dt) is None
        assert detect_wednesday_bias(summary, None, decision_at=wed_dt) is None
        assert detect_wednesday_bias(None, None, decision_at=wed_dt) is None

    def test_incomplete_sessions_return_none(self):
        """Si complete es False en cualquiera de los dos, retorna None."""
        mon_date = date(2024, 6, 10)
        tue_date = date(2024, 6, 11)
        wed_dt = datetime(2024, 6, 12, 9, 30, 0, tzinfo=NY_TZ)

        monday_inc = _make_summary(mon_date, complete=False)
        tuesday = _make_summary(tue_date, high=18080.0, low=17950.0, close=18020.0)
        assert detect_wednesday_bias(monday_inc, tuesday, decision_at=wed_dt) is None

        monday = _make_summary(mon_date)
        tuesday_inc = _make_summary(tue_date, high=18080.0, low=17950.0, close=18020.0, complete=False)
        assert detect_wednesday_bias(monday, tuesday_inc, decision_at=wed_dt) is None

    def test_future_completed_at_returns_none(self):
        """Si completed_at > decision_at, hay fuga temporal -> retorna None."""
        mon_date = date(2024, 6, 10)
        tue_date = date(2024, 6, 11)
        wed_dt = datetime(2024, 6, 12, 9, 30, 0, tzinfo=NY_TZ)

        monday = _make_summary(mon_date)
        # Martes completado a las 10:00 ET del miercoles (en el futuro de decision_at)
        tuesday_future = _make_summary(
            tue_date,
            high=18080.0,
            low=17950.0,
            close=18020.0,
            completed_at=datetime(2024, 6, 12, 10, 0, 0, tzinfo=NY_TZ),
        )
        assert detect_wednesday_bias(monday, tuesday_future, decision_at=wed_dt) is None

    def test_unordered_completed_at_returns_none(self):
        """Si lunes completed_at >= martes completed_at (orden imposible) -> retorna None."""
        mon_date = date(2024, 6, 10)
        tue_date = date(2024, 6, 11)
        wed_dt = datetime(2024, 6, 12, 9, 30, 0, tzinfo=NY_TZ)

        # Lunes completado despues del martes
        monday = _make_summary(
            mon_date, completed_at=datetime(2024, 6, 11, 17, 0, 0, tzinfo=NY_TZ)
        )
        tuesday = _make_summary(
            tue_date,
            high=18080.0,
            low=17950.0,
            close=18020.0,
            completed_at=datetime(2024, 6, 11, 16, 0, 0, tzinfo=NY_TZ),
        )
        assert detect_wednesday_bias(monday, tuesday, decision_at=wed_dt) is None

    def test_mismatched_session_basis_returns_none(self):
        """Si session_basis difiere entre lunes y martes, retorna None."""
        mon_date = date(2024, 6, 10)
        tue_date = date(2024, 6, 11)
        wed_dt = datetime(2024, 6, 12, 9, 30, 0, tzinfo=NY_TZ)

        monday = _make_summary(mon_date, session_basis="RTH")
        tuesday = _make_summary(
            tue_date, session_basis="ETH", high=18080.0, low=17950.0, close=18020.0
        )
        assert detect_wednesday_bias(monday, tuesday, decision_at=wed_dt) is None

    def test_dates_not_matching_iso_week_returns_none(self):
        """Si session_date no corresponde exactamente al lunes y martes de la semana de decision_at."""
        wed_dt = datetime(2024, 6, 12, 9, 30, 0, tzinfo=NY_TZ)  # Semana 24 (lunes 10, martes 11)

        # Lunes de la semana anterior (2024-06-03)
        monday_prev = _make_summary(date(2024, 6, 3))
        tuesday = _make_summary(date(2024, 6, 11), high=18080.0, low=17950.0, close=18020.0)
        assert detect_wednesday_bias(monday_prev, tuesday, decision_at=wed_dt) is None

        # Fechas invertidas
        monday_inv = _make_summary(date(2024, 6, 11))
        tuesday_inv = _make_summary(date(2024, 6, 10), high=18080.0, low=17950.0, close=18020.0)
        assert detect_wednesday_bias(monday_inv, tuesday_inv, decision_at=wed_dt) is None

    def test_decision_timing_must_be_exact_wednesday_0930(self):
        """Solo es elegible exactamente a las 09:30:00.000000 ET de un miercoles."""
        mon_date = date(2024, 6, 10)
        tue_date = date(2024, 6, 11)
        monday = _make_summary(mon_date)
        tuesday = _make_summary(tue_date, high=18080.0, low=17950.0, close=18020.0)

        # Miercoles 09:30:01 (1 segundo tarde)
        dt_late = datetime(2024, 6, 12, 9, 30, 1, tzinfo=NY_TZ)
        assert detect_wednesday_bias(monday, tuesday, decision_at=dt_late) is None

        # Miercoles 09:29:59 (1 segundo temprano)
        dt_early = datetime(2024, 6, 12, 9, 29, 59, tzinfo=NY_TZ)
        assert detect_wednesday_bias(monday, tuesday, decision_at=dt_early) is None

        # Miercoles 09:30:00 con microsegundos
        dt_micro = datetime(2024, 6, 12, 9, 30, 0, microsecond=500, tzinfo=NY_TZ)
        assert detect_wednesday_bias(monday, tuesday, decision_at=dt_micro) is None

        # Martes 09:30:00 (dia incorrecto)
        dt_tue = datetime(2024, 6, 11, 9, 30, 0, tzinfo=NY_TZ)
        assert detect_wednesday_bias(monday, tuesday, decision_at=dt_tue) is None

        # Jueves 09:30:00 (dia incorrecto)
        dt_thu = datetime(2024, 6, 13, 9, 30, 0, tzinfo=NY_TZ)
        assert detect_wednesday_bias(monday, tuesday, decision_at=dt_thu) is None


class TestInputValidationAndEdgeCases:
    """Validacion estricta de tipos, invariantes numericos y robustez temporal."""

    def test_naive_datetime_raises(self):
        """Datetimes naive en decision_at o completed_at deben levantar ValueError."""
        mon_date = date(2024, 6, 10)
        tue_date = date(2024, 6, 11)

        # completed_at naive en summary
        with pytest.raises((ValueError, TypeError)):
            WeeklySessionSummary(
                session_date=mon_date,
                session_basis="RTH",
                high=18100.0,
                low=18000.0,
                close=18050.0,
                completed_at=datetime(2024, 6, 10, 16, 0, 0),  # naive
                complete=True,
            )

        # decision_at naive en detector
        monday = _make_summary(mon_date)
        tuesday = _make_summary(tue_date, high=18080.0, low=17950.0, close=18020.0)
        with pytest.raises((ValueError, TypeError)):
            detect_wednesday_bias(
                monday, tuesday, decision_at=datetime(2024, 6, 12, 9, 30, 0)  # naive
            )

    def test_session_date_must_be_real_date_not_datetime(self):
        """session_date no debe ser datetime (que hereda de date)."""
        dt_as_date = datetime(2024, 6, 10, 0, 0, 0)
        with pytest.raises((ValueError, TypeError)):
            WeeklySessionSummary(
                session_date=dt_as_date,  # type: ignore
                session_basis="RTH",
                high=18100.0,
                low=18000.0,
                close=18050.0,
                completed_at=datetime(2024, 6, 10, 16, 0, 0, tzinfo=NY_TZ),
                complete=True,
            )

    def test_empty_or_invalid_session_basis_raises(self):
        """session_basis vacio o no-string debe levantar ValueError/TypeError."""
        mon_date = date(2024, 6, 10)
        with pytest.raises((ValueError, TypeError)):
            _make_summary(mon_date, session_basis="")
        with pytest.raises((ValueError, TypeError)):
            _make_summary(mon_date, session_basis="   ")
        with pytest.raises((ValueError, TypeError)):
            _make_summary(mon_date, session_basis=123)  # type: ignore

    def test_strict_bool_for_complete_raises(self):
        """complete debe ser bool estricto, no int 1 o 0."""
        mon_date = date(2024, 6, 10)
        with pytest.raises((ValueError, TypeError)):
            _make_summary(mon_date, complete=1)  # type: ignore
        with pytest.raises((ValueError, TypeError)):
            _make_summary(mon_date, complete="True")  # type: ignore

    def test_reject_bool_in_prices(self):
        """Rechazar bool en high/low/close (ya que en Python bool es subclase de int)."""
        mon_date = date(2024, 6, 10)
        with pytest.raises((ValueError, TypeError)):
            _make_summary(mon_date, high=True)  # type: ignore
        with pytest.raises((ValueError, TypeError)):
            _make_summary(mon_date, low=False)  # type: ignore
        with pytest.raises((ValueError, TypeError)):
            _make_summary(mon_date, close=True)  # type: ignore

    def test_nan_and_inf_prices_raise(self):
        """Precios NaN o infinitos levantan ValueError."""
        mon_date = date(2024, 6, 10)
        with pytest.raises((ValueError, TypeError)):
            _make_summary(mon_date, high=float("nan"))
        with pytest.raises((ValueError, TypeError)):
            _make_summary(mon_date, low=float("-inf"))
        with pytest.raises((ValueError, TypeError)):
            _make_summary(mon_date, close=float("inf"))

    def test_negative_or_zero_prices_raise(self):
        """Precios menores o iguales a cero levantan ValueError."""
        mon_date = date(2024, 6, 10)
        with pytest.raises(ValueError):
            _make_summary(mon_date, high=0.0)
        with pytest.raises(ValueError):
            _make_summary(mon_date, low=-50.0)

    def test_price_invariants_raise(self):
        """high >= low y low <= close <= high deben cumplirse estrictamente."""
        mon_date = date(2024, 6, 10)
        # high < low
        with pytest.raises(ValueError):
            _make_summary(mon_date, high=17900.0, low=18000.0, close=17950.0)
        # close < low
        with pytest.raises(ValueError):
            _make_summary(mon_date, high=18100.0, low=18000.0, close=17990.0)
        # close > high
        with pytest.raises(ValueError):
            _make_summary(mon_date, high=18100.0, low=18000.0, close=18150.0)

    def test_iso_new_year_boundary(self):
        """Semana de cambio de ano calendario con semana ISO compartida."""
        # En 2024-2025: Lunes 2024-12-30, Martes 2024-12-31, Miercoles 2025-01-01
        # Todos pertenecen a la semana ISO 2025-W01.
        mon_date = date(2024, 12, 30)
        tue_date = date(2024, 12, 31)
        wed_dt = datetime(2025, 1, 1, 9, 30, 0, tzinfo=NY_TZ)

        monday = _make_summary(mon_date, high=21000.0, low=20800.0, close=20900.0)
        tuesday = _make_summary(tue_date, high=20950.0, low=20750.0, close=20850.0)

        bias = detect_wednesday_bias(monday, tuesday, decision_at=wed_dt)
        assert bias is not None
        assert bias.direction == "long"
        assert bias.iso_year == 2025
        assert bias.iso_week == 1
        assert bias.monday_date == mon_date
        assert bias.tuesday_date == tue_date

    def test_dst_transitions(self):
        """Verificar consistencia a ambos lados de las transiciones de DST."""
        # Semana en horario de invierno EST (Noviembre 2024)
        mon_est = date(2024, 11, 11)
        tue_est = date(2024, 11, 12)
        wed_est = datetime(2024, 11, 13, 9, 30, 0, tzinfo=NY_TZ)
        m_est = _make_summary(mon_est, high=20000.0, low=19800.0, close=19900.0)
        t_est = _make_summary(tue_est, high=19950.0, low=19700.0, close=19850.0)
        b_est = detect_wednesday_bias(m_est, t_est, decision_at=wed_est)
        assert b_est is not None
        assert b_est.direction == "long"

        # Semana en horario de verano EDT (Julio 2024)
        mon_edt = date(2024, 7, 8)
        tue_edt = date(2024, 7, 9)
        wed_edt = datetime(2024, 7, 10, 9, 30, 0, tzinfo=NY_TZ)
        m_edt = _make_summary(mon_edt, high=20000.0, low=19800.0, close=19900.0)
        t_edt = _make_summary(tue_edt, high=20100.0, low=19850.0, close=19950.0)
        b_edt = detect_wednesday_bias(m_edt, t_edt, decision_at=wed_edt)
        assert b_edt is not None
        assert b_edt.direction == "short"

    def test_determinism_and_repetition(self):
        """Invocaciones repetidas con identicos inputs generan salidas identicas."""
        mon_date = date(2024, 6, 10)
        tue_date = date(2024, 6, 11)
        wed_dt = datetime(2024, 6, 12, 9, 30, 0, tzinfo=NY_TZ)

        monday = _make_summary(mon_date, high=18100.0, low=18000.0, close=18050.0)
        tuesday = _make_summary(tue_date, high=18080.0, low=17950.0, close=18020.0)

        bias1 = detect_wednesday_bias(monday, tuesday, decision_at=wed_dt)
        bias2 = detect_wednesday_bias(monday, tuesday, decision_at=wed_dt)

        assert bias1 == bias2
        assert hash(bias1) == hash(bias2)

    def test_immutability(self):
        """WeeklySessionSummary y WednesdayBias deben ser inmutables (frozen)."""
        mon_date = date(2024, 6, 10)
        tue_date = date(2024, 6, 11)
        wed_dt = datetime(2024, 6, 12, 9, 30, 0, tzinfo=NY_TZ)

        monday = _make_summary(mon_date)
        with pytest.raises(FrozenInstanceError):
            monday.high = 99999.0  # type: ignore

        tuesday = _make_summary(tue_date, high=18080.0, low=17950.0, close=18020.0)
        bias = detect_wednesday_bias(monday, tuesday, decision_at=wed_dt)
        assert bias is not None

        with pytest.raises(FrozenInstanceError):
            bias.direction = "short"  # type: ignore
        with pytest.raises(FrozenInstanceError):
            bias.structural_stop = 0.0  # type: ignore

    def test_boundary_completed_at_equal_to_decision_at(self):
        """completed_at exactamente igual a decision_at es valido (condicion <=)."""
        mon_date = date(2024, 6, 10)
        tue_date = date(2024, 6, 11)
        wed_dt = datetime(2024, 6, 12, 9, 30, 0, tzinfo=NY_TZ)

        monday = _make_summary(mon_date, completed_at=datetime(2024, 6, 10, 16, 0, 0, tzinfo=NY_TZ))
        tuesday = _make_summary(
            tue_date,
            high=18080.0,
            low=17950.0,
            close=18020.0,
            completed_at=wed_dt,  # exactamente igual a decision_at
        )
        bias = detect_wednesday_bias(monday, tuesday, decision_at=wed_dt)
        assert bias is not None
        assert bias.direction == "long"

    def test_decision_at_in_utc_aware(self):
        """decision_at provisto en UTC (13:30:00 UTC en horario EDT) se evalua correctamente."""
        mon_date = date(2024, 6, 10)
        tue_date = date(2024, 6, 11)
        # 09:30:00 EDT = 13:30:00 UTC
        wed_dt_utc = datetime(2024, 6, 12, 13, 30, 0, tzinfo=timezone.utc)

        monday = _make_summary(mon_date)
        tuesday = _make_summary(tue_date, high=18080.0, low=17950.0, close=18020.0)

        bias = detect_wednesday_bias(monday, tuesday, decision_at=wed_dt_utc)
        assert bias is not None
        assert bias.direction == "long"
        assert bias.decision_at == wed_dt_utc

    def test_no_lookahead_resilience(self):
        """Verificacion de no-lookahead: cualquier resumen posterior a decision_at anula la senal."""
        mon_date = date(2024, 6, 10)
        tue_date = date(2024, 6, 11)
        wed_dt = datetime(2024, 6, 12, 9, 30, 0, tzinfo=NY_TZ)

        monday = _make_summary(mon_date)
        # Martes se cierra 1 microsegundo despues de decision_at
        tuesday_future = _make_summary(
            tue_date,
            high=18080.0,
            low=17950.0,
            close=18020.0,
            completed_at=wed_dt + timedelta(microseconds=1),
        )
        assert detect_wednesday_bias(monday, tuesday_future, decision_at=wed_dt) is None

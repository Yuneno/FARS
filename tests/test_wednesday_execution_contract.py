"""Pruebas de caracterizacion del contrato de ejecucion para el Wednesday Model (Bloque W3).

Este modulo evalua de forma estricta el comportamiento REAL del motor de backtest
de FARS (src/backtest/executor.py) ante las exigencias del contrato Wednesday:
1. Conflicto de Signal:
   - Modo precios absolutos (stop_target_as_points=False) fija el stop estructural pero
     desvia el target de 1.5R real ante slippage o gap de apertura.
   - Modo distancias (stop_target_as_points=True) mantiene 1.5R pero desplaza el stop
     estructural lejos del minimo/maximo del martes por el importe del slippage/gap.
2. Continuidad y rechazo por gap: Si la barra 09:30 falta, no hay fill tardio en 09:35.
3. Invalidez de niveles: Si el fill cruza el stop estructural, el motor rechaza la entrada.
4. Salida temporal (16:00 ET):
   - Barra 16:00 presente: salida por time_exit al open de las 16:00.
   - Barra 16:00 ausente con barra posterior (16:05): salida tardia en 16:05 sin inventar precio 16:00.
   - Dataset truncado antes de 16:00:
     * Modo absoluto sufre cierre forzado legacy pese a end_of_data_policy="unresolved".
     * Modo distancia preserva open_position sin forzar cierre.
5. Causalidad en evaluate: la barra de fill nunca esta visible en el historial evaluado.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.backtest.executor import BacktestConfig, run_backtest
from src.backtest.history import Bar
from src.backtest.markets import MNQ
from src.backtest.strategy import Signal, Strategy

NY_TZ = ZoneInfo("America/New_York")


def _make_bar(
    dt: datetime,
    open_: float = 18300.0,
    high: float = 18310.0,
    low: float = 18290.0,
    close: float = 18305.0,
    volume: float = 100.0,
) -> Bar:
    """Helper para crear barras sinteticas con timestamp aware."""
    return Bar(
        timestamp=dt,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


class _MockWednesdayStrategy:
    """Fixture de estrategia minima para emitir una Signal al cerrar la barra de 09:25."""

    def __init__(self, signal: Signal, trigger_dt: datetime) -> None:
        self.signal = signal
        self.trigger_dt = trigger_dt
        self.history_snapshots: list[list[Bar]] = []

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        if not history:
            return None
        self.history_snapshots.append(list(history))
        if history[-1].timestamp == self.trigger_dt:
            return self.signal
        return None


def _build_test_bars(
    wed_date: date,
    open_0930: float = 18300.0,
    include_0930: bool = True,
    bars_until: datetime | None = None,
    interim_high: float = 18350.0,
    interim_low: float = 18250.0,
) -> list[Bar]:
    """Construye secuencia sintetica de barras M5 para el miercoles."""
    bars: list[Bar] = []
    # Pre-market desde 09:00 hasta 09:25 inclusive (6 barras)
    for m in range(0, 30, 5):
        dt = datetime(wed_date.year, wed_date.month, wed_date.day, 9, m, 0, tzinfo=NY_TZ)
        bars.append(_make_bar(dt, open_=18300.0, high=18305.0, low=18295.0, close=18300.0))

    if not include_0930:
        # Omitimos la barra 09:30; siguiente barra es 09:35
        start_m = 35
    else:
        start_m = 30

    end_dt = bars_until or datetime(wed_date.year, wed_date.month, wed_date.day, 16, 5, 0, tzinfo=NY_TZ)

    # Barras de sesion hasta end_dt
    curr = datetime(wed_date.year, wed_date.month, wed_date.day, 9, start_m, 0, tzinfo=NY_TZ)
    while curr <= end_dt:
        o = open_0930 if curr.time().minute == 30 and curr.time().hour == 9 else 18300.0
        h = max(o + 5.0, interim_high) if curr.time().hour == 11 else o + 5.0
        l = min(o - 5.0, interim_low) if curr.time().hour == 11 else o - 5.0
        c = o
        bars.append(_make_bar(curr, open_=o, high=h, low=l, close=c))
        curr += timedelta(minutes=5)

    return bars


class TestWednesdaySignalConventionConflict:
    """Caracterizacion del conflicto de Signal: precios absolutos vs distancias relativas."""

    @pytest.mark.parametrize("direction", ["long", "short"])
    def test_absolute_mode_preserves_stop_but_drifts_target_rr(self, direction: str):
        """En modo absoluto, slippage de apertura altera el ratio R del target proyectado."""
        wed_date = date(2024, 6, 12)
        trigger_dt = datetime(2024, 6, 12, 9, 25, 0, tzinfo=NY_TZ)

        # Parametros de la anomalia
        tue_structural_stop = 18200.0 if direction == "long" else 18400.0
        expected_open = 18300.0
        nominal_risk_pts = abs(expected_open - tue_structural_stop)  # 100.0 pts
        target_pts = nominal_risk_pts * 1.5  # 150.0 pts
        absolute_target = expected_open + target_pts if direction == "long" else expected_open - target_pts

        # Signal con precios absolutos (stop_target_as_points=False)
        signal = Signal(
            direction=direction,
            entry=expected_open,
            stop=tue_structural_stop,
            target=absolute_target,
            stop_target_as_points=False,
        )
        strategy = _MockWednesdayStrategy(signal, trigger_dt)

        config = BacktestConfig(
            tick_size=0.25,
            dollar_per_point=2.0,
            slippage_points=0.50,  # 2 ticks de slippage adverso
            commission_per_side=0.62,
            bar_interval_seconds=300,
            max_hold_minutes=390.0,
        )

        bars = _build_test_bars(wed_date, open_0930=18300.0)
        result = run_backtest(bars, strategy, config)

        # La entrada sufre slippage adverso
        expected_fill = 18300.50 if direction == "long" else 18299.50

        # Verificamos que el fill ocurrio
        assert len(result.trades) > 0 or result.open_position is not None

        # Posicion creada en el motor:
        # Verificamos en el trade completado por time_exit a las 16:00
        trade = result.trades[0]
        assert trade.entry_price == expected_fill

        # En modo absoluto, el stop_price se conserva exactamente en el nivel estructural del martes
        assert trade.stop_price == tue_structural_stop

        # Sin embargo, el target efectivo NO conserva la relacion 1.5R respecto al fill real
        effective_risk = abs(expected_fill - trade.stop_price)  # 100.50 pts
        effective_reward = abs(trade.target_price - expected_fill)  # 149.50 pts
        real_rr = effective_reward / effective_risk

        # Demostracion mediante assertion: el ratio real difiere de 1.50R
        assert real_rr != 1.50
        assert round(real_rr, 5) == round(149.50 / 100.50, 5)  # ~1.48756R

    @pytest.mark.parametrize("direction", ["long", "short"])
    def test_distance_mode_preserves_target_rr_but_displaces_structural_stop(self, direction: str):
        """En modo puntos/distancias, slippage de apertura desplaza el stop estructural objetivo."""
        wed_date = date(2024, 6, 12)
        trigger_dt = datetime(2024, 6, 12, 9, 25, 0, tzinfo=NY_TZ)

        tue_structural_stop = 18200.0 if direction == "long" else 18400.0
        expected_open = 18300.0
        nominal_risk_pts = abs(expected_open - tue_structural_stop)  # 100.0 pts
        target_pts = nominal_risk_pts * 1.5  # 150.0 pts

        # Signal con distancias en puntos (stop_target_as_points=True)
        signal = Signal(
            direction=direction,
            entry=expected_open,
            stop=nominal_risk_pts,  # 100.0 pts de distancia
            target=target_pts,      # 150.0 pts de distancia
            stop_target_as_points=True,
        )
        strategy = _MockWednesdayStrategy(signal, trigger_dt)

        config = BacktestConfig(
            tick_size=0.25,
            dollar_per_point=2.0,
            slippage_points=0.50,  # 2 ticks de slippage adverso
            commission_per_side=0.62,
            bar_interval_seconds=300,
            max_hold_minutes=390.0,
        )

        bars = _build_test_bars(wed_date, open_0930=18300.0)
        result = run_backtest(bars, strategy, config)

        trade = result.trades[0]
        expected_fill = 18300.50 if direction == "long" else 18299.50
        assert trade.entry_price == expected_fill

        # En modo distancia, el target si conserva exactamente 1.5R respecto al fill
        effective_risk = abs(trade.entry_price - trade.stop_price)  # 100.0 pts
        effective_reward = abs(trade.target_price - trade.entry_price)  # 150.0 pts
        assert effective_reward / effective_risk == 1.50

        # PERO el stop estructural fue desplazado por el importe del slippage
        assert trade.stop_price != tue_structural_stop
        displaced_expected = 18200.50 if direction == "long" else 18399.50
        assert trade.stop_price == displaced_expected

    def test_open_gap_magnifies_target_rr_distortion_in_absolute_mode(self):
        """Un gap de apertura (ej. apertura 20 pts arriba) distorsiona severamente el target en modo absoluto."""
        wed_date = date(2024, 6, 12)
        trigger_dt = datetime(2024, 6, 12, 9, 25, 0, tzinfo=NY_TZ)

        tue_low = 18200.0
        expected_open = 18300.0
        absolute_target = 18450.0  # Proyectado para 18300

        signal = Signal(
            direction="long",
            entry=expected_open,
            stop=tue_low,
            target=absolute_target,
            stop_target_as_points=False,
        )
        strategy = _MockWednesdayStrategy(signal, trigger_dt)

        config = BacktestConfig(
            tick_size=0.25,
            dollar_per_point=2.0,
            slippage_points=0.50,
            commission_per_side=0.62,
            bar_interval_seconds=300,
            max_hold_minutes=390.0,
        )

        # Barra 09:30 abre con gap en 18320.00
        bars = _build_test_bars(wed_date, open_0930=18320.0)
        result = run_backtest(bars, strategy, config)

        trade = result.trades[0]
        assert trade.entry_price == 18320.50  # 18320 + 0.50 slippage
        assert trade.stop_price == 18200.00   # Stop estructural intacto

        # Reward real: 18450 - 18320.50 = 129.50 pts
        # Risk real: 18320.50 - 18200 = 120.50 pts
        # Ratio real = 129.50 / 120.50 = 1.0747R << 1.50R
        effective_rr = (trade.target_price - trade.entry_price) / (trade.entry_price - trade.stop_price)
        assert round(effective_rr, 4) == round(129.50 / 120.50, 4)
        assert effective_rr < 1.10


class TestMissingBarAndGapRejection:
    """Caracterizacion de continuidad: ausencia de barra 09:30 rechaza la entrada sin fill tardio."""

    def test_missing_0930_bar_triggers_gap_rejection_no_retrospective_fill(self):
        """Si la barra 09:30 no existe en el feed, la senal se rechaza por gap y no llena en 09:35."""
        wed_date = date(2024, 6, 12)
        trigger_dt = datetime(2024, 6, 12, 9, 25, 0, tzinfo=NY_TZ)

        signal = Signal(
            direction="long",
            entry=18300.0,
            stop=18200.0,
            target=18450.0,
            stop_target_as_points=False,
        )
        strategy = _MockWednesdayStrategy(signal, trigger_dt)

        config = BacktestConfig(
            tick_size=0.25,
            dollar_per_point=2.0,
            slippage_points=0.25,
            bar_interval_seconds=300,  # 5 minutos
        )

        # Construimos dataset OMITIENDO la barra 09:30 (salto directo de 09:25 a 09:35)
        bars = _build_test_bars(wed_date, include_0930=False)
        result = run_backtest(bars, strategy, config)

        # El motor registra el rechazo por gap y no abre ninguna operacion
        assert result.gap_rejections == 1
        assert len(result.trades) == 0
        assert result.open_position is None


class TestFillCrossingStructuralStop:
    """Caracterizacion de niveles invalidos cuando el fill cruza el stop estructural."""

    def test_long_fill_below_stop_is_rejected_by_valid_levels(self):
        """Si un largo abre por debajo del stop estructural (gap adverso severo), se rechaza."""
        wed_date = date(2024, 6, 12)
        trigger_dt = datetime(2024, 6, 12, 9, 25, 0, tzinfo=NY_TZ)

        tue_low = 18200.0
        signal = Signal(
            direction="long",
            entry=18300.0,
            stop=tue_low,
            target=18450.0,
            stop_target_as_points=False,
        )
        strategy = _MockWednesdayStrategy(signal, trigger_dt)

        config = BacktestConfig(
            tick_size=0.25,
            dollar_per_point=2.0,
            slippage_points=0.25,
            bar_interval_seconds=300,
        )

        # La barra 09:30 abre en 18190.00 (por debajo del stop 18200.00)
        bars = _build_test_bars(wed_date, open_0930=18190.0)
        result = run_backtest(bars, strategy, config)

        # _valid_levels detecta stop >= entry y rechaza la entrada
        assert len(result.trades) == 0
        assert result.open_position is None

    def test_short_fill_above_stop_is_rejected_by_valid_levels(self):
        """Si un corto abre por encima del stop estructural, se rechaza sin abrir posicion."""
        wed_date = date(2024, 6, 12)
        trigger_dt = datetime(2024, 6, 12, 9, 25, 0, tzinfo=NY_TZ)

        tue_high = 18400.0
        signal = Signal(
            direction="short",
            entry=18300.0,
            stop=tue_high,
            target=18150.0,
            stop_target_as_points=False,
        )
        strategy = _MockWednesdayStrategy(signal, trigger_dt)

        config = BacktestConfig(
            tick_size=0.25,
            dollar_per_point=2.0,
            slippage_points=0.25,
            bar_interval_seconds=300,
        )

        # La barra 09:30 abre en 18410.00 (por encima del stop 18400.00)
        bars = _build_test_bars(wed_date, open_0930=18410.0)
        result = run_backtest(bars, strategy, config)

        assert len(result.trades) == 0
        assert result.open_position is None


class TestTimeExitAndDatasetTruncation:
    """Caracterizacion de salidas temporales a las 16:00 ET y comportamiento ante datos truncados."""

    def test_position_exits_at_1600_open_when_bar_present(self):
        """Si la barra 16:00 esta presente, la posicion se cierra a su apertura con slippage configurado."""
        wed_date = date(2024, 6, 12)
        trigger_dt = datetime(2024, 6, 12, 9, 25, 0, tzinfo=NY_TZ)

        signal = Signal(
            direction="long",
            entry=18300.0,
            stop=18100.0,
            target=18600.0,
            stop_target_as_points=False,
        )
        strategy = _MockWednesdayStrategy(signal, trigger_dt)

        config = BacktestConfig(
            tick_size=0.25,
            dollar_per_point=2.0,
            slippage_points=0.25,
            commission_per_side=0.62,
            bar_interval_seconds=300,
            max_hold_minutes=390.0,  # 09:30 a 16:00 son 390 min
            time_exit_mode="market",
            time_exit_slippage_points=0.50,  # 2 ticks slippage de salida
        )

        # Bars hasta las 16:05 (incluye 16:00)
        bars = _build_test_bars(
            wed_date,
            bars_until=datetime(2024, 6, 12, 16, 5, 0, tzinfo=NY_TZ),
            interim_high=18310.0,
            interim_low=18290.0,
        )
        result = run_backtest(bars, strategy, config)

        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.exit_reason == "time_exit"
        assert trade.exit_time == datetime(2024, 6, 12, 16, 0, 0, tzinfo=NY_TZ)
        # 18300 - 0.50 slippage en venta
        assert trade.exit_price == 18299.50
        assert result.open_position is None

    def test_missing_1600_bar_exits_at_next_available_bar(self):
        """Si la barra 16:00 falta pero llega la 16:05, time_exit cierra en la primera barra >= limite."""
        wed_date = date(2024, 6, 12)
        trigger_dt = datetime(2024, 6, 12, 9, 25, 0, tzinfo=NY_TZ)

        signal = Signal(
            direction="long",
            entry=18300.0,
            stop=18100.0,
            target=18600.0,
            stop_target_as_points=False,
        )
        strategy = _MockWednesdayStrategy(signal, trigger_dt)

        config = BacktestConfig(
            tick_size=0.25,
            dollar_per_point=2.0,
            slippage_points=0.25,
            bar_interval_seconds=300,
            max_hold_minutes=390.0,
            time_exit_mode="market",
            time_exit_slippage_points=0.0,
        )

        # Construimos barras y removemos manualmente la de 16:00
        bars = _build_test_bars(
            wed_date,
            bars_until=datetime(2024, 6, 12, 16, 5, 0, tzinfo=NY_TZ),
            interim_high=18310.0,
            interim_low=18290.0,
        )
        bars = [b for b in bars if b.timestamp != datetime(2024, 6, 12, 16, 0, 0, tzinfo=NY_TZ)]

        result = run_backtest(bars, strategy, config)

        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.exit_reason == "time_exit"
        # El motor no invento las 16:00: cerro en la apertura de 16:05
        assert trade.exit_time == datetime(2024, 6, 12, 16, 5, 0, tzinfo=NY_TZ)

    def test_dataset_truncated_before_1600_end_of_data_behavior(self):
        """Dataset truncado antes de 16:00: modo absoluto fuerza cierre legacy; modo puntos preserva open_position."""
        wed_date = date(2024, 6, 12)
        trigger_dt = datetime(2024, 6, 12, 9, 25, 0, tzinfo=NY_TZ)

        # 1. Modo absoluto (stop_target_as_points=False)
        signal_abs = Signal(
            direction="long",
            entry=18300.0,
            stop=18100.0,
            target=18600.0,
            stop_target_as_points=False,
        )
        strategy_abs = _MockWednesdayStrategy(signal_abs, trigger_dt)

        config = BacktestConfig(
            tick_size=0.25,
            dollar_per_point=2.0,
            slippage_points=0.25,
            bar_interval_seconds=300,
            max_hold_minutes=390.0,
            end_of_data_policy="unresolved",
        )

        # Truncamos el dataset a las 15:30 (antes de las 16:00)
        trunc_dt = datetime(2024, 6, 12, 15, 30, 0, tzinfo=NY_TZ)
        bars = _build_test_bars(wed_date, bars_until=trunc_dt, interim_high=18310.0, interim_low=18290.0)

        result_abs = run_backtest(bars, strategy_abs, config)

        # En modo absoluto, executor.py:555 (not position["distance_mode"]) fuerza should_close=True
        # a pesar de end_of_data_policy="unresolved"
        assert len(result_abs.trades) == 1
        assert result_abs.trades[0].exit_reason == "end_of_data"
        assert result_abs.trades[0].exit_time == trunc_dt
        assert result_abs.open_position is None

        # 2. Modo distancia (stop_target_as_points=True)
        signal_pts = Signal(
            direction="long",
            entry=18300.0,
            stop=200.0,
            target=300.0,
            stop_target_as_points=True,
        )
        strategy_pts = _MockWednesdayStrategy(signal_pts, trigger_dt)

        result_pts = run_backtest(bars, strategy_pts, config)

        # En modo distancia, position["distance_mode"] es True -> should_close=False con "unresolved"
        assert len(result_pts.trades) == 0
        assert result_pts.unresolved_positions == 1
        assert result_pts.open_position is not None
        assert result_pts.open_position["entry_time"] == datetime(2024, 6, 12, 9, 30, 0, tzinfo=NY_TZ)


class TestCausalityAndHistoricalEvaluation:
    """Verificacion de no-lookahead: evaluate solo observa barras cerradas antes del fill."""

    def test_evaluate_only_sees_closed_bars_before_fill(self):
        """Strategy.evaluate nunca ve la barra 09:30 en el historial cuando propone la entrada."""
        wed_date = date(2024, 6, 12)
        trigger_dt = datetime(2024, 6, 12, 9, 25, 0, tzinfo=NY_TZ)

        signal = Signal(
            direction="long",
            entry=18300.0,
            stop=18200.0,
            target=18450.0,
            stop_target_as_points=False,
        )
        strategy = _MockWednesdayStrategy(signal, trigger_dt)

        config = BacktestConfig(
            tick_size=0.25,
            dollar_per_point=2.0,
            bar_interval_seconds=300,
        )

        bars = _build_test_bars(wed_date, bars_until=datetime(2024, 6, 12, 10, 0, 0, tzinfo=NY_TZ))
        run_backtest(bars, strategy, config)

        # Buscamos el snapshot de historial con el que se emitio la señal
        trigger_histories = [
            h for h in strategy.history_snapshots if h and h[-1].timestamp == trigger_dt
        ]
        assert len(trigger_histories) == 1
        eval_history = trigger_histories[0]

        # La ultima barra en el historial evaluado fue exactamente la de 09:25:00
        assert eval_history[-1].timestamp == trigger_dt
        # Cero barras de 09:30 o posteriores presentes en el historial
        future_bars = [b for b in eval_history if b.timestamp >= datetime(2024, 6, 12, 9, 30, 0, tzinfo=NY_TZ)]
        assert len(future_bars) == 0

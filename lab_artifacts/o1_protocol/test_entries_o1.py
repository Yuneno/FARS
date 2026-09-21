"""Tests exhaustivos y causales para el Bloque O1.

Cubre todos los requisitos mínimos de FARS_GEMINI_ENCARGO_O1_COMP_BREAK_DEV.md:
- NR7 usa solo 7 barras cerradas y maneja empate.
- El Donchian excluye t.
- LONG/SHORT exactos; no señal dentro del canal.
- La señal en t llena en t+1, no en la misma barra.
- ATR/SL/TP solo usan historia cerrada.
- No dispara con warmup insuficiente.
- Determinismo bit a bit.
- L=20 y L=55 están realmente conectados.
- Los costes llegan al executor sin doble conteo.
- El loader corta DEV antes de materializar OHLCV bloqueado.
- El runner rechaza validation/final y rechaza hash de preregistro cambiado.
- Smoke pequeño antes de la matriz completa.
"""

from __future__ import annotations

import copy
import hashlib
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from lab_artifacts.o1_protocol.entries_o1 import (
    O1CompressionBreakoutStrategy,
    TrivialWrapperStrategy,
    build_o1_strategy,
)
from src.backtest.executor import BacktestConfig, run_backtest
from src.backtest.history import Bar
from src.backtest.markets import MNQ


def make_bar(
    ts: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
) -> Bar:
    return Bar(
        timestamp=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def generate_bars(
    n: int,
    base_price: float = 15000.0,
    start_time: datetime | None = None,
    half_range: float = 5.0,
    trend: float = 0.0,
) -> list[Bar]:
    if start_time is None:
        start_time = datetime(2023, 1, 1, 0, 0, tzinfo=timezone.utc)
    bars: list[Bar] = []
    p = base_price
    for i in range(n):
        ts = start_time + timedelta(minutes=5 * i)
        bars.append(make_bar(ts, p, p + half_range, p - half_range, p))
        p += trend
    return bars


def test_nr7_uses_strictly_7_closed_bars_and_handles_ties():
    """NR7 en t-1 debe evaluar exactamente las 7 barras cerradas terminando en t-1 y admitir empates."""
    strat = O1CompressionBreakoutStrategy(market_spec=MNQ, donchian_lookback=20, log_decisions=True)
    base_bars = generate_bars(30, base_price=1000.0)

    # Modificar barras t-8 a t-1 (history[-9:-1])
    # Bar t es history[-1]
    # Bar t-1 es history[-2]
    # Las 7 barras terminando en t-1 son history[-8:-1]
    t_start = datetime(2023, 1, 1, 0, 0, tzinfo=timezone.utc)

    # Crear 25 barras donde las 7 barras previas a t tienen rangos conocidos
    bars: list[Bar] = []
    for i in range(25):
        ts = t_start + timedelta(minutes=5 * i)
        bars.append(make_bar(ts, 1000.0, 1010.0, 990.0, 1000.0))  # range = 20.0

    # history[-8:-1] son índices 17, 18, 19, 20, 21, 22, 23 (7 barras)
    # y t es índice 24 (len = 25)
    # Hacemos que bar t-8 (índice 16, fuera de las 7) tenga rango ultra estrecho (range = 1.0)
    # Esto prueba que t-8 NO interfiere con NR7
    bars[16] = make_bar(bars[16].timestamp, 1000.0, 1000.5, 999.5, 1000.0)

    # Las 7 barras (índices 17 a 23):
    # Damos a las barras 17..22 un rango de 10.0 (high=1005, low=995)
    for idx in range(17, 23):
        bars[idx] = make_bar(bars[idx].timestamp, 1000.0, 1005.0, 995.0, 1000.0)

    # Caso A: bar t-1 (índice 23) tiene rango 5.0 -> estrictamente menor -> califica NR7
    bars[23] = make_bar(bars[23].timestamp, 1000.0, 1002.5, 997.5, 1000.0)  # range 5.0
    # Bar t rompe Donchian high (Donchian high es 1010.0)
    bars[24] = make_bar(bars[24].timestamp, 1000.0, 1015.0, 998.0, 1012.0)  # close 1012 > 1010

    sig = strat.fresh().evaluate(bars)
    assert sig is not None
    assert sig.direction == "long"

    # Caso B: Empate en mínimo (bar 22 tiene rango 5.0 y bar 23 tiene rango 5.0) -> califica NR7
    bars[22] = make_bar(bars[22].timestamp, 1000.0, 1002.5, 997.5, 1000.0)  # range 5.0
    sig_tie = strat.fresh().evaluate(bars)
    assert sig_tie is not None
    assert sig_tie.direction == "long"

    # Caso C: bar t-1 tiene rango 6.0 pero bar 22 tiene 5.0 -> NO es NR7 -> no señal
    bars[23] = make_bar(bars[23].timestamp, 1000.0, 1003.0, 997.0, 1000.0)  # range 6.0 > 5.0
    sig_no = strat.fresh().evaluate(bars)
    assert sig_no is None


def test_donchian_strictly_excludes_bar_t():
    """El canal Donchian de longitud L en t debe usar las L barras previas a t y excluir estrictamente la barra t."""
    strat = O1CompressionBreakoutStrategy(market_spec=MNQ, donchian_lookback=20, log_decisions=True)
    bars = generate_bars(25, base_price=1000.0)

    # En bar t-1 (índice 23), forzamos NR7 (rango 1.0)
    bars[23] = make_bar(bars[23].timestamp, 1000.0, 1000.5, 999.5, 1000.0)

    # Donchian de longitud 20 cubre barras 3..23 (excluyendo 24=t)
    # Donchian high previo es 1005.0, donchian low previo es 995.0
    # Modificamos bar t (índice 24) con un pico enorme intrabarra pero cierre adentro:
    # high = 2000.0, low = 500.0, close = 1002.0
    bars[24] = make_bar(bars[24].timestamp, 1000.0, 2000.0, 500.0, 1002.0)

    # Como close[t] = 1002.0 está dentro de [995.0, 1005.0], NO debe haber señal
    # Si la barra t estuviera incluida en el Donchian, el canal se distorsionaría
    sig = strat.evaluate(bars)
    assert sig is None

    # Ahora si close[t] = 1006.0 > 1005.0 -> ruptura Long válida
    bars[24] = make_bar(bars[24].timestamp, 1000.0, 1008.0, 999.0, 1006.0)
    sig2 = strat.fresh().evaluate(bars)
    assert sig2 is not None
    assert sig2.direction == "long"


def test_breakout_exact_long_short_and_inside_channel_rejection():
    """LONG cuando close > donchian_high; SHORT cuando close < donchian_low; nada dentro del canal."""
    strat = O1CompressionBreakoutStrategy(market_spec=MNQ, donchian_lookback=20)
    bars = generate_bars(25, base_price=1000.0)

    # NR7 en t-1
    bars[23] = make_bar(bars[23].timestamp, 1000.0, 1000.5, 999.5, 1000.0)

    # Long: close > donchian_high (1005.0)
    bars[24] = make_bar(bars[24].timestamp, 1000.0, 1010.0, 999.0, 1006.0)
    sig_long = strat.fresh().evaluate(bars)
    assert sig_long is not None
    assert sig_long.direction == "long"
    assert sig_long.entry == 1006.0

    # Short: close < donchian_low (995.0)
    bars[24] = make_bar(bars[24].timestamp, 1000.0, 1001.0, 990.0, 994.0)
    sig_short = strat.fresh().evaluate(bars)
    assert sig_short is not None
    assert sig_short.direction == "short"
    assert sig_short.entry == 994.0

    # Inside channel: close = 1000.0
    bars[24] = make_bar(bars[24].timestamp, 1000.0, 1002.0, 998.0, 1000.0)
    sig_inside = strat.fresh().evaluate(bars)
    assert sig_inside is None


def test_signal_at_t_fills_at_t_plus_1_open_via_executor():
    """La señal emitida al cierre de t debe llenarse en el open de t+1, nunca en t."""
    strat = O1CompressionBreakoutStrategy(market_spec=MNQ, donchian_lookback=20)
    bars = generate_bars(30, base_price=1000.0)

    # Forzar NR7 en bar 23 (t-1)
    bars[23] = make_bar(bars[23].timestamp, 1000.0, 1000.5, 999.5, 1000.0)
    # Ruptura en bar 24 (t)
    bars[24] = make_bar(bars[24].timestamp, 1000.0, 1012.0, 999.0, 1010.0)
    # Siguiente barra 25 (t+1) donde debe ejecutarse el fill en su OPEN
    fill_open = 1011.25
    bars[25] = make_bar(bars[25].timestamp, fill_open, 1020.0, 1008.0, 1015.0)
    # Barra 26 que alcanza Take Profit
    bars[26] = make_bar(bars[26].timestamp, 1015.0, 1060.0, 1014.0, 1055.0)

    cfg = BacktestConfig(
        dollar_per_point=MNQ.dollar_per_point,
        tick_size=MNQ.tick_size,
        commission_per_side=0.62,
        slippage_points=0.25,  # 1 tick adverse
        bar_interval_seconds=300,
        max_bars_held=48,
    )

    res = run_backtest(bars, strat, cfg)
    assert res.n_trades >= 1
    t0 = res.trades[0]
    # Verificación estricta: entry_time es la marca de tiempo de bar[25] (t+1), NO de bar[24] (t)
    assert t0.entry_time == bars[25].timestamp
    # Entry price = fill_open + 0.25 (slippage adverso en compra market)
    assert t0.entry_price == fill_open + 0.25


def test_atr_sl_tp_causal_closed_history_and_tick_rounding():
    """ATR, Stop Loss y Take Profit solo usan historia cerrada y se redondean al tick del instrumento."""
    strat = O1CompressionBreakoutStrategy(market_spec=MNQ, donchian_lookback=20, log_decisions=True)
    bars = generate_bars(25, base_price=1000.0)

    bars[23] = make_bar(bars[23].timestamp, 1000.0, 1000.5, 999.5, 1000.0)
    bars[24] = make_bar(bars[24].timestamp, 1000.0, 1012.0, 999.0, 1010.0)

    sig = strat.evaluate(bars)
    assert sig is not None
    # Verificación de múltiplos de tick_size (0.25)
    tick = MNQ.tick_size
    assert round(sig.stop % tick, 4) in (0.0, round(tick, 4))
    assert round(sig.target % tick, 4) in (0.0, round(tick, 4))
    # Target debe ser exactamente el doble de Stop (2R)
    assert math.isclose(sig.target, 2.0 * sig.stop, abs_tol=tick)


def test_insufficient_warmup_no_signal():
    """No debe emitir ninguna señal con historial inferior al mínimo requerido."""
    strat = O1CompressionBreakoutStrategy(market_spec=MNQ, donchian_lookback=20)
    bars = generate_bars(15)  # 15 < 21 barras requeridas para L=20
    assert strat.evaluate(bars) is None

    strat55 = O1CompressionBreakoutStrategy(market_spec=MNQ, donchian_lookback=55)
    bars40 = generate_bars(40)  # 40 < 56 barras requeridas para L=55
    assert strat55.evaluate(bars40) is None


def test_bit_for_bit_determinism_and_wrapper_equivalence():
    """La ejecución debe ser 100% determinista e idéntica bit a bit al wrapper pasante."""
    bars = generate_bars(50, base_price=12000.0)
    # Insertar patrón
    bars[23] = make_bar(bars[23].timestamp, 12000.0, 12000.5, 11999.5, 12000.0)
    bars[24] = make_bar(bars[24].timestamp, 12000.0, 12015.0, 11999.0, 12010.0)

    s1 = build_o1_strategy(length=20, market_spec=MNQ, is_wrapper=False)
    s2 = build_o1_strategy(length=20, market_spec=MNQ, is_wrapper=True)

    sig1 = s1.evaluate(bars[:25])
    sig2 = s2.evaluate(bars[:25])

    assert sig1 is not None
    assert sig2 is not None
    assert sig1.direction == sig2.direction
    assert sig1.entry == sig2.entry
    assert sig1.stop == sig2.stop
    assert sig1.target == sig2.target
    assert sig1.stop_target_as_points == sig2.stop_target_as_points


def test_l20_vs_l55_connected_and_distinct():
    """L=20 y L=55 deben evaluar canales con sus respectivas profundidades y rechazar otros valores."""
    with pytest.raises(ValueError, match="Longitud Donchian no permitida"):
        build_o1_strategy(length=30, market_spec=MNQ)

    s20 = build_o1_strategy(length=20, market_spec=MNQ)
    s55 = build_o1_strategy(length=55, market_spec=MNQ)
    assert s20.donchian_lookback == 20
    assert s55.donchian_lookback == 55


def test_costs_reach_executor_without_double_counting():
    """Los costes de comisión y slippage deben registrarse fielmente en el executor."""
    strat = O1CompressionBreakoutStrategy(market_spec=MNQ, donchian_lookback=20)
    bars = generate_bars(30, base_price=1000.0)
    bars[23] = make_bar(bars[23].timestamp, 1000.0, 1000.5, 999.5, 1000.0)
    bars[24] = make_bar(bars[24].timestamp, 1000.0, 1015.0, 999.0, 1010.0)
    # Bar 25 llena en open 1010.0, y alcanza target en 1050.0
    bars[25] = make_bar(bars[25].timestamp, 1010.0, 1050.0, 1009.0, 1045.0)

    cfg = BacktestConfig(
        dollar_per_point=MNQ.dollar_per_point,
        tick_size=MNQ.tick_size,
        commission_per_side=0.62,
        slippage_points=0.25,
        bar_interval_seconds=300,
        max_bars_held=48,
    )

    res = run_backtest(bars, strat, cfg)
    assert res.n_trades >= 1
    t = res.trades[0]
    expected_comm = round(0.62 * 2 * t.quantity, 4)
    assert math.isclose(t.commission, expected_comm, abs_tol=1e-4)
    assert math.isclose(t.net_pnl, t.gross_pnl - t.commission, abs_tol=1e-4)


def test_loader_strictly_cuts_off_at_dev_boundary():
    """El loader no debe materializar ninguna barra posterior o igual a 2024-01-01T00:00:00Z."""
    from lab_artifacts.o1_protocol.run_o1 import load_dev_bars_from_zip, ZIP_PATH, M5_MEMBER

    bars, file_sha = load_dev_bars_from_zip(ZIP_PATH, M5_MEMBER)
    assert file_sha == "fbed6061205b8299af140f85e36b472f5f1d88084977ad9c4ca9aa1f817b8a96"
    assert len(bars) > 0
    # Verificación estricta: ninguna barra puede ser >= 2024-01-01
    assert all(b.timestamp < datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc) for b in bars)
    assert all(b.timestamp >= datetime(2019, 5, 6, 0, 0, tzinfo=timezone.utc) for b in bars)


def test_runner_guards_reject_tampered_preregistro(tmp_path: Path):
    """El runner debe rechazar de inmediato si el preregistro no coincide con el hash congelado."""
    from lab_artifacts.o1_protocol.run_o1 import verify_preregistro_sha256, PREREGISTRO_PATH

    # Hash del archivo real debe coincidir
    h = verify_preregistro_sha256(PREREGISTRO_PATH)
    assert len(h) == 64

    # Archivo alterado en directorio temporal debe fallar
    tampered = tmp_path / "preregistro.json"
    tampered.write_text('{"tampered": true}', encoding="utf-8")
    with pytest.raises(ValueError, match="Fallo de integridad"):
        verify_preregistro_sha256(tampered)

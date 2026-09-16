"""Tests unitarios y de integración para el bridge de zonas (Z5).

Verifica exhaustivamente los 6 criterios normativos de aceptación (§1 T3):
1. Cumplimiento del protocolo Strategy (sólo barras cerradas).
2. Filtro trivial == corrida directa BIT A BIT (mismos r_result, timestamps, motivos).
3. Filtro que bloquea todo == cero trades y log de rechazos con contexto completo.
4. Motor actualizado una sola vez por barra (contador y append-only).
5. Contexto point-in-time: available_at <= cierre de la barra de decisión.
6. session_pools=False: contexto con 17 keys y baseline intacto.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Sequence

import pytest

from lab_artifacts.run_c1_walkforward import ZIP_PATH, load_canonical_m5
from src.backtest.executor import run_backtest, BacktestConfig
from src.backtest.history import Bar
from src.backtest.markets import MNQ
from src.backtest.smc_fvg import SmcFvgStrategy, smc_fvg_config
from src.backtest.strategy import Signal
from src.backtest.zone_bridge import (
    ZoneContextProvider,
    ZoneDecisionRecord,
    ZoneFilter,
    ZoneFilteredStrategy,
    build_filter,
    make_always_true_filter,
)


class MockConstantSignalStrategy:
    """Estrategia determinista que emite una señal fija cada vez que se evalúa."""

    def __init__(self, direction: str = "long", entry: float = 100.0, stop: float = 90.0, target: float = 120.0) -> None:
        self.direction = direction
        self.entry = entry
        self.stop = stop
        self.target = target
        self.evaluated_histories: list[Sequence[Bar]] = []

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        self.evaluated_histories.append(history)
        if not history:
            return None
        return Signal(
            direction=self.direction,  # type: ignore[arg-type]
            entry=self.entry,
            stop=self.stop,
            target=self.target,
            stop_target_as_points=False,
        )


def _generate_synthetic_bars(n: int = 50, start_dt: datetime | None = None) -> list[Bar]:
    if start_dt is None:
        start_dt = datetime(2023, 6, 1, 14, 0, tzinfo=timezone.utc)
    bars: list[Bar] = []
    cur_p = 15000.0
    for i in range(n):
        ts = start_dt + timedelta(minutes=5 * i)
        op = cur_p
        hi = cur_p + 10.0
        lo = cur_p - 10.0
        cl = cur_p + 2.0
        bars.append(Bar(timestamp=ts, open=op, high=hi, low=lo, close=cl, volume=100.0))
        cur_p = cl
    return bars


@pytest.fixture(scope="module")
def canonical_bars_slice() -> list[Bar]:
    """Carga una rebanada de 3.000 barras canónicas de MNQ M5 para pruebas de integración."""
    bars, _ = load_canonical_m5(ZIP_PATH)
    return bars[:3000]


# ---------------------------------------------------------------------------
# Test 1: Cumplimiento del protocolo Strategy
# ---------------------------------------------------------------------------


def test_wrapper_conforms_to_strategy_protocol() -> None:
    """Verifica que ZoneFilteredStrategy cumple el protocolo Strategy (callable evaluate con Sequence[Bar])."""
    mock_inner = MockConstantSignalStrategy()
    provider = ZoneContextProvider(symbol="MNQ", timeframe="5m", session_pools=False)
    filtered = ZoneFilteredStrategy(inner=mock_inner, provider=provider)

    assert hasattr(filtered, "evaluate")
    assert callable(filtered.evaluate)

    # Evaluación con lista vacía
    assert filtered.evaluate([]) is None
    assert len(filtered.log) == 0

    # Evaluación con barras cerradas
    bars = _generate_synthetic_bars(5)
    sig = filtered.evaluate(bars)
    assert sig is not None
    assert sig.direction == "long"
    assert sig.entry == 100.0
    assert len(filtered.log) == 1
    assert filtered.log[0].verdict is True
    assert filtered.log[0].timestamp == bars[-1].timestamp
    assert filtered.log[0].price == bars[-1].close


# ---------------------------------------------------------------------------
# Test 2: Filtro trivial == corrida directa, BIT A BIT (Criterio Anti-Fraude)
# ---------------------------------------------------------------------------


def test_wrapper_trivial_bit_a_bit_parity(canonical_bars_slice: list[Bar]) -> None:
    """Garantía anti-fraude: wrapper_trivial (always True) genera trades 100% idénticos al baseline directo."""
    bars = canonical_bars_slice
    cfg = smc_fvg_config(market=MNQ, commission_per_side=2.0, slippage_points=0.0)

    # Corrida 1: Directa (Baseline)
    strat_baseline = SmcFvgStrategy(market=MNQ)
    res_baseline = run_backtest(bars, strat_baseline, cfg)

    # Corrida 2: Wrapper Trivial
    provider = ZoneContextProvider(symbol="MNQ", timeframe="5m", session_pools=True)
    filter_trivial = make_always_true_filter()
    decision_log: list[ZoneDecisionRecord] = []
    strat_wrapped = ZoneFilteredStrategy(
        inner=SmcFvgStrategy(market=MNQ),
        provider=provider,
        filter_spec=filter_trivial,
        log=decision_log,
    )
    res_wrapped = run_backtest(bars, strat_wrapped, cfg)

    # Comparación exacta de trades
    assert res_wrapped.n_trades == res_baseline.n_trades
    assert res_wrapped.n_trades > 0, "El baseline canónico debe generar trades"

    for tb, tw in zip(res_baseline.trades, res_wrapped.trades):
        assert tb.direction == tw.direction
        assert tb.entry_time == tw.entry_time
        assert tb.exit_time == tw.exit_time
        assert tb.entry_price == tw.entry_price
        assert tb.exit_price == tw.exit_price
        assert tb.r_result == pytest.approx(tw.r_result, abs=1e-12)
        assert tb.net_pnl == pytest.approx(tw.net_pnl, abs=1e-12)
        assert tb.exit_reason == tw.exit_reason

    assert res_wrapped.net_pnl == pytest.approx(res_baseline.net_pnl, abs=1e-12)
    assert res_wrapped.profit_factor == pytest.approx(res_baseline.profit_factor, abs=1e-12)
    assert len(decision_log) >= res_wrapped.n_trades


# ---------------------------------------------------------------------------
# Test 3: Filtro que bloquea todo == cero trades y log completo
# ---------------------------------------------------------------------------


def test_blocking_filter_zero_trades_and_records_rejections(canonical_bars_slice: list[Bar]) -> None:
    """Un filtro que rechaza todo debe producir 0 trades y registrar todos los rechazos con su contexto."""
    bars = canonical_bars_slice
    cfg = smc_fvg_config(market=MNQ)

    # Verificar que el baseline sí produce trades
    res_base = run_backtest(bars, SmcFvgStrategy(market=MNQ), cfg)
    assert res_base.n_trades > 0

    # Filtro bloqueador: siempre False
    blocking_filter = ZoneFilter(name="block_all", predicate=lambda ctx, sig: False)
    provider = ZoneContextProvider(symbol="MNQ", timeframe="5m", session_pools=True)
    decision_log: list[ZoneDecisionRecord] = []
    strat_blocked = ZoneFilteredStrategy(
        inner=SmcFvgStrategy(market=MNQ),
        provider=provider,
        filter_spec=blocking_filter,
        log=decision_log,
    )
    res_blocked = run_backtest(bars, strat_blocked, cfg)

    assert res_blocked.n_trades == 0
    assert len(decision_log) >= res_base.n_trades
    for rec in decision_log:
        assert rec.verdict is False
        assert rec.filter_name == "block_all"
        assert isinstance(rec.context, dict)
        assert len(rec.context) >= 17


# ---------------------------------------------------------------------------
# Test 4: Motor actualizado una vez por barra y append-only
# ---------------------------------------------------------------------------


def test_provider_updated_once_per_bar_and_append_only() -> None:
    """Verifica que el provider no re-procesa la misma barra y respeta append-only."""
    provider = ZoneContextProvider(symbol="MNQ", timeframe="5m", session_pools=False)
    bars = _generate_synthetic_bars(10)

    # Primera actualización con 5 barras
    provider.update(bars[:5])
    assert provider.update_calls_count == 1
    assert provider.bars_processed_count == 5

    # Llamada redundante con la misma longitud (ej. múltiples consultas en la misma barra)
    provider.update(bars[:5])
    assert provider.update_calls_count == 2
    assert provider.bars_processed_count == 5  # No se incrementa

    # Actualización incremental con 3 barras más (total 8)
    provider.update(bars[:8])
    assert provider.update_calls_count == 3
    assert provider.bars_processed_count == 8

    # Violación de append-only (historia encoge)
    with pytest.raises(ValueError, match="Contrato append-only violado"):
        provider.update(bars[:4])


# ---------------------------------------------------------------------------
# Test 5: Contexto point-in-time: available_at <= cierre de barra de decisión
# ---------------------------------------------------------------------------


def test_point_in_time_causality_decision_log(canonical_bars_slice: list[Bar]) -> None:
    """Verifica que para cada anclaje registrado en decision_log, available_at <= timestamp de la barra."""
    bars = canonical_bars_slice[:1000]

    mock_strat = MockConstantSignalStrategy(direction="long", entry=7500.0, stop=7480.0, target=7540.0)
    provider = ZoneContextProvider(symbol="MNQ", timeframe="5m", session_pools=True)
    decision_log: list[ZoneDecisionRecord] = []
    strat = ZoneFilteredStrategy(inner=mock_strat, provider=provider, log=decision_log)

    cfg = BacktestConfig(dollar_per_point=MNQ.dollar_per_point, tick_size=MNQ.tick_size, max_hold_minutes=15.0)
    run_backtest(bars, strat, cfg)

    assert len(decision_log) > 0
    for rec in decision_log:
        d_ts = rec.timestamp
        # Verificar cada anclaje reportado en anchor_meta
        for kind, meta in rec.anchor_meta.items():
            avail_at = meta["available_at"]
            pattern_time = meta["pattern_time"]
            assert avail_at <= d_ts, (
                f"Lookahead detectado en decisión {d_ts}: anclaje {kind} tiene available_at {avail_at} > decision_time {d_ts}"
            )
            assert pattern_time <= avail_at


# ---------------------------------------------------------------------------
# Test 6: session_pools=False -> 17 keys y baseline intacto
# ---------------------------------------------------------------------------


def test_session_pools_false_17_keys() -> None:
    """Con session_pools=False, el contexto solo contiene las 17 keys preexistentes de Z3."""
    bars = _generate_synthetic_bars(20)
    provider = ZoneContextProvider(symbol="MNQ", timeframe="5m", session_pools=False)
    provider.update(bars)

    ctx = provider.context(price=bars[-1].close)
    assert len(ctx) == 17
    # Ninguna key de sesión presente
    assert "distance_to_prev_day_high_pts" not in ctx
    assert "overnight_swept_prev_day_high" not in ctx
    assert "inside_prev_day_range" not in ctx


# ---------------------------------------------------------------------------
# Test 7: Catálogo de filtros nombrados declarativos
# ---------------------------------------------------------------------------


def test_filter_catalog_predicates() -> None:
    """Verifica el comportamiento de los predicados individuales del catálogo build_filter."""
    sig_long = Signal("long", 100.0, 95.0, 110.0)
    sig_short = Signal("short", 100.0, 105.0, 90.0)

    # 1. inside_prev_day_range
    f_pdr = build_filter("inside_prev_day_range")
    assert f_pdr.predicate({"inside_prev_day_range": True}, sig_long) is True
    assert f_pdr.predicate({"inside_prev_day_range": False}, sig_long) is False
    assert f_pdr.predicate({"inside_prev_day_range": None}, sig_long) is False

    # 2. prev_day_range_position
    f_pos = build_filter("prev_day_range_position", {"threshold": 0.5})
    assert f_pos.predicate({"prev_day_range_position": 0.3}, sig_long) is True
    assert f_pos.predicate({"prev_day_range_position": 0.7}, sig_long) is False
    assert f_pos.predicate({"prev_day_range_position": 0.7}, sig_short) is True
    assert f_pos.predicate({"prev_day_range_position": 0.3}, sig_short) is False
    assert f_pos.predicate({"prev_day_range_position": None}, sig_long) is False

    # 3. distance_to_overnight_low_atr
    f_onl = build_filter("distance_to_overnight_low_atr", {"max_atr": 1.0})
    assert f_onl.predicate({"distance_to_overnight_low_atr": 0.5}, sig_long) is True
    assert f_onl.predicate({"distance_to_overnight_low_atr": 1.5}, sig_long) is False
    assert f_onl.predicate({"distance_to_overnight_low_atr": None}, sig_long) is False
    assert f_onl.predicate({"distance_to_overnight_low_atr": 99.0}, sig_short) is True

    # 4. overnight_swept_prev_day_low
    f_swp_pdl = build_filter("overnight_swept_prev_day_low")
    assert f_swp_pdl.predicate({"overnight_swept_prev_day_low": True}, sig_long) is True
    assert f_swp_pdl.predicate({"overnight_swept_prev_day_low": False}, sig_long) is False

    # 5. inside_fvg
    f_fvg = build_filter("inside_fvg")
    assert f_fvg.predicate({"inside_bullish_fvg": True, "inside_bearish_fvg": False}, sig_long) is True
    assert f_fvg.predicate({"inside_bullish_fvg": False, "inside_bearish_fvg": True}, sig_long) is False
    assert f_fvg.predicate({"inside_bullish_fvg": False, "inside_bearish_fvg": True}, sig_short) is True

    # 6. combo_fvg_and_liquidity
    f_combo = build_filter("combo_fvg_and_liquidity")
    assert f_combo.predicate({"inside_bullish_fvg": True, "liquidity_swept": True}, sig_long) is True
    assert f_combo.predicate({"inside_bullish_fvg": True, "liquidity_swept": False}, sig_long) is False


# ---------------------------------------------------------------------------
# Test 8: Identidad matemática exacta con precompute_fold_cache
# ---------------------------------------------------------------------------


def test_precomputed_cache_identity(canonical_bars_slice: list[Bar]) -> None:
    """Verifica que el cache precalculado genera trades y contextos 100% idénticos al motor interactivo."""
    from src.backtest.zone_bridge import precompute_fold_cache

    bars = canonical_bars_slice[:1000]
    cfg = smc_fvg_config(market=MNQ)

    # 1. Corrida interactiva directa
    p_direct = ZoneContextProvider(symbol="MNQ", timeframe="5m", session_pools=True)
    f_pdr = build_filter("inside_prev_day_range")
    strat_direct = ZoneFilteredStrategy(inner=SmcFvgStrategy(market=MNQ), provider=p_direct, filter_spec=f_pdr)
    res_direct = run_backtest(bars, strat_direct, cfg)

    # 2. Corrida con cache precalculado
    ctx_cache, anchor_cache = precompute_fold_cache(bars, symbol="MNQ", timeframe="5m", session_pools=True)
    p_cached = ZoneContextProvider(symbol="MNQ", timeframe="5m", session_pools=True, cache=ctx_cache, anchor_cache=anchor_cache)
    strat_cached = ZoneFilteredStrategy(inner=SmcFvgStrategy(market=MNQ), provider=p_cached, filter_spec=f_pdr)
    res_cached = run_backtest(bars, strat_cached, cfg)

    assert res_direct.n_trades == res_cached.n_trades
    for t1, t2 in zip(res_direct.trades, res_cached.trades):
        assert t1.entry_time == t2.entry_time
        assert t1.exit_time == t2.exit_time
        assert t1.entry_price == t2.entry_price
        assert t1.exit_price == t2.exit_price
        assert t1.r_result == pytest.approx(t2.r_result, abs=1e-12)


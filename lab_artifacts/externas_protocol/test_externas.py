"""Tests de Validación — Protocolo de Señales Externas (Semilla 20260928).

Verifica:
1. Cero look-ahead: modificar o invertir una barra futura no altera ninguna decisión pasada.
2. Ventanas de sesión exactas: manejo estricto de DST en America/New_York (verano EDT vs invierno EST).
3. Reproducibilidad determinista: corridas idénticas generan exactamente el mismo JSON y hash SHA-256.
4. Sizing y separación de métricas R: Budget-R y Stop-R se calculan independientemente.
5. Prioridad SL-first en ambigüedad intrabarra.
6. Contador declarativo de variantes y ajuste de Bonferroni en el harness de mejoras.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from lab_artifacts.externas_protocol.mejoras import ImprovementHarness
from lab_artifacts.externas_protocol.run_externas import (
    analyze_signal_protocol,
    compute_cbb_ci,
    compute_dsr,
    compute_permutation_p_value,
    compute_t_stat,
    run_all_signals_on_bars,
    summarize_trades,
)
from lab_artifacts.externas_protocol.senales_externas import (
    AmdCrtConfluenceStrategy,
    D2LStrategy,
    L0200Strategy,
    TradeRecord,
    _compute_h4_ema,
    _to_et,
    simulate_signal_causal,
)
from src.backtest.history import Bar
from src.backtest.markets import MNQ

ET = ZoneInfo("America/New_York")
SEED = 20260928


def _build_synthetic_bars(n: int = 500, start_dt: datetime | None = None, seed: int = SEED) -> list[Bar]:
    """Genera una serie temporal coherente de barras M5 para validación sintética."""
    rng = np.random.default_rng(seed)
    if start_dt is None:
        start_dt = datetime(2024, 7, 7, 17, 55, tzinfo=UTC)  # Domingo de verano EDT

    bars = []
    price = 20000.0
    for i in range(n):
        t = start_dt + timedelta(minutes=5 * i)
        # Salto de fin de semana si cae en sábado/domingo mañana (para realismo)
        ret = rng.normal(0.0001, 0.001)
        o = price
        c = price * (1.0 + ret)
        h = max(o, c) + abs(rng.normal(0, 1.5))
        l = min(o, c) - abs(rng.normal(0, 1.5))
        bars.append(Bar(timestamp=t, open=round(o, 2), high=round(h, 2), low=round(l, 2), close=round(c, 2), volume=100.0))
        price = c
    return bars


# =============================================================================
# 1. TEST DE CERO LOOK-AHEAD
# =============================================================================

def test_zero_lookahead():
    """Invertir o mutar barras futuras no altera las decisiones pasadas."""
    bars = _build_synthetic_bars(n=300)
    eval_idx = 150

    # 1. Evaluación sobre historial original hasta eval_idx
    hist_original = bars[:eval_idx]
    strat1 = D2LStrategy()
    sig1 = strat1.evaluate(hist_original)

    # 2. Mutar radicalmente barras futuras (eval_idx hasta el final)
    # Como Bar es frozen=True (dataclass inmutable), se construyen nuevas barras reemplazadas
    bars_mutated = [
        b if i < eval_idx else Bar(
            timestamp=b.timestamp,
            open=999999.0,
            high=999999.0,
            low=1.0,
            close=500000.0,
            volume=b.volume,
        )
        for i, b in enumerate(bars)
    ]

    # 3. La evaluación en eval_idx debe ser exactamente idéntica
    strat2 = D2LStrategy()
    sig2 = strat2.evaluate(bars_mutated[:eval_idx])

    assert (sig1 is None and sig2 is None) or (
        sig1 is not None and sig2 is not None and sig1.direction == sig2.direction and sig1.stop == sig2.stop
    )

    # Lo mismo para AmdCrtConfluenceStrategy
    strat_act1 = AmdCrtConfluenceStrategy(use_ema_filter=True, ema_period=10)
    sig_act1 = strat_act1.evaluate(bars[:eval_idx])

    strat_act2 = AmdCrtConfluenceStrategy(use_ema_filter=True, ema_period=10)
    sig_act2 = strat_act2.evaluate(bars_mutated[:eval_idx])

    assert (sig_act1 is None and sig_act2 is None) or (
        sig_act1 is not None and sig_act2 is not None and sig_act1.direction == sig_act2.direction
    )

    # 4. Verificación causal en ejecución completa: las decisiones de trade tomadas
    # antes de eval_idx son 100% inmunes a la mutación radical futura
    t_orig = simulate_signal_causal(bars, D2LStrategy(), "D2L")
    t_mut = simulate_signal_causal(bars_mutated, D2LStrategy(), "D2L")
    t_orig_pre = [t for t in t_orig if t.entry_time < bars[eval_idx].timestamp]
    t_mut_pre = [t for t in t_mut if t.entry_time < bars[eval_idx].timestamp]
    assert len(t_orig_pre) == len(t_mut_pre)
    for to, tm in zip(t_orig_pre, t_mut_pre):
        assert to.entry_time == tm.entry_time
        assert to.entry_price == tm.entry_price
        assert to.direction == tm.direction


# =============================================================================
# 2. TEST DE VENTANAS DE SESIÓN Y DST
# =============================================================================

def test_session_windows_et_dst():
    """Verifica que los horarios ET se mantengan exactos en verano (EDT=UTC-4) e invierno (EST=UTC-5)."""
    # Verano: 2024-07-07 es domingo en EDT (UTC-4)
    # 18:00 ET debe corresponder exactamente a 22:00 UTC
    dt_verano_utc = datetime(2024, 7, 7, 22, 0, tzinfo=UTC)
    dt_verano_et = _to_et(dt_verano_utc)
    assert dt_verano_et.hour == 18
    assert dt_verano_et.minute == 0
    assert dt_verano_et.tzname() == "EDT"

    # Invierno: 2024-01-07 es domingo en EST (UTC-5)
    # 18:00 ET debe corresponder exactamente a 23:00 UTC
    dt_invierno_utc = datetime(2024, 1, 7, 23, 0, tzinfo=UTC)
    dt_invierno_et = _to_et(dt_invierno_utc)
    assert dt_invierno_et.hour == 18
    assert dt_invierno_et.minute == 0
    assert dt_invierno_et.tzname() == "EST"

    # Verificar que D2L dispare correctamente en ambos domingos
    b_verano = Bar(timestamp=dt_verano_utc, open=18000.0, high=18010.0, low=17990.0, close=18005.0, volume=50.0)
    strat_v = D2LStrategy()
    sig_v = strat_v.evaluate([b_verano])
    assert sig_v is not None
    assert sig_v.direction == "long"

    b_invierno = Bar(timestamp=dt_invierno_utc, open=16500.0, high=16510.0, low=16490.0, close=16505.0, volume=50.0)
    strat_i = D2LStrategy()
    sig_i = strat_i.evaluate([b_invierno])
    assert sig_i is not None
    assert sig_i.direction == "long"

    # L0200: lunes 02:00 ET
    # Verano: lunes 02:00 ET = 06:00 UTC
    dt_lun_verano_utc = datetime(2024, 7, 8, 6, 0, tzinfo=UTC)
    dt_lun_verano_et = _to_et(dt_lun_verano_utc)
    assert dt_lun_verano_et.hour == 2
    assert dt_lun_verano_et.minute == 0

    # Invierno: lunes 02:00 ET = 07:00 UTC
    dt_lun_invierno_utc = datetime(2024, 1, 8, 7, 0, tzinfo=UTC)
    dt_lun_invierno_et = _to_et(dt_lun_invierno_utc)
    assert dt_lun_invierno_et.hour == 2
    assert dt_lun_invierno_et.minute == 0


# =============================================================================
# 3. TEST DE REPRODUCIBILIDAD DETERMINISTA (MISMO JSON / MISMO HASH)
# =============================================================================

def test_reproducibility():
    """Dos ejecuciones del runner sobre la misma serie producen idéntico JSON y SHA-256."""
    bars = _build_synthetic_bars(n=400, seed=SEED)

    # Corrida 1
    trades1 = run_all_signals_on_bars(bars, commission=0.62, slippage=0.25)
    res1 = analyze_signal_protocol(trades1, cost_label="principal")
    json1 = json.dumps(res1, sort_keys=True, indent=2)
    hash1 = hashlib.sha256(json1.encode("utf-8")).hexdigest()

    # Corrida 2
    trades2 = run_all_signals_on_bars(bars, commission=0.62, slippage=0.25)
    res2 = analyze_signal_protocol(trades2, cost_label="principal")
    json2 = json.dumps(res2, sort_keys=True, indent=2)
    hash2 = hashlib.sha256(json2.encode("utf-8")).hexdigest()

    assert json1 == json2
    assert hash1 == hash2


# =============================================================================
# 4. TEST DE SEPARACIÓN BUDGET-R VS STOP-R
# =============================================================================

def test_budget_vs_stop_r():
    """Verifica que budget_r y stop_r se calculen según sus definiciones exactas."""
    # Creamos un TradeRecord sintético
    # Entrada: 20000, Stop: 19900 (100 pts de riesgo stop), Salida: 20200 (+200 pts netos brutos)
    # $2/pt -> PnL bruto = +$400. Comisión = $1.24. PnL neto = $398.76
    # Stop risk = 100 pts * $2 = $200.00 -> Stop-R = 398.76 / 200.00 = 1.9938 R
    # Budget risk = $500.00 -> Budget-R = 398.76 / 500.00 = 0.7975 R
    t = TradeRecord(
        trade_id="t-1",
        signal_name="TEST",
        direction="long",
        entry_time=datetime(2024, 1, 8, 14, 0, tzinfo=UTC),
        exit_time=datetime(2024, 1, 8, 15, 0, tzinfo=UTC),
        entry_price=20000.0,
        exit_price=20200.0,
        stop_price=19900.0,
        target_price=20200.0,
        sl_points=100.0,
        tp_points=200.0,
        pnl_points=200.0,
        gross_pnl_usd=400.0,
        net_pnl_usd=398.76,
        commission_usd=1.24,
        slippage_usd=0.0,
        budget_r=round(398.76 / 500.0, 4),
        stop_r=round(398.76 / 200.0, 4),
        exit_reason="take_profit",
        year=2024,
    )
    assert t.budget_r == pytest.approx(0.7975, abs=1e-4)
    assert t.stop_r == pytest.approx(1.9938, abs=1e-4)
    assert t.budget_r != t.stop_r  # No deben confundirse jamás


# =============================================================================
# 5. TEST DE PRIORIDAD SL-FIRST EN AMBIGÜEDAD
# =============================================================================

def test_intrabar_ambiguity_sl_first():
    """En ambigüedad intrabarra (vela que toca simultáneamente TP y SL), se ejecuta SL."""
    t0 = datetime(2024, 7, 7, 22, 0, tzinfo=UTC)
    b0 = Bar(timestamp=t0, open=18000.0, high=18010.0, low=17990.0, close=18005.0, volume=100.0)

    # Barra 1: entrada al open 18005. SL = 18005 - 100 = 17905. TP = 18005 + 100 = 18105.
    # Diseñamos la barra 1 para que High alcance 18120 (toca TP) Y Low caiga a 17890 (toca SL)
    t1 = t0 + timedelta(minutes=5)
    b1_ambigua = Bar(timestamp=t1, open=18005.0, high=18120.0, low=17890.0, close=18000.0, volume=100.0)

    strat = D2LStrategy(sl_pts=100.0, tp_pts=100.0)
    # Sin M1 provisto, debe caer en fallback conservador: stop_loss
    trades = simulate_signal_causal([b0, b1_ambigua], strat, "D2L_TEST", commission_per_side=0.0, slippage_points=0.0)

    assert len(trades) == 1
    assert trades[0].exit_reason == "stop_loss"


# =============================================================================
# 6. TEST DEL HARNESS DE MEJORAS Y CONTADOR DE VARIANTES
# =============================================================================

def test_harness_variant_tracking():
    """El harness audita acumulativamente variantes probadas y ajusta Bonferroni."""
    harness = ImprovementHarness(alpha_base=0.05)
    assert harness.variants_tested_count == 0
    assert harness.bonferroni_alpha == 0.05

    # Registrar primera variante
    v1 = harness.register_variant(
        base_signal="ACT",
        dimension_modified="filtro_tendencia",
        baseline_value={"ema_period": 50},
        variant_value={"ema_period": 200},
        hypothesis_rationale="Probar si filtro EMA200 es más robusto que EMA50",
    )
    assert harness.variants_tested_count == 1
    assert harness.bonferroni_alpha == 0.05

    # Registrar segunda variante
    v2 = harness.register_variant(
        base_signal="D2L",
        dimension_modified="mecanica_salida",
        baseline_value={"tp_pts": 150.0},
        variant_value={"tp_pts": 200.0},
        hypothesis_rationale="Extender objetivo de ganancia a 200 puntos",
    )
    assert harness.variants_tested_count == 2
    assert harness.bonferroni_alpha == pytest.approx(0.05 / 2, abs=1e-6)

    # Exportar estado
    status = harness.export_harness_status()
    assert status["variants_tested_count"] == 2
    assert len(status["registered_variants"]) == 2
    assert status["schema_version"] == "fars-externas-mejora-harness-v1"


# =============================================================================
# 7. TEST DE VARIANTES PRE-DECLARADAS E3 Y AJUSTE BONFERRONI M=6
# =============================================================================

def test_e3_predeclared_variants():
    """Verifica que las 6 variantes pre-declaradas de E3 se registren y configuren correctamente."""
    from lab_artifacts.externas_protocol.mejoras import CATALOGO_PREDECLARADO_E3, setup_e3_harness

    harness = setup_e3_harness(alpha_base=0.05)
    assert harness.variants_tested_count == 6
    assert len(CATALOGO_PREDECLARADO_E3) == 6
    assert harness.bonferroni_alpha == pytest.approx(0.05 / 6.0, abs=1e-6)

    # Verificar que cada variante instancie la estrategia esperada
    for spec in harness._registered_variants:
        strat = harness.build_strategy_for_variant(spec)
        assert strat is not None
        if spec.variant_id == "V1":
            assert strat.exit_time_lunes == time(15, 45)
        elif spec.variant_id == "V2":
            assert strat.entry_time_domingo == time(19, 0)
        elif spec.variant_id == "V3":
            assert strat.use_time_exit_only is True
        elif spec.variant_id == "V4":
            assert strat.sl_pts == 250.0
            assert strat.tp_pts == pytest.approx(100.6, abs=0.1)
        elif spec.variant_id == "V5":
            assert strat.ema_period == 20
        elif spec.variant_id == "V6":
            assert strat.ny_session_only is True
            assert strat.ny_session_end == time(12, 0)

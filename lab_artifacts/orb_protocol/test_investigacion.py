#!/usr/bin/env python3
"""
test_investigacion.py — pruebas de los scripts de investigación estadística (FASE 2).

Verifica:
  1. MBB sin costura: índices reales generados por producción nunca envuelven (sin % n, starts <= n - block_size).
  2. Semillas reproducibles: dos corridas con master_seed=20260928 sobre run_full_bootstrap son bit a bit idénticas.
  3. Sensibilidad de costes monótona: costes RT esperados y monotonía estricta en escenarios separados contra producción.
  4. Separación S/A/R consistente: S = A + R en el ledger y exportación de ledger aceptado.
"""
from __future__ import annotations

import math
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, r"E:\FARS-LAB\FARS")
sys.path.insert(0, str(HERE))
sys.path.insert(0, r"E:\FARS-LAB\ext_review2\nq-intraday-breakout\src")

import bootstrap_camino as bc  # noqa: E402
import separar_senales as ss  # noqa: E402
import sensibilidad_costes as sc  # noqa: E402
from run_orb import MNQ_CFG  # noqa: E402
from src.metrics import compute_metrics  # noqa: E402
from src.types import FundedAccountRules  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

LEDGER_CSV = HERE / "ledger_orb_fars_stop.csv"
ACCEPTED_CSV = HERE / "ledger_orb_fars_accepted.csv"


def test_mbb_sin_costura():
    """1. Fix H10: Verifica sobre índices REALES de producción que el MBB sin envoltura nunca envuelve ni usa índices >= n."""
    n = 200
    block_size = 15
    max_start = n - block_size  # 185
    k = math.ceil(n / block_size)
    B = 500

    # Observar directamente la matriz de índices generada por la función de producción
    idx_matrix = bc.generate_mbb_unwrapped_indices(n=n, block_size=block_size, B=B, seed=20260928)

    assert idx_matrix.shape == (B, n), f"Forma esperada {(B, n)}, obtenida {idx_matrix.shape}"
    assert np.all(idx_matrix >= 0), "No deben existir índices negativos"
    assert np.all(idx_matrix < n), f"Ningún índice debe ser >= n ({n})"

    # Verificar cada réplica y bloque
    for b_idx in range(B):
        row = idx_matrix[b_idx]
        for blk_i in range(k):
            start_pos = blk_i * block_size
            end_pos = min(n, start_pos + block_size)
            block = row[start_pos:end_pos]
            if len(block) > 1:
                diffs = np.diff(block)
                # Cada bloque debe ser estrictamente secuencial (+1) y SIN salto modular ni envoltura
                assert np.all(diffs == 1), (
                    f"Réplica {b_idx}, bloque {blk_i} tiene saltos no unitarios (posible wrap modular): {block}"
                )
                assert not np.any(diffs < 0), f"Réplica {b_idx} tiene diff negativo (envoltura prohibida): {block}"

    # Verificar también que run_unwrapped_mbb ejecuta sin errores sobre serie real con fechas
    r = np.sin(np.linspace(0, 20, n))
    dates = np.array([f"2026-06-{(i % 20) + 1:02d}" for i in range(n)])
    dd, st, acts = bc.run_unwrapped_mbb(r, block_size=10, B=100, seed=12345, dates=dates)
    assert len(dd) == 100
    assert len(st) == 100
    assert len(acts) == 100
    assert np.all(np.isfinite(dd))
    assert np.all(st >= 0)


def test_semillas_reproducibles():
    """2. Fix H10: Verifica que dos corridas completas de run_full_bootstrap con master_seed=20260928 son bit a bit idénticas."""
    assert LEDGER_CSV.exists(), f"Falta el ledger {LEDGER_CSV}"

    res1 = bc.run_full_bootstrap(LEDGER_CSV, master_seed=20260928, B=2000)
    res2 = bc.run_full_bootstrap(LEDGER_CSV, master_seed=20260928, B=2000)

    # 1. Métodos y bloques óptimos
    assert res1["method_used_gross"] == res2["method_used_gross"], "Método reportado debe ser idéntico"
    assert res1["method_used_allowed"] == res2["method_used_allowed"], "Método permitido debe ser idéntico"
    assert res1["blocks"] == res2["blocks"], "Bloques óptimos deben ser idénticos"

    # 2. Funcionales de camino brutos
    pf1_g = res1["gross"]["path_functionals"]
    pf2_g = res2["gross"]["path_functionals"]
    assert pf1_g == pf2_g, "Funcionales de camino brutos deben ser idénticos"

    # 3. Funcionales de camino riesgo-permitido
    pf1_a = res1["allowed"]["path_functionals"]
    pf2_a = res2["allowed"]["path_functionals"]
    assert pf1_a == pf2_a, "Funcionales de camino permitidos deben ser idénticos"

    # 4. Probabilidades de límites
    assert res1["gross"]["limit_probabilities"] == res2["gross"]["limit_probabilities"]
    assert res1["allowed"]["limit_probabilities"] == res2["allowed"]["limit_probabilities"]

    # 5. Estimandos oficiales FARS
    est1 = res1["gross"]["fars_bootstrap"]["estimands"]
    est2 = res2["gross"]["fars_bootstrap"]["estimands"]
    for k in est1:
        assert est1[k]["value"] == est2[k]["value"], f"Estimando {k} debe ser idéntico"
        assert est1[k]["intervals"] == est2[k]["intervals"], f"Intervalos para {k} deben ser idénticos"


def test_sensibilidad_costes_monotona():
    """3. Fix H11: Comprueba costes RT esperados y escenarios separados (comisión vs slippage) contra la implementación real."""
    # A. Comprobar costes RT esperados en todos los escenarios contra la implementación real
    expected_rt_costs = {
        "base": 2.74,             # 2*0.62 + 2*1.5*0.50
        "x1.5": 4.11,             # 2*0.93 + 2*2.25*0.50
        "x2.0": 5.48,             # 2*1.24 + 2*3.00*0.50
        "solo_comision_x2": 3.98, # 2*1.24 + 2*1.5*0.50
        "solo_slippage_x2": 4.24, # 2*0.62 + 2*3.0*0.50
    }
    for name, expected_cost in expected_rt_costs.items():
        assert name in sc.COST_SCENARIOS, f"Escenario {name} debe estar en COST_SCENARIOS"
        params = sc.COST_SCENARIOS[name]
        cfg = replace(
            MNQ_CFG,
            commission_per_side=params["commission"],
            slippage_ticks_per_side=params["slippage_ticks"],
        )
        assert round(cfg.round_turn_cost, 2) == expected_cost, (
            f"Escenario {name}: coste RT esperado {expected_cost}, obtenido {round(cfg.round_turn_cost, 2)}"
        )

    # B. Construcción determinista de 2 sesiones (requerido por SimFlags.require_prev_session)
    # garantizando trades de forma incondicional sin skips
    rows = []
    # Día 1: sesión previa (para cumplir require_prev_session)
    for m in range(0, 360, 5):  # 08:30 a 14:25
        t = datetime(2026, 6, 1, 8, 30) + pd.Timedelta(minutes=m)
        rows.append({"date": t, "open": 18000.0, "high": 18020.0, "low": 17980.0, "close": 18000.0, "volume": 100})

    # Día 2: sesión con breakout y target
    # 08:30 a 09:55: ventana ORB [17950.0, 18050.0]
    for m in range(0, 90, 5):
        t = datetime(2026, 6, 2, 8, 30) + pd.Timedelta(minutes=m)
        rows.append({"date": t, "open": 18000.0, "high": 18050.0, "low": 17950.0, "close": 18000.0, "volume": 100})

    # 10:00: barra de breakout (high=18070 > 18050)
    rows.append({"date": datetime(2026, 6, 2, 10, 0), "open": 18040.0, "high": 18070.0, "low": 18030.0, "close": 18065.0, "volume": 100})
    # 10:05: barra que alcanza target (target = 18050 + 200 = 18250)
    rows.append({"date": datetime(2026, 6, 2, 10, 5), "open": 18070.0, "high": 18280.0, "low": 18060.0, "close": 18270.0, "volume": 100})

    # Resto de sesión hasta las 14:30
    for m in range(10, 270, 5):
        t = datetime(2026, 6, 2, 10, 0) + pd.Timedelta(minutes=m)
        rows.append({"date": t, "open": 18270.0, "high": 18275.0, "low": 18265.0, "close": 18270.0, "volume": 100})

    df_slice = pd.DataFrame(rows)

    # C. Ejecutar la implementación de producción run_cost_sensitivity
    res = sc.run_cost_sensitivity(df_slice)

    # Verificaciones incondicionales (sin 'if trades > 0')
    assert res["base"]["trades"] > 0, "El slice determinista debe generar trades en el motor"
    n_trades = res["base"]["trades"]
    for name in sc.COST_SCENARIOS:
        assert res[name]["trades"] == n_trades, f"El número de trades debe ser idéntico en {name}"

    base_er = res["base"]["expectancy_r"]
    x15_er = res["x1.5"]["expectancy_r"]
    x20_er = res["x2.0"]["expectancy_r"]
    com_er = res["solo_comision_x2"]["expectancy_r"]
    slip_er = res["solo_slippage_x2"]["expectancy_r"]

    # 1. Monotonía estricta: base > x1.5 > x2.0
    assert base_er > x15_er > x20_er, (
        f"Violación de monotonía decreciente: base={base_er:.4f}, x1.5={x15_er:.4f}, x2.0={x20_er:.4f}"
    )

    # 2. Desglose separado: slippage x2 ($4.24 RT) debe degradar más que comisión x2 ($3.98 RT)
    assert com_er > slip_er, (
        f"Comisión x2 debe tener mayor E[R] que Slippage x2 (por menor coste RT): "
        f"com_er={com_er:.4f}, slip_er={slip_er:.4f}"
    )


def test_separacion_sar_consistente():
    """4. Verifica que la separación de señales cumple S = A + R, exporta el ledger aceptado y atribuye PnL a salidas."""
    assert LEDGER_CSV.exists(), f"Falta el ledger {LEDGER_CSV}"

    out = ss.run_separation(LEDGER_CSV, max_trades_month=42, export_accepted_path=ACCEPTED_CSV)

    s = out["n_signals"]
    a = out["n_accepted"]
    r = out["n_rejected"]

    assert s == 2444, f"Se esperaban 2.444 señales en el ledger, se obtuvieron {s}"
    assert s == a + r, f"Identidad violada: S={s} != A={a} + R={r}"
    assert a > 0, "Debe haber órdenes aceptadas"
    assert r > 0, "Debe haber órdenes rechazadas por límites"
    assert len(out["reasons"]) > 0, "Debe haber desglose de motivos de rechazo"

    # Verificar exportación de ledger aceptado (Fix H8)
    assert ACCEPTED_CSV.exists(), f"Debe existir el ledger exportado {ACCEPTED_CSV}"
    assert len(out["accepted_trades"]) == a

    # Verificar coherencia de métricas
    mg = out["metrics_gross"]
    ma = out["metrics_allowed"]
    assert mg.n_trades == s
    assert ma.n_trades == a


def test_atribucion_pnl_timestamp_salida(tmp_path=None):
    """5. Fix H6 & CRITICAL 5: Prueba atribución de PnL en función de producción run_separation.

    Verifica que run_separation procesa los exits según su exit_time real:
    - Trade 1 entra a las 10:15 y sale a las 10:45 con pérdida severa (-11R).
    - Trade 2 llega a las 11:30.
    Como Trade 1 salió a las 10:45 (antes de las 11:30), el PnL se liquida antes de evaluar Trade 2,
    por lo que Trade 2 ve la cuenta en pérdida diaria y es RECHAZADO por el motor de riesgo.
    En cambio, si Trade 1 saliera a las 14:30 (después de 11:30), Trade 2 sería aprobado al momento de su evaluación.
    """
    if tmp_path is None:
        import tempfile
        temp_dir = Path(tempfile.mkdtemp())
    else:
        temp_dir = tmp_path

    csv_file = temp_dir / "test_trades.csv"

    # Caso 1: Trade 1 sale temprano (10:45) -> PnL se aplica antes de Trade 2 (11:30)
    lines_early = [
        "trade_id,timestamp,exit_time,asset,direction,entry_price,stop_price,exit_price,r_result,strategy",
        "T1,2026-06-01T10:15:00-05:00,2026-06-01T10:45:00-05:00,MNQ,long,18000.0,17900.0,16900.0,-11.0,orb",
        "T2,2026-06-01T11:30:00-05:00,2026-06-01T12:00:00-05:00,MNQ,long,18000.0,17900.0,18200.0,2.0,orb",
    ]
    csv_file.write_text("\n".join(lines_early), encoding="utf-8")

    res_early = ss.run_separation(csv_file, max_trades_month=42)
    assert res_early["n_signals"] == 2
    assert res_early["n_accepted"] == 1, "Solo Trade 1 debió ser aceptado"
    assert res_early["n_rejected"] == 1, "Trade 2 debió ser rechazado por pérdida diaria previa de T1"
    assert res_early["rejected_trades"][0]["trade_id"] == "T2"

    # Caso 2: Trade 1 sale a las 14:30 (EOD) -> A las 11:30 Trade 1 sigue abierto y Trade 2 no ve la pérdida
    lines_late = [
        "trade_id,timestamp,exit_time,asset,direction,entry_price,stop_price,exit_price,r_result,strategy",
        "T1,2026-06-01T10:15:00-05:00,2026-06-01T14:30:00-05:00,MNQ,long,18000.0,17900.0,16900.0,-11.0,orb",
        "T2,2026-06-01T11:30:00-05:00,2026-06-01T12:00:00-05:00,MNQ,long,18000.0,17900.0,18200.0,2.0,orb",
    ]
    csv_file.write_text("\n".join(lines_late), encoding="utf-8")
    res_late = ss.run_separation(csv_file, max_trades_month=42)
    assert res_late["n_accepted"] == 2, "A las 11:30 Trade 1 sigue abierto, ambos trades deben ser aceptados"


def test_reporte_metodo_real_bootstrap():
    """6. Fix H7 & WARNING 1: Verifica que el reporte de bootstrap indica el método real y no confunde unsupported con CBB."""
    # 1. Verificación de clasificación canónica FARS
    assert bc.classify_bootstrap_method("iid_eligible") == "IID"
    assert bc.classify_bootstrap_method("dependent_resampling_candidate") == "CBB (dependent_resampling_candidate)"
    unsupported_label = bc.classify_bootstrap_method("unsupported_or_inconclusive")
    assert unsupported_label == "unsupported (unsupported_or_inconclusive)"
    assert "CBB" not in unsupported_label, "unsupported_or_inconclusive NUNCA debe etiquetarse como CBB"

    # 2. Verificación en ejecución real sobre ledger
    assert LEDGER_CSV.exists()
    res = bc.run_full_bootstrap(LEDGER_CSV, master_seed=20260928, B=2000)
    state_g = res["gross"]["fars_bootstrap"]["eligibility"]["state"]
    method_used_g = res["gross"]["method_used"]
    assert method_used_g == bc.classify_bootstrap_method(state_g)
    assert res["method_used_gross"] == method_used_g

    state_a = res["allowed"]["fars_bootstrap"]["eligibility"]["state"]
    method_used_a = res["allowed"]["method_used"]
    assert method_used_a == bc.classify_bootstrap_method(state_a)
    assert res["method_used_allowed"] == method_used_a


def test_camino_doble_bruto_y_permitido():
    """7. Fix H8: Verifica que run_full_bootstrap calcula funcionales de camino y límites para AMBOS caminos: bruto y riesgo-permitido."""
    assert LEDGER_CSV.exists()
    assert ACCEPTED_CSV.exists()
    res = bc.run_full_bootstrap(LEDGER_CSV, accepted_csv_path=ACCEPTED_CSV, master_seed=20260928, B=2000)

    # Comprobar presencia y completitud de ambos caminos
    assert "gross" in res and "allowed" in res
    assert res["n"] == 2444
    assert res["n_allowed"] > 0
    assert res["n_allowed"] <= res["n"]

    for path_key in ("gross", "allowed"):
        pf_mbb = res[path_key]["path_functionals"]["mbb_unwrapped"]
        assert "max_drawdown_ci_95" in pf_mbb
        assert "max_losing_streak_ci_95" in pf_mbb
        assert pf_mbb["max_drawdown_ci_95"][0] <= pf_mbb["max_drawdown_ci_95"][1]

        pf_sb = res[path_key]["path_functionals"]["stationary_bootstrap"]
        assert "max_drawdown_ci_95" in pf_sb
        assert "max_losing_streak_ci_95" in pf_sb

        lp = res[path_key]["limit_probabilities"]
        assert "p_drawdown_limit_exceeded" in lp
        assert "p_daily_loss_limit_exceeded" in lp
        assert "p_max_ops_limit_exceeded" in lp
        assert 0.0 <= lp["p_drawdown_limit_exceeded"] <= 1.0
        assert 0.0 <= lp["p_daily_loss_limit_exceeded"] <= 1.0
        assert 0.0 <= lp["p_max_ops_limit_exceeded"] <= 1.0

        # Semántica terminal mutuamente excluyente (Fix CRITICAL 1, 2)
        assert "p_target" in lp
        assert "p_breach_trailing" in lp
        assert "p_breach_daily" in lp
        assert "p_horizon_exhausted" in lp
        assert "p_breach_total" in lp
        p_sum = lp["p_target"] + lp["p_breach_trailing"] + lp["p_breach_daily"] + lp["p_horizon_exhausted"]
        assert abs(p_sum - 1.0) < 1e-6, f"Las 4 categorías terminales deben sumar 1.0 en {path_key}, suman {p_sum}"
        assert abs(lp["p_breach_total"] - (lp["p_breach_trailing"] + lp["p_breach_daily"])) < 1e-6, (
            f"P(breach total) debe ser la suma exacta de trailing + daily en {path_key}"
        )



def test_b3_probabilidades_responden_a_datos():
    """8. Fix B3 (CRITICAL 1 y 2): Verifica que las probabilidades de camino respondan a los datos y cumplan exclusión mutua."""
    # 1. Pérdidas severas diarias (-5R por trade) -> Daily loss limit se activa en trade 1 (PnL = -$5.000 <= -$2.000).
    # Con stop_on_daily_loss=True, la réplica se detiene de inmediato en breach_daily (hit_daily=True, hit_dd=False).
    r_loss_daily = np.full(50, -5.0)
    dates_loss_daily = np.asarray([f"2026-06-{(i % 20) + 1:02d}" for i in range(50)])
    _, _, acts_daily = bc.run_unwrapped_mbb(r_loss_daily, block_size=5, B=100, seed=123, dates=dates_loss_daily)
    p_daily = sum(x["hit_daily"] for x in acts_daily) / 100
    p_dd = sum(x["hit_dd"] for x in acts_daily) / 100
    p_target_loss = sum(x["passed"] for x in acts_daily) / 100
    assert p_daily == 1.0, f"Con pérdidas intradía severas P(Daily) debe ser 1.0, obtuvo {p_daily}"
    assert p_dd == 0.0, f"P(DD) debe ser 0.0 porque la parada diaria detiene la réplica antes de $8k DD, obtuvo {p_dd}"
    assert p_target_loss == 0.0, f"P(Target) debe ser 0.0 ante pérdidas severas, obtuvo {p_target_loss}"
    for x in acts_daily:
        assert x["terminal_condition"] == "breach_daily"
        assert (int(x["passed"]) + int(x["hit_dd"]) + int(x["hit_daily"]) + int(x["terminal_condition"] == "horizon_exhausted")) == 1

    # 1b. Pérdidas moderadas multi-día (-0.5R por trade, 1 trade/día) -> Daily loss NUNCA se activa (-$500 > -$2.000),
    # pero el drawdown acumulado alcanza el límite trailing ($8.000 USD), resultando en breach_trailing.
    r_loss_dd = np.full(30, -0.5)
    dates_loss_dd = np.asarray([f"2026-06-{i + 1:02d}" for i in range(30)])
    _, _, acts_dd = bc.run_unwrapped_mbb(r_loss_dd, block_size=5, B=100, seed=123, dates=dates_loss_dd)
    p_daily_b = sum(x["hit_daily"] for x in acts_dd) / 100
    p_dd_b = sum(x["hit_dd"] for x in acts_dd) / 100
    assert p_daily_b == 0.0, f"Con pérdidas diarias moderadas P(Daily) debe ser 0.0, obtuvo {p_daily_b}"
    assert p_dd_b == 1.0, f"Con DD acumulado >= $8.000 P(DD) debe ser 1.0, obtuvo {p_dd_b}"
    for x in acts_dd:
        assert x["terminal_condition"] == "breach_trailing"
        assert (int(x["passed"]) + int(x["hit_dd"]) + int(x["hit_daily"]) + int(x["terminal_condition"] == "horizon_exhausted")) == 1

    # 2. Ganancias continuas (+2R por trade) -> Drawdown y Daily loss limit NUNCA deben activarse (0.0),
    # y el Profit Target (+6.000 USD) se alcanza con éxito (1.0).
    r_gain = np.full(50, 2.0)
    dates_gain = np.asarray([f"2026-06-{(i % 20) + 1:02d}" for i in range(50)])
    _, _, acts_gain = bc.run_unwrapped_mbb(r_gain, block_size=5, B=100, seed=123, dates=dates_gain)
    p_dd_gain = sum(x["hit_dd"] for x in acts_gain) / 100
    p_daily_gain = sum(x["hit_daily"] for x in acts_gain) / 100
    p_target_gain = sum(x["passed"] for x in acts_gain) / 100
    assert p_dd_gain == 0.0, f"Con ganancias puras P(DD) debe ser 0.0, obtuvo {p_dd_gain}"
    assert p_daily_gain == 0.0, f"Con ganancias puras P(Daily) debe ser 0.0, obtuvo {p_daily_gain}"
    assert p_target_gain == 1.0, f"Con ganancias puras P(Target) debe ser 1.0, obtuvo {p_target_gain}"
    for x in acts_gain:
        assert x["terminal_condition"] == "target"
        assert (int(x["passed"]) + int(x["hit_dd"]) + int(x["hit_daily"]) + int(x["terminal_condition"] == "horizon_exhausted")) == 1

    # 3. Cupo mensual calendario: 50 operaciones en Junio 2026 (> 42 ops en mes calendario) -> P(max_ops) DEBE ser 1.0
    r_ops_high = np.full(50, 0.1)
    dates_ops_high = np.full(50, "2026-06-15")
    _, _, acts_high = bc.run_unwrapped_mbb(r_ops_high, block_size=5, B=100, seed=123, dates=dates_ops_high)
    p_ops_high = sum(x["hit_max_ops"] for x in acts_high) / 100
    assert p_ops_high == 1.0, f"Con 50 ops en mes calendario P(max_ops) debe ser 1.0, obtuvo {p_ops_high}"

    # 4. Cupo mensual calendario: 10 operaciones en Junio 2026 (<= 42 ops) -> P(max_ops) DEBE ser 0.0
    r_ops_low = np.full(10, 0.1)
    dates_ops_low = np.full(10, "2026-06-15")
    _, _, acts_low = bc.run_unwrapped_mbb(r_ops_low, block_size=5, B=100, seed=123, dates=dates_ops_low)
    p_ops_low = sum(x["hit_max_ops"] for x in acts_low) / 100
    assert p_ops_low == 0.0, f"Con 10 ops en mes calendario P(max_ops) debe ser 0.0, obtuvo {p_ops_low}"

    # 5. Sizing monetario dependiente de equity (Fix WARNING 3):
    # A $100.000: riesgo 1% = $1.000 -> 5 contratos ($1.000 por 1R).
    # Al caer a $99.000: riesgo 1% = $990 -> 4 contratos ($800 por 1R).
    r_sizing = np.array([-1.0, -1.0])
    dates_sizing = np.array(["2026-06-01", "2026-06-02"])
    _, _, acts_sz = bc.run_unwrapped_mbb(r_sizing, block_size=2, B=1, seed=42, dates=dates_sizing)
    assert not acts_sz[0]["hit_dd"]
    assert not acts_sz[0]["hit_daily"]
    assert acts_sz[0]["sizes"] == [5, 4], f"Esperaba escalonamiento de contratos [5, 4], obtuvo {acts_sz[0].get('sizes')}"



def test_ledger_export_exit_time_real(tmp_path=None):
    """Regresión R4/CR4: ledger_fars exporta el exit_time real (sin fallback 14:30
    inventado) y sobrevive a timestamps NaT — el bug tz_localize(ambiguous='infer')
    de los exports pesados no tenía cobertura de tests."""
    from types import SimpleNamespace

    import run_orb

    if tmp_path is None:
        import tempfile
        tmp_path = Path(tempfile.mkdtemp(prefix="orb_ledger_test_"))
    trades = pd.DataFrame([
        {"entry_time": datetime(2026, 7, 2, 9, 35), "exit_time": datetime(2026, 7, 2, 10, 45),
         "direction": "long", "entry_price": 20000.0, "exit_price": 20050.0, "stop_points": 100.0},
        {"entry_time": datetime(2026, 7, 6, 10, 5), "exit_time": pd.NaT,
         "direction": "short", "entry_price": 20100.0, "exit_price": 20080.0, "stop_points": 100.0},
        # fold DST (hora ambigua 01:30 del fin del DST): convención determinista
        {"entry_time": datetime(2026, 11, 1, 0, 30), "exit_time": datetime(2026, 11, 1, 1, 30),
         "direction": "long", "entry_price": 20200.0, "exit_price": 20210.0, "stop_points": 100.0},
        # input ya tz-aware: no debe tronar ni re-localizar
        {"entry_time": pd.Timestamp("2026-07-07 09:35", tz="America/Chicago"),
         "exit_time": pd.Timestamp("2026-07-07 12:00", tz="America/Chicago"),
         "direction": "long", "entry_price": 20300.0, "exit_price": 20330.0, "stop_points": 100.0},
        # input tz-aware en UTC: debe normalizarse con tz_convert a America/Chicago (Fix WARNING 4)
        {"entry_time": pd.Timestamp("2026-07-08 14:00:00+00:00"),
         "exit_time": pd.Timestamp("2026-07-08 15:30:00+00:00"),
         "direction": "long", "entry_price": 20400.0, "exit_price": 20450.0, "stop_points": 100.0},
    ])
    res = SimpleNamespace(trades=trades)
    out = Path(tmp_path) / "ledger_test.csv"
    n = run_orb.ledger_fars(res, run_orb.MNQ_CFG, out, "test")
    assert n == 5
    df = pd.read_csv(out)
    assert "exit_time" in df.columns
    # exit_time REAL de la primera operación, no el fallback 14:30
    assert str(df.loc[0, "exit_time"]).startswith("2026-07-02T10:45:00")
    assert "T14:30" not in str(df.loc[0, "exit_time"])
    # timestamps tz-aware (offset CT, CDT en julio)
    assert str(df.loc[0, "timestamp"])[-6:] == "-05:00"
    # fila sin exit_time → vacío en el CSV, NUNCA inventado
    assert pd.isna(df.loc[1, "exit_time"]) or str(df.loc[1, "exit_time"]).strip() == ""
    # fold DST: convención determinista ambiguous=False → hora estándar (-06:00)
    assert str(df.loc[2, "exit_time"]).startswith("2026-11-01T01:30:00")
    assert str(df.loc[2, "exit_time"])[-6:] == "-06:00"
    # input tz-aware Chicago: se respeta su offset, sin crash de re-localización
    assert str(df.loc[3, "exit_time"]).startswith("2026-07-07T12:00:00")
    assert str(df.loc[3, "exit_time"])[-6:] == "-05:00"
    # input tz-aware UTC: se normaliza a America/Chicago (-05:00 en verano CDT) (Fix WARNING 4)
    assert str(df.loc[4, "timestamp"]).startswith("2026-07-08T09:00:00")
    assert str(df.loc[4, "timestamp"])[-6:] == "-05:00"
    assert str(df.loc[4, "exit_time"]).startswith("2026-07-08T10:30:00")
    assert str(df.loc[4, "exit_time"])[-6:] == "-05:00"


def test_mc_fondeo_dos_replicas_juguete_calculables_a_mano():
    """10. Fix CRITICAL 1, 2: Monte Carlo de Fondeo con réplicas de juguete calculables a mano.

    Verifica la semántica terminal mutuamente excluyente (Apex-like):
      - Réplica 1 (Éxito / Profit Target): parada terminal inmediata al alcanzar +$6.000 USD.
      - Réplica 2 (Fracaso / Daily Loss Breach): parada terminal inmediata al cruzar -$2.000 USD en el día.
      - Réplica 3 (Fracaso / Trailing DD Breach): parada terminal inmediata al cruzar $8.000 USD de DD acumulado.
      - Réplica 4 (Horizonte Agotado): finalización sin alcanzar target ni ningún breach.
      - Réplica 5 (Capa de Riesgo FARS pareada): veto de riesgo descarta la operación y la réplica continúa.
      - Exclusión mutua estricta: exactamente un estado terminal por réplica.
    """
    from datetime import datetime

    # Réplica 1: Profit Target alcanzado y parada estricta
    r_rep1 = np.array([2.0, 2.0, 2.0, -5.0])
    tl_rep1 = [
        datetime(2026, 6, 1, 10, 0, tzinfo=ZoneInfo("America/Chicago")),
        datetime(2026, 6, 2, 10, 0, tzinfo=ZoneInfo("America/Chicago")),
        datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("America/Chicago")),
        datetime(2026, 6, 4, 10, 0, tzinfo=ZoneInfo("America/Chicago")),
    ]
    res1 = bc.simulate_funded_trajectory(r_rep1, tl_rep1, profit_target_usd=6_000.0)
    assert res1["passed"] is True
    assert res1["terminal_condition"] == "target"
    assert res1["final_equity"] == 106_000.0
    assert res1["peak_equity"] == 106_000.0
    assert res1["trades_executed"] == 3  # El 4to trade (-5R) no se ejecutó tras parada terminal
    assert res1["sizes"] == [5, 5, 5]
    assert res1["hit_dd"] is False
    assert res1["hit_daily"] is False

    # Réplica 2: Pérdida diaria terminal (Apex hard stop: primer breach detiene la simulación)
    # Trade 1: r=-1.0, size 5, PnL=-$1000, equity=$99k, day_pnl=-$1000
    # Trade 2: r=-1.0, size 4, PnL=-$800, equity=$98.2k, day_pnl=-$1800
    # Trade 3: r=-1.0, size 4, PnL=-$800, equity=$97.4k, day_pnl=-$2600 <= -$2000 -> BREACH DIARIO TERMINAL
    r_rep2 = np.array([-1.0, -1.0, -1.0, -5.0, -5.0, 10.0])
    tl_rep2 = [
        datetime(2026, 6, 1, 10, 0, tzinfo=ZoneInfo("America/Chicago")),
        datetime(2026, 6, 1, 10, 15, tzinfo=ZoneInfo("America/Chicago")),
        datetime(2026, 6, 1, 10, 30, tzinfo=ZoneInfo("America/Chicago")),
        datetime(2026, 6, 2, 10, 0, tzinfo=ZoneInfo("America/Chicago")),
        datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("America/Chicago")),
        datetime(2026, 6, 4, 10, 0, tzinfo=ZoneInfo("America/Chicago")),
    ]
    res2 = bc.simulate_funded_trajectory(r_rep2, tl_rep2, profit_target_usd=6_000.0)
    assert res2["passed"] is False
    assert res2["hit_daily"] is True
    assert res2["hit_dd"] is False  # Estrictamente False: se detiene de inmediato sin solapamiento
    assert res2["terminal_condition"] == "breach_daily"
    assert res2["final_equity"] == 97_400.0
    assert res2["peak_equity"] == 100_000.0
    assert res2["trades_executed"] == 3  # Trades 4, 5, 6 NUNCA se ejecutan tras breach diario
    assert res2["sizes"] == [5, 4, 4]  # Sizing 5 -> 4 -> 4 contratos

    # Réplica 3: Trailing Drawdown terminal sin violar límite diario
    # Multi-día (1 trade/día): pérdidas diarias <= $2.000, pero DD acumulado alcanza $8.000 USD
    # Trade 1: r=-1.5, size 5, PnL=-$1500, eq=$98.500, DD=$1.500, day=-$1500
    # Trade 2: r=-2.0, size 4, PnL=-$1600, eq=$96.900, DD=$3.100, day=-$1600
    # Trade 3: r=-2.0, size 4, PnL=-$1600, eq=$95.300, DD=$4.700, day=-$1600
    # Trade 4: r=-2.0, size 4, PnL=-$1600, eq=$93.700, DD=$6.300, day=-$1600
    # Trade 5: r=-2.2, size 4, PnL=-$1760, eq=$91.940, DD=$8.060 >= $8.000 -> BREACH TRAILING TERMINAL
    r_rep3 = np.array([-1.5, -2.0, -2.0, -2.0, -2.2, 10.0])
    tl_rep3 = [
        datetime(2026, 6, 1, 10, 0, tzinfo=ZoneInfo("America/Chicago")),
        datetime(2026, 6, 2, 10, 0, tzinfo=ZoneInfo("America/Chicago")),
        datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("America/Chicago")),
        datetime(2026, 6, 4, 10, 0, tzinfo=ZoneInfo("America/Chicago")),
        datetime(2026, 6, 5, 10, 0, tzinfo=ZoneInfo("America/Chicago")),
        datetime(2026, 6, 8, 10, 0, tzinfo=ZoneInfo("America/Chicago")),
    ]
    res3 = bc.simulate_funded_trajectory(r_rep3, tl_rep3, profit_target_usd=6_000.0)
    assert res3["passed"] is False
    assert res3["hit_daily"] is False
    assert res3["hit_dd"] is True
    assert res3["terminal_condition"] == "breach_trailing"
    assert res3["final_equity"] == 91_940.0
    assert res3["trades_executed"] == 5  # El 6to trade (+10R) no se ejecutó
    assert res3["sizes"] == [5, 4, 4, 4, 4]

    # Réplica 4: Horizonte agotado sin target ni breach
    r_rep4 = np.array([0.5, 0.5])
    tl_rep4 = [
        datetime(2026, 6, 1, 10, 0, tzinfo=ZoneInfo("America/Chicago")),
        datetime(2026, 6, 2, 10, 0, tzinfo=ZoneInfo("America/Chicago")),
    ]
    res4 = bc.simulate_funded_trajectory(r_rep4, tl_rep4, profit_target_usd=6_000.0)
    assert res4["passed"] is False
    assert res4["hit_daily"] is False
    assert res4["hit_dd"] is False
    assert res4["terminal_condition"] == "horizon_exhausted"
    assert res4["trades_executed"] == 2
    assert res4["final_equity"] == 101_000.0

    # Réplica 5: Capa de riesgo FARS pareada (use_risk_engine=True):
    # Un veto de riesgo descarta la operación (trades_vetoed=1), la réplica continúa sin cambio en equity
    r_rep5 = np.array([2.0, 2.0, 2.0])
    tl_rep5 = [
        datetime(2026, 6, 1, 10, 0, tzinfo=ZoneInfo("America/Chicago")),
        datetime(2026, 6, 1, 10, 15, tzinfo=ZoneInfo("America/Chicago")),
        datetime(2026, 6, 2, 10, 0, tzinfo=ZoneInfo("America/Chicago")),
    ]
    custom_rules = FundedAccountRules(
        initial_balance=100_000.0,
        profit_target_pct=0.10,
        max_drawdown_pct=0.08,
        daily_loss_limit_pct=0.02,
        risk_per_trade=0.01,
        daily_loss_base="initial",
        drawdown_mode="trailing",
        max_trades=None,
        daily_profit_target_usd=1_500.0,  # Tras Trade 1 (+2k), veta Trade 2 en el mismo día
        daily_loss_limit_usd=2_000.0,
        max_drawdown_usd=8_000.0,
        max_risk_dollars_per_order=1_000.0,
    )
    res5 = bc.simulate_funded_trajectory(
        r_rep5, tl_rep5, profit_target_usd=10_000.0,
        use_risk_engine=True, rules=custom_rules,
    )
    assert res5["trades_vetoed"] == 1, f"Trade 2 debió ser vetado por daily profit target, vetados: {res5['trades_vetoed']}"
    assert res5["trades_executed"] == 2, f"Debieron ejecutarse Trade 1 y Trade 3, ejecutados: {res5['trades_executed']}"

    # Réplica 6 (Regresión Fix R9): cambio de día NO debe producir vetos falsos.
    # Día 1: -1.5R (-$1.500, sin breach). Día 2: -0.8R (-$800) y luego +2.0R.
    # Pérdida real del día 2 = $800 < $2.000 → el trade ganador NO puede vetarse.
    # Sin el snapshot POSTERIOR al cierre, el engine tomaba como start_of_day la
    # equity pre-cierre del día 1 ($100.000) y vetaba por "pérdida diaria" de $2.300.
    r_rep6 = np.array([-1.5, -0.8, 2.0])
    tl_rep6 = [
        datetime(2026, 6, 1, 10, 0, tzinfo=ZoneInfo("America/Chicago")),
        datetime(2026, 6, 2, 10, 0, tzinfo=ZoneInfo("America/Chicago")),
        datetime(2026, 6, 2, 10, 15, tzinfo=ZoneInfo("America/Chicago")),
    ]
    rules_rep6 = FundedAccountRules(
        initial_balance=100_000.0,
        profit_target_pct=0.10,
        max_drawdown_pct=0.08,
        daily_loss_limit_pct=0.02,
        risk_per_trade=0.01,
        daily_loss_base="initial",
        drawdown_mode="trailing",
        max_trades=None,
        daily_profit_target_usd=None,
        daily_loss_limit_usd=2_000.0,
        max_drawdown_usd=8_000.0,
        max_risk_dollars_per_order=1_000.0,
    )
    res6 = bc.simulate_funded_trajectory(
        r_rep6, tl_rep6, profit_target_usd=10_000.0,
        use_risk_engine=True, rules=rules_rep6,
    )
    assert res6["trades_vetoed"] == 0, (
        f"Veto falso al cambiar de día (pérdida real día 2 = $640): {res6['trades_vetoed']}"
    )
    assert res6["trades_executed"] == 3, f"Las 3 debieron ejecutarse: {res6['trades_executed']}"
    assert res6["sizes"] == [5, 4, 4]  # step-down de sizing: 100k→5, 98.5k→4, 97.86k→4
    # 100.000 -1.500 (5×200×-1.5) -640 (4×200×-0.8) +1.600 (4×200×+2.0) = 99.460
    assert res6["final_equity"] == 99_460.0
    assert res6["terminal_condition"] == "horizon_exhausted"

    # Verificación de exclusión mutua estricta en todas las réplicas de prueba
    for r in [res1, res2, res3, res4, res5, res6]:
        tc_valid = r["terminal_condition"] in ("target", "breach_trailing", "breach_daily", "horizon_exhausted")
        assert tc_valid, f"Estado terminal no reconocido: {r['terminal_condition']}"
        assert (int(r["passed"]) + int(r["hit_dd"]) + int(r["hit_daily"]) + int(r["terminal_condition"] == "horizon_exhausted")) == 1



if __name__ == "__main__":
    fails = 0
    test_funcs = [
        test_mbb_sin_costura,
        test_semillas_reproducibles,
        test_sensibilidad_costes_monotona,
        test_separacion_sar_consistente,
        test_atribucion_pnl_timestamp_salida,
        test_reporte_metodo_real_bootstrap,
        test_camino_doble_bruto_y_permitido,
        test_b3_probabilidades_responden_a_datos,
        test_ledger_export_exit_time_real,
        test_mc_fondeo_dos_replicas_juguete_calculables_a_mano,
    ]
    for func in test_funcs:
        try:
            func()
            print(f"PASS {func.__name__}")
        except Exception as exc:  # noqa: BLE001
            fails += 1
            print(f"FAIL {func.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{'TODAS VERDES' if fails == 0 else f'{fails} FALLANDO'}")
    sys.exit(1 if fails else 0)

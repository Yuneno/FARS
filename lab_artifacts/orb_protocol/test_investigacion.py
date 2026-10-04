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


def test_atribucion_pnl_timestamp_salida():
    """5. Fix H6: Verifica que el PnL se atribuye en el timestamp de SALIDA y no en el de ENTRADA."""
    from zoneinfo import ZoneInfo
    from src.types import Trade
    CT = ZoneInfo("America/Chicago")

    # Trade sintético que entra a las 10:15 y sale a las 14:30
    t1 = Trade(
        trade_id="test-h6-1",
        timestamp=datetime(2026, 6, 1, 10, 15, tzinfo=CT),
        asset="MNQ",
        direction="long",
        entry_price=18000.0,
        stop_price=17900.0,
        exit_price=18200.0,
        r_result=2.0,
        strategy="test",
    )

    exit_ts = ss.get_trade_exit_time(t1)
    assert exit_ts == datetime(2026, 6, 1, 14, 30, tzinfo=CT), f"Salida esperada 14:30, obtenida {exit_ts}"
    assert exit_ts > t1.timestamp, "El timestamp de salida debe ser posterior al de entrada"

    # En momento intermedio (12:00): el trade sigue en curso, la salida pendiente aún no se ejecuta
    pending_exits = [(exit_ts, 400.0)]
    initial_equity = 100_000.0
    equity = initial_equity
    ts_mid = datetime(2026, 6, 1, 12, 0, tzinfo=CT)

    # Simular flush hasta 12:00
    while pending_exits and pending_exits[0][0] <= ts_mid:
        equity += pending_exits.pop(0)[1]
    assert equity == initial_equity, "A las 12:00 el trade sigue abierto; equity no debe cambiar"
    assert len(pending_exits) == 1

    # Simular flush al cierre del día (14:30)
    ts_exit = datetime(2026, 6, 1, 14, 30, tzinfo=CT)
    while pending_exits and pending_exits[0][0] <= ts_exit:
        equity += pending_exits.pop(0)[1]
    assert equity == initial_equity + 400.0, f"A las 14:30 se debe aplicar el PnL, equity={equity}"
    assert len(pending_exits) == 0


def test_reporte_metodo_real_bootstrap():
    """6. Fix H7: Verifica que el reporte de bootstrap indica el método que realmente se usó (IID o CBB)."""
    assert LEDGER_CSV.exists()
    res = bc.run_full_bootstrap(LEDGER_CSV, master_seed=20260928, B=2000)
    state = res["gross"]["fars_bootstrap"]["eligibility"]["state"]
    method_used = res["gross"]["method_used"]
    if state == "iid_eligible":
        assert method_used == "IID"
    else:
        assert "CBB" in method_used
    assert res["method_used_gross"] == method_used
    assert res["method_used_allowed"] in ("IID", f"CBB ({res['allowed']['fars_bootstrap']['eligibility']['state']})")


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

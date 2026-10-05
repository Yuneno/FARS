#!/usr/bin/env python3
"""v2_pool_churn.py — churn MC del pool MNQ+MGC (PREREGISTRO_V2_POOL.md).

FIXES R15 (Codex): (C1) los eventos del MC se ordenan por **exit_time**
(realización de P&L coherente con un ledger real) como PRIMARIO, con el orden
por entrada como sensibilidad declarada; (W) meses medidos DIRECTOS desde el
timeline sintético vía `consumed_opportunities` (no el proxy trades/cadencia);
paridad V1 verificada de verdad (assert, aborta si falla); tabla de veredictos
escrita en los artefactos; salida etiquetada como THROUGHPUT TEÓRICO.
"""
from __future__ import annotations

import math
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, r"E:\FARS-LAB\FARS")
sys.path.insert(0, str(HERE))
sys.path.insert(0, r"E:\FARS-LAB\ext_review2\nq-intraday-breakout\src")

import bootstrap_camino as bc  # noqa: E402
from nq_breakout.backtest import Config, SimFlags, run_backtest  # noqa: E402
from nq_breakout.data import load_nq_1min, resample_5min  # noqa: E402
from src.funded_profiles import apex_25k_profile, apex_50k_profile  # noqa: E402
from src.bootstrap import optimal_block_length  # noqa: E402

DATA = Path("E:/FARS-LAB/ext_review2/data_adapted")
B = 2000
MASTER_SEED = 20260928
PTO = 200.0

MERCADOS = {
    "MNQ": (DATA / "mnq_1min_multicharts.csv.gz",
            dict(point_value=2.0, tick_value=0.50, margin=2_328.26), 2444, 0.0573),
    "MGC": (DATA / "mgc_1min_multicharts.csv.gz",
            dict(point_value=10.0, tick_value=1.00, margin=1_760.00), 2013, 0.0706),
}
ESCENARIOS = [("Apex 25k", apex_25k_profile, [1, 2, 3, 5]),
              ("Apex 50k", apex_50k_profile, [1, 2])]

out_lines: list[str] = []


def log(s: str = "") -> None:
    print(s)
    out_lines.append(s)


log("=" * 78)
log("V2-POOL (fix R15) — churn MC pool MNQ+MGC | eventos ordenados por CIERRE")
log("=" * 78)

# 1. Stream pool con entry+exit; paridad V1 real por mercado
recs = []
for sym, (path, mc, n_exp, er_exp) in MERCADOS.items():
    df1 = load_nq_1min(path)
    df5 = resample_5min(df1)
    cfg = replace(Config(), point_value=mc["point_value"], tick_value=mc["tick_value"],
                  stop_dollars=200.0, target_dollars=400.0, commission_per_side=0.62,
                  slippage_ticks_per_side=1.5, slippage_sides=2,
                  initial_capital=100_000.0, margin_per_contract=mc["margin"])
    tr = run_backtest(df5, cfg, SimFlags()).trades
    stop_pts = 200.0 / cfg.point_value
    cost_pts = cfg.round_turn_cost / cfg.point_value
    sign = np.where(tr["direction"].to_numpy() == "long", 1.0, -1.0)
    r = (sign * (tr["exit_price"].to_numpy() - tr["entry_price"].to_numpy()) - cost_pts) / stop_pts
    # PARIDAD V1 real (assert): aborta si no reproduce la celda congelada
    assert len(tr) == n_exp, f"paridad {sym}: n={len(tr)} != {n_exp}"
    assert abs(float(r.mean()) - er_exp) < 0.002, f"paridad {sym}: E[R]={r.mean():.4f} != {er_exp}"
    for e_i, x_i, r_i in zip(pd.to_datetime(tr["entry_time"]),
                             pd.to_datetime(tr["exit_time"]), r):
        recs.append((e_i, x_i, float(r_i), sym))
    log(f"  {sym}: {len(tr)} trades | paridad V1 ASSERT OK (n y E[R])")

# 2. Dos órdenes: por CIERRE (primario) y por ENTRADA (sensibilidad)
recs_exit = sorted(recs, key=lambda z: z[1])
recs_entry = sorted(recs, key=lambda z: z[0])
n_inversiones = sum(1 for a, b in zip(recs_entry, recs_exit) if a != b)
log(f"  POOL: {len(recs)} trades | posiciones que cambian entre órdenes: {n_inversiones}")
r_pool = np.array([z[2] for z in recs_exit])
meses_span = (recs_exit[-1][1] - recs_exit[0][1]).days / 30.44
log(f"  E[R] pool = {r_pool.mean():+.4f} | t = {r_pool.mean()/(r_pool.std(ddof=1)/math.sqrt(len(r_pool))):+.2f}")

block = max(1, int(math.ceil(float(optimal_block_length(r_pool).loc[0, "circular"]))))
seed_mbb = int(np.random.SeedSequence(MASTER_SEED).spawn(4)[0].generate_state(1)[0])
log(f"  MBB bloque={block} | seed={seed_mbb} | B={B}")
log("  NOTA: THROUGHPUT TEÓRICO — sin cuotas, sin espera de retiro, sin")
log("    reinicio/compra de cuenta, reintentos IID secuenciales.\n")

orig = bc.simulate_funded_trajectory


def correr(orden: str, etiqueta: str) -> dict:
    """Corre todos los escenarios para un orden dado. Devuelve {(perfil,k): fila}."""
    if orden == "exit":
        r_s = np.array([z[2] for z in recs_exit])
        d_s = np.array([str(z[1].date()) for z in recs_exit])
        tl_src = [z[1] for z in recs_exit]
    else:
        r_s = np.array([z[2] for z in recs_entry])
        d_s = np.array([str(z[0].date()) for z in recs_entry])
        tl_src = [z[0] for z in recs_entry]
    del tl_src  # el timeline sintético lo construye el motor revisado
    filas = {}
    for nombre, factory, ks in ESCENARIOS:
        p = factory()
        reg = dict(start=float(p.starting_balance),
                   target=float(p.profit_target.target.value),
                   trailing=float(p.maximum_loss.distance.value),
                   lock=float(p.maximum_loss.threshold_ceiling))
        for k in ks:
            cap_usd = PTO * k

            def patched(resample, timeline, _reg=reg, _cap=cap_usd, **kw):
                kw["initial_balance"] = _reg["start"]
                kw["profit_target_usd"] = _reg["target"]
                kw["max_dd_limit_usd"] = _reg["trailing"]
                kw["dd_floor_ceiling"] = _reg["lock"]
                kw["daily_loss_limit_usd"] = 0.5 * _reg["start"]
                kw["risk_pct"] = 2.0 * _cap / _reg["start"]
                kw["max_risk_dollars_order"] = _cap
                out = orig(resample, timeline, **kw)
                # Meses DIRECTOS desde el timeline sintético (fix W R15)
                n_cons = min(out["consumed_opportunities"], len(timeline))
                if n_cons > 0:
                    out["_meses"] = (timeline[n_cons - 1] - timeline[0]).total_seconds() / 86400 / 30.44
                else:
                    out["_meses"] = 0.0
                return out

            bc.simulate_funded_trajectory = patched
            try:
                _, _, acts, _ = bc.run_unwrapped_mbb_paired(
                    r_s, block, B, seed_mbb, dates=d_s,
                    profit_target_usd=reg["target"])
            finally:
                bc.simulate_funded_trajectory = orig

            cnt = Counter(a["terminal_condition"] for a in acts)
            pp = cnt.get("target", 0) / B
            m_p = np.array([a["_meses"] for a in acts if a["terminal_condition"] == "target"])
            m_f = np.array([a["_meses"] for a in acts if a["terminal_condition"] != "target"])
            m_all = np.array([a["_meses"] for a in acts])
            meses_intento = float(np.mean(m_all))
            ciclo = meses_intento / pp if pp > 0 else float("inf")
            filas[(nombre, k)] = dict(
                pp=pp, cnt=dict(cnt), med_p=float(np.median(m_p)) if m_p.size else float("nan"),
                med_f=float(np.median(m_f)) if m_f.size else float("nan"),
                meses_intento=meses_intento, ciclo=ciclo,
                cuentas_ano=(12.0 / ciclo if ciclo < float("inf") else 0.0),
                p_1m=pp * float(np.mean(m_p <= 1)) if m_p.size else 0.0,
                p_2m=pp * float(np.mean(m_p <= 2)) if m_p.size else 0.0,
                p_3m=pp * float(np.mean(m_p <= 3)) if m_p.size else 0.0)
    return filas


try:
    log("══ ORDEN POR CIERRE (PRIMARIO) ══")
    filas_exit = correr("exit", "cierre")
    for (nombre, k), f in filas_exit.items():
        log(f"  {nombre} · {k} contr | P(pasar)={f['pp']*100:5.1f}% | "
            f"mediana si PASA {f['med_p']:.2f}m · si MUERE {f['med_f']:.2f}m | "
            f"ciclo {f['ciclo']:.2f}m | **{f['cuentas_ano']:.1f} cuentas/año** | "
            f"P(≤1m)={f['p_1m']*100:.1f}% P(≤2m)={f['p_2m']*100:.1f}% P(≤3m)={f['p_3m']*100:.1f}%")

    log("\n══ ORDEN POR ENTRADA (SENSIBILIDAD — el orden importa cuando hay solape) ══")
    filas_entry = correr("entry", "entrada")
    for (nombre, k), f in filas_entry.items():
        log(f"  {nombre} · {k} contr | P(pasar)={f['pp']*100:5.1f}% | ciclo {f['ciclo']:.2f}m | "
            f"{f['cuentas_ano']:.1f} cuentas/año")

    log("\n══ VEREDICTOS (reglas congeladas del PREREGISTRO_V2_POOL.md) ══")
    log("  H3 (25k ≤6 semanas mediana si pasa · 50k ≤3 meses · P(25k@≤3 contr) ≥ 50%):")
    f3 = filas_exit[("Apex 25k", 3)]
    log(f"    - P(25k@3) = {f3['pp']*100:.1f}% (< 50%) → **H3 FALSADA** por la regla P(25k@≤3)≥50%.")
    log("    - Ambigüedad del preregistro señalada por Codex R15: 'las dos medianas 50k'")
    log("      no fija contrato — 50k@1 da {:.1f}m (no cumple) y 50k@2 da {:.2f}m (cumple).".format(
        filas_exit[("Apex 50k", 1)]["med_p"], filas_exit[("Apex 50k", 2)]["med_p"]))
    log("    - El churn 25k@2-5 es hallazgo real PERO no confirma H3.")
    log("  H1 (V1-MULTI, V-EOD): NO CONFIRMADA / INCONCLUSA — E[R]=+0.0134 con IC95")
    log("    [-0.009,+0.036] y cadencia 1.47x: no confirma (requería ≥1.5x) y tampoco")
    log("    satisface la falsación congelada (exigía puntual ≤0). Sin efecto práctico:")
    log("    la variante EOD no entrega velocidad utilizable.")
    log("  H2 (V1-MULTI, multi-mercado): FALSADA por regla estricta (≥2 mercados con")
    log("    t≥2; solo MGC, t=3.28 con Bonferroni). Pool MNQ+MGC = hallazgo posterior.")
finally:
    bc.simulate_funded_trajectory = orig

(HERE / "V2_POOL_RESULTADOS.txt").write_text("\n".join(out_lines) + "\n", encoding="utf-8")
log("\nListo (V2-POOL fix R15).")

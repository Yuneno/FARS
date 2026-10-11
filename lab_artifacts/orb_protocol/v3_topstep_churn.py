#!/usr/bin/env python3
"""v3_topstep_churn.py — churn MC con las reglas REALES de Topstep
(PREREGISTRO_V3_TOPSTEP.md). Pool MNQ+MGC, eventos por CIERRE (primario)."""
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
from src.funded_profiles import topstep_50k_profile, topstep_100k_profile  # noqa: E402
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
# (nombre, factory, contratos, extras)
ESCENARIOS = [
    ("Topstep 50k", topstep_50k_profile, [1, 2, 3, 5], {}),
    ("Topstep 100k", topstep_100k_profile, [1, 2], {}),
]
SENSIBILIDADES = [
    ("Topstep 50k · min_días=2 (regla oficial)", topstep_50k_profile, 2, {"min_trading_days": 2}),
    ("Topstep 50k · DLL $1,000 terminal", topstep_50k_profile, 2, {"daily_loss_limit_usd": 1_000.0}),
]

out: list[str] = []


def log(s: str = "") -> None:
    print(s)
    out.append(s)


log("=" * 78)
log("V3-TOPSTEP — churn MC con reglas REALES de Topstep (pool MNQ+MGC, orden CIERRE)")
log("=" * 78)

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
    assert len(tr) == n_exp and abs(float(r.mean()) - er_exp) < 0.002, f"paridad {sym} FALLIDA"
    for e_i, x_i, r_i in zip(pd.to_datetime(tr["entry_time"]),
                             pd.to_datetime(tr["exit_time"]), r):
        recs.append((e_i, x_i, float(r_i), sym))
    log(f"  {sym}: {len(tr)} trades | paridad ASSERT OK")

recs_exit = sorted(recs, key=lambda z: z[1])
r_pool = np.array([z[2] for z in recs_exit])
d_pool = np.array([str(z[1].date()) for z in recs_exit])
log(f"  POOL: {len(recs)} trades | E[R]={r_pool.mean():+.4f} | "
    f"t={r_pool.mean()/(r_pool.std(ddof=1)/math.sqrt(len(r_pool))):+.2f}")

block = max(1, int(math.ceil(float(optimal_block_length(r_pool).loc[0, "circular"]))))
seed_mbb = int(np.random.SeedSequence(MASTER_SEED).spawn(4)[0].generate_state(1)[0])
log(f"  MBB bloque={block} | seed={seed_mbb} | B={B}")
log("  THROUGHPUT TEÓRICO — sin cuotas/esperas/reinicio; reintentos IID.\n")

orig = bc.simulate_funded_trajectory


def correr(nombre: str, factory, k: int, extras: dict) -> dict:
    p = factory()
    reg = dict(start=float(p.starting_balance),
               target=float(p.profit_target.target.value),
               mll=float(p.maximum_loss.distance.value),
               lock=float(p.maximum_loss.threshold_ceiling))
    cap_usd = PTO * k

    def patched(resample, timeline, _reg=reg, _cap=cap_usd, _ex=extras, **kw):
        kw["initial_balance"] = _reg["start"]
        kw["profit_target_usd"] = _reg["target"]
        kw["max_dd_limit_usd"] = _reg["mll"]
        kw["dd_floor_ceiling"] = _reg["lock"]          # Topstep: lock en starting balance
        kw["daily_loss_limit_usd"] = _ex.get("daily_loss_limit_usd", 0.5 * _reg["start"])
        kw["consistency_max_day_ratio"] = 0.5           # regla suave de consistencia
        kw["min_trading_days"] = _ex.get("min_trading_days", 5)  # conservador
        kw["risk_pct"] = 2.0 * _cap / _reg["start"]
        kw["max_risk_dollars_order"] = _cap
        out_r = orig(resample, timeline, **kw)
        n_cons = min(out_r["consumed_opportunities"], len(timeline))
        out_r["_meses"] = ((timeline[n_cons - 1] - timeline[0]).total_seconds() / 86400 / 30.44
                           if n_cons > 0 else 0.0)
        return out_r

    bc.simulate_funded_trajectory = patched
    try:
        _, _, acts, _ = bc.run_unwrapped_mbb_paired(
            r_pool, block, B, seed_mbb, dates=d_pool, profit_target_usd=reg["target"])
    finally:
        bc.simulate_funded_trajectory = orig

    cnt = Counter(a["terminal_condition"] for a in acts)
    pp = cnt.get("target", 0) / B
    m_p = np.array([a["_meses"] for a in acts if a["terminal_condition"] == "target"])
    m_all = np.array([a["_meses"] for a in acts])
    meses_intento = float(np.mean(m_all))
    ciclo = meses_intento / pp if pp > 0 else float("inf")
    fila = dict(pp=pp, cnt=dict(cnt), med_p=float(np.median(m_p)) if m_p.size else float("nan"),
                med_f=float(np.median([a["_meses"] for a in acts if a["terminal_condition"] != "target"]) or 0.0),
                ciclo=ciclo, cuentas_ano=(12.0 / ciclo if ciclo < float("inf") else 0.0),
                p_1m=pp * float(np.mean(m_p <= 1)) if m_p.size else 0.0,
                p_2m=pp * float(np.mean(m_p <= 2)) if m_p.size else 0.0)
    log(f"  {nombre} · {k} contr | P(pasar)={pp*100:5.1f}% | "
        f"mediana si PASA {fila['med_p']:.2f}m | ciclo {ciclo:.2f}m | "
        f"**{fila['cuentas_ano']:.1f} cuentas/año** | P(≤1m)={fila['p_1m']*100:.1f}% "
        f"P(≤2m)={fila['p_2m']*100:.1f}% | estados {dict(cnt)}")
    return fila


try:
    log("══ PRIMARIOS (5 días mín conservador, sin DLL, consistencia activa) ══")
    filas = {}
    for nombre, factory, ks, extras in ESCENARIOS:
        for k in ks:
            filas[(nombre, k)] = correr(nombre, factory, k, extras)

    log("\n══ SENSIBILIDADES ══")
    for nombre, factory, k, extras in SENSIBILIDADES:
        correr(nombre, factory, k, extras)

    log("\n══ VEREDICTO H6 (regla congelada) ══")
    f = filas[("Topstep 50k", 2)]
    ok = f["pp"] >= 0.45 and f["med_p"] <= 2.0
    log(f"  50k@2 contratos: P={f['pp']*100:.1f}% (≥45%: {'sí' if f['pp']>=0.45 else 'NO'}) · "
        f"mediana si pasa {f['med_p']:.2f}m (≤2m: {'sí' if f['med_p']<=2.0 else 'NO'})")
    log(f"  → **H6 {'CONFIRMADA' if ok else 'FALSADA'}**")
finally:
    bc.simulate_funded_trajectory = orig

(HERE / "V3_TOPSTEP_RESULTADOS.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
log("\nListo (V3-TOPSTEP).")

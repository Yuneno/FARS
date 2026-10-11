#!/usr/bin/env python3
"""T4 — Pool MNQ+MGC bajo reglas Topstep 50K con flat 15:10 CT (plan semana 12-oct).

Misma semántica que T1 (V-EOD = flat por sesión) sobre el pool MNQ+MGC (V2-POOL).
MGC se lee con la desconfianza de la lección M8 (su 'edge' ya fue artefacto de escala una vez).
Escenarios predeclarados: 1 y 2 contratos (iso-riesgo $200/contrato), consistencia 0.55/0.50,
sin DLL (0.5*balance, convención declarada). Controles sin-deriva e iid emparejado.
B=2000, semilla maestra 20260928. Paridad: celdas MNQ/eod (3594, +0.0134) y MGC/eod (3489, -0.0003).
"""
from __future__ import annotations

import datetime as dt
import hashlib
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
from src.funded_profiles import topstep_50k_profile  # noqa: E402
from src.bootstrap import optimal_block_length  # noqa: E402

DATA = Path("E:/FARS-LAB/ext_review2/data_adapted")
B = 2000
MASTER_SEED = 20260928
PTO = 200.0
NO_DLL = 25_000.0

MERCADOS = {
    "MNQ": (DATA / "mnq_1min_multicharts.csv.gz",
            dict(point_value=2.0, tick_value=0.50, margin=2_328.26), 3594, 0.0134),
    "MGC": (DATA / "mgc_1min_multicharts.csv.gz",
            dict(point_value=10.0, tick_value=1.00, margin=1_760.00), 3489, -0.0003),
}
# PARIDAD_V2 (F1 Fix B, 2026-10-11; INVALIDADA en ronda 2 por Codex §4.1: los
# valores capturados incluían 25 entradas indebidas a las 14:30 — ver §9 de
# F1_FIXES_MOTOR.md).
# ANTES (trade_end=14:30 para entradas y cierre): MNQ (3594, +0.0134),
#   MGC (3489, -0.0003) — valores arriba conservados como referencia ANTES.
# DESPUÉS (ronda 2): PENDIENTE de recaptura por Hermes tras aplicar C1
#   (entradas == legacy; E[R] cambia solo por salidas 15:10). Valores previos
#   invalidados: MNQ (3619, 0.0128), MGC (3498, 0.0013) — NO reutilizar.
# Digest canónico por mercado (F1 ronda 2, Codex §4.4): SHA-256 de
# to_csv(index=False) con columnas fijas CANON_COLS y orden establecido
# (entry_time, exit_time, direction). Congela cada ledger bit a bit.
CANON_COLS = ["entry_time", "exit_time", "direction", "entry_price", "exit_price"]
PARIDAD_V2: dict[str, tuple[int, float, str] | None] = {
    # Capturado por Hermes 2026-10-11 (ronda 2, tras fix C1). Congelado con pandas 3.0.5.
    "MNQ": (3594, 0.012892237061769599,
            "9a1261c827ac92bcd5a2410f2fd6c7731f3b09ae8bbc196f8bc64c60c59cf536"),
    "MGC": (3489, 0.0013250788191456944,
            "d1ca91cae813e4c33bceba5ec2f11a7ad4856c1ac4030fa99dce46db56d9f1a5"),
}

out: list[str] = []


def log(s: str = "") -> None:
    print(s)
    out.append(s)


log("=" * 78)
log("T4 — POOL MNQ+MGC · reglas Topstep 50K · flat 15:10 CT (V-EOD)")
log("=" * 78)

recs = []
for sym, (path, mc, n_exp, er_exp) in MERCADOS.items():
    df1 = load_nq_1min(path)
    df5 = resample_5min(df1)
    cfg = replace(Config(), point_value=mc["point_value"], tick_value=mc["tick_value"],
                  stop_dollars=200.0, target_dollars=400.0, commission_per_side=0.62,
                  slippage_ticks_per_side=1.5, slippage_sides=2,
                  initial_capital=100_000.0, margin_per_contract=mc["margin"],
                  max_long_hold_days=1,                     # V-EOD = flat diario
                  entry_end=dt.time(14, 30), flat_time=dt.time(15, 10))  # F1 Fix B: flat Topstep
    tr = run_backtest(df5, cfg, SimFlags()).trades
    stop_pts = 200.0 / cfg.point_value
    cost_pts = cfg.round_turn_cost / cfg.point_value
    sign = np.where(tr["direction"].to_numpy() == "long", 1.0, -1.0)
    r = (sign * (tr["exit_price"].to_numpy() - tr["entry_price"].to_numpy()) - cost_pts) / stop_pts
    canon = tr[CANON_COLS].copy()
    canon = canon.sort_values(["entry_time", "exit_time", "direction"], kind="mergesort").reset_index(drop=True)
    digest = hashlib.sha256(canon.to_csv(index=False).encode("utf-8")).hexdigest()
    er_full = float(r.mean())
    exp = PARIDAD_V2[sym]
    if exp is None:
        log(f"  {sym}: {len(tr)} trades | E[R]={r.mean():+.4f} (full {er_full!r}) | LEDGER_SHA256={digest} | "
            f"PARIDAD_V2 PENDIENTE de captura (ANTES n={n_exp}, E[R]={er_exp:+.4f})")
    else:
        assert len(tr) == exp[0], f"paridad V2 {sym} n FALLIDA: {len(tr)} != {exp[0]}"
        assert abs(er_full - exp[1]) < 1e-12, f"paridad V2 {sym} E[R] FALLIDA: {er_full!r}"
        assert digest == exp[2], f"paridad V2 {sym} SHA FALLIDA: {digest}"
        log(f"  {sym}: {len(tr)} trades | E[R]={r.mean():+.4f} (full {er_full!r}) | LEDGER_SHA256={digest} | "
            f"paridad V2 ASSERT OK")
    for x_i, r_i in zip(pd.to_datetime(tr["exit_time"]), r):
        recs.append((x_i, float(r_i), sym))

recs_exit = sorted(recs, key=lambda z: z[0])
r_pool = np.array([z[1] for z in recs_exit])
d_pool = np.array([str(z[0].date()) for z in recs_exit])
t_pool = r_pool.mean() / (r_pool.std(ddof=1) / math.sqrt(len(r_pool)))
log(f"  POOL: {len(recs)} trades | E[R]={r_pool.mean():+.4f} | t={t_pool:+.2f}")

block = max(1, int(math.ceil(float(optimal_block_length(r_pool).loc[0, "circular"]))))
seed_mbb = int(np.random.SeedSequence(MASTER_SEED).spawn(4)[0].generate_state(1)[0])
seed_iid = int(np.random.SeedSequence(MASTER_SEED).spawn(4)[1].generate_state(1)[0])
log(f"  MBB bloque={block} | seed_mbb={seed_mbb} | seed_iid={seed_iid} | B={B}")
log("  NOTA M8: el 'edge' de MGC fue artefacto de escala una vez — leer con desconfianza.")
log("")

p = topstep_50k_profile()
REG = dict(start=float(p.starting_balance), target=float(p.profit_target.target.value),
           mll=float(p.maximum_loss.distance.value), lock=float(p.maximum_loss.threshold_ceiling))
assert REG == dict(start=50_000.0, target=3_000.0, mll=2_000.0, lock=50_000.0)

orig = bc.simulate_funded_trajectory


def correr(nombre: str, r_in: np.ndarray, d_in: np.ndarray, k: int,
           consist: float, min_dias: int, dll: float) -> dict:
    cap_usd = PTO * k

    def patched(resample, timeline, _cons=consist, _md=min_dias, _dll=dll, _cap=cap_usd, **kw):
        kw["initial_balance"] = REG["start"]
        kw["profit_target_usd"] = REG["target"]
        kw["max_dd_limit_usd"] = REG["mll"]
        kw["dd_floor_ceiling"] = REG["lock"]
        kw["daily_loss_limit_usd"] = _dll
        kw["consistency_max_day_ratio"] = _cons
        kw["min_trading_days"] = _md
        kw["trailing_update_cadence"] = "end_of_day"       # F1 Fix A: ratchet EOD (spec Topstep)
        kw["consistency_day_consolidation"] = "day_close"  # F1 Fix A: mejor día al cierre
        kw["risk_pct"] = 2.0 * _cap / REG["start"]
        kw["max_risk_dollars_order"] = _cap
        out_r = orig(resample, timeline, **kw)
        n_cons = min(out_r["consumed_opportunities"], len(timeline))
        out_r["_meses"] = ((timeline[n_cons - 1] - timeline[0]).total_seconds() / 86400 / 30.44
                           if n_cons > 0 else 0.0)
        return out_r

    bc.simulate_funded_trajectory = patched
    try:
        _, _, acts, _ = bc.run_unwrapped_mbb_paired(
            r_in, block, B, seed_mbb, dates=d_in, profit_target_usd=REG["target"])
    finally:
        bc.simulate_funded_trajectory = orig

    cnt = Counter(a["terminal_condition"] for a in acts)
    pp = cnt.get("target", 0) / B
    quemar = (cnt.get("breach_trailing", 0) + cnt.get("breach_daily", 0)) / B
    m_p = np.array([a["_meses"] for a in acts if a["terminal_condition"] == "target"])
    m_f = np.array([a["_meses"] for a in acts if a["terminal_condition"] != "target"])
    fila = dict(pp=pp, quemar=quemar, cnt=dict(cnt),
                med_p=float(np.median(m_p)) if m_p.size else float("nan"),
                med_f=float(np.median(m_f)) if m_f.size else float("nan"))
    log(f"  {nombre:46s} | P(pasar)={pp*100:5.1f}% | P(quemar)={quemar*100:5.1f}% | "
        f"mediana si pasa {fila['med_p']:.2f}m | si muere {fila['med_f']:.2f}m | {dict(cnt)}")
    return fila


log("══ ESCENARIOS PREDECLARADOS (pool MNQ+MGC, flat 15:10 CT) ══")
filas = {}
for k in (1, 2):
    filas[(k, "A")] = correr(f"A · {k} contr · cons 0.55, min2", r_pool, d_pool, k, 0.55, 2, NO_DLL)
for k in (1, 2):
    filas[(k, "B")] = correr(f"B · {k} contr · cons 0.50, min2", r_pool, d_pool, k, 0.50, 2, NO_DLL)
filas[(2, "C")] = correr("C · 2 contr · cons 0.50 + DLL $1,000 terminal", r_pool, d_pool, 2, 0.50, 2, 1_000.0)

log("")
log("══ CONTROLES ══")
r_noderiva = r_pool - r_pool.mean()
mu, sd = float(r_pool.mean()), float(r_pool.std(ddof=1))
r_iid = np.random.default_rng(seed_iid).normal(mu, sd, size=len(r_pool))
log(f"  sin-deriva: media={r_noderiva.mean():+.4f} · iid emparejado: N({mu:+.4f},{sd:.4f})")
for k in (1, 2):
    filas[(k, "nd")] = correr(f"CTRL sin-deriva · {k} contr", r_noderiva, d_pool, k, 0.55, 2, NO_DLL)
    filas[(k, "iid")] = correr(f"CTRL iid emparejado · {k} contr", r_iid, d_pool, k, 0.55, 2, NO_DLL)

log("")
log("══ DELTAS (control − candidato, escenario A) ══")
for k in (1, 2):
    log(f"  {k} contr: Δ(sin-deriva)={(filas[(k,'nd')]['pp']-filas[(k,'A')]['pp'])*100:+.1f} pp · "
        f"Δ(iid emparejado)={(filas[(k,'iid')]['pp']-filas[(k,'A')]['pp'])*100:+.1f} pp")

(HERE / "T4_POOL_MNQ_MGC_TOPSTEP_RESULTADOS.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
log("\nFIN T4 — cifras crudas; análisis en T4_POOL_MNQ_MGC_TOPSTEP.md")

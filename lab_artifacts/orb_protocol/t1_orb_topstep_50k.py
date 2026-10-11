#!/usr/bin/env python3
"""T1 — ORB MNQ bajo reglas Topstep 50K (plan semana 12-oct, T1_ORB_TOPSTEP_50K.md).

Reglas congeladas (fuente: T5_VERIFICACION_REGLAS.md, help.topstep.com 2026-10-10):
- Topstep 50K: target $3,000 · MLL trailing EOD $2,000 (lock en $50,000 = starting balance).
- Flat obligatorio 3:10 PM CT -> semántica V-EOD (max_long_hold_days=1, cierre al último bar
  de sesión; los shorts ya cierran a EOD por diseño del motor del autor).
- Mínimo 2 días de trading (oficial). Sin límite de tiempo.
- Consistencia SUAVE: mejor día <= ratio*target; al romperla target = mejor_día/ratio
  (fórmula oficial; el motor ya implementa best_day/ratio en bootstrap_camino.py:374).
- 1 contrato MNQ (riesgo $200 = stop 100 pts * $2/pto).

Escenarios predeclarados (todos se reportan):
  A) Base oficial:    consistencia 0.55, min_días=2, sin DLL.
  B) Conservador:     consistencia 0.50, min_días=2, sin DLL.
  C) Conservador+DLL: consistencia 0.50, min_días=2, DLL $1,000 terminal (cota conservadora:
                      el DLL real es soft/pausa de sesión, NO terminal — etiquetado).
Controles (para A y B): sin-deriva (R centrado, media 0, misma forma) e iid emparejado
(N(media,σ) con semilla declarada). B=2000, semilla maestra 20260928.
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
# Sensibilidad de costos (T3): argv[1] = ticks EXTRA de slippage por lado sobre el base 1.5
# (p.ej. `...py 1.0` = +1 tick → 2.5 ticks/side). Default 0 = corrida base de T1.
SLIP_EXTRA = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
SUF = f"_slip{SLIP_EXTRA:+.1f}" if SLIP_EXTRA else ""
B = 2000
MASTER_SEED = 20260928
K = 1                      # 1 contrato MNQ (T1 lo fija)
PTO = 200.0                # stop_dollars MNQ = 100 pts * $2
NO_DLL = 25_000.0           # "sin DLL": 0.5*balance (frolución grande, nunca binde antes
                            # del trailing) — convención declarada del motor, ver skill pitfall

out: list[str] = []


def log(s: str = "") -> None:
    print(s)
    out.append(s)


log("=" * 78)
log("T1 — ORB MNQ · reglas Topstep 50K · flat 15:10 CT (V-EOD) · 1 contrato")
log("=" * 78)

# ── 1. Ledger ORB MNQ con flat por sesión ─────────────────────────────────────
df1 = load_nq_1min(DATA / "mnq_1min_multicharts.csv.gz")
df5 = resample_5min(df1)
cfg = replace(Config(), point_value=2.0, tick_value=0.50,
              stop_dollars=200.0, target_dollars=400.0,
              commission_per_side=0.62, slippage_ticks_per_side=1.5 + SLIP_EXTRA,
              slippage_sides=2, initial_capital=100_000.0,
              margin_per_contract=2_328.26, max_long_hold_days=1,  # V-EOD = flat diario
              entry_end=dt.time(14, 30), flat_time=dt.time(15, 10))  # F1 Fix B: flat Topstep
tr = run_backtest(df5, cfg, SimFlags()).trades

stop_pts = 200.0 / cfg.point_value
cost_pts = cfg.round_turn_cost / cfg.point_value
sign = np.where(tr["direction"].to_numpy() == "long", 1.0, -1.0)
r = (sign * (tr["exit_price"].to_numpy() - tr["entry_price"].to_numpy()) - cost_pts) / stop_pts

# PARIDAD_V2 (F1 Fix B, 2026-10-11; INVALIDADA en ronda 2 por Codex §4.1: el
# n=3619 incluía 25 entradas indebidas a las 14:30 — ver patch_r2 y §9 de
# F1_FIXES_MOTOR.md).
# ANTES (trade_end=14:30 para entradas y cierre): n=3594, E[R]=+0.013403
#   (resultados_v1_multi.json celda MNQ/eod).
# DESPUÉS (ronda 2): PENDIENTE de recaptura por Hermes tras aplicar C1
#   (entradas == legacy ⇒ n esperado 3594; E[R] cambia solo por salidas 15:10).
# Digest canónico del ledger (F1 ronda 2, Codex §4.4): SHA-256 de
# to_csv(index=False) con columnas fijas y orden establecido
# (entry_time, exit_time, direction). Congela el ledger bit a bit.
CANON_COLS = ["entry_time", "exit_time", "direction", "entry_price", "exit_price"]
canon = tr[CANON_COLS].copy()
canon = canon.sort_values(["entry_time", "exit_time", "direction"], kind="mergesort").reset_index(drop=True)
LEDGER_SHA256 = hashlib.sha256(canon.to_csv(index=False).encode("utf-8")).hexdigest()
PARIDAD_V2_N: int | None = 3594
PARIDAD_V2_ER: float | None = 0.012892237061769599
PARIDAD_V2_SHA256: str | None = "9a1261c827ac92bcd5a2410f2fd6c7731f3b09ae8bbc196f8bc64c60c59cf536"
# Capturado por Hermes 2026-10-11 (ronda 2, tras fix C1): n=3594, entradas idénticas
# al legacy (0 añadidas/0 retiradas) y salidas a las 15:10. Congelado con pandas 3.0.5.
PARIDAD_V2_PANDAS: str = "3.0.5"
if SLIP_EXTRA == 0.0:
    er_full = float(r.mean())
    if PARIDAD_V2_N is None or PARIDAD_V2_ER is None or PARIDAD_V2_SHA256 is None:
        log(f"  PARIDAD_V2 PENDIENTE de captura: n={len(tr)} | E[R]={r.mean():+.4f} (full {er_full!r}) | "
            f"σ={r.std(ddof=1):.4f} | WR={float(np.mean(r > 0))*100:.1f}% | LEDGER_SHA256={LEDGER_SHA256}")
    else:
        assert len(tr) == PARIDAD_V2_N, f"paridad V2 n FALLIDA: {len(tr)} != {PARIDAD_V2_N}"
        assert abs(er_full - PARIDAD_V2_ER) < 1e-12, f"paridad V2 E[R] FALLIDA: {er_full!r}"
        assert LEDGER_SHA256 == PARIDAD_V2_SHA256, f"paridad V2 SHA FALLIDA: {LEDGER_SHA256}"
        log(f"  PARIDAD_V2 MNQ OK: n={len(tr)} | E[R]={r.mean():+.4f} (full {er_full!r}) | σ={r.std(ddof=1):.4f} | "
            f"WR={float(np.mean(r > 0))*100:.1f}% | LEDGER_SHA256={LEDGER_SHA256}")
else:
    log(f"  SENSIBILIDAD slippage +{SLIP_EXTRA:.1f} ticks/side (n={len(tr)} | "
        f"E[R]={r.mean():+.4f}) — sin assert de paridad (corrida derivada)")
t_stat = r.mean() / (r.std(ddof=1) / math.sqrt(len(r)))
log(f"  t={t_stat:+.2f} | trades/mes=18.44 (V1-MULTI) | riesgo/trade=${PTO*K:.0f} (1 contr)")

recs = sorted(zip(pd.to_datetime(tr["exit_time"]).tolist(), r.tolist()), key=lambda z: z[0])
r_led = np.array([z[1] for z in recs])
d_led = np.array([str(z[0].date()) for z in recs])

block = max(1, int(math.ceil(float(optimal_block_length(r_led).loc[0, "circular"]))))
seed_mbb = int(np.random.SeedSequence(MASTER_SEED).spawn(4)[0].generate_state(1)[0])
seed_iid = int(np.random.SeedSequence(MASTER_SEED).spawn(4)[1].generate_state(1)[0])
log(f"  MBB bloque={block} | seed_mbb={seed_mbb} | seed_iid={seed_iid} | B={B}")
log("  NOTA: 'bloqueada' no aplica en este perfil (sin tope de margen que ate a 1 contr).")
log("")

# ── 2. Motor de cuenta Topstep 50K (constantes leídas del perfil del repo) ────
p = topstep_50k_profile()
REG = dict(start=float(p.starting_balance),
           target=float(p.profit_target.target.value),
           mll=float(p.maximum_loss.distance.value),
           lock=float(p.maximum_loss.threshold_ceiling))
log(f"  Perfil topstep_50k_profile: start=${REG['start']:,.0f} target=${REG['target']:,.0f} "
    f"MLL=${REG['mll']:,.0f} lock=${REG['lock']:,.0f}")
assert REG["start"] == 50_000.0 and REG["target"] == 3_000.0 and REG["mll"] == 2_000.0 \
    and REG["lock"] == 50_000.0, "constantes del perfil no cuadran con T5"
log("")

orig = bc.simulate_funded_trajectory
cap_usd = PTO * K


def correr(nombre: str, r_in: np.ndarray, d_in: np.ndarray,
           consist: float, min_dias: int, dll: float) -> dict:
    def patched(resample, timeline, _cons=consist, _md=min_dias, _dll=dll, **kw):
        kw["initial_balance"] = REG["start"]
        kw["profit_target_usd"] = REG["target"]
        kw["max_dd_limit_usd"] = REG["mll"]
        kw["dd_floor_ceiling"] = REG["lock"]            # lock en starting balance (T5)
        kw["daily_loss_limit_usd"] = _dll
        kw["consistency_max_day_ratio"] = _cons          # 0.55 oficial / 0.50 conservador
        kw["min_trading_days"] = _md                     # 2 oficial / 5 conservador
        kw["trailing_update_cadence"] = "end_of_day"     # F1 Fix A: ratchet EOD (spec Topstep)
        kw["consistency_day_consolidation"] = "day_close"  # F1 Fix A: mejor día al cierre
        kw["risk_pct"] = 2.0 * cap_usd / REG["start"]
        kw["max_risk_dollars_order"] = cap_usd
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
    fracaso = 1.0 - pp
    quemar = (cnt.get("breach_trailing", 0) + cnt.get("breach_daily", 0)) / B
    m_p = np.array([a["_meses"] for a in acts if a["terminal_condition"] == "target"])
    m_f = np.array([a["_meses"] for a in acts if a["terminal_condition"] != "target"])
    fila = dict(nombre=nombre, pp=pp, quemar=quemar, fracaso=fracaso, cnt=dict(cnt),
                med_p=float(np.median(m_p)) if m_p.size else float("nan"),
                med_f=float(np.median(m_f)) if m_f.size else float("nan"),
                med_all=float(np.median([a["_meses"] for a in acts])))
    log(f"  {nombre:42s} | P(pasar)={pp*100:5.1f}% | P(quemar)={quemar*100:5.1f}% | "
        f"mediana si pasa {fila['med_p']:.2f}m | si muere {fila['med_f']:.2f}m | "
        f"estados {dict(cnt)}")
    return fila


log("══ ESCENARIOS PREDECLARADOS (1 contrato, flat 15:10 CT) ══")
filas = {}
filas["A"] = correr("A · Base oficial (cons 0.55, min2)", r_led, d_led, 0.55, 2, NO_DLL)
filas["B"] = correr("B · Conservador (cons 0.50, min2)", r_led, d_led, 0.50, 2, NO_DLL)
filas["C"] = correr("C · Cons+DLL $1,000 terminal (cota)", r_led, d_led, 0.50, 2, 1_000.0)

log("")
log("══ CONTROLES (mismo motor, mismas reglas; semilla iid declarada) ══")
r_noderiva = r_led - r_led.mean()          # misma forma/σ, media 0 → SIN edge
mu, sd = float(r_led.mean()), float(r_led.std(ddof=1))
r_iid = np.random.default_rng(seed_iid).normal(mu, sd, size=len(r_led))  # emparejado (con media)
log(f"  sin-deriva: media={r_noderiva.mean():+.4f} · iid emparejado: N({mu:+.4f},{sd:.4f})")

for tag, consist in (("A", 0.55), ("B", 0.50)):
    filas[f"ctrl_nd_{tag}"] = correr(
        f"CTRL sin-deriva · cons {consist:.2f}", r_noderiva, d_led, consist, 2, NO_DLL)
    filas[f"ctrl_iid_{tag}"] = correr(
        f"CTRL iid emparejado · cons {consist:.2f}", r_iid, d_led, consist, 2, NO_DLL)

log("")
log("══ DELTAS (control − candidato; el signo puede invertirse al subir sizing) ══")
for tag in ("A", "B"):
    d_nd = filas[f"ctrl_nd_{tag}"]["pp"] - filas[tag]["pp"]
    d_iid = filas[f"ctrl_iid_{tag}"]["pp"] - filas[tag]["pp"]
    log(f"  Escenario {tag}: Δ(sin-deriva)={d_nd*100:+.1f} pp · Δ(iid emparejado)={d_iid*100:+.1f} pp")

log("")
log("══ LECTURA (regla de decisión del plan §5) ══")
a = filas["A"]
d_nd = filas["ctrl_nd_A"]["pp"] - a["pp"]
log(f"  1) ORB vs control sin edge (escenario A): Δ={d_nd*100:+.1f} pp "
    f"({'a favor del control' if d_nd > 0 else 'a favor de ORB'})")
log(f"  2) Sensibilidad +1/+2 ticks: T3 (Codex).")
log(f"  3) Reglas verificadas en T5 (55% consistencia CONFIRMADO, flat CONFIRMADO).")
log("")
log("FIN T1 — cifras crudas; el análisis va en T1_ORB_TOPSTEP_50K.md")

(HERE / f"T1_ORB_TOPSTEP_50K_RESULTADOS{SUF}.txt").write_text("\n".join(out) + "\n", encoding="utf-8")

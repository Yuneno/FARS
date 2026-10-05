#!/usr/bin/env python3
"""Curva de riesgo predeclarada sobre el MC revisado (R11) de fondeo ORB.

Escenarios declarados ANTES de correr (sin cazar el óptimo):
  1. VALIDACION  : riesgo 1%          (debe reproducir 45.40/19.60/35.00 publicado)
  2. L015        : riesgo 0.15%       (la leyenda "~90% de pasar")
  3. UN_CONTRATO : sizing fijo 1 MNQ  ($200/trade ≈ 0.2% de $100k)
  4. DOS_CONTRATOS: sizing fijo 2 MNQ ($400/trade ≈ 0.4%)

Método: se inyectan risk_pct/max_risk_dollars_order en simulate_funded_trajectory
y se reusa run_unwrapped_mbb_paired SIN reimplementar nada (mismo remuestreo,
mismas semillas hijas de SeedSequence(20260928)). Cuenta $100k Apex-like
(target +$6k, trailing $8k, pérdida diaria $2k), B=2000, semilla 20260928.
"""
from __future__ import annotations

import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, r"E:/FARS-LAB/FARS")
sys.path.insert(0, r"E:/FARS-LAB/FARS/lab_artifacts/orb_protocol")

import bootstrap_camino as bc
from src.ingestion import load_trade_csv
from src.bootstrap import optimal_block_length

LEDGER = Path(r"E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/ledger_orb_fars_stop.csv")
MASTER_SEED = 20260928
B = 2000

print("=" * 72)
print("CURVA DE RIESGO ORB — supuestos declarados (autocontenido)")
print("=" * 72)
print(f"  Ledger: ledger_orb_fars_stop.csv (n=2444 trades, 2010-06..2026-09)")
print(f"  Cuenta: $100k Apex-like — target +$6.000 | trailing $8.000 | diaria $2.000")
print(f"  Sizing: entero por contrato MNQ (stop 100 pts = $200/contrato de riesgo)")
print(f"  MC: B={B} réplicas | remuestreo MBB sobre el camino bruto (bloque=optimal_block_length)")
print(f"  Semilla maestra: {MASTER_SEED} (hijas SeedSequence.spawn(4)[0])")
print(f"  Cupo: 42 ops/mes calendario sintético (proceso de interarribos)")
print(f"  Horizonte por réplica: hasta 2.444 oportunidades remuestreadas")
print(f"  INCERTIDUMBRE MONTE CARLO: con B={B}, el error típico de una probabilidad")
print(f"    ~50% es ~1,1 pts porcentuales (1 desv. = sqrt(p(1-p)/B)); los extremos")
print(f"    (0% / 100%) tienen cota ~3/B = 0,15% por regla de tres.")
print(f"  APROXIMACIÓN POR CIERRES: trailing sobre equity al cierre de cada trade;")
print(f"    es OPTIMISTA para supervivencia (excursiones intratrade favorables")
print(f"    elevarían el piso y sumarían breaches => las P(pasar) aquí son techo).")
print(f"  Costes: ya netos en r_result (0,62 USD/side + 1,5 ticks/side).")
print("=" * 72)

ds = load_trade_csv(LEDGER, outcomes_finalized=True, analysis_timezone="America/New_York")
r = np.asarray([t.r_result for t in ds.trades], dtype=np.float64)
dates = np.asarray([str(t.timestamp.date() if t.timestamp else t.date) for t in ds.trades])
block = max(1, int(math.ceil(float(optimal_block_length(r).loc[0, "circular"]))))
seed_mbb = int(np.random.SeedSequence(MASTER_SEED).spawn(4)[0].generate_state(1)[0])
print(f"n={len(r)} trades | block={block} | seed_mbb={seed_mbb} | B={B}")

orig = bc.simulate_funded_trajectory

ESCENARIOS = [
    ("VALIDACION 1%      ", 0.01, 1_000.0),
    ("LEYENDA 0.15%      ", 0.0015, 1_000.0),
    ("1 CONTRATO (0.2%)  ", 0.004, 200.0),
    ("2 CONTRATOS (0.4%) ", 0.008, 400.0),
]

for nombre, risk_pct, cap in ESCENARIOS:
    def patched(resample, timeline, _rp=risk_pct, _cap=cap, **kw):
        kw["risk_pct"] = _rp
        kw["max_risk_dollars_order"] = _cap
        return orig(resample, timeline, **kw)

    bc.simulate_funded_trajectory = patched
    try:
        _, _, acts_raw, _ = bc.run_unwrapped_mbb_paired(
            r, block, B, seed_mbb, dates=dates, profit_target_usd=6_000.0
        )
    finally:
        bc.simulate_funded_trajectory = orig

    cnt = Counter(a["terminal_condition"] for a in acts_raw)
    sizes = [s for a in acts_raw for s in a.get("sizes", [])]
    media_ops = float(np.mean([a["trades_executed"] for a in acts_raw]))
    p = {k: 100.0 * cnt.get(k, 0) / B for k in
         ("target", "breach_trailing", "breach_daily", "horizon_exhausted")}
    size_medio = float(np.mean(sizes)) if sizes else 0.0
    print(f"\n== {nombre} ==")
    print(f"  P(target +$6k)      : {p['target']:6.2f}%")
    print(f"  P(breach_trailing)  : {p['breach_trailing']:6.2f}%")
    print(f"  P(breach_daily)     : {p['breach_daily']:6.2f}%")
    print(f"  P(horizon_exhausted): {p['horizon_exhausted']:6.2f}%")
    print(f"  ops/réplica (media) : {media_ops:.1f} | size medio: {size_medio:.2f} contratos")

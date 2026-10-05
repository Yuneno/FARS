#!/usr/bin/env python3
"""
foco_50k.py — foco económico en la cuenta 50k (la más común / mejor relación
calidad-precio según Ricardo), con 25k y 100k como contraste.

Mismo motor REVISADO (simulate_funded_trajectory + dd_floor_ceiling Apex) y mismo
remuestreo que perfiles_orb.py. Escenarios predeclarados: 1 y 2 contratos por
perfil. Añade: tiempo a resolución con la CADENCIA REAL del ledger (medida, no
supuesta) y la matemática de reintentos de evaluación.

Aproximaciones declaradas:
- Meses ≈ trades ÷ cadencia histórica del ledger (medida abajo). El MC remuestrea
  interarribos históricos, así que la cadencia sintética ≈ la real.
- Reintentos modelados como intentos independientes con la MISMA p (en la
  realidad la skill del operador varía — simplificación optimista/pesimista según
  el caso, se declara).
"""
from __future__ import annotations

import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, r"E:/FARS-LAB/FARS")
sys.path.insert(0, str(HERE))

import bootstrap_camino as bc  # noqa: E402
from src.funded_profiles import apex_25k_profile, apex_50k_profile, apex_100k_profile  # noqa: E402
from src.ingestion import load_trade_csv  # noqa: E402
from src.bootstrap import optimal_block_length  # noqa: E402

LEDGER = HERE / "ledger_orb_fars_stop.csv"
MASTER_SEED = 20260928
B = 2000
PTO = 200.0

print("=" * 78)
print("FOCO 50k — economía de la evaluación (contraste 25k / 100k)")
print("=" * 78)

ds = load_trade_csv(str(LEDGER), outcomes_finalized=True,
                    analysis_timezone="America/New_York")
r = np.asarray([t.r_result for t in ds.trades], dtype=np.float64)
dates = np.asarray([str(t.timestamp.date() if t.timestamp else t.date) for t in ds.trades])

# Cadencia REAL medida del ledger
d0 = min(t.timestamp for t in ds.trades if t.timestamp)
d1 = max(t.timestamp for t in ds.trades if t.timestamp)
meses_ledger = (d1 - d0).days / 30.44
cadencia = len(ds.trades) / meses_ledger
print(f"  Cadencia real del ledger: {len(ds.trades)} trades / {meses_ledger:.1f} meses"
      f" = {cadencia:.2f} trades/mes")

block = max(1, int(math.ceil(float(optimal_block_length(r).loc[0, "circular"]))))
seed_mbb = int(np.random.SeedSequence(MASTER_SEED).spawn(4)[0].generate_state(1)[0])

orig = bc.simulate_funded_trajectory
resultados = {}
try:
    for nombre, factory in (("Apex 25k", apex_25k_profile),
                            ("Apex 50k", apex_50k_profile),
                            ("Apex 100k", apex_100k_profile)):
        perfil = factory()
        reg = dict(start=float(perfil.starting_balance),
                   target=float(perfil.profit_target.target.value),
                   trailing=float(perfil.maximum_loss.distance.value),
                   lock=float(perfil.maximum_loss.threshold_ceiling))
        for k in (1, 2):
            cap_usd = PTO * k

            def patched(resample, timeline, _reg=reg, _cap=cap_usd, **kw):
                kw["initial_balance"] = _reg["start"]
                kw["profit_target_usd"] = _reg["target"]
                kw["max_dd_limit_usd"] = _reg["trailing"]
                kw["dd_floor_ceiling"] = _reg["lock"]
                kw["daily_loss_limit_usd"] = 0.5 * _reg["start"]
                kw["risk_pct"] = 2.0 * _cap / _reg["start"]
                kw["max_risk_dollars_order"] = _cap
                return orig(resample, timeline, **kw)

            bc.simulate_funded_trajectory = patched
            try:
                _, _, acts, _ = bc.run_unwrapped_mbb_paired(
                    r, block, B, seed_mbb, dates=dates,
                    profit_target_usd=reg["target"])
            finally:
                bc.simulate_funded_trajectory = orig

            cnt = Counter(a["terminal_condition"] for a in acts)
            p_pass = cnt.get("target", 0) / B
            ops_pass = [a["trades_executed"] for a in acts if a["terminal_condition"] == "target"]
            ops_fail = [a["trades_executed"] for a in acts if a["terminal_condition"] != "target"]
            meses_pass = [o / cadencia for o in ops_pass]
            meses_fail = [o / cadencia for o in ops_fail]
            resultados[(nombre, k)] = p_pass

            print(f"\n══ {nombre} · {k} contrato(s) (riesgo ${cap_usd:.0f}/trade) ══")
            print(f"  P(pasar) = {p_pass*100:.2f}%  |  P(reventar) = {(1-p_pass)*100:.2f}%")
            if ops_pass:
                mp = np.array(meses_pass)
                print(f"  Si PASA: trades mediana {np.median(ops_pass):.0f} "
                      f"(p25-p75: {np.percentile(ops_pass,25):.0f}-{np.percentile(ops_pass,75):.0f})"
                      f" → meses mediana {np.median(mp):.1f} ({np.percentile(mp,25):.1f}-{np.percentile(mp,75):.1f})")
                for horiz in (3, 6, 12):
                    frac = float(np.mean(mp <= horiz))
                    print(f"    P(pasar dentro de {horiz:2d} meses | pasa) = {frac*100:.1f}%"
                          f"  → P(pasar en ≤{horiz} meses) = {p_pass*frac*100:.1f}%")
            if ops_fail:
                mf = np.array(meses_fail)
                print(f"  Si MUERE: meses mediana {np.median(mf):.1f} "
                      f"(p25-p75: {np.percentile(mf,25):.1f}-{np.percentile(mf,75):.1f})")
            print(f"  Reintentos (independientes, misma p): "
                  + " · ".join(f"{n} intento(s) → {1-(1-p_pass)**n:.1%}" for n in (1, 2, 3, 4)))
            print(f"  Intentos esperados hasta el primer pase: {1/p_pass:.2f}")
finally:
    bc.simulate_funded_trajectory = orig

print("\n" + "=" * 78)
print("Resumen P(pasar):")
for (nombre, k), p in resultados.items():
    print(f"  {nombre} · {k} contr: {p*100:.2f}%")
print("Aproximaciones declaradas arriba (cadencia ≈ real; reintentos independientes).")

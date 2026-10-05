#!/usr/bin/env python3
"""
perfiles_orb.py — ORB contra los perfiles REALES de fondeo (Apex 25k/50k/100k/150k).

Reglas leídas de los OBJETOS de `src/funded_profiles.py` (spec verificada
2026-07-29 contra apextraderfunding.com) — sin copiar valores a mano:
starting_balance, profit target, trailing DD, threshold_ceiling (lock del piso
en start+$100), daily_loss (None = no aplicado en evaluación) y caps de
contratos (MNQ = micro). Simulación con el motor YA REVISADO
(`bootstrap_camino.simulate_funded_trajectory`, con `dd_floor_ceiling` para la
regla de lock de Apex) vía inyección de parámetros — sin reimplementation.

Escenarios PREDECLARADOS: contratos fijos [1, 2, 3, 5] por perfil (sin cazar el
óptimo). Sizing: k contratos mientras equity >= 50% del inicio; por debajo se
reduce (convención declarada, igual que curva_riesgo_orb.py).

Rapid 25k: EXCLUIDO — `rapid_25k_profile()` es provisional/fail-closed a
propósito (test_funded_rules_v2: "provisional_and_fail_closed"; campos sin
resolver como micros_per_mini). Usarlo sería fabricar reglas.

MC: B=2000, remuestreo MBB bloque=2, semilla maestra 20260928. El "pase" es
tocar el profit target ANTES de cualquier breach (semántica terminal Apex del
MC aprobado). Aproximación por cierres = optimista para supervivencia.
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
from src.funded_profiles import (  # noqa: E402
    apex_25k_profile, apex_50k_profile, apex_100k_profile, apex_150k_profile,
)
from src.ingestion import load_trade_csv  # noqa: E402
from src.bootstrap import optimal_block_length  # noqa: E402

LEDGER = HERE / "ledger_orb_fars_stop.csv"
MASTER_SEED = 20260928
B = 2000
CONTRATOS = [1, 2, 3, 5]          # predeclarado, se reporta entero
PTO = 200.0                        # USD de riesgo por contrato MNQ (stop 100 pts x $2)


def reglas_de(perfil) -> dict:
    """Lee las reglas del objeto real del perfil (sin duplicar valores)."""
    return {
        "start": float(perfil.starting_balance),
        "target": float(perfil.profit_target.target.value),
        "trailing_dd": float(perfil.maximum_loss.distance.value),
        "lock_ceiling": (float(perfil.maximum_loss.threshold_ceiling)
                         if perfil.maximum_loss.threshold_ceiling is not None else None),
        "daily_loss": (None if perfil.daily_loss is None
                       else float(perfil.daily_loss.loss.value)),
        "max_micros": int(perfil.operational.maximum_micros),
    }


def main() -> None:
    ds = load_trade_csv(str(LEDGER), outcomes_finalized=True,
                        analysis_timezone="America/New_York")
    r = np.asarray([t.r_result for t in ds.trades], dtype=np.float64)
    dates = np.asarray([str(t.timestamp.date() if t.timestamp else t.date)
                        for t in ds.trades])
    block = max(1, int(math.ceil(float(optimal_block_length(r).loc[0, "circular"]))))
    seed_mbb = int(np.random.SeedSequence(MASTER_SEED).spawn(4)[0].generate_state(1)[0])

    print("=" * 78)
    print("PERFILES REALES DE FONDEO — ORB (MNQ) — supuestos declarados")
    print("=" * 78)
    print(f"  Ledger: ledger_orb_fars_stop.csv (n={len(r)} trades, costes netos)")
    print(f"  MC: B={B} | MBB bloque={block} | semilla maestra {MASTER_SEED}")
    print("  Motor: simulate_funded_trajectory (REVISADO) + dd_floor_ceiling Apex")
    print("  Reglas: leídas de src/funded_profiles.py (spec Apex verificada 2026-07-29)")
    print(f"  Escenarios: contratos fijos {CONTRATOS} (MNQ=micro, cap por perfil)")
    print("  'Pasar' = tocar el profit target ANTES de cualquier breach (terminal)")
    print(f"  INCERTIDUMBRE MC: B={B} → error estándar ≈1,1 pts en P≈50%")
    print("    (1 desv. = sqrt(p(1-p)/B)); extremos 0%/100% con cota ~3/B = 0,15%")
    print("  Aproximación por cierres: OPTIMISTA para supervivencia (las P son techo)")
    print("  Rapid 25k: EXCLUIDO (perfil provisional/fail-closed — no fabricar reglas)")
    print("  Apex real: SIN límite diario en evaluación → modelado como 50% del")
    print("    balance (nunca bindeante; el trailing mata antes). Lock del piso en")
    print("    start+$100 modelado con dd_floor_ceiling (tocar el piso = breach).")
    print("=" * 78)

    orig = bc.simulate_funded_trajectory
    try:
        for nombre, factory in (("Apex 25k", apex_25k_profile),
                                ("Apex 50k", apex_50k_profile),
                                ("Apex 100k", apex_100k_profile),
                                ("Apex 150k", apex_150k_profile)):
            reg = reglas_de(factory())
            print(f"\n══ {nombre} — start ${reg['start']:,.0f} | target ${reg['target']:,.0f} "
                  f"| trailing ${reg['trailing_dd']:,.0f} | lock "
                  f"{'$%.0f' % reg['lock_ceiling'] if reg['lock_ceiling'] else 'sin lock'} "
                  f"| diaria {'N/A' if reg['daily_loss'] is None else reg['daily_loss']} "
                  f"| cap {reg['max_micros']} micros ══")
            for k in CONTRATOS:
                if k > reg["max_micros"]:
                    print(f"  {k} contratos: EXCEDE cap ({reg['max_micros']}) — omitido")
                    continue
                cap_usd = PTO * k

                def patched(resample, timeline, _k=k, _reg=reg, _cap=cap_usd, **kw):
                    kw["initial_balance"] = _reg["start"]
                    kw["profit_target_usd"] = _reg["target"]
                    kw["max_dd_limit_usd"] = _reg["trailing_dd"]
                    kw["dd_floor_ceiling"] = _reg["lock_ceiling"]
                    kw["daily_loss_limit_usd"] = (
                        _reg["daily_loss"] if _reg["daily_loss"] is not None
                        else 0.5 * _reg["start"])  # Apex real: sin límite diario
                        # en evaluación; modelado como 50% del balance (nunca
                        # bindeante: el trailing mata al 3-6%).
                    kw["risk_pct"] = 2.0 * _cap / _reg["start"]
                    kw["max_risk_dollars_order"] = _cap
                    return orig(resample, timeline, **kw)

                bc.simulate_funded_trajectory = patched
                try:
                    _, _, acts, _ = bc.run_unwrapped_mbb_paired(
                        r, block, B, seed_mbb, dates=dates,
                        profit_target_usd=reg["target"],
                    )
                finally:
                    bc.simulate_funded_trajectory = orig

                cnt = Counter(a["terminal_condition"] for a in acts)
                p = {t: 100.0 * cnt.get(t, 0) / B for t in
                     ("target", "breach_trailing", "breach_daily", "horizon_exhausted")}
                ops = float(np.mean([a["trades_executed"] for a in acts]))
                print(f"  {k} contrato(s) (riesgo ${cap_usd:.0f}/trade): "
                      f"P(pasar)={p['target']:6.2f}% | trailing={p['breach_trailing']:5.2f}% "
                      f"| diaria={p['breach_daily']:5.2f}% | sin resolver={p['horizon_exhausted']:5.2f}% "
                      f"| ops/rep={ops:6.1f}")
    finally:
        bc.simulate_funded_trajectory = orig
    print("\nListo.")


if __name__ == "__main__":
    main()

"""FARS E5 — Verificacion de la cola de ejecucion contra las barras reales (Hermes).

Para los 7 trades con r < -2R (10.0): compara el precio de salida registrado con
el rango [low, high] de las barras M5 REALES entre entrada y salida.
Si la salida cae fuera de todo rango -> llenado imposible -> artefacto/bug de fill.
"""
from __future__ import annotations

import sys
from datetime import timedelta

import numpy as np

sys.path.insert(0, "E:/FARS-LAB/FARS")
from lab_artifacts.run_d_protocol import (  # noqa: E402
    ZIP_PATH,
    generate_smc_fvg_oos_trades,
    load_canonical_m5,
)

bars, _ = load_canonical_m5(ZIP_PATH)
by_ts = {b.timestamp: b for b in bars}
print(f"[bars] {len(bars)} cargadas, {len(by_ts)} stamps unicos")

trades = generate_smc_fvg_oos_trades(bars)
r = np.array([float(t.r_result) for t in trades])
cola = [int(i) for i in np.argsort(r)[:7]]

print(f"\n{'trade':<18} {'dir':<6} {'dur(min)':>7} {'entry':>9} {'stop':>9} {'exit':>9}  {'rango real [low,high]':>28} {'veredicto'}")
imposibles = []
for i in cola:
    t = trades[i]
    dur = (t.exit_time - t.entry_time).total_seconds() / 60.0
    lo = hi = None
    nbars = 0
    cur = t.entry_time
    while cur <= t.exit_time:
        b = by_ts.get(cur)
        if b is not None:
            nbars += 1
            lo = b.low if lo is None else min(lo, b.low)
            hi = b.high if hi is None else max(hi, b.high)
        cur += timedelta(minutes=5)
    if lo is None:
        verdict = "SIN BARRAS EN EL DATASET"
        rng = "-"
    elif lo <= t.exit_price <= hi:
        verdict = "POSIBLE (dentro del rango)"
        rng = f"[{lo:.1f}, {hi:.1f}]"
    else:
        verdict = "IMPOSIBLE (fuera de todo rango)"
        rng = f"[{lo:.1f}, {hi:.1f}]"
        imposibles.append(t)
    print(f"{t.trade_id:<18} {t.direction:<6} {dur:>7.0f} {t.entry_price:>9.1f} {t.stop_price:>9.1f} {t.exit_price:>9.1f}  {rng:>28} {verdict}")

print(f"\n[veredicto] imposibles: {len(imposibles)} de 7")
for t in imposibles:
    b0 = by_ts.get(t.entry_time)
    if b0 is not None:
        print(f"  {t.trade_id}: barra de entrada {t.entry_time} O={b0.open:.1f} H={b0.high:.1f} L={b0.low:.1f} C={b0.close:.1f} | exit={t.exit_price:.1f} stop={t.stop_price:.1f}")

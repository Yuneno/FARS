#!/usr/bin/env python3
"""adaptar_multi.py — adapta MES/MGC/MYM M1 de databento.zip al formato del motor.

Reutiliza adapter_data.adapt()/diagnostics() SIN duplicar lógica (solo se
parametrizan MEMBER/OUT_CSV). Misma conversión que MNQ (UTC ISO → wall-clock CT
MultiCharts). Ver PREREGISTRO_V1_MULTI.md.

Uso: python adaptar_multi.py [MES|MGC|MYM|todos]
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import adapter_data as ad  # noqa: E402

MERCADOS = ["MES", "MGC", "MYM"]  # MNQ ya existe


def adaptar(sym: str) -> None:
    ad.MEMBER = f"databento/{sym}_M1.csv"
    ad.OUT_CSV = ad.OUT_DIR / f"{sym.lower()}_1min_multicharts.csv.gz"
    print(f"\n══ Adaptando {sym}: {ad.MEMBER} → {ad.OUT_CSV.name} ══")
    p = ad.adapt()
    ad.diagnostics(p)


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "todos"
    syms = MERCADOS if arg == "todos" else [arg.upper()]
    for s in syms:
        if s not in MERCADOS:
            print(f"mercado desconocido: {s} (válidos: {MERCADOS})")
            sys.exit(2)
        adaptar(s)
    print("\nListo.")

#!/usr/bin/env python3
"""
mc_fondeo.py — probabilidad real de pasar / reventar una cuenta fondeada.

Por qué esto y no run_probabilistic_paths: ese motor exige CAP_POSITION_SIZES
(perfiles Apex limitan minis/micros) y un ledger Phase 8A es solo-R por diseño.
Aquí la política de sizing es EXPLÍCITA y declarada, sobre la misma secuencia R.

Método: Circular Block Bootstrap sobre la secuencia de r_result (bloque óptimo
reportado por FARS: 2), camino de equity con comisión + slippage por trade,
reglas de cuenta (profit target, drawdown trailing, pérdida diaria). Se reporta
TODA la rejilla predeclarada, sin elegir el óptimo.

Semilla: 20260928. Nada de datos en vivo, nada de broker.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, r"E:\FARS-LAB\FARS")

SEED = 20260928
N_PATHS = 2000
BLOCK = 2                      # longitud óptima reportada por FARS (arch)
RISK_GRID = [0.0010, 0.0015, 0.0020, 0.0025, 0.0035, 0.0050, 0.0100]
COST_PER_TRADE = 1.99          # MNQ: 1.24 comisión + 0.75 slippage (1,5 ticks)

# Reglas de cuenta (perfil tipo Apex 100k, declaradas aquí para no fabricar)
PROFILES = {
    "Apex-like 100k": dict(start=100_000.0, target_pct=0.06,
                           max_loss_pct=0.08, daily_loss_pct=0.05),
    "Apex-like 50k":  dict(start=50_000.0, target_pct=0.06,
                           max_loss_pct=0.08, daily_loss_pct=0.05),
}


def load_r() -> np.ndarray:
    import csv
    with (HERE / "ledger_orb_fars_stop.csv").open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    return np.array([float(r["r_result"]) for r in rows], dtype=np.float64)


def cbb_indices(n: int, block: int, rng: np.random.Generator) -> np.ndarray:
    """Circular Block Bootstrap (igual que FARS: starts+offsets mod n)."""
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, n, size=n_blocks)
    offs = np.arange(block)
    idx = (starts[:, None] + offs[None, :]).ravel() % n
    return idx[:n]


def simulate(r: np.ndarray, risk_frac: float, p: dict, rng: np.random.Generator):
    n = len(r)
    target = p["start"] * p["target_pct"]
    max_loss = p["start"] * p["max_loss_pct"]
    daily_loss = p["start"] * p["daily_loss_pct"]
    passed = breached = 0
    for _ in range(N_PATHS):
        idx = cbb_indices(n, BLOCK, rng)
        equity = p["start"]
        peak = p["start"]
        day_start = p["start"]
        day_pnl = 0.0
        outcome = "unfinished"
        for i in idx:
            risk_usd = equity * risk_frac
            if risk_usd <= 0:
                outcome = "breach"
                break
            pnl = risk_usd * float(r[i]) - COST_PER_TRADE
            equity += pnl
            day_pnl += pnl
            peak = max(peak, equity)
            if equity - peak <= -max_loss or equity <= p["start"] - max_loss:
                outcome = "breach"
                break
            if -day_pnl >= daily_loss:
                outcome = "breach"
                break
            if equity - p["start"] >= target:
                outcome = "pass"
                break
        if outcome == "pass":
            passed += 1
        elif outcome == "breach":
            breached += 1
    return passed / N_PATHS, breached / N_PATHS


def main() -> None:
    r = load_r()
    print(f"Secuencia R: n={len(r)}  E[R]={r.mean():+.4f}  "
          f"DD(R) observado=22.61  bloque CBB={BLOCK}")
    print(f"Rejilla predeclarada: {[f'{x*100:.2f}%' for x in RISK_GRID]}")
    print(f"Coste por trade: ${COST_PER_TRADE} (comisión 1,24 + slippage 0,75)\n")

    for nombre, p in PROFILES.items():
        print(f"══ {nombre} — target {p['target_pct']*100:.0f}% | "
              f"máx pérdida {p['max_loss_pct']*100:.0f}% | "
              f"pérdida diaria {p['daily_loss_pct']*100:.0f}% ══")
        print(f"{'riesgo/trade':>12} {'P(pasar)':>10} {'P(reventar)':>12} {'P(sin cerrar)':>14}")
        for f in RISK_GRID:
            rng = np.random.default_rng(SEED)
            pp, pb = simulate(r, f, p, rng)
            print(f"{f*100:>11.2f}% {pp:>10.1%} {pb:>12.1%} {1-pp-pb:>14.1%}")
        print()


if __name__ == "__main__":
    main()

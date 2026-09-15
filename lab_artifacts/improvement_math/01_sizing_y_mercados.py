"""FARS — Matematica de mejora del plan de cuentas (Hermes).

Usa la distribucion REAL de trades OOS (2.842, por_tramo) + MAE causal, y el motor
de reglas Apex 25K, para responder:
  A) Optimo de sizing (rejilla extendida 0.20% - 1.50%).
  B) Multi-mercado: N streams sobre la MISMA cuenta con correlacion rho.
  C) Mejor combinacion pase/quema.

Vectorizado (2000 caminos en paralelo). No escribe nada en el repo.
"""
from __future__ import annotations

import json
import sys

import numpy as np

sys.path.insert(0, "E:/FARS-LAB/FARS")
from lab_artifacts.run_d_protocol import (  # noqa: E402
    ZIP_PATH,
    generate_smc_fvg_oos_trades,
    load_canonical_m5,
)

ACCOUNT, TARGET, DD, LOCK = 25000.0, 1500.0, 1500.0, 100.0
SAFETY, MAX_MICROS, DPP = 0.75, 20, 2.0
TPD = 1.8631
HORIZON_DAYS = 30
NS = 2000
SEED = 20260729

bars, _ = load_canonical_m5(ZIP_PATH)
trades = generate_smc_fvg_oos_trades(bars)
n = len(trades)
qty0 = np.array([float(t.quantity) for t in trades])
bud = np.array([float(t.budgeted_risk_dollars) for t in trades])
net_pc = np.array([float(t.net_pnl) / max(float(t.quantity), 1.0) for t in trades])
unit = np.where((bud > 0) & (qty0 > 0), bud / np.maximum(qty0, 1.0), np.inf)
with open("E:/FARS-LAB/FARS/lab_artifacts/d_protocol/smc_fvg_risk_10_maes.json", encoding="utf-8") as fh:
    mapa = {m["trade_id"]: float(m["mae_points"]) * DPP for m in json.load(fh)["trades_mae"]}
mae_pc = np.array([mapa.get(t.trade_id, 0.0) for t in trades])
valid = np.isfinite(unit) & (unit > 0)
print(f"[prep] trades={n}  validos={valid.sum()}  MAE mediana ${np.median(mae_pc):.2f}/contrato")

T = max(1, round(TPD * HORIZON_DAYS))  # 56 trades por mercado en 30 dias


def simulate(risk_pct: float, n_markets: int = 1, rho: float = 0.0, seed: int = SEED) -> dict:
    """MC vectorizado. rho = P(que los mercados compartan el mismo trade en un slot)."""
    rng = np.random.default_rng(seed)
    nominal = risk_pct * ACCOUNT
    bal = np.full(NS, ACCOUNT)
    peak = np.full(NS, ACCOUNT)
    status = np.zeros(NS, dtype=np.int8)          # 0 timeout, 1 passed, 2 blown, 3 blocked
    trades_used = np.zeros(NS, dtype=np.int32)
    common = rng.integers(0, n, size=(NS, T))
    indep = rng.integers(0, n, size=(NS, T, n_markets))
    share = rng.random(size=(NS, T)) < rho
    for t in range(T):
        for m in range(n_markets):
            idx = np.where(share[:, t], common[:, t], indep[:, t, m])
            live = status == 0
            if not live.any():
                break
            i = idx
            floor = np.minimum(peak - DD, ACCOUNT + LOCK)
            buffer = bal - floor
            u = unit[i]
            qmax = np.floor((SAFETY * buffer) / u)
            blocked_now = live & ((qmax < 1) | ~valid[i])
            status[blocked_now & (buffer > 0)] = 3
            blown_pre = live & (buffer <= 0)
            status[blown_pre] = 2
            live = status == 0
            if not live.any():
                break
            q = np.maximum(1.0, np.minimum(np.round(nominal / u), np.minimum(qmax, MAX_MICROS)))
            dip = bal - q * mae_pc[i]
            blown_mae = live & (dip <= floor)
            status[blown_mae] = 2
            bal = np.where(live, bal + q * net_pc[i], bal)
            peak = np.maximum(peak, bal)
            newly_down = (status == 0) & (bal <= np.minimum(peak - DD, ACCOUNT + LOCK))
            status[newly_down] = 2
            passed = (status == 0) & (bal >= ACCOUNT + TARGET)
            status[passed] = 1
            trades_used[status != 0] = np.where(trades_used[status != 0] == 0, t * n_markets + m + 1, trades_used[status != 0])
    res = {
        "pass": float((status == 1).mean() * 100),
        "blown": float((status == 2).mean() * 100),
        "blocked": float((status == 3).mean() * 100),
        "timeout": float((status == 0).mean() * 100),
        "days": float(np.median(trades_used[status == 1]) / TPD) if (status == 1).any() else float("nan"),
    }
    return res


print("\n=== A) OPTIMO DE SIZING (1 mercado, 25K) ===")
print(f"{'riesgo%':>8} {'pase':>7} {'quema':>7} {'bloq':>7} {'timeout':>8} {'dias':>6} {'pase/(1+quema)':>15}")
sweep = [0.20, 0.30, 0.40, 0.4671, 0.60, 0.75, 0.90, 1.00, 1.20, 1.50]
best = None
for r in sweep:
    out = simulate(r / 100.0)
    ratio = out["pass"] / (1 + out["blown"] + out["blocked"])
    print(f"{r:>7.2f}% {out['pass']:>6.2f}% {out['blown']:>6.2f}% {out['blocked']:>6.2f}% {out['timeout']:>7.2f}% {out['days']:>5.1f} {ratio:>15.2f}")
    if best is None or out["pass"] > best[1]:
        best = (r, out["pass"])
print(f"[A] mejor pase: {best[0]:.2f}% -> {best[1]:.2f}%")

print("\n=== B) MULTI-MERCADO sobre la MISMA cuenta (0.4671% por trade) ===")
print(f"{'mercados':>8} {'rho':>5} {'pase':>7} {'quema':>7} {'bloq':>7} {'dias':>6}")
for nm in (1, 2, 3, 4):
    for rho in (0.0, 0.5, 1.0):
        if nm == 1 and rho != 0.0:
            continue
        out = simulate(0.004671, nm, rho)
        print(f"{nm:>8} {rho:>5.1f} {out['pass']:>6.2f}% {out['blown']:>6.2f}% {out['blocked']:>6.2f}% {out['days']:>5.1f}")

print("\n=== C) PAQUETE RECOMENDADO (mejor sizing x mercados, rho=0.5) ===")
print(f"{'riesgo%':>8} {'mercados':>8} {'pase':>7} {'quema':>7} {'bloq':>7} {'fracaso':>8}")
for r in (0.4671, 0.75, 1.00):
    for nm in (2, 3):
        out = simulate(r / 100.0, nm, 0.5)
        print(f"{r:>7.2f}% {nm:>8} {out['pass']:>6.2f}% {out['blown']:>6.2f}% {out['blocked']:>6.2f}% {out['blown']+out['blocked']:>7.2f}%")

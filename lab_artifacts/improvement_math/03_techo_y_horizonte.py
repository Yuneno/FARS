"""FARS — Cuanto del problema es el LIMITE DE TIEMPO (Hermes).

Barre el presupuesto de trades (= tiempo disponible) manteniendo el riesgo fijo,
y compara con la probabilidad teorica de tocar objetivo antes del suelo
(formula cerrada de movimiento browniano con deriva).
"""
from __future__ import annotations

import json
import math
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
TPS, NS, SEED = 1.8631, 3000, 20260729
RISK = 0.004671

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
nominal = RISK * ACCOUNT
r = net_pc / np.where(valid, unit, np.inf)
r = r[valid]
mu, sigma = r.mean(), r.std(ddof=1)
print(f"[stats] R: media {mu:+.4f}  sigma {sigma:.4f}  n={len(r)}  (money: 1R=${nominal:.2f})")


def sim(T: int, seed: int = SEED) -> dict:
    rng = np.random.default_rng(seed)
    bal = np.full(NS, ACCOUNT); peak = np.full(NS, ACCOUNT)
    status = np.zeros(NS, dtype=np.int8); used = np.zeros(NS, dtype=np.int32)
    idx_all = rng.integers(0, n, size=(NS, T))
    for t in range(T):
        live = status == 0
        if not live.any():
            break
        i = idx_all[:, t]
        floor = np.minimum(peak - DD, ACCOUNT + LOCK)
        buffer = bal - floor
        qmax = np.floor((SAFETY * buffer) / unit[i])
        status[live & ((qmax < 1) | ~valid[i]) & (buffer > 0)] = 3
        status[live & (buffer <= 0)] = 2
        live = status == 0
        if not live.any():
            break
        q = np.maximum(1.0, np.minimum(np.round(nominal / unit[i]), np.minimum(qmax, MAX_MICROS)))
        dip = bal - q * mae_pc[i]
        status[live & (dip <= floor)] = 2
        live = live & (status == 0)
        bal = np.where(live, bal + q * net_pc[i], bal)
        peak = np.maximum(peak, bal)
        status[(status == 0) & (bal <= np.minimum(peak - DD, ACCOUNT + LOCK))] = 2
        status[(status == 0) & (bal >= ACCOUNT + TARGET)] = 1
        used = np.where((status != 0) & (used == 0), t + 1, used)
    return {
        "pass": float((status == 1).mean() * 100),
        "blown": float((status == 2).mean() * 100),
        "blocked": float((status == 3).mean() * 100),
        "timeout": float((status == 0).mean() * 100),
        "median_trades": float(np.median(used[status == 1])) if (status == 1).any() else float("nan"),
    }


# --- Formula cerrada (movimiento browniano con deriva, barreras simetricas) ---
a_r = TARGET / nominal          # objetivo en R
b_r = DD / nominal              # suelo en R
theta = 2 * mu / (sigma ** 2)
p_inf = (1 - math.exp(-theta * b_r)) / (1 - math.exp(-theta * (a_r + b_r)))
print(f"[teoria] objetivo {a_r:.2f}R, suelo {b_r:.2f}R, theta={theta:.4f} -> P(tocar objetivo antes del suelo, tiempo ilimitado) = {p_inf*100:.1f}%")
print("         (cota superior optimista: ignora el trailing/lock, la asimetria real y la granularidad)")
print(f"\n{'trades':>7} {'= dias':>7} {'pase':>7} {'quema':>7} {'bloq':>7} {'timeout':>8} {'trades al pase (med)':>20}")
for T, dias in [(56, 30), (112, 60), (224, 120), (448, 240), (896, 480), (1792, 960)]:
    o = sim(T)
    print(f"{T:>7} {dias:>7} {o['pass']:>6.2f}% {o['blown']:>6.2f}% {o['blocked']:>6.2f}% {o['timeout']:>7.2f}% {o['median_trades']:>20.0f}")

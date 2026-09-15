"""FARS — Cuanta de la quema viene de la COLA DE EJECUCION (Hermes).

Compara el plan actual contra variantes que respetan el stop:
  A) tal cual
  B) tope de perdida por trade en -1.5R (stop duro respetado)
  C) excluyendo los 7 trades con r < -2R
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
TPD, HORIZON, NS, SEED = 1.8631, 30, 3000, 20260729
T = max(1, round(TPD * HORIZON))

bars, _ = load_canonical_m5(ZIP_PATH)
trades = generate_smc_fvg_oos_trades(bars)
n = len(trades)
qty0 = np.array([float(t.quantity) for t in trades])
bud = np.array([float(t.budgeted_risk_dollars) for t in trades])
net_pc = np.array([float(t.net_pnl) / max(float(t.quantity), 1.0) for t in trades])
unit = np.where((bud > 0) & (qty0 > 0), bud / np.maximum(qty0, 1.0), np.inf)
r_leg = net_pc / np.where(unit > 0, unit, np.inf)
with open("E:/FARS-LAB/FARS/lab_artifacts/d_protocol/smc_fvg_risk_10_maes.json", encoding="utf-8") as fh:
    mapa = {m["trade_id"]: float(m["mae_points"]) * DPP for m in json.load(fh)["trades_mae"]}
mae_pc = np.array([mapa.get(t.trade_id, 0.0) for t in trades])
valid = np.isfinite(unit) & (unit > 0)
tail = r_leg < -2.0
print(f"[prep] n={n} cola(r<-2R)={int(tail.sum())} E[R]={np.nanmean(r_leg[valid]):+.4f}")


def sim(net_pc_l, unit_l, mae_pc_l, valid_l, n_pool, risk=0.004671, seed=SEED):
    rng = np.random.default_rng(seed)
    nominal = risk * ACCOUNT
    bal = np.full(NS, ACCOUNT); peak = np.full(NS, ACCOUNT)
    status = np.zeros(NS, dtype=np.int8)
    idx = rng.integers(0, n_pool, size=(NS, T))
    for t in range(T):
        live = status == 0
        if not live.any():
            break
        i = idx[:, t]
        floor = np.minimum(peak - DD, ACCOUNT + LOCK)
        buffer = bal - floor
        qmax = np.floor((SAFETY * buffer) / unit_l[i])
        status[live & ((qmax < 1) | ~valid_l[i]) & (buffer > 0)] = 3
        status[live & (buffer <= 0)] = 2
        live = status == 0
        if not live.any():
            break
        q = np.maximum(1.0, np.minimum(np.round(nominal / unit_l[i]), np.minimum(qmax, MAX_MICROS)))
        net_l = net_pc_l[i]
        dip = bal - q * mae_pc_l[i]
        status[live & (dip <= floor)] = 2
        live = live & (status == 0)
        bal = np.where(live, bal + q * net_l, bal)
        peak = np.maximum(peak, bal)
        status[(status == 0) & (bal <= np.minimum(peak - DD, ACCOUNT + LOCK))] = 2
        status[(status == 0) & (bal >= ACCOUNT + TARGET)] = 1
    return {"pass": (status == 1).mean() * 100, "blown": (status == 2).mean() * 100,
            "blocked": (status == 3).mean() * 100, "timeout": (status == 0).mean() * 100}


print(f"\n{'variante':<32} {'pase':>7} {'quema':>7} {'bloq':>7} {'timeout':>8}")
a = sim(net_pc, unit, mae_pc, valid, n)
print(f"{'A) tal cual (baseline)':<32} {a['pass']:>6.2f}% {a['blown']:>6.2f}% {a['blocked']:>6.2f}% {a['timeout']:>7.2f}%")

net_cap = np.maximum(net_pc, -1.5 * unit)
b = sim(net_cap, unit, mae_pc, valid, n)
print(f"{'B) stop duro: tope -1.5R':<32} {b['pass']:>6.2f}% {b['blown']:>6.2f}% {b['blocked']:>6.2f}% {b['timeout']:>7.2f}%")

keep = ~tail
kk = np.where(keep & valid)[0]
net_k, unit_k, mae_k = net_pc[kk], unit[kk], mae_pc[kk]
c = sim(net_k, unit_k, mae_k, np.ones(len(kk), bool), len(kk))
print(f"{'C) sin los 7 de la cola':<32} {c['pass']:>6.2f}% {c['blown']:>6.2f}% {c['blocked']:>6.2f}% {c['timeout']:>7.2f}%")

# Cuantos caminos mueren POR un trade de la cola (diagnostico directo)
rng = np.random.default_rng(SEED)
muertos_cola = 0
for _ in range(3000):
    bal = ACCOUNT; peak = ACCOUNT
    idx = rng.integers(0, n, size=T)
    for t in range(T):
        i = idx[t]
        floor = min(peak - DD, ACCOUNT + LOCK)
        buffer = bal - floor
        if buffer <= 0:
            break
        qmax = int(np.floor((SAFETY * buffer) / unit[i])) if valid[i] else 0
        if qmax < 1:
            break
        q = max(1, min(int(round(nominal / unit[i])), qmax, MAX_MICROS))
        if bal - q * mae_pc[i] <= floor:
            if tail[i]:
                muertos_cola += 1
            break
        bal += q * net_pc[i]
        peak = max(peak, bal)
        if bal >= ACCOUNT + TARGET:
            break
print(f"\n[diag] de 3000 caminos, la MAE de un trade de la cola quemo: {muertos_cola} ({muertos_cola/30:.2f}%)")

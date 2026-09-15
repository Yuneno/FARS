"""FARS — Politicas de apuesta para cuentas con trailing DD (Hermes) — v2 limpia."""
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
TPD, HORIZON, NS, SEED = 1.8631, 30, 2000, 20260729
T = max(1, round(TPD * HORIZON))
FIXED = 0.004671

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


def run(policy: str, param: float, n_markets: int = 1, rho: float = 0.0,
        floor_frac: float = 0.001, cap_frac: float = 0.015, seed: int = SEED) -> dict:
    rng = np.random.default_rng(seed)
    bal = np.full(NS, ACCOUNT); peak = np.full(NS, ACCOUNT)
    status = np.zeros(NS, dtype=np.int8); used = np.zeros(NS, dtype=np.int32)
    streak = np.zeros(NS, dtype=np.int32)
    day_loss = np.zeros(NS); day_count = np.zeros(NS, dtype=np.int32)
    common = rng.integers(0, n, size=(NS, T))
    indep = rng.integers(0, n, size=(NS, T, n_markets))
    share = rng.random(size=(NS, T)) < rho

    for t in range(T):
        for m in range(n_markets):
            live = status == 0
            if not live.any():
                break
            i = np.where(share[:, t], common[:, t], indep[:, t, m])
            floor = np.minimum(peak - DD, ACCOUNT + LOCK)
            buffer = bal - floor
            to_target = ACCOUNT + TARGET - bal
            base = FIXED * ACCOUNT
            if policy == "fixed":
                nominal = np.full(NS, param * ACCOUNT)
            elif policy == "buffer_prop":
                nominal = np.clip(param * buffer, floor_frac * ACCOUNT, cap_frac * ACCOUNT)
            elif policy == "target_prop":
                nominal = np.clip(param * to_target, floor_frac * ACCOUNT, cap_frac * ACCOUNT)
            elif policy == "streak":
                nominal = param * ACCOUNT * np.where(streak >= 2, 0.5, 1.0)
            elif policy == "daily_cap":
                nominal = np.full(NS, base)
            else:
                raise ValueError(policy)

            u = unit[i]
            qmax = np.floor((SAFETY * buffer) / u)
            blocked_now = live & ((qmax < 1) | ~valid[i]) & (buffer > 0)
            status[blocked_now] = 3
            blown_pre = live & (buffer <= 0)
            status[blown_pre] = 2
            live = status == 0
            if not live.any():
                break
            q = np.maximum(1.0, np.minimum(np.round(nominal / u), np.minimum(qmax, MAX_MICROS)))
            pnl = q * net_pc[i]

            skip = live & (day_loss >= param * ACCOUNT) if policy == "daily_cap" else np.zeros(NS, bool)
            live = live & ~skip
            if not live.any():
                continue
            dip = bal - q * mae_pc[i]
            blown_mae = live & (dip <= np.minimum(peak - DD, ACCOUNT + LOCK))
            status[blown_mae] = 2
            live = live & (status == 0)
            bal = np.where(live, bal + pnl, bal)
            peak = np.maximum(peak, bal)
            streak = np.where(live, np.where(pnl < 0, streak + 1, 0), streak)
            if policy == "daily_cap":
                day_loss = np.where(live, day_loss + np.where(pnl < 0, -pnl, 0.0), day_loss)
            day_count = np.where(status == 0, day_count + 1, day_count)
            nd = day_count >= 2
            day_loss = np.where(nd, 0.0, day_loss)
            day_count = np.where(nd, 0, day_count)
            down = (status == 0) & (bal <= np.minimum(peak - DD, ACCOUNT + LOCK))
            status[down] = 2
            win = (status == 0) & (bal >= ACCOUNT + TARGET)
            status[win] = 1
            newly = status != 0
            used = np.where(newly & (used == 0), t * n_markets + m + 1, used)
    return {
        "pass": float((status == 1).mean() * 100),
        "blown": float((status == 2).mean() * 100),
        "blocked": float((status == 3).mean() * 100),
        "timeout": float((status == 0).mean() * 100),
        "days": float(np.median(used[status == 1]) / (TPD * n_markets)) if (status == 1).any() else float("nan"),
    }


def show(title, rows, **kw):
    print(f"\n=== {title} ===")
    print(f"{'politica':<12} {'param':>8} {'pase':>7} {'quema':>7} {'bloq':>7} {'timeout':>8} {'dias':>6} {'pase/(1+f)':>11}")
    for pol, p in rows:
        o = run(pol, p, **kw)
        ratio = o["pass"] / (1 + o["blown"] + o["blocked"])
        print(f"{pol:<12} {p:>8.3f} {o['pass']:>6.2f}% {o['blown']:>6.2f}% {o['blocked']:>6.2f}% {o['timeout']:>7.2f}% {o['days']:>5.1f} {ratio:>11.2f}")


show("1 MERCADO", [
    ("fixed", 0.004671), ("fixed", 0.010),
    ("buffer_prop", 0.10), ("buffer_prop", 0.25), ("buffer_prop", 0.50),
    ("target_prop", 0.10), ("target_prop", 0.25),
    ("streak", 0.004671), ("streak", 0.010),
    ("daily_cap", 0.010), ("daily_cap", 0.015),
])
show("2 MERCADOS (rho=0.5)", [
    ("fixed", 0.004671), ("buffer_prop", 0.25), ("target_prop", 0.10),
    ("streak", 0.004671), ("daily_cap", 0.010), ("daily_cap", 0.015),
], n_markets=2, rho=0.5)

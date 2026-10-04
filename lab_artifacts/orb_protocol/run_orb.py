#!/usr/bin/env python3
"""
run_orb.py — reproduce y mide el motor de `nq-intraday-breakout` sobre nuestros
históricos MNQ, y exporta el ledger al contrato real de FARS (Phase 8A).

SEPARACIÓN:
  estrategia + motor = código del autor (nq_breakout, MIT, SIN modificar)
  adaptación de datos = adapter_data.py (databento -> MultiCharts/CT)
  FARS Core            = solo se invoca después, con el CSV canónico

Escenarios:
  repro_stop   — SimFlags() corregido + costes del autor + entry_mode='stop'
  repro_close  — LEGACY_FLAGS + LEGACY_CONFIG_KWARGS + entry_mode='close'
  fars_stop    — SimFlags() + costes MNQ realistas + entry_mode='stop' (decisorio)
Todos corren dos veces: ventana de muestra del autor (2015-03..2025-04) y
muestra completa nuestra (2010-06..2026-09).
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

sys.path.insert(0, r"E:\FARS-LAB\ext_review2\nq-intraday-breakout\src")
from nq_breakout.backtest import (  # noqa: E402
    Config, LEGACY_CONFIG_KWARGS, LEGACY_FLAGS, SimFlags, position_size, run_backtest,
)
from nq_breakout.data import load_nq_1min, resample_5min  # noqa: E402
from nq_breakout.metrics import compute_metrics, trade_pnl_tstat  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = Path("E:/FARS-LAB/ext_review2/data_adapted/mnq_1min_multicharts.csv.gz")

# ── Configuraciones ────────────────────────────────────────────────────────
NQ_CFG = Config()                                   # mundo del autor (NQ $20/pt)
MNQ_CFG = replace(
    Config(),
    point_value=2.0,            # MNQ $2/pt
    tick_value=0.50,            # 0.25 pt = $0.50
    stop_dollars=200.0,         # mismos 100 puntos de stop
    target_dollars=400.0,       # mismos 200 puntos de target
    commission_per_side=0.62,   # MNQ realista
    slippage_ticks_per_side=1.5,
    slippage_sides=2,
    initial_capital=100_000.0,
    margin_per_contract=2_328.26,   # 1/10 del margen NQ
)

SCENARIOS = {
    "repro_stop":  (NQ_CFG, SimFlags()),
    "repro_close": (replace(NQ_CFG, **LEGACY_CONFIG_KWARGS), LEGACY_FLAGS),
    "fars_stop":   (MNQ_CFG, SimFlags()),
}

WINDOWS = {
    "muestra_autor": ("2015-03-01", "2025-04-30"),   # 2015-03..2025-04
    "muestra_nuestra": ("2010-06-01", "2026-09-30"),
}


def metrics_block(res, cfg: Config) -> dict:
    eq = res.equity
    if eq.empty:
        return {"trades": 0}
    m = compute_metrics(eq)                      # Sharpe/Vol/DD%/DD$/CAGR
    t = trade_pnl_tstat(res.trades) if len(res.trades) else {}
    return {
        "trades": int(len(res.trades)),
        "sharpe": round(float(m["Sharpe"]), 4),
        "volatility_pct": round(float(m["Volatility"]) * 100, 4),
        "max_dd_pct": round(float(m["Max Drawdown %"]) * 100, 4),
        "t_stat": round(float(t.get("t_stat", float("nan"))), 4) if t else None,
        "cagr_pct": round(float(m["CAGR"]) * 100, 4),
        "net_pnl_usd": round(float(res.trades["pnl"].sum()), 2) if len(res.trades) else 0.0,
        "pnl_per_contract_mean": round(float(res.trades["pnl_per_contract"].mean()), 2) if len(res.trades) else None,
        "round_turn_cost_usd": round(cfg.round_turn_cost, 2),
        "first": str(eq.index[0].date()),
        "last": str(eq.index[-1].date()),
    }


def ledger_fars(res, cfg: Config, path: Path, tag: str) -> int:
    """Ledger Phase 8A: r_result = PnL neto / riesgo, en unidades R."""
    rows = []
    for t in res.trades.itertuples(index=False):
        stop_pts = float(t.stop_points)
        sign = 1.0 if t.direction == "long" else -1
        gross_pts = sign * (t.exit_price - t.entry_price)
        cost_pts = cfg.round_turn_cost / cfg.point_value
        r = (gross_pts - cost_pts) / stop_pts
        rows.append({
            "trade_id": f"{tag}-{t.entry_time}-{len(rows)+1:05d}",
            "timestamp": (pd.Timestamp(t.entry_time)
                          .tz_localize("America/Chicago", ambiguous="infer",
                                       nonexistent="shift_forward")
                          .isoformat()),
            "asset": "MNQ",
            "direction": t.direction,
            "entry_price": round(float(t.entry_price), 4),
            "stop_price": round(float(t.entry_price - sign * stop_pts), 4),
            "exit_price": round(float(t.exit_price), 4),
            "r_result": round(float(r), 6),
            "strategy": f"orb_nqbreakout:{tag}",
        })
    pd.DataFrame(rows).to_csv(path, index=False)
    return len(rows)


def main() -> None:
    print("Cargando datos adaptados …")
    df1 = load_nq_1min(DATA)
    df5 = resample_5min(df1)
    print(f"  1-min {len(df1):,} | 5-min {len(df5):,}")

    out = {}
    for wname, (lo, hi) in WINDOWS.items():
        sub = df5[(df5["date"] >= lo) & (df5["date"] <= hi + " 23:59:59")]
        print(f"\n══ {wname}: {lo} → {hi}  ({len(sub):,} barras 5m) ══")
        for sname, (cfg, flags) in SCENARIOS.items():
            res = run_backtest(sub, cfg, flags)
            m = metrics_block(res, cfg)
            out[f"{wname}_{sname}"] = m
            print(f"  [{sname:11}] trades={m.get('trades')} sharpe={m.get('sharpe')} "
                  f"dd={m.get('max_dd_pct')}% t={m.get('t_stat')} net=${m.get('net_pnl_usd')}")
            if wname == "muestra_nuestra":
                n = ledger_fars(res, cfg, HERE / f"ledger_orb_{sname}.csv", sname)
                print(f"      ledger FARS: {n} trades -> ledger_orb_{sname}.csv")

    (HERE / "metrics_orb.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nEscrito metrics_orb.json con {len(out)} escenarios")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""run_orb_multi.py — ORB multi-mercado × variante EOD (PREREGISTRO_V1_MULTI.md).

Corre el motor del autor SIN modificar sobre 4 mercados (MNQ/MES/MGC/MYM) y
2 variantes (base max_long_hold_days=5 · eod=1 con cierre al último bar):
- Iso-riesgo USD: stop_dollars=200 / target_dollars=400 en todos (config nativo).
- Costes: 0.62 USD/side + 1.5 ticks/side (tick por mercado).
- Ventanas: 2010-2022 · 2023-2026 · total real observado por mercado.
- PARIDAD PRIMERO: la celda MNQ/base debe reproducir fars_stop (2444 trades,
  E[R]=+0.0573) o nada se lee. Se verifica automáticamente.

Salida: RESULTADOS_V1_MULTI.txt + resultados_v1_multi.json.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, r"E:\FARS-LAB\FARS")
sys.path.insert(0, str(HERE))
sys.path.insert(0, r"E:\FARS-LAB\ext_review2\nq-intraday-breakout\src")

from nq_breakout.backtest import Config, SimFlags, run_backtest  # noqa: E402
from nq_breakout.data import load_nq_1min, resample_5min  # noqa: E402

DATA_DIR = Path("E:/FARS-LAB/ext_review2/data_adapted")

MERCADOS = {
    "MNQ": dict(point_value=2.0, tick_value=0.50, margin=2_328.26),
    "MES": dict(point_value=5.0, tick_value=1.25, margin=2_520.00),
    "MGC": dict(point_value=10.0, tick_value=1.00, margin=1_760.00),
    "MYM": dict(point_value=0.50, tick_value=0.50, margin=1_540.00),
}
FILES = {
    "MNQ": DATA_DIR / "mnq_1min_multicharts.csv.gz",
    "MES": DATA_DIR / "mes_1min_multicharts.csv.gz",
    "MGC": DATA_DIR / "mgc_1min_multicharts.csv.gz",
    "MYM": DATA_DIR / "mym_1min_multicharts.csv.gz",
}
VARIANTES = {"base": 5, "eod": 1}   # max_long_hold_days


def stats(r: np.ndarray) -> dict:
    n = len(r)
    if n == 0:
        return {"n": 0}
    mu = float(np.mean(r)); sd = float(np.std(r, ddof=1)) if n > 1 else float("nan")
    t = mu / (sd / math.sqrt(n)) if n > 1 and sd > 0 else float("nan")
    return {"n": n, "er": round(mu, 6), "std": round(sd, 4),
            "wr": round(float(np.mean(r > 0)) * 100, 2),
            "t": round(t, 3)}


def main() -> None:
    out = {"preregistro": "PREREGISTRO_V1_MULTI.md", "celdas": []}
    lineas = []
    paridad_ok = False

    for sym, mc in MERCADOS.items():
        path = FILES[sym]
        if not path.exists():
            lineas.append(f"{sym}: SIN DATOS ({path.name} no existe) — omitido")
            continue
        df1 = load_nq_1min(path)
        df5 = resample_5min(df1)
        span_meses = (df5["date"].iloc[-1] - df5["date"].iloc[0]).days / 30.44

        for vname, mld in VARIANTES.items():
            cfg = replace(
                Config(),
                point_value=mc["point_value"], tick_value=mc["tick_value"],
                stop_dollars=200.0, target_dollars=400.0,
                commission_per_side=0.62, slippage_ticks_per_side=1.5,
                slippage_sides=2, initial_capital=100_000.0,
                margin_per_contract=mc["margin"], max_long_hold_days=mld,
            )
            res = run_backtest(df5, cfg, SimFlags())
            tr = res.trades
            # r_result idéntico a run_orb.ledger_fars: (bruto_pts − costes_pts)/stop_pts
            stop_pts = 200.0 / cfg.point_value
            cost_pts = cfg.round_turn_cost / cfg.point_value
            sign = np.where(tr["direction"].to_numpy() == "long", 1.0, -1.0)
            gross_pts = sign * (tr["exit_price"].to_numpy() - tr["entry_price"].to_numpy())
            r_all = (gross_pts - cost_pts) / stop_pts
            ts = pd.to_datetime(tr["entry_time"])
            cortes = {
                "2010-2022": r_all[(ts.dt.year >= 2010) & (ts.dt.year <= 2022)],
                "2023-2026": r_all[ts.dt.year >= 2023],
                "total": r_all,
            }
            cella = {"mercado": sym, "variante": vname,
                     "meses_datos": round(span_meses, 1),
                     "por_ventana": {k: stats(v) for k, v in cortes.items()},
                     "trades_mes": round(len(r_all) / span_meses, 2)}
            out["celdas"].append(cella)
            s = cella["por_ventana"]["total"]
            lineas.append(
                f"{sym:4} {vname:4} | n={s['n']:5} | E[R]={s['er']:+.4f} | t={s['t']:+.2f} "
                f"| WR={s['wr']:.1f}% | trades/mes={cella['trades_mes']:.2f} "
                f"| 2010-22: {cortes['2010-2022'].size} tr {np.mean(cortes['2010-2022']) if cortes['2010-2022'].size else float('nan'):+.4f} "
                f"| 2023-26: {cortes['2023-2026'].size} tr {np.mean(cortes['2023-2026']) if cortes['2023-2026'].size else float('nan'):+.4f}"
            )
            if sym == "MNQ" and vname == "base":
                paridad_ok = (s["n"] == 2444 and abs(s["er"] - 0.0573) < 0.002)
                lineas.append(f"   PARIDAD vs fars_stop: {'OK' if paridad_ok else 'FALLIDA — NO LEER EL RESTO'}")

    header = ("=" * 78 + "\nRESULTADOS V1-MULTI (PREREGISTRO_V1_MULTI.md congelado)\n"
              + "=" * 78 + "\n")
    print(header)
    print("\n".join(lineas))
    out["paridad_mnq_base"] = paridad_ok
    (HERE / "resultados_v1_multi.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    (HERE / "RESULTADOS_V1_MULTI.txt").write_text(
        header + "\n".join(lineas) + "\n", encoding="utf-8")
    if not paridad_ok:
        print("\n⚠️ PARIDAD FALLIDA — resultados no interpretables.")
        sys.exit(1)


if __name__ == "__main__":
    main()

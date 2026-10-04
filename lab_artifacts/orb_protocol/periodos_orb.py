"""Periodos + metricas de FARS Core (API oficial) sobre el ledger decisorio."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, r"E:\FARS-LAB\FARS")

D = Path("E:/FARS-LAB/FARS/lab_artifacts/orb_protocol")
SRC = D / "ledger_orb_fars_stop.csv"


def fars_metrics(path):
    from dataclasses import asdict

    from src.ingestion import load_trade_csv
    from src.metrics import compute_metrics
    res = load_trade_csv(str(path), outcomes_finalized=True,
                         analysis_timezone="America/New_York")
    if not res.trades:
        return {"n": 0}
    return asdict(compute_metrics([t.r_result for t in res.trades]))


def split(tag, lo, hi):
    import pandas as pd
    df = pd.read_csv(SRC)
    ts = pd.to_datetime(df["timestamp"], utc=True)
    sel = df[(ts >= pd.Timestamp(lo, tz="UTC")) & (ts < pd.Timestamp(hi, tz="UTC"))]
    out = D / f"ledger_orb_{tag}.csv"
    sel.to_csv(out, index=False)
    m = fars_metrics(out)
    print(f"{tag:22} n={m.get('n', m.get('trades'))}  " + "  ".join(
        f"{k}={round(v, 4) if isinstance(v, float) else v}"
        for k, v in m.items() if k not in ('n', 'trades', 'skewness', 'kurtosis',
                                           'avg_win_r', 'avg_loss_r',
                                           'avg_win_R', 'avg_loss_R')))
    return m


print("== FARS Core sobre el ledger decisorio (fars_stop, MNQ, costes realistas) ==")
full = fars_metrics(SRC)
for k, v in full.items():
    print(f"  {k}: {v}")

print("\n== por periodos ==")
split("2010-2022", "2010-01-01", "2023-01-01")
split("2023-2026", "2023-01-01", "2027-01-01")
print("  -- corte segun la fecha de congelacion de reglas del autor (2022-01-31) --")
split("2010-2022_01", "2010-01-01", "2022-02-01")
split("2022_02-2026", "2022-02-01", "2027-01-01")

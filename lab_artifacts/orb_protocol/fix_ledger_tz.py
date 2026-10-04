"""Repara los ledgers ya exportados: timestamps CT naive -> tz-aware.

El motor del autor trabaja con wall-clock US/Central sin zona (así lo exige su
loader). FARS exige timestamps tz-aware para temporal_analysis y
daily_rule_simulation, así que se les adjunta America/Chicago antes de entregar
el ledger al contrato real.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

D = Path("E:/FARS-LAB/FARS/lab_artifacts/orb_protocol")
for p in sorted(D.glob("ledger_orb_*.csv")):
    if p.name.endswith("_tz.csv"):
        continue
    df = pd.read_csv(p)
    ts = pd.to_datetime(df["timestamp"])
    df["timestamp"] = ts.dt.tz_localize(
        "America/Chicago", ambiguous="infer", nonexistent="shift_forward").map(
            lambda x: x.isoformat())
    df.to_csv(p, index=False)
    print(f"{p.name}: {len(df)} filas -> tz-aware America/Chicago "
          f"({df['timestamp'].iloc[0][:25]} … {df['timestamp'].iloc[-1][:25]})")

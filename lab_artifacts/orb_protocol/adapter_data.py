#!/usr/bin/env python3
"""
adapter_data.py — capa de ADAPTACIÓN DE DATOS (separada de la estrategia y de FARS Core).

Convierte el MNQ M1 canónico de FARS (databento.zip, timestamps UTC ISO)
al formato que espera el motor de `nq-intraday-breakout` (MultiCharts:
Date dd/mm/yyyy + Time HH:MM:%S, OHLC + TotalVolume, marcado en US/Central).

Por qué US/Central: `nq_breakout/data.py:21-24` toma los timestamps tal cual,
sin zona, y el motor asume que el feed está en CT (ventana 08:30-10:00 CT =
primeros 90 min de la sesión de cash = 09:30-11:00 ET). Escribir ET o UTC
desplazaría la ventana una hora entera.

Uso:
    python adapter_data.py            # escribe el CSV adaptado + diagnóstico
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, r"E:\FARS-LAB\ext_review2\nq-intraday-breakout\src")

ZIP_PATH = Path("E:/FARS-LAB/databento.zip")
MEMBER = "databento/MNQ_M1.csv"
OUT_DIR = Path("E:/FARS-LAB/ext_review2/data_adapted")
OUT_CSV = OUT_DIR / "mnq_1min_multicharts.csv.gz"
CT = "America/Chicago"


def adapt(chunksize: int = 500_000) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    first_ts = last_ts = None
    with zipfile.ZipFile(ZIP_PATH) as zf, zf.open(MEMBER) as raw:
        reader = pd.read_csv(
            raw,
            usecols=["timestamp", "open", "high", "low", "close", "volume"],
            chunksize=chunksize,
        )
        for i, chunk in enumerate(reader):
            ts = pd.to_datetime(chunk["timestamp"], utc=True, format="ISO8601")
            ts = ts.dt.tz_convert(CT).dt.tz_localize(None)  # wall-clock CT
            out = pd.DataFrame({
                "Date": ts.dt.strftime("%d/%m/%Y"),
                "Time": ts.dt.strftime("%H:%M:%S"),
                "Open": chunk["open"].to_numpy(),
                "High": chunk["high"].to_numpy(),
                "Low": chunk["low"].to_numpy(),
                "Close": chunk["close"].to_numpy(),
                "TotalVolume": chunk["volume"].to_numpy(),
            })
            out.to_csv(OUT_CSV, mode="w" if i == 0 else "a",
                       header=(i == 0), index=False, compression="gzip")
            n += len(chunk)
            first_ts = first_ts if first_ts is not None else ts.iloc[0]
            last_ts = ts.iloc[-1]
            print(f"  chunk {i}: {n:,} filas ({ts.iloc[0]} → {ts.iloc[-1]})")
    print(f"\nEscrito {OUT_CSV} con {n:,} barras 1-min")
    print(f"  cobertura CT: {first_ts} → {last_ts}")
    return OUT_CSV


def diagnostics(path: Path) -> None:
    """Corre el diagnóstico del propio motor sobre el dato adaptado."""
    from nq_breakout.data import load_nq_1min, resample_5min
    from nq_breakout.diagnostics import run_data_diagnostics

    df1 = load_nq_1min(path)
    df5 = resample_5min(df1)
    print(f"\n1-min: {len(df1):,} barras | 5-min: {len(df5):,} barras")
    run_data_diagnostics(df1)


if __name__ == "__main__":
    p = adapt()
    diagnostics(p)

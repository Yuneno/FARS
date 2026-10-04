"""Verificar por que el Sharpe legacy no calza: es la construccion de la curva
de equity (padding calendario + anualizacion) y no un bug del motor."""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, r"E:\FARS-LAB\ext_review2\nq-intraday-breakout\src")
from nq_breakout.backtest import Config, LEGACY_CONFIG_KWARGS, LEGACY_FLAGS, run_backtest  # noqa
from nq_breakout.data import load_nq_1min, resample_5min  # noqa
from nq_breakout.metrics import (  # noqa
    compute_metrics, legacy_equity_curve, pad_to_calendar_days,
)

DATA = Path("E:/FARS-LAB/ext_review2/data_adapted/mnq_1min_multicharts.csv.gz")
df5 = resample_5min(load_nq_1min(DATA))
sub = df5[(df5["date"] >= "2015-03-01") & (df5["date"] <= "2025-04-30 23:59:59")]

cfg = replace(Config(), **LEGACY_CONFIG_KWARGS)
res = run_backtest(sub, cfg, LEGACY_FLAGS)
print(f"legacy: {len(res.trades)} trades, engine equity obs = {len(res.equity)}")

eq_engine = res.equity
eq_legacy = legacy_equity_curve(res.trades, cfg.initial_capital)
eq_cal = pad_to_calendar_days(eq_legacy)

print(f"  legacy_equity_curve obs = {len(eq_legacy)} | padded obs = {len(eq_cal)}")
print()
print(f"{'construccion':46} {'freq':>5} {'Sharpe':>8}")
for label, eq, freq in [
    ("engine equity (dias de trading)", eq_engine, 252),
    ("legacy_equity_curve (dias de trading)", eq_legacy, 252),
    ("legacy_equity_curve PADDED, freq 252 (artefacto)", eq_cal, 252),
    ("legacy_equity_curve PADDED, freq 365", eq_cal, 365),
]:
    m = compute_metrics(eq, freq=freq)
    print(f"{label:46} {freq:>5} {m['Sharpe']:>8.4f}")

print("\nPublicado en el README del autor: legacy close fill Sharpe = 0.95")

#!/usr/bin/env python3
"""
fondeo_profiles.py — ¿sobrevive la estrategia ORB a una cuenta fondeada real?

Usa las herramientas REVISADAS de FARS (Phase 11C) contra perfiles de prop firms
(`src/funded_profiles.py`: Apex 25k/50k/100k/150k y Rapid 25k), con una CURVA DE
RIESGO PREDECLARADA — sin elegir el óptimo (eso sería ajustar para mejorar el
resultado, prohibido por diseño y por instrucción de Ricardo).

La pregunta concreta: el ledger ORB tiene MaxDD = 22,6 R. Con 1% de riesgo por
trade sobre $100k (=$1.000/trade) eso son ~$22.600 de drawdown, muy por encima
de los $8.000 que aguanta una cuenta. ¿Existe un tamaño de posición donde la
cuenta sobreviva Y quede expectativa?

Semilla: 20260928. Dataset role: calibration (único rol que admite rejilla
multi-nivel según el contrato de FARS).
"""
from __future__ import annotations

import sys
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, r"E:\FARS-LAB\FARS")

from src.bootstrap import analyze_bootstrap  # noqa: E402
from src.funded_profiles import apex_100k_profile, rapid_25k_profile  # noqa: E402
from src.ingestion import load_trade_csv  # noqa: E402
from src.probabilistic_paths import (  # noqa: E402
    CostAssumptions, PathSimulationConfig, RiskSizingPolicy,
    run_risk_sensitivity,
)

ET = ZoneInfo("America/New_York")
LEDGER = HERE / "ledger_orb_fars_stop.csv"
SEED = 20260928

# Rejilla PREDECLARADA antes de correr. Se reporta entera, sin filtrar.
RISK_GRID_PCT = [0.0010, 0.0015, 0.0020, 0.0025, 0.0035, 0.0050, 0.0100]

COSTS = CostAssumptions(
    currency="USD",
    commission_per_trade=Decimal("1.24"),          # MNQ 0,62 USD/side
    modeled_slippage_per_trade=Decimal("0.75"),    # 1,5 ticks * 0,50 USD
)


def main() -> None:
    print("Cargando ledger ORB …")
    ds = load_trade_csv(str(LEDGER), outcomes_finalized=True,
                        analysis_timezone="America/New_York")
    print(f"  {len(ds.trades)} trades aceptados por el contrato Phase 8A")

    print("Bootstrap de FARS (semilla única, B=2000) …")
    boot = analyze_bootstrap(ds, master_seed=SEED, B=2000)

    config = PathSimulationConfig(
        n_simulations=2000,
        max_trades=len(ds.trades),
        seed=SEED,
        start_at=datetime(2026, 6, 1, 9, 30, tzinfo=ET),
        trades_per_day=1,
        dataset_role="calibration",
    )

    for nombre, perfil in (("Apex 100k", apex_100k_profile()),
                           ("Rapid 25k", rapid_25k_profile())):
        grid = [RiskSizingPolicy(mode="starting_balance_fraction",
                                 value=Decimal(str(p)), currency="USD")
                for p in RISK_GRID_PCT]
        print(f"\n══ {nombre} — curva de riesgo predeclarada ══")
        try:
            res = run_risk_sensitivity(ds, boot, perfil, grid, COSTS, config)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR: {type(exc).__name__}: {exc}")
            continue
        print(f"  tipo resultado: {type(res).__name__}")
        try:
            puntos = res.points
        except AttributeError:
            print(f"  repr: {str(res)[:1500]}")
            continue
        for pt in puntos:
            try:
                pol = pt.policy
                r = pt.result if hasattr(pt, "result") else pt
                d = asdict(r) if hasattr(r, "__dataclass_fields__") else dict(r)
                clave = {k: v for k, v in d.items()
                         if any(s in k.lower() for s in
                                ("pass", "breach", "prob", "drawdown", "daily",
                                 "target", "ruin", "surviv"))}
                print(f"  riesgo {float(pol.value)*100:.2f}% -> {clave}")
            except Exception as exc:  # noqa: BLE001
                print(f"  (no pude formatear el punto: {exc})")


if __name__ == "__main__":
    main()

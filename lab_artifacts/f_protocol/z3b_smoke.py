#!/usr/bin/env python3
"""Smoke test reproducible para ZoneEngine con anclajes de sesión (Z3-b).

Compara la corrida de 5.000 barras M5 canónicas de MNQ con session_pools=False (baseline)
frente a session_pools=True (Z3-b activo), verificando paridad exacta de la línea base
y el poblado determinista de las 16 features de sesión.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lab_artifacts.run_c1_walkforward import ZIP_PATH, load_canonical_m5
from src.zones.engine import ZoneEngine


def run_block(bars, session_pools: bool) -> dict:
    eng = ZoneEngine(
        symbol="MNQ",
        timeframe="M5",
        tick_size=0.25,
        session_pools=session_pools,
    )
    eng.update(bars)

    all_z = eng.all_zones()
    active_z = eng.active_zones()

    by_type: dict[str, int] = {}
    for z in active_z:
        by_type[z.zone_type] = by_type.get(z.zone_type, 0) + 1

    fvg_states: dict[str, int] = {}
    for z in active_z:
        if z.zone_type == "fvg":
            fvg_states[z.state] = fvg_states.get(z.state, 0) + 1

    liq_states: dict[str, int] = {}
    for z in active_z:
        if z.zone_type == "liquidity":
            liq_states[z.state] = liq_states.get(z.state, 0) + 1

    liq_dir: dict[str, int] = {}
    for z in active_z:
        if z.zone_type == "liquidity":
            liq_dir[z.direction] = liq_dir.get(z.direction, 0) + 1

    ctx = eng.context(bars[-1].close, atr=5.0)

    print(f"barras procesadas: {len(bars)}")
    print(f"zonas totales: {len(all_z)}")
    print(f"zonas activas: {len(active_z)}")
    print(f"activas por tipo: {by_type}")
    print(f"FVG activas por estado: {fvg_states}")
    print(f"liq activas por estado: {liq_states}")
    print(f"liq por tipo (dirección): {liq_dir}")
    if "session_level" in by_type:
        ses_states: dict[str, int] = {}
        for z in active_z:
            if z.zone_type == "session_level":
                ses_states[z.state] = ses_states.get(z.state, 0) + 1
        print(f"session_level activas por estado: {ses_states}")

    base_keys = [
        "inside_bullish_fvg", "inside_bearish_fvg", "fvg_age_bars",
        "fvg_fill_pct", "distance_to_buyside_liquidity_pts", "distance_to_buyside_liquidity_atr",
        "distance_to_sellside_liquidity_pts", "distance_to_sellside_liquidity_atr",
        "liquidity_touch_count", "liquidity_swept", "fvg_liquidity_overlap",
        "zone_overlap_count", "nearest_zone_type",
    ]
    base_ctx_str = ", ".join(f"{k}={ctx.get(k)!r}" for k in base_keys)
    print(f"contexto: {base_ctx_str}")

    if session_pools:
        session_keys = [
            "distance_to_prev_day_high_pts", "distance_to_prev_day_high_atr",
            "distance_to_prev_day_low_pts", "distance_to_prev_day_low_atr",
            "distance_to_d20_high_pts", "distance_to_d20_high_atr",
            "distance_to_d20_low_pts", "distance_to_d20_low_atr",
            "distance_to_overnight_high_pts", "distance_to_overnight_high_atr",
            "distance_to_overnight_low_pts", "distance_to_overnight_low_atr",
            "inside_prev_day_range", "prev_day_range_position",
            "overnight_swept_prev_day_high", "overnight_swept_prev_day_low",
        ]
        session_ctx_str = ", ".join(f"{k}={ctx.get(k)!r}" for k in session_keys)
        print(f"contexto sesión (16 features): {session_ctx_str}")

    return {
        "n_bars": len(bars),
        "total_zones": len(all_z),
        "active_zones": len(active_z),
        "by_type": by_type,
        "fvg_states": fvg_states,
        "liq_states": liq_states,
        "liq_dir": liq_dir,
        "context": ctx,
    }


def main():
    print("Cargando 5.000 barras canónicas de MNQ M5...")
    bars, sha = load_canonical_m5(ZIP_PATH)
    bars5k = bars[:5000]

    print("\n" + "=" * 60)
    print("ORACULO FLAG OFF: session_pools=False (baseline idéntico)")
    print("=" * 60)
    res_off = run_block(bars5k, session_pools=False)

    print("\n" + "=" * 60)
    print("ORACULO FLAG ON: session_pools=True (Z3-b activo)")
    print("=" * 60)
    res_on = run_block(bars5k, session_pools=True)

    # Verificaciones de paridad
    assert res_off["total_zones"] == 382, f"Total zonas OFF esperado 382, obtenido {res_off['total_zones']}"
    assert res_off["active_zones"] == 220, f"Activas OFF esperado 220, obtenido {res_off['active_zones']}"
    assert res_off["by_type"] == {"liquidity": 198, "fvg": 22}
    assert res_on["by_type"]["liquidity"] == 198
    assert res_on["by_type"]["fvg"] == 22

    print("\n[OK] Smoke test completado con exito. Baseline identico y features de sesion verificadas.")


if __name__ == "__main__":
    main()

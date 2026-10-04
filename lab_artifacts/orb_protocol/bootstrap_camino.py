#!/usr/bin/env python3
"""
bootstrap_camino.py — cuantificación de incertidumbre y funcionales de camino.

METODOLOGÍA Y JUSTIFICACIÓN ESTADÍSTICA (Decisión pre-registrada Opus 4.6):
─────────────────────────────────────────────────────────────────────────────
1. IID Bootstrap para funcionales de media:
   Para estimadores lineales puntuales como E[R], win rate y desviación estándar,
   se utiliza el motor oficial de FARS `src.bootstrap.analyze_bootstrap` con
   semilla fija y B=2000.

2. Moving Block Bootstrap (MBB) SIN envoltura para funcionales de camino:
   El Drawdown Máximo y la Racha Perdedora Máxima son "funcionales de camino"
   (path functionals), altamente sensibles a la topología temporal y a la
   persistencia secuencial de pérdidas.
   - En el Circular Block Bootstrap (CBB), los bloques se muestrean con envoltura
     modular `(starts + offsets) % n`. Esta "costura" periódica une artificialmente
     el final de la serie histórica con el inicio, conectando regímenes de mercado
     completamente desconectados en el tiempo. Aunque asintóticamente despreciable
     para medias, la costura FRACTURA los funcionales de camino, generando rachas
     híbridas ficticias y distorsionando severamente la distribución del drawdown.
   - Por ello, se implementa Moving Block Bootstrap (MBB) NO CIRCULAR / SIN ENVOLTURA:
     cada bloque se selecciona únicamente dentro del rango válido `0 <= start <= n - block_size`.
     Cada bloque muestreado es un fragmento temporal 100% contiguo de la historia observada.
   - La longitud de bloque se selecciona de forma objetiva mediante la regla
     óptima de Politis & White (2004) / Patton, Politis & White (2009) implementada
     en `arch.bootstrap.optimal_block_length`, SIN ajuste manual post-hoc.

3. Stationary Bootstrap (SB) como chequeo de robustez:
   Se complementa con el Stationary Bootstrap (Politis & Romano, 1994) con longitud
   de bloque distribuida geométricamente alrededor de la longitud óptima estacionaria.

4. Diagnósticos de dependencia como contexto:
   Los tests de Ljung-Box (rank-portmanteau) y test de rachas se reportan como contexto
   diagnóstico del proceso, no para cherry-picking metodológico.

5. Probabilidad de activación de límites de cuenta financiada:
   Bajo cada trayectoria simulada de B=2000 réplicas, se mide la probabilidad empírica
   de alcanzar el límite de pérdida diaria ($2.000 USD), el drawdown trailing ($8.000 USD)
   y el techo operativo.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
from arch.bootstrap import optimal_block_length

HERE = Path(__file__).resolve().parent
sys.path.insert(0, r"E:\FARS-LAB\FARS")
sys.path.insert(0, str(HERE))

from src.bootstrap import analyze_bootstrap  # noqa: E402
from src.ingestion import load_trade_csv  # noqa: E402
from src.metrics import compute_metrics, max_drawdown_r, max_losing_streak  # noqa: E402

MASTER_SEED = 20260928
B_REPLICATES = 2000
CONFIDENCE_LEVEL = 0.95


def generate_mbb_unwrapped_indices(n: int, block_size: int, B: int, seed: int) -> np.ndarray:
    """Genera la matriz de índices de remuestreo MBB estrictamente sin envoltura circular (shape: B, n)."""
    max_start = n - block_size
    if max_start < 0:
        raise ValueError(f"block_size ({block_size}) mayor que n ({n})")
    k = math.ceil(n / block_size)
    rng = np.random.Generator(np.random.PCG64(seed))
    starts = rng.integers(0, max_start + 1, size=(B, k))
    offsets = np.arange(block_size)
    blocks = starts[:, :, None] + offsets
    return blocks.reshape(B, k * block_size)[:, :n]


def run_unwrapped_mbb(r: np.ndarray, block_size: int, B: int,
                      seed: int, dates: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """Moving Block Bootstrap SIN envoltura circular para funcionales de camino."""
    n = len(r)
    max_start = n - block_size
    if max_start < 0:
        raise ValueError(f"block_size ({block_size}) mayor que n ({n})")

    drawdowns = np.empty(B, dtype=np.float64)
    streaks = np.empty(B, dtype=np.int64)
    limit_activations: list[dict] = []

    # Parámetros de cuenta para límites
    initial_balance = 100_000.0
    risk_per_trade_usd = 1_000.0  # 1% de 100k
    daily_loss_limit_usd = 2_000.0  # 2R ($2.000)
    max_dd_limit_usd = 8_000.0     # 8R ($8.000)

    if dates is None:
        dates = np.arange(n, dtype=np.int64)

    idx_matrix = generate_mbb_unwrapped_indices(n, block_size, B, seed)

    for b_idx in range(B):
        idx = idx_matrix[b_idx]
        resample = r[idx]
        resample_dates = dates[idx]

        # 1. Funcionales de camino
        dd_r = max_drawdown_r(list(resample))
        streak = max_losing_streak(list(resample))
        drawdowns[b_idx] = dd_r
        streaks[b_idx] = streak

        # 2. Simulación de límites de cuenta en el camino
        dollar_pnl = resample * risk_per_trade_usd
        cum_equity = initial_balance + np.cumsum(dollar_pnl)
        cum_peaks = np.maximum.accumulate(np.insert(cum_equity, 0, initial_balance))[1:]
        trailing_dd = cum_peaks - cum_equity

        # Límite de drawdown ($8.000)
        hit_dd = bool(np.any(trailing_dd >= max_dd_limit_usd))

        # Fix B3: Límite diario ($2.000 = 2R acumulado intradía agrupado por día real)
        hit_daily = False
        current_day = None
        day_pnl_r = 0.0
        for r_val, d_val in zip(resample, resample_dates):
            if d_val != current_day:
                current_day = d_val
                day_pnl_r = 0.0
            day_pnl_r += r_val
            if day_pnl_r <= -2.0:
                hit_daily = True
                break

        # Fix B3: Tope de operaciones en una ventana móvil de 21 días de trading (1 mes operativo)
        # Se agrupan operaciones respetando el orden secuencial cronológico del camino remuestreado
        day_trade_counts: list[int] = []
        cur_day = None
        cur_count = 0
        for d_val in resample_dates:
            if d_val != cur_day:
                if cur_day is not None:
                    day_trade_counts.append(cur_count)
                cur_day = d_val
                cur_count = 0
            cur_count += 1
        if cur_day is not None:
            day_trade_counts.append(cur_count)

        counts_arr = np.array(day_trade_counts, dtype=np.int64)
        if len(counts_arr) >= 21:
            rolling_21 = np.convolve(counts_arr, np.ones(21, dtype=np.int64), mode="valid")
            hit_max_ops = bool(np.any(rolling_21 > 42))
        else:
            hit_max_ops = bool(np.sum(counts_arr) > 42)

        limit_activations.append({
            "hit_dd": hit_dd,
            "hit_daily": hit_daily,
            "hit_max_ops": hit_max_ops,
        })

    return drawdowns, streaks, limit_activations


def run_stationary_bootstrap(r: np.ndarray, mean_block_size: int, B: int,
                             seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Stationary Bootstrap (Politis & Romano 1994) con longitudes geométricas."""
    n = len(r)
    p_geom = 1.0 / max(1, mean_block_size)
    rng = np.random.Generator(np.random.PCG64(seed))

    drawdowns = np.empty(B, dtype=np.float64)
    streaks = np.empty(B, dtype=np.int64)

    for b_idx in range(B):
        indices: list[int] = []
        while len(indices) < n:
            start = int(rng.integers(0, n))
            block_len = int(rng.geometric(p_geom))
            for o in range(block_len):
                indices.append((start + o) % n)
                if len(indices) == n:
                    break
        resample = r[np.array(indices)]
        drawdowns[b_idx] = max_drawdown_r(list(resample))
        streaks[b_idx] = max_losing_streak(list(resample))

    return drawdowns, streaks


def _evaluate_path_suite(r: np.ndarray, dates: np.ndarray, mbb_block: int,
                         sb_block: int, B: int, mbb_seed: int, sb_seed: int) -> dict:
    """Evalúa funcionales de camino y límites para una subsecuencia dada."""
    mbb_dd, mbb_str, limit_acts = run_unwrapped_mbb(r, mbb_block, B, mbb_seed, dates=dates)
    sb_dd, sb_str = run_stationary_bootstrap(r, sb_block, B, sb_seed)

    lo_q, hi_q = 0.025, 0.975
    mbb_dd_ci = (float(np.quantile(mbb_dd, lo_q)), float(np.quantile(mbb_dd, hi_q)))
    mbb_str_ci = (int(round(np.quantile(mbb_str, lo_q))), int(round(np.quantile(mbb_str, hi_q))))
    sb_dd_ci = (float(np.quantile(sb_dd, lo_q)), float(np.quantile(sb_dd, hi_q)))
    sb_str_ci = (int(round(np.quantile(sb_str, lo_q))), int(round(np.quantile(sb_str, hi_q))))

    p_hit_dd = sum(x["hit_dd"] for x in limit_acts) / B
    p_hit_daily = sum(x["hit_daily"] for x in limit_acts) / B
    p_hit_max_ops = sum(x["hit_max_ops"] for x in limit_acts) / B

    return {
        "path_functionals": {
            "mbb_unwrapped": {
                "max_drawdown_ci_95": mbb_dd_ci,
                "max_drawdown_mean": float(np.mean(mbb_dd)),
                "max_drawdown_median": float(np.median(mbb_dd)),
                "max_losing_streak_ci_95": mbb_str_ci,
                "max_losing_streak_mean": float(np.mean(mbb_str)),
                "max_losing_streak_median": int(round(np.median(mbb_str))),
            },
            "stationary_bootstrap": {
                "max_drawdown_ci_95": sb_dd_ci,
                "max_drawdown_mean": float(np.mean(sb_dd)),
                "max_losing_streak_ci_95": sb_str_ci,
                "max_losing_streak_mean": float(np.mean(sb_str)),
            },
        },
        "limit_probabilities": {
            "p_drawdown_limit_exceeded": p_hit_dd,
            "p_daily_loss_limit_exceeded": p_hit_daily,
            "p_max_ops_limit_exceeded": p_hit_max_ops,
            "max_ops_note": "P(ops en ventana móvil de 21 días > 42). En cuenta FARS max_trades=None.",
        },
    }


def run_full_bootstrap(csv_path: Path, accepted_csv_path: Path | None = None,
                       master_seed: int = MASTER_SEED,
                       B: int = B_REPLICATES) -> dict:
    """Ejecuta bootstrap de camino e incertidumbre para AMBOS caminos: bruto y riesgo-permitido (Fix H8)."""
    dataset_gross = load_trade_csv(
        csv_path,
        outcomes_finalized=True,
        analysis_timezone="America/New_York",
    )
    r_gross = np.asarray([t.r_result for t in dataset_gross.trades], dtype=np.float64)
    dates_gross = np.asarray([str(t.timestamp.date() if t.timestamp else t.date) for t in dataset_gross.trades])
    n_gross = len(r_gross)

    # 1. Pipeline oficial FARS para funcionales de media
    fars_boot_gross = analyze_bootstrap(dataset_gross, master_seed=master_seed, B=B)

    # 2. Selección de bloque óptimo objetivo con arch (Politis & White)
    block_opt_gross = optimal_block_length(r_gross)
    block_circular_est_g = float(block_opt_gross.loc[0, "circular"])
    block_stationary_est_g = float(block_opt_gross.loc[0, "stationary"])
    block_mbb_g = max(1, int(math.ceil(block_circular_est_g)))
    block_sb_g = max(1, int(math.ceil(block_stationary_est_g)))

    # Generar semillas hijas reproducibles desde master_seed
    root = np.random.SeedSequence(master_seed)
    children = root.spawn(4)
    seed_mbb_g = int(children[0].generate_state(1)[0])
    seed_sb_g = int(children[1].generate_state(1)[0])
    seed_mbb_a = int(children[2].generate_state(1)[0])
    seed_sb_a = int(children[3].generate_state(1)[0])

    suite_gross = _evaluate_path_suite(
        r_gross, dates_gross, block_mbb_g, block_sb_g, B, seed_mbb_g, seed_sb_g
    )

    # Fix H8: Camino Riesgo-Permitido
    if accepted_csv_path is None:
        cand_accepted = csv_path.parent / "ledger_orb_fars_accepted.csv"
        if not cand_accepted.exists():
            import separar_senales as ss
            ss.run_separation(csv_path, export_accepted_path=cand_accepted)
        accepted_csv_path = cand_accepted

    dataset_allowed = load_trade_csv(
        accepted_csv_path,
        outcomes_finalized=True,
        analysis_timezone="America/New_York",
    )
    r_allowed = np.asarray([t.r_result for t in dataset_allowed.trades], dtype=np.float64)
    dates_allowed = np.asarray([str(t.timestamp.date() if t.timestamp else t.date) for t in dataset_allowed.trades])
    n_allowed = len(r_allowed)

    fars_boot_allowed = analyze_bootstrap(dataset_allowed, master_seed=master_seed, B=B)

    block_opt_allowed = optimal_block_length(r_allowed)
    block_circular_est_a = float(block_opt_allowed.loc[0, "circular"])
    block_stationary_est_a = float(block_opt_allowed.loc[0, "stationary"])
    block_mbb_a = max(1, int(math.ceil(block_circular_est_a)))
    block_sb_a = max(1, int(math.ceil(block_stationary_est_a)))

    suite_allowed = _evaluate_path_suite(
        r_allowed, dates_allowed, block_mbb_a, block_sb_a, B, seed_mbb_a, seed_sb_a
    )

    obs_metrics_gross = compute_metrics(list(r_gross))
    obs_metrics_allowed = compute_metrics(list(r_allowed))

    # Fix H7: Extraer el método que realmente se usó según clasificación de FARS
    state_g = fars_boot_gross.get("eligibility", {}).get("state", "iid_eligible")
    method_g = "IID" if state_g == "iid_eligible" else f"CBB ({state_g})"

    state_a = fars_boot_allowed.get("eligibility", {}).get("state", "iid_eligible")
    method_a = "IID" if state_a == "iid_eligible" else f"CBB ({state_a})"

    # Estructura de salida con ambos caminos (H8) y compatibilidad directa
    return {
        "n": n_gross,
        "n_allowed": n_allowed,
        "master_seed": master_seed,
        "B": B,
        "accepted_csv_path": str(accepted_csv_path),
        "method_used_gross": method_g,
        "method_used_allowed": method_a,
        "observed": obs_metrics_gross,
        "observed_allowed": obs_metrics_allowed,
        "fars_bootstrap": fars_boot_gross,
        "fars_bootstrap_allowed": fars_boot_allowed,
        "blocks": {
            "mbb_optimal": block_mbb_g,
            "mbb_raw": block_circular_est_g,
            "sb_optimal": block_sb_g,
            "sb_raw": block_stationary_est_g,
            "mbb_optimal_allowed": block_mbb_a,
            "sb_optimal_allowed": block_sb_a,
        },
        "path_functionals": suite_gross["path_functionals"],
        "limit_probabilities": suite_gross["limit_probabilities"],
        "path_functionals_allowed": suite_allowed["path_functionals"],
        "limit_probabilities_allowed": suite_allowed["limit_probabilities"],
        "gross": {
            "n": n_gross,
            "method_used": method_g,
            "observed": obs_metrics_gross,
            "fars_bootstrap": fars_boot_gross,
            "path_functionals": suite_gross["path_functionals"],
            "limit_probabilities": suite_gross["limit_probabilities"],
        },
        "allowed": {
            "n": n_allowed,
            "method_used": method_a,
            "observed": obs_metrics_allowed,
            "fars_bootstrap": fars_boot_allowed,
            "path_functionals": suite_allowed["path_functionals"],
            "limit_probabilities": suite_allowed["limit_probabilities"],
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Bootstrap de camino e incertidumbre FARS")
    ap.add_argument("--csv", type=Path, default=HERE / "ledger_orb_fars_stop.csv")
    ap.add_argument("--accepted-csv", type=Path, default=None,
                    help="Ruta al ledger aceptado (si se omite, se usa/genera ledger_orb_fars_accepted.csv)")
    ap.add_argument("--seed", type=int, default=MASTER_SEED)
    ap.add_argument("--b", type=int, default=B_REPLICATES)
    args = ap.parse_args()

    print(f"Ejecutando bootstrap reproducible (master_seed={args.seed}, B={args.b})...")
    res = run_full_bootstrap(args.csv, accepted_csv_path=args.accepted_csv,
                             master_seed=args.seed, B=args.b)

    obs_g = res["gross"]["observed"]
    obs_a = res["allowed"]["observed"]
    fb_g = res["gross"]["fars_bootstrap"]
    pf_mbb_g = res["gross"]["path_functionals"]["mbb_unwrapped"]
    pf_sb_g = res["gross"]["path_functionals"]["stationary_bootstrap"]
    probs_g = res["gross"]["limit_probabilities"]

    pf_mbb_a = res["allowed"]["path_functionals"]["mbb_unwrapped"]
    pf_sb_a = res["allowed"]["path_functionals"]["stationary_bootstrap"]
    probs_a = res["allowed"]["limit_probabilities"]

    print("\n" + "═" * 74)
    print("1. CONTEXTO DIAGNÓSTICO DE DEPENDENCIA TEMPORAL (FARS Phase 10A)")
    print("═" * 74)
    diag = fb_g.get("diagnostics", {})
    tests = diag.get("tests", [])
    state = fb_g.get("eligibility", {}).get("state", "iid_eligible")
    print(f"  Clasificación de elegibilidad : {state}")
    print(f"  Número de tests en familia    : m={diag.get('m')} (alpha_b={diag.get('alpha_b', 0):.6f})")
    for t in tests:
        print(f"    - {t.get('id'):<30}: stat={t.get('statistic'):>8.4f}, p={t.get('p_value'):.5f}")

    # Fix H7: Reportar el método que realmente se usó según elegibilidad de FARS
    method_title = "BOOTSTRAP IID" if state == "iid_eligible" else f"CIRCULAR BLOCK BOOTSTRAP ({state.upper()})"
    print("\n" + "═" * 74)
    print(f"2. FUNCIONALES DE MEDIA — {method_title} (Intervalos 95% FARS)")
    print("═" * 74)
    print(f"{'Estimando':<18} {'Puntual Obs.':>14} {'Intervalo 95%':>26} {'Método':>14}")
    print("─" * 74)
    for est_name, label in [("expectancy", "E[R]"), ("win_rate", "Win Rate"), ("std", "Std R")]:
        entry = fb_g.get("estimands", {}).get(est_name, {})
        val = entry.get("value", 0.0)
        ints = entry.get("intervals", [])
        if ints:
            i0 = ints[0]
            ci_str = f"[{i0['lower']:.4f}, {i0['upper']:.4f}]"
            m_str = f"{i0.get('method', '')}"
        else:
            ci_str = "N/A"
            m_str = ""
        val_str = f"{val:.4f}" if est_name != "win_rate" else f"{val*100:.2f}%"
        print(f"{label:<18} {val_str:>14} {ci_str:>26} {m_str:>14}")

    # Fix H8: Comparación de funcionales de camino entre Bruto y Riesgo-Permitido
    print("\n" + "═" * 74)
    print("3. FUNCIONALES DE CAMINO — (i) BRUTO vs (ii) RIESGO-PERMITIDO (Fix H8)")
    print("═" * 74)
    print(f"  Bloques óptimos MBB : Bruto={res['blocks']['mbb_optimal']} | Riesgo-Permitido={res['blocks']['mbb_optimal_allowed']}")
    print("─" * 74)
    print(f"{'Métrica de Camino':<24} {'(i) Bruto (Obs)':>15} {'(i) MBB 95%':>16} {'(ii) Permitido':>15}")
    print("─" * 74)
    dd_g_ci = f"[{pf_mbb_g['max_drawdown_ci_95'][0]:.2f}, {pf_mbb_g['max_drawdown_ci_95'][1]:.2f}]"
    dd_a_ci = f"[{pf_mbb_a['max_drawdown_ci_95'][0]:.2f}, {pf_mbb_a['max_drawdown_ci_95'][1]:.2f}]"
    print(f"{'Max Drawdown (R)':<24} {obs_g.max_drawdown_r:>15.2f} {dd_g_ci:>16} {obs_a.max_drawdown_r:>15.2f} (CI: {dd_a_ci})")

    str_g_ci = f"[{pf_mbb_g['max_losing_streak_ci_95'][0]}, {pf_mbb_g['max_losing_streak_ci_95'][1]}]"
    str_a_ci = f"[{pf_mbb_a['max_losing_streak_ci_95'][0]}, {pf_mbb_a['max_losing_streak_ci_95'][1]}]"
    print(f"{'Max Losing Streak':<24} {obs_g.max_losing_streak:>15d} {str_g_ci:>16} {obs_a.max_losing_streak:>15d} (CI: {str_a_ci})")

    # Fix B3: Probabilidades reales calculadas con agrupación por día real
    print("\n" + "═" * 74)
    print("4. PROBABILIDADES DE LÍMITES BAJO MBB (Cuenta $100k, Riesgo 1%) (Fix B3, H8)")
    print("═" * 74)
    print(f"{'Límite de Cuenta':<40} {'(i) Bruto':>14} {'(ii) Riesgo-Permitido':>18}")
    print("─" * 74)
    print(f"  P(Trailing Drawdown >= $8.000 USD)    : {probs_g['p_drawdown_limit_exceeded']*100:>13.2f}% {probs_a['p_drawdown_limit_exceeded']*100:>17.2f}%")
    print(f"  P(Límite Pérdida Diaria >= $2.000 USD): {probs_g['p_daily_loss_limit_exceeded']*100:>13.2f}% {probs_a['p_daily_loss_limit_exceeded']*100:>17.2f}%")
    print(f"  P(Tope Operativo > 42 ops / 21 días)  : {probs_g['p_max_ops_limit_exceeded']*100:>13.2f}% {probs_a['p_max_ops_limit_exceeded']*100:>17.2f}%")
    print("═" * 74)
    print("  Nota: La probabilidad de límite diario agrupa operaciones estrictamente dentro del mismo día real (Fix B3).")

    return 0


if __name__ == "__main__":
    sys.exit(main())

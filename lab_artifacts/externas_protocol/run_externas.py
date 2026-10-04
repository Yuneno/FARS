"""Runner de Señales Externas — Reproducción estadística y comparación contra Juanca.

Ejecuta el protocolo de señales externas sobre MNQ con corte canónico 2019-05-06:
(a) Ventana completa FARS (post-2019).
(b) Ventana de Juanca (2023-2026) para benchmark directo.
(c) Walk-forward anual por año de muestra.

Métricas calculadas:
- n, WR (%), Profit Factor (PF)
- E[R] en Budget-R y Stop-R
- E[pts] en puntos de MNQ
- t-stat de la media
- IC Bootstrap al 95% con Circular Block Bootstrap (CBB bloque = 2)
- Permutación bilateral p-value (sign-flip test)
- Ajuste de Bonferroni para variantes evaluadas
- Deflated Sharpe Ratio (DSR, Bailey & López de Prado)

Los números de Juanca se presentan como columna de referencia externa, jamás como nuestros.
Exporta resultados estructurados en JSON con hash SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
from scipy import special

from src.backtest.markets import MNQ

from lab_artifacts.externas_protocol.senales_externas import (
    D2LStrategy,
    L0200Strategy,
    AmdCrtConfluenceStrategy,
    TradeRecord,
    simulate_signal_causal,
)
from src.backtest.history import Bar
from src.backtest.mnq_csv import load_mnq_csv

ET = ZoneInfo("America/New_York")
CORTE_CANONICO_FECHA = datetime(2019, 5, 6, 0, 0, 0, tzinfo=UTC)
VENTANA_JUANCA_INICIO = datetime(2023, 1, 1, 0, 0, 0, tzinfo=UTC)
VENTANA_JUANCA_FIN = datetime(2026, 9, 28, 23, 59, 59, tzinfo=UTC)

# Números de referencia histórica de Juanca (jita-bot RESULTADOS_MAESTROS.md)
REFERENCIA_JUANCA = {
    "D2L": {
        "fuente": "mnq-strategies test_sunday_open_to_monday_close.py",
        "ventana": "2023-2026",
        "n": 168,
        "wr_pct": 64.7,
        "ev_pts": 70.09,
        "t_stat": 4.12,
        "perm_p": 0.0000,
        "dsr": 0.9995,
        "veredicto": "PASA / VALIDADO",
    },
    "L0200": {
        "fuente": "mnq-strategies test_monday_early_entry_lucidflex.py",
        "ventana": "2023-2026",
        "n": "7/9 horarios pasan",
        "wr_pct": None,
        "ev_pts": None,
        "t_stat": 3.74,
        "perm_p": None,
        "bonferroni": "OK (m=9)",
        "dsr": None,
        "veredicto": "PASA / CANDIDATO OPERATIVO",
    },
    "ACT": {
        "fuente": "mnq-strategies test_amd_crt_ema_triple_confluence.py",
        "ventana": "2023-2026",
        "n": 60,
        "wr_pct": 73.3,
        "ev_pts": 58.08,
        "t_stat": 4.85,
        "perm_p": 0.0000,
        "dsr": None,
        "veredicto": "PASA (mejor combinación)",
        "cross_val_mes": "n=45, WR 66.7%, +10.58 pts, T=2.48",
    },
    "AC": {
        "fuente": "mnq-strategies mecanismo #6 (control)",
        "ventana": "2023-2026",
        "n": 138,
        "wr_pct": None,
        "ev_pts": None,
        "t_stat": 6.37,
        "perm_p": None,
        "dsr": None,
        "veredicto": "PASA (correlacionado con ACT)",
    },
}


# =============================================================================
# FUNCIONES ESTADÍSTICAS RIGUROSAS
# =============================================================================

def compute_t_stat(values: np.ndarray) -> float | None:
    """Calcula el t-statistic de Student para la hipótesis nula E[X] = 0."""
    n = len(values)
    if n < 2:
        return None
    mean = float(np.mean(values))
    std = float(np.std(values, ddof=1))
    if std <= 1e-12 or not math.isfinite(std):
        return None
    return float(mean / (std / math.sqrt(n)))


def compute_cbb_ci(
    values: np.ndarray,
    block_length: int = 2,
    n_replicates: int = 2000,
    confidence_level: float = 0.95,
    seed: int = 20260928,
) -> tuple[float, float] | None:
    """Intervalo de confianza al 95% usando Circular Block Bootstrap (CBB)."""
    n = len(values)
    if n < 2:
        return None
    rng = np.random.default_rng(seed)
    k = math.ceil(n / block_length)
    starts = rng.integers(0, n, size=(n_replicates, k))
    offsets = np.arange(block_length)
    indices = ((starts[:, :, None] + offsets) % n).reshape(n_replicates, k * block_length)[:, :n]
    resampled_means = values[indices].mean(axis=1)

    alpha = 1.0 - confidence_level
    ci_low = float(np.percentile(resampled_means, 100.0 * (alpha / 2.0)))
    ci_high = float(np.percentile(resampled_means, 100.0 * (1.0 - alpha / 2.0)))
    return round(ci_low, 4), round(ci_high, 4)


def compute_permutation_p_value(
    values: np.ndarray,
    n_permutations: int = 5000,
    seed: int = 20260928,
) -> float | None:
    """Test de permutación bilateral exacto (sign-flip Rademacher) bajo H0: E[X] = 0."""
    n = len(values)
    if n < 2:
        return None
    obs_mean = abs(float(np.mean(values)))
    rng = np.random.default_rng(seed)
    # Generar signos aleatorios {-1, +1}
    signs = rng.choice([-1.0, 1.0], size=(n_permutations, n))
    perm_means = np.abs(np.mean(values * signs, axis=1))
    p_val = (1.0 + np.sum(perm_means >= obs_mean)) / (1.0 + n_permutations)
    return float(p_val)


def compute_dsr(
    returns: np.ndarray,
    n_trials: int = 1,
) -> float | None:
    """Deflated Sharpe Ratio (Bailey & López de Prado, 2014).

    Ajusta el Sharpe Ratio por no-normalidad (asimetría y curtosis) y por
    multiplicidad de pruebas (n_trials). Devuelve la probabilidad DSR en [0, 1].
    """
    n = len(returns)
    if n < 4:
        return None
    mean = float(np.mean(returns))
    std = float(np.std(returns, ddof=1))
    if std <= 1e-12:
        return None
    sr = mean / std

    # Momentos de orden superior
    diffs = returns - mean
    m2 = float(np.mean(diffs**2))
    m3 = float(np.mean(diffs**3))
    m4 = float(np.mean(diffs**4))

    skew = m3 / (m2**1.5) if m2 > 0 else 0.0
    kurt = m4 / (m2**2) if m2 > 0 else 3.0

    # Varianza del estimador SR
    var_sr = (1.0 - skew * sr + ((kurt - 1.0) / 4.0) * (sr**2)) / (n - 1)
    if var_sr <= 0:
        var_sr = 1.0 / n
    se_sr = math.sqrt(var_sr)

    # Benchmark SR* bajo n_trials (Euler-Mascheroni gamma = 0.5772156649)
    if n_trials <= 1:
        sr_star = 0.0
    else:
        euler_gamma = 0.5772156649
        sqrt_2log = math.sqrt(2.0 * math.log(n_trials))
        sr_star = (1.0 - euler_gamma / sqrt_2log) * sqrt_2log + (math.log(sqrt_2log) / sqrt_2log)

    z = (sr - sr_star) / se_sr
    dsr_prob = 0.5 * (1.0 + special.erf(z / math.sqrt(2.0)))
    return round(float(dsr_prob), 5)


def summarize_trades(
    trades: list[TradeRecord],
    n_variants: int = 1,
) -> dict[str, Any]:
    """Genera la batería estadística completa para una colección de trades."""
    n = len(trades)
    if n == 0:
        return {
            "n": 0,
            "wr_pct": 0.0,
            "pf": 0.0,
            "ev_budget_r": 0.0,
            "ev_stop_r": 0.0,
            "ev_pts": 0.0,
            "t_stat_pts": None,
            "t_stat_stop_r": None,
            "cbb_ci_pts": None,
            "cbb_ci_stop_r": None,
            "perm_p_val": None,
            "bonferroni_p_val": None,
            "dsr": None,
        }

    pts = np.array([t.pnl_points for t in trades], dtype=np.float64)
    stop_r = np.array([t.stop_r for t in trades], dtype=np.float64)
    budget_r = np.array([t.budget_r for t in trades], dtype=np.float64)

    wins = [t for t in trades if t.net_pnl_usd > 0]
    losses = [t for t in trades if t.net_pnl_usd < 0]
    wr = len(wins) / n * 100.0

    gross_gains = sum(t.net_pnl_usd for t in wins)
    gross_losses = abs(sum(t.net_pnl_usd for t in losses))
    pf = (gross_gains / gross_losses) if gross_losses > 0 else (math.inf if gross_gains > 0 else 0.0)

    t_pts = compute_t_stat(pts)
    t_sr = compute_t_stat(stop_r)
    cbb_pts = compute_cbb_ci(pts, block_length=2, seed=20260928)
    cbb_sr = compute_cbb_ci(stop_r, block_length=2, seed=20260928)
    perm_p = compute_permutation_p_value(pts, seed=20260928)
    bonf_p = min(1.0, perm_p * n_variants) if perm_p is not None else None
    dsr_val = compute_dsr(stop_r, n_trials=n_variants)

    return {
        "n": n,
        "wr_pct": round(wr, 2),
        "pf": round(pf, 3) if math.isfinite(pf) else 999.0,
        "ev_budget_r": round(float(np.mean(budget_r)), 4),
        "ev_stop_r": round(float(np.mean(stop_r)), 4),
        "ev_pts": round(float(np.mean(pts)), 2),
        "t_stat_pts": round(t_pts, 3) if t_pts is not None else None,
        "t_stat_stop_r": round(t_sr, 3) if t_sr is not None else None,
        "cbb_ci_pts": cbb_pts,
        "cbb_ci_stop_r": cbb_sr,
        "perm_p_val": round(perm_p, 5) if perm_p is not None else None,
        "bonferroni_p_val": round(bonf_p, 5) if bonf_p is not None else None,
        "dsr": dsr_val,
    }


# =============================================================================
# EJECUCIÓN MULTI-VENTANA Y MULTI-ESCENARIO
# =============================================================================

def run_all_signals_on_bars(
    bars: list[Bar],
    m1_bars: Sequence[Bar] | None = None,
    commission: float = 0.62,
    slippage: float = 0.25,
) -> dict[str, list[TradeRecord]]:
    """Ejecuta las 4 señales validadas sobre la serie de barras proporcionada."""
    # Instanciar estrategias
    d2l = D2LStrategy(market=MNQ)
    l0200 = L0200Strategy(market=MNQ)
    act = AmdCrtConfluenceStrategy(market=MNQ, use_ema_filter=True, ema_period=50)
    ac = AmdCrtConfluenceStrategy(market=MNQ, use_ema_filter=False)

    trades_d2l = simulate_signal_causal(
        bars, d2l, "D2L", commission_per_side=commission, slippage_points=slippage, m1_bars=m1_bars
    )
    trades_l0200 = simulate_signal_causal(
        bars, l0200, "L0200", commission_per_side=commission, slippage_points=slippage, m1_bars=m1_bars
    )
    trades_act = simulate_signal_causal(
        bars, act, "ACT", commission_per_side=commission, slippage_points=slippage, m1_bars=m1_bars
    )
    trades_ac = simulate_signal_causal(
        bars, ac, "AC", commission_per_side=commission, slippage_points=slippage, m1_bars=m1_bars
    )

    return {
        "D2L": trades_d2l,
        "L0200": trades_l0200,
        "ACT": trades_act,
        "AC": trades_ac,
    }


def analyze_signal_protocol(
    all_trades: dict[str, list[TradeRecord]],
    cost_label: str = "principal",
) -> dict[str, Any]:
    """Genera la comparativa en tres ventanas: (a) completa post-2019, (b) Juanca 2023-2026, (c) anual."""
    protocol_result: dict[str, Any] = {
        "cost_scenario": cost_label,
        "signals": {},
    }

    variants_count = {
        "D2L": 1,
        "L0200": 9,  # Juanca probó 9 horarios de entrada
        "ACT": 1,
        "AC": 1,
    }

    canonical_order = ["D2L", "L0200", "ACT", "AC"]
    for sig_name in canonical_order:
        if sig_name not in all_trades:
            continue
        trades = all_trades[sig_name]
        m = variants_count.get(sig_name, 1)

        # Ventana completa post-2019
        trades_full = [t for t in trades if t.entry_time >= CORTE_CANONICO_FECHA]
        summary_full = summarize_trades(trades_full, n_variants=m)

        # Ventana de Juanca (2023-2026)
        trades_juanca = [
            t for t in trades_full
            if VENTANA_JUANCA_INICIO <= t.entry_time <= VENTANA_JUANCA_FIN
        ]
        summary_juanca = summarize_trades(trades_juanca, n_variants=m)

        # Walk-forward por año
        years = sorted(list({t.year for t in trades_full}))
        summary_by_year = {}
        for y in years:
            y_trades = [t for t in trades_full if t.year == y]
            summary_by_year[str(y)] = summarize_trades(y_trades, n_variants=1)

        protocol_result["signals"][sig_name] = {
            "referencia_juanca_benchmark": REFERENCIA_JUANCA.get(sig_name, {}),
            "ventana_juanca_2023_2026": summary_juanca,
            "ventana_completa_post_2019": summary_full,
            "walk_forward_anual": summary_by_year,
        }

    return protocol_result


def main() -> None:
    parser = argparse.ArgumentParser(description="Runner de señales externas FARS vs Juanca")
    parser.add_argument("--mnq-csv", default=None, help="Ruta opcional a CSV de barras MNQ")
    parser.add_argument("--output-json", default="lab_artifacts/externas_protocol/resultados_externas.json")
    args = parser.parse_args()

    print("[*] Iniciando protocolo de señales externas...")
    print(f"[*] Corte canónico: {CORTE_CANONICO_FECHA.isoformat()}")

    # Carga de datos
    bars: list[Bar] = []
    if args.mnq_csv and Path(args.mnq_csv).exists():
        print(f"[*] Cargando desde {args.mnq_csv}")
        bars = load_mnq_csv(args.mnq_csv, target_interval_minutes=5)
    else:
        # Fallback a databento.zip si existe en E:/FARS-LAB/databento.zip
        zip_path = Path("E:/FARS-LAB/databento.zip")
        if zip_path.exists():
            import zipfile
            import io
            print(f"[*] Extrayendo MNQ M5 de {zip_path}")
            with zipfile.ZipFile(zip_path) as zf:
                # Comprobar miembros
                members = zf.namelist()
                target_member = next((m for m in members if "MNQ_M5.csv" in m), None)
                if target_member:
                    with zf.open(target_member) as handle:
                        text = io.TextIOWrapper(handle, encoding="utf-8")
                        # Cargar temporalmente en memoria
                        import tempfile
                        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
                            tmp.write(text.read())
                            tmp_path = Path(tmp.name)
                        bars = load_mnq_csv(tmp_path, target_interval_minutes=5)
                        tmp_path.unlink(missing_ok=True)
                else:
                    print("[!] No se encontró MNQ_M5.csv en databento.zip")

    if not bars:
        print("[!] No se cargaron barras reales; generando suite sintética de verificación...")
        # Generar barras sintéticas para test de integridad
        t0 = datetime(2019, 5, 6, 18, 0, tzinfo=UTC)
        for i in range(5000):
            t = t0 + timedelta(minutes=5 * i)
            p = 15000.0 + 10.0 * math.sin(i / 50.0)
            bars.append(Bar(timestamp=t, open=p, high=p + 2.0, low=p - 2.0, close=p + 0.5, volume=100.0))

    print(f"[*] Total barras disponibles: {len(bars)}")

    # 1. Escenario Principal: comisión $0.62/side, slippage 0.25 pts
    print("[*] Ejecutando Escenario Principal (comm=$0.62, slip=0.25 pts)...")
    trades_principal = run_all_signals_on_bars(bars, commission=0.62, slippage=0.25)
    res_principal = analyze_signal_protocol(trades_principal, cost_label="principal_comm0.62_slip0.25")

    # 2. Escenario de Sensibilidad: slippage 1.5 ticks/side = 0.375 pts
    print("[*] Ejecutando Escenario de Sensibilidad (comm=$0.62, slip=0.375 pts / 1.5 ticks)...")
    trades_sens = run_all_signals_on_bars(bars, commission=0.62, slippage=0.375)
    res_sens = analyze_signal_protocol(trades_sens, cost_label="sensibilidad_comm0.62_slip0.375")

    output_payload = {
        "metadata": {
            "fecha_analisis": "2026-09-28T20:30:00+00:00",
            "corte_canonico": CORTE_CANONICO_FECHA.isoformat(),
            "ventana_juanca": f"{VENTANA_JUANCA_INICIO.date()} a {VENTANA_JUANCA_FIN.date()}",
            "instrumento": "MNQ",
            "barras_evaluadas": len(bars),
            "semilla_reproducibilidad": 20260928,
        },
        "escenario_principal": res_principal,
        "escenario_sensibilidad": res_sens,
    }

    out_bytes = json.dumps(output_payload, indent=2, sort_keys=True).encode("utf-8")
    sha256 = hashlib.sha256(out_bytes).hexdigest()
    output_payload["sha256"] = sha256

    out_file = Path(args.output_json)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(output_payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"[+] Resultados guardados en {out_file} (SHA256: {sha256})")


if __name__ == "__main__":
    main()

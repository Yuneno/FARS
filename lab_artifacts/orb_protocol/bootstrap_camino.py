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
from datetime import date, datetime, timedelta
import math
import sys
from pathlib import Path

import numpy as np
from arch.bootstrap import optimal_block_length
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, r"E:\FARS-LAB\FARS")
sys.path.insert(0, str(HERE))

import paper_bot as pb  # noqa: E402
from src.bootstrap import analyze_bootstrap  # noqa: E402
from src.ingestion import load_trade_csv  # noqa: E402
from src.metrics import compute_metrics, max_drawdown_r, max_losing_streak  # noqa: E402
from src.realtime.events import AccountSnapshot, Signal  # noqa: E402
from src.realtime.risk import AccountAwareRiskEngine  # noqa: E402
from src.types import FundedAccountRules  # noqa: E402

CT = ZoneInfo("America/Chicago")

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


def _prepare_interarrivals(dates: np.ndarray | None, n: int) -> tuple[datetime, np.ndarray, list[datetime]]:
    """Construye fecha base e interarribos empíricos (en días) a partir de fechas observadas.
    Garantiza que la simulación de camino avance de forma estrictamente monótona (Fix CRITICAL 2)."""
    if dates is None:
        base_dt = datetime(2026, 1, 1, 10, 0, tzinfo=CT)
        parsed = [base_dt + timedelta(days=int(i)) for i in range(n)]
        interarrivals = np.array([1], dtype=np.int64)
        return base_dt, interarrivals, parsed

    parsed: list[datetime] = []
    for d in dates:
        if isinstance(d, datetime):
            parsed.append(d if d.tzinfo is not None else d.replace(tzinfo=CT))
        elif isinstance(d, date):
            parsed.append(datetime(d.year, d.month, d.day, 10, 0, tzinfo=CT))
        elif isinstance(d, (int, np.integer)):
            parsed.append(datetime(2026, 1, 1, 10, 0, tzinfo=CT) + timedelta(days=int(d)))
        else:
            s = str(d)[:10]
            try:
                parsed.append(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=CT))
            except Exception:
                parsed.append(datetime(2026, 1, 1, 10, 0, tzinfo=CT))

    interarrivals_list: list[int] = []
    for i in range(len(parsed) - 1):
        diff = (parsed[i + 1].date() - parsed[i].date()).days
        interarrivals_list.append(max(0, diff))

    if not interarrivals_list:
        interarrivals_list = [1]

    interarrivals = np.asarray(interarrivals_list, dtype=np.int64)
    base_dt = parsed[0] if parsed else datetime(2026, 1, 1, 10, 0, tzinfo=CT)
    return base_dt, interarrivals, parsed


def build_replicate_timeline(
    idx: np.ndarray,
    parsed_dates: list[datetime],
    interarrivals: np.ndarray,
    rng: np.random.Generator,
) -> list[datetime]:
    """Construye un cronograma sintético estrictamente monótono hacia adelante para una réplica.
    - Dentro de cada bloque contiguo, preserva el espaciamiento temporal observado.
    - En las costuras entre bloques, avanza el tiempo según interarribos empíricos observados.
    - Nunca retrocede en el tiempo; ningún mes calendario pasado se vuelve a visitar (Fix CRITICAL 2)."""
    n = len(idx)
    if n == 0:
        return []
    curr_t = parsed_dates[idx[0]] if idx[0] < len(parsed_dates) else datetime(2026, 1, 1, 10, 0, tzinfo=CT)
    timeline = [curr_t]
    for j in range(1, n):
        prev_idx = idx[j - 1]
        curr_idx = idx[j]
        if curr_idx == prev_idx + 1 and curr_idx < len(parsed_dates) and prev_idx < len(parsed_dates):
            # Contiguo dentro del mismo bloque histórico
            dt_days = max(0, (parsed_dates[curr_idx].date() - parsed_dates[prev_idx].date()).days)
        else:
            # Costura entre bloques no contiguos: muestrear un interarribo empírico observado
            dt_days = int(rng.choice(interarrivals))
        curr_t = curr_t + timedelta(days=dt_days)
        timeline.append(curr_t)
    return timeline


def simulate_funded_trajectory(
    r_series: np.ndarray,
    timeline: list[datetime],
    initial_balance: float = 100_000.0,
    risk_pct: float = 0.01,
    max_risk_dollars_order: float = 1_000.0,
    stop_pts: float = 100.0,
    point_value: float = 2.0,
    daily_loss_limit_usd: float = 2_000.0,
    max_dd_limit_usd: float = 8_000.0,
    monthly_trade_cap: int = 42,
    profit_target_usd: float = 6_000.0,
    stop_on_daily_loss: bool = True,
    use_risk_engine: bool = False,
    rules: FundedAccountRules | None = None,
    dd_floor_ceiling: float | None = None,
) -> dict:
    """Simula la trayectoria monetaria y límites de cuenta dentro de una réplica (Fix CRITICAL 1 y 2).

    dd_floor_ceiling: si se fija (p.ej. Apex: starting_balance + $100), el piso del
    trailing drawdown = min(peak - max_dd_limit_usd, dd_floor_ceiling) y se congela
    al alcanzarlo (regla real de Apex 2026-07-29: "floor freezes when peak reaches
    DD + $100"). None = trailing clásico sin techo (comportamiento previo intacto).
    Tocar el piso exactamente = breach (boundary '<='), como en la spec de Apex.

    Reglas aplicadas dentro de la réplica:
    1. Sizing entero: min(equity * 1%, $1000) / ($200 por contrato).
    2. Cupo mensual de estrategia: máximo 42 operaciones por mes calendario sintético.
       Las operaciones posteriores en el mismo mes se descartan / no se toman (hit_max_ops=True).
    3. Si use_risk_engine=True: cada oportunidad pasa primero por AccountAwareRiskEngine de FARS.
       Un veto de riesgo DESCARTA esa operación (la réplica continúa sin cambio en equity).
    4. Semántica terminal mutuamente excluyente (Apex-like):
       Cada réplica termina en EXACTAMENTE UNO de estos 4 estados disjuntos:
       - target: equity >= initial_balance + profit_target_usd ($106.000). Parada terminal exitosa.
       - breach_trailing: trailing drawdown >= max_dd_limit_usd ($8.000 desde el pico). Parada terminal.
       - breach_daily: pérdida acumulada del día <= -daily_loss_limit_usd (-$2.000). Parada terminal.
       - horizon_exhausted: horizonte de oportunidades agotado sin alcanzar target ni breach.
       P(target) = P(target antes del primer breach).
       P(breach total) = P(breach_trailing) + P(breach_daily) (estrictamente disjuntos).
    """
    equity = initial_balance
    peak_equity = initial_balance
    hit_dd = False
    hit_daily = False
    hit_max_ops = False
    passed = False
    terminal_condition = "horizon_exhausted"

    if use_risk_engine:
        if rules is None:
            rules = FundedAccountRules(
                initial_balance=initial_balance,
                profit_target_pct=profit_target_usd / initial_balance,
                max_drawdown_pct=max_dd_limit_usd / initial_balance,
                daily_loss_limit_pct=daily_loss_limit_usd / initial_balance,
                risk_per_trade=risk_pct,
                daily_loss_base="initial",
                drawdown_mode="trailing",
                max_trades=None,
                daily_loss_limit_usd=daily_loss_limit_usd,
                max_drawdown_usd=max_dd_limit_usd,
                max_risk_dollars_per_order=max_risk_dollars_order,
            )
        first_dt = timeline[0] if timeline else datetime(2026, 1, 1, 10, 0, tzinfo=CT)
        if first_dt.tzinfo is None:
            first_dt = first_dt.replace(tzinfo=CT)
        clock = pb.StepClock(first_dt)
        engine = AccountAwareRiskEngine(rules, clock, source="fars-mc-risk")
        trades_applied = 0
        realized = 0.0
        seq = 0

    current_day: date | None = None
    trades_today = 0
    day_pnl = 0.0

    current_month: tuple[int, int] | None = None
    trades_this_month = 0

    per_contract_risk = stop_pts * point_value  # $200.0 por contrato
    sizes: list[int] = []
    trades_executed: int = 0
    trades_vetoed: int = 0
    consumed_opportunities: int = 0

    target_equity = initial_balance + profit_target_usd

    for r_val, dt in zip(r_series, timeline):
        consumed_opportunities += 1  # oportunidad EXAMINADA (aunque se descarte)
        t_date = dt.date() if isinstance(dt, datetime) else dt
        if t_date != current_day:
            current_day = t_date
            trades_today = 0
            day_pnl = 0.0
        else:
            trades_today += 1

        t_month = (t_date.year, t_date.month)
        if t_month != current_month:
            current_month = t_month
            trades_this_month = 0

        # Cupo mensual de estrategia
        trades_this_month += 1
        if trades_this_month > monthly_trade_cap:
            hit_max_ops = True
            continue

        trade_ts = datetime(t_date.year, t_date.month, t_date.day, 10, 0, tzinfo=CT) + timedelta(minutes=15 * trades_today)

        # Capa de riesgo FARS
        # Nota estructural (Codex R10): con la parada terminal al primer breach,
        # el engine solo puede vetar señales ANTERIORES a ese breach — cero vetos
        # es estructural a cualquier horizonte, y las columnas (i)/(ii) coinciden
        # con estos límites por construcción (comparación redundante).
        if use_risk_engine:
            clock.set(trade_ts)
            seq += 1
            engine.observe(AccountSnapshot(
                event_id=f"snap-{seq}",
                source="fars-mc-risk",
                timestamp=trade_ts,
                sequence=seq,
                origin="replay",
                balance=equity,
                equity=equity,
                peak_equity=peak_equity,
                realized_pnl=realized,
                trades_applied=trades_applied,
            ))
            sig = Signal(
                event_id=f"sig-{seq}",
                source="fars-mc-risk",
                timestamp=trade_ts,
                sequence=seq,
                symbol="MNQ",
                action="LONG",
                origin="replay",
            )
            decision = engine.evaluate(sig)
            if not decision.approved:
                # Veto de riesgo: descarta la operación, la réplica continúa
                trades_vetoed += 1
                continue

        # Sizing entero
        risk_usd = min(equity * risk_pct, max_risk_dollars_order) if equity > 0 else 0.0
        size = max(0, int(risk_usd / per_contract_risk))
        sizes.append(size)
        if size <= 0:
            continue

        trade_pnl = float(r_val) * per_contract_risk * size
        trades_executed += 1
        if use_risk_engine:
            trades_applied += 1
            realized += trade_pnl
        equity += trade_pnl
        peak_equity = max(peak_equity, equity)
        day_pnl += trade_pnl

        if use_risk_engine:
            # Snapshot POSTERIOR al cierre (Fix R9): sin él, al cambiar de día el
            # engine toma como start_of_day la equity PRE-cierre del último
            # snapshot del día previo y veta en falso por DAILY_LOSS_LIMIT.
            seq += 1
            engine.observe(AccountSnapshot(
                event_id=f"exit-snap-{seq}",
                source="fars-mc-risk",
                timestamp=trade_ts + timedelta(minutes=1),
                sequence=seq,
                origin="replay",
                balance=equity,
                equity=equity,
                peak_equity=peak_equity,
                realized_pnl=realized,
                trades_applied=trades_applied,
            ))

        # Stopping conditions terminales mutuamente excluyentes (Apex-like)
        # Al terminar la réplica aquí, ninguna señal posterior llega al engine:
        # por eso los vetos de la capa (ii) son estructuralmente 0 con estos límites.
        if day_pnl <= -daily_loss_limit_usd:
            hit_daily = True
            if stop_on_daily_loss:
                terminal_condition = "breach_daily"
                break

        if dd_floor_ceiling is None:
            # Rama default: predicado ORIGINAL bit a bit (peak-equity >= max_dd).
            # NO reemplazar por `equity <= peak-max_dd`: bajo IEEE-754 divergen en
            # bordes exactos (contraejemplo Codex R13: peak=30179.25,
            # max_dd=1763.85, equity=28415.40 → viejo False, nuevo True).
            trailing_dd_usd = peak_equity - equity
            if trailing_dd_usd >= max_dd_limit_usd:
                hit_dd = True
                terminal_condition = "breach_trailing"
                break
        else:
            floor = min(peak_equity - max_dd_limit_usd, dd_floor_ceiling)
            if equity <= floor:
                hit_dd = True
                terminal_condition = "breach_trailing"
                break

        if equity >= target_equity:
            passed = True
            terminal_condition = "target"
            break

    return {
        "hit_dd": hit_dd,
        "hit_daily": hit_daily,
        "hit_max_ops": hit_max_ops,
        "passed": passed,
        "terminal_condition": terminal_condition,
        "final_equity": equity,
        "peak_equity": peak_equity,
        "trades_executed": trades_executed,
        "trades_vetoed": trades_vetoed,
        "consumed_opportunities": consumed_opportunities,
        "sizes": sizes,
    }


def run_unwrapped_mbb(r: np.ndarray, block_size: int, B: int,
                      seed: int, dates: np.ndarray | None = None,
                      profit_target_usd: float = 6_000.0,
                      stop_on_daily_loss: bool = True,
                      use_risk_engine: bool = False,
                      rules: FundedAccountRules | None = None) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """Moving Block Bootstrap SIN envoltura circular para funcionales de camino y simulación de fondeo."""
    n = len(r)
    max_start = n - block_size
    if max_start < 0:
        raise ValueError(f"block_size ({block_size}) mayor que n ({n})")

    drawdowns = np.empty(B, dtype=np.float64)
    streaks = np.empty(B, dtype=np.int64)
    limit_activations: list[dict] = []

    base_dt, interarrivals, parsed_dates = _prepare_interarrivals(dates, n)
    idx_matrix = generate_mbb_unwrapped_indices(n, block_size, B, seed)
    rng_replicate = np.random.Generator(np.random.PCG64(seed + 1000))

    for b_idx in range(B):
        idx = idx_matrix[b_idx]
        resample = r[idx]

        # 1. Funcionales de camino estadísticos puros (en unidades R)
        dd_r = max_drawdown_r(list(resample))
        streak = max_losing_streak(list(resample))
        drawdowns[b_idx] = dd_r
        streaks[b_idx] = streak

        # 2. Construir cronograma sintético monótono (Fix CRITICAL 2)
        timeline = build_replicate_timeline(idx, parsed_dates, interarrivals, rng_replicate)

        # 3. Simulación monetaria real con política de riesgo y stopping conditions (Fix CRITICAL 1, 2)
        traj_res = simulate_funded_trajectory(
            resample,
            timeline,
            profit_target_usd=profit_target_usd,
            stop_on_daily_loss=stop_on_daily_loss,
            use_risk_engine=use_risk_engine,
            rules=rules,
        )
        limit_activations.append(traj_res)

    return drawdowns, streaks, limit_activations


def run_unwrapped_mbb_paired(
    r: np.ndarray,
    block_size: int,
    B: int,
    seed: int,
    dates: np.ndarray | None = None,
    profit_target_usd: float = 6_000.0,
    rules: FundedAccountRules | None = None,
) -> tuple[np.ndarray, np.ndarray, list[dict], list[dict]]:
    """Moving Block Bootstrap pareado: ejecuta (i) sin capa de riesgo y (ii) con capa de riesgo FARS
    sobre el EXACTO MISMO remuestreo por réplica (Fix CRITICAL 1)."""
    n = len(r)
    max_start = n - block_size
    if max_start < 0:
        raise ValueError(f"block_size ({block_size}) mayor que n ({n})")

    drawdowns = np.empty(B, dtype=np.float64)
    streaks = np.empty(B, dtype=np.int64)
    limit_acts_raw: list[dict] = []
    limit_acts_risk: list[dict] = []

    base_dt, interarrivals, parsed_dates = _prepare_interarrivals(dates, n)
    idx_matrix = generate_mbb_unwrapped_indices(n, block_size, B, seed)
    rng_replicate = np.random.Generator(np.random.PCG64(seed + 1000))

    for b_idx in range(B):
        idx = idx_matrix[b_idx]
        resample = r[idx]

        # 1. Funcionales de camino estadísticos puros (en unidades R)
        dd_r = max_drawdown_r(list(resample))
        streak = max_losing_streak(list(resample))
        drawdowns[b_idx] = dd_r
        streaks[b_idx] = streak

        # 2. Cronograma sintético monótono
        timeline = build_replicate_timeline(idx, parsed_dates, interarrivals, rng_replicate)

        # 3. (i) Sin capa de riesgo
        traj_raw = simulate_funded_trajectory(
            resample,
            timeline,
            profit_target_usd=profit_target_usd,
            use_risk_engine=False,
            rules=rules,
        )
        limit_acts_raw.append(traj_raw)

        # 4. (ii) Con capa de riesgo FARS (mismo remuestreo y timeline)
        traj_risk = simulate_funded_trajectory(
            resample,
            timeline,
            profit_target_usd=profit_target_usd,
            use_risk_engine=True,
            rules=rules,
        )
        limit_acts_risk.append(traj_risk)

    return drawdowns, streaks, limit_acts_raw, limit_acts_risk


# Alias para conveniencia de tests y análisis
mbb_unwrapped_path_simulation = run_unwrapped_mbb


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


def summarize_limit_probabilities(limit_acts: list[dict], B: int) -> dict:
    """Calcula las probabilidades y estadísticas de fondeo con exclusión mutua estricta (Fix CRITICAL 1, 2)."""
    lo_q, hi_q = 0.025, 0.975
    p_hit_dd = sum(1 for x in limit_acts if x["hit_dd"]) / B
    p_hit_daily = sum(1 for x in limit_acts if x["hit_daily"]) / B
    p_hit_max_ops = sum(1 for x in limit_acts if x["hit_max_ops"]) / B
    p_pass = sum(1 for x in limit_acts if x.get("passed", False)) / B
    p_breach_total = p_hit_dd + p_hit_daily

    final_equities = [x.get("final_equity", 100_000.0) for x in limit_acts]
    eq_mean = float(np.mean(final_equities))
    eq_med = float(np.median(final_equities))
    eq_ci = (float(np.quantile(final_equities, lo_q)), float(np.quantile(final_equities, hi_q)))

    term_counts: dict[str, int] = {
        "target": 0,
        "breach_trailing": 0,
        "breach_daily": 0,
        "horizon_exhausted": 0,
    }
    for x in limit_acts:
        tc = x.get("terminal_condition", "horizon_exhausted")
        term_counts[tc] = term_counts.get(tc, 0) + 1
    term_rates = {k: v / B for k, v in term_counts.items()}

    trades_exec_mean = float(np.mean([x.get("trades_executed", 0) for x in limit_acts]))
    trades_vetoed_mean = float(np.mean([x.get("trades_vetoed", 0) for x in limit_acts]))

    return {
        "p_drawdown_limit_exceeded": p_hit_dd,
        "p_daily_loss_limit_exceeded": p_hit_daily,
        "p_max_ops_limit_exceeded": p_hit_max_ops,
        "max_ops_note": "P(ops en mes calendario sintético > 42). En cuenta FARS max_trades=None.",
        "p_pass_target": p_pass,
        "p_breach_trailing_dd": p_hit_dd,
        "p_breach_daily_loss": p_hit_daily,
        "p_breach_total": p_breach_total,
        "p_target": term_rates["target"],
        "p_breach_trailing": term_rates["breach_trailing"],
        "p_breach_daily": term_rates["breach_daily"],
        "p_horizon_exhausted": term_rates["horizon_exhausted"],
        "final_equity_mean": eq_mean,
        "final_equity_median": eq_med,
        "final_equity_ci_95": eq_ci,
        "trades_executed_mean": trades_exec_mean,
        "trades_vetoed_mean": trades_vetoed_mean,
        "terminal_condition_rates": term_rates,
        "terminal_condition_counts": term_counts,
    }


def _evaluate_path_suite(r: np.ndarray, dates: np.ndarray, mbb_block: int,
                         sb_block: int, B: int, mbb_seed: int, sb_seed: int,
                         profit_target_usd: float = 6_000.0,
                         use_risk_engine: bool = False,
                         rules: FundedAccountRules | None = None) -> dict:
    """Evalúa funcionales de camino y límites para una subsecuencia dada."""
    mbb_dd, mbb_str, limit_acts = run_unwrapped_mbb(
        r, mbb_block, B, mbb_seed, dates=dates, profit_target_usd=profit_target_usd,
        use_risk_engine=use_risk_engine, rules=rules,
    )
    sb_dd, sb_str = run_stationary_bootstrap(r, sb_block, B, sb_seed)

    lo_q, hi_q = 0.025, 0.975
    mbb_dd_ci = (float(np.quantile(mbb_dd, lo_q)), float(np.quantile(mbb_dd, hi_q)))
    mbb_str_ci = (int(round(np.quantile(mbb_str, lo_q))), int(round(np.quantile(mbb_str, hi_q))))
    sb_dd_ci = (float(np.quantile(sb_dd, lo_q)), float(np.quantile(sb_dd, hi_q)))
    sb_str_ci = (int(round(np.quantile(sb_str, lo_q))), int(round(np.quantile(sb_str, hi_q))))

    limits_summary = summarize_limit_probabilities(limit_acts, B)

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
        "limit_probabilities": limits_summary,
    }


def classify_bootstrap_method(state: str) -> str:
    """Clasifica el método de bootstrap según el estado de FARS (Fix WARNING 1)."""
    if state == "iid_eligible":
        return "IID"
    elif state == "dependent_resampling_candidate":
        return f"CBB ({state})"
    elif state == "unsupported_or_inconclusive":
        return f"unsupported ({state})"
    return f"unknown ({state})"


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

    # 3. MBB pareado sobre camino bruto (Fix CRITICAL 1):
    # Genera simultáneamente (i) sin capa de riesgo y (ii) con capa de riesgo FARS
    # sobre el EXACTO MISMO remuestreo por réplica
    mbb_dd_g, mbb_str_g, limit_acts_raw, limit_acts_risk = run_unwrapped_mbb_paired(
        r_gross, block_mbb_g, B, seed_mbb_g, dates=dates_gross, profit_target_usd=6_000.0
    )
    sb_dd_g, sb_str_g = run_stationary_bootstrap(r_gross, block_sb_g, B, seed_sb_g)

    lo_q, hi_q = 0.025, 0.975
    suite_gross_paths = {
        "mbb_unwrapped": {
            "max_drawdown_ci_95": (float(np.quantile(mbb_dd_g, lo_q)), float(np.quantile(mbb_dd_g, hi_q))),
            "max_drawdown_mean": float(np.mean(mbb_dd_g)),
            "max_drawdown_median": float(np.median(mbb_dd_g)),
            "max_losing_streak_ci_95": (int(round(np.quantile(mbb_str_g, lo_q))), int(round(np.quantile(mbb_str_g, hi_q)))),
            "max_losing_streak_mean": float(np.mean(mbb_str_g)),
            "max_losing_streak_median": int(round(np.median(mbb_str_g))),
        },
        "stationary_bootstrap": {
            "max_drawdown_ci_95": (float(np.quantile(sb_dd_g, lo_q)), float(np.quantile(sb_dd_g, hi_q))),
            "max_drawdown_mean": float(np.mean(sb_dd_g)),
            "max_losing_streak_ci_95": (int(round(np.quantile(sb_str_g, lo_q))), int(round(np.quantile(sb_str_g, hi_q)))),
            "max_losing_streak_mean": float(np.mean(sb_str_g)),
        },
    }
    suite_gross_limits = summarize_limit_probabilities(limit_acts_raw, B)
    suite_allowed_limits = summarize_limit_probabilities(limit_acts_risk, B)

    # 4. Funcionales de camino estadísticos sobre trades aceptados observados (Fix H8)
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

    mbb_dd_a, mbb_str_a, _ = run_unwrapped_mbb(r_allowed, block_mbb_a, B, seed_mbb_a, dates=dates_allowed)
    sb_dd_a, sb_str_a = run_stationary_bootstrap(r_allowed, block_sb_a, B, seed_sb_a)

    suite_allowed_paths = {
        "mbb_unwrapped": {
            "max_drawdown_ci_95": (float(np.quantile(mbb_dd_a, lo_q)), float(np.quantile(mbb_dd_a, hi_q))),
            "max_drawdown_mean": float(np.mean(mbb_dd_a)),
            "max_drawdown_median": float(np.median(mbb_dd_a)),
            "max_losing_streak_ci_95": (int(round(np.quantile(mbb_str_a, lo_q))), int(round(np.quantile(mbb_str_a, hi_q)))),
            "max_losing_streak_mean": float(np.mean(mbb_str_a)),
            "max_losing_streak_median": int(round(np.median(mbb_str_a))),
        },
        "stationary_bootstrap": {
            "max_drawdown_ci_95": (float(np.quantile(sb_dd_a, lo_q)), float(np.quantile(sb_dd_a, hi_q))),
            "max_drawdown_mean": float(np.mean(sb_dd_a)),
            "max_losing_streak_ci_95": (int(round(np.quantile(sb_str_a, lo_q))), int(round(np.quantile(sb_str_a, hi_q)))),
            "max_losing_streak_mean": float(np.mean(sb_str_a)),
        },
    }

    obs_metrics_gross = compute_metrics(list(r_gross))
    obs_metrics_allowed = compute_metrics(list(r_allowed))

    # Fix H7 & WARNING 1: Extraer el método que realmente se usó según clasificación de FARS
    state_g = fars_boot_gross.get("eligibility", {}).get("state", "iid_eligible")
    method_g = classify_bootstrap_method(state_g)

    state_a = fars_boot_allowed.get("eligibility", {}).get("state", "iid_eligible")
    method_a = classify_bootstrap_method(state_a)

    # Estructura de salida con ambos caminos pareados (Fix CRITICAL 1) y compatibilidad directa
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
        "path_functionals": suite_gross_paths,
        "limit_probabilities": suite_gross_limits,
        "path_functionals_allowed": suite_allowed_paths,
        "limit_probabilities_allowed": suite_allowed_limits,
        "gross": {
            "n": n_gross,
            "method_used": method_g,
            "observed": obs_metrics_gross,
            "fars_bootstrap": fars_boot_gross,
            "path_functionals": suite_gross_paths,
            "limit_probabilities": suite_gross_limits,
        },
        "allowed": {
            "n": n_allowed,
            "method_used": method_a,
            "observed": obs_metrics_allowed,
            "fars_bootstrap": fars_boot_allowed,
            "path_functionals": suite_allowed_paths,
            "limit_probabilities": suite_allowed_limits,
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

    print("\n" + "═" * 76)
    print("1. CONTEXTO DIAGNÓSTICO DE DEPENDENCIA TEMPORAL (FARS Phase 10A)")
    print("═" * 76)
    diag = fb_g.get("diagnostics", {})
    tests = diag.get("tests", [])
    state = fb_g.get("eligibility", {}).get("state", "iid_eligible")
    print(f"  Clasificación de elegibilidad : {state}")
    print(f"  Número de tests en familia    : m={diag.get('m')} (alpha_b={diag.get('alpha_b', 0):.6f})")
    for t in tests:
        print(f"    - {t.get('id'):<30}: stat={t.get('statistic'):>8.4f}, p={t.get('p_value'):.5f}")

    # Fix WARNING 1: Reportar el método clasificado exactamente según elegibilidad de FARS
    classified = classify_bootstrap_method(state)
    method_title = f"BOOTSTRAP {classified.upper()}"
    print("\n" + "═" * 76)
    print(f"2. FUNCIONALES DE MEDIA — {method_title} (Intervalos 95% FARS)")
    print("═" * 76)
    print(f"{'Estimando':<18} {'Puntual Obs.':>14} {'Intervalo 95%':>26} {'Método':>14}")
    print("─" * 76)
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
    print("\n" + "═" * 76)
    print("3. FUNCIONALES DE CAMINO — (i) BRUTO vs (ii) RIESGO-PERMITIDO (Fix H8)")
    print("═" * 76)
    print(f"  Bloques óptimos MBB : Bruto={res['blocks']['mbb_optimal']} | Riesgo-Permitido={res['blocks']['mbb_optimal_allowed']}")
    print("─" * 76)
    print(f"{'Métrica de Camino':<24} {'(i) Bruto (Obs)':>15} {'(i) MBB 95%':>16} {'(ii) Permitido':>15}")
    print("─" * 76)
    dd_g_ci = f"[{pf_mbb_g['max_drawdown_ci_95'][0]:.2f}, {pf_mbb_g['max_drawdown_ci_95'][1]:.2f}]"
    dd_a_ci = f"[{pf_mbb_a['max_drawdown_ci_95'][0]:.2f}, {pf_mbb_a['max_drawdown_ci_95'][1]:.2f}]"
    print(f"{'Max Drawdown (R)':<24} {obs_g.max_drawdown_r:>15.2f} {dd_g_ci:>16} {obs_a.max_drawdown_r:>15.2f} (CI: {dd_a_ci})")

    str_g_ci = f"[{pf_mbb_g['max_losing_streak_ci_95'][0]}, {pf_mbb_g['max_losing_streak_ci_95'][1]}]"
    str_a_ci = f"[{pf_mbb_a['max_losing_streak_ci_95'][0]}, {pf_mbb_a['max_losing_streak_ci_95'][1]}]"
    print(f"{'Max Losing Streak':<24} {obs_g.max_losing_streak:>15d} {str_g_ci:>16} {obs_a.max_losing_streak:>15d} (CI: {str_a_ci})")

    # Fix B3, CRITICAL 1 & 2: Monte Carlo de Fondeo pareado con semántica terminal mutuamente excluyente
    print("\n" + "═" * 76)
    print("4. MONTE CARLO DE FONDEO (Cuenta $100k, Riesgo 1%, Apex-like) (Fix CRITICAL 1, 2)")
    print("═" * 76)
    print("  Supuestos declarados:")
    print("  - Capital inicial: $100.000 USD | Riesgo por trade: 1% (escalonado, máx $1.000/orden)")
    print("  - Stop MNQ: 100 pts ($200/contrato) | Trailing DD: $8.000 USD | Pérdida diaria: $2.000 USD")
    print(f"  - Horizonte: {res['n']} oportunidades | Réplicas: B={args.b} | Semilla: {args.seed}")
    print("  - Cupo mensual: 42 ops/mes calendario sintético (proceso interarribos monótono)")
    print("  - Profit Target: +$6.000 USD ($106.000 USD) | Parada terminal al primer breach o target")
    print("  - Aproximación por cierres: trailing sobre equity a cierre de operación;")
    print("    excursiones intratrade no realizadas no elevan el piso")
    print("─" * 76)
    print(f"{'Estado Terminal (Excluyentes)':<38} {'(i) Sin Capa Riesgo':>16} {'(ii) Con Riesgo FARS':>20}")
    print("─" * 76)
    print(f"  P(target - Profit Target +$6k USD)    : {probs_g['p_target']*100:>15.2f}% {probs_a['p_target']*100:>19.2f}%")
    print(f"  P(breach_trailing - Trailing DD >= $8k): {probs_g['p_breach_trailing']*100:>15.2f}% {probs_a['p_breach_trailing']*100:>19.2f}%")
    print(f"  P(breach_daily - Pérdida Diaria >= $2k): {probs_g['p_breach_daily']*100:>15.2f}% {probs_a['p_breach_daily']*100:>19.2f}%")
    print(f"  P(horizon_exhausted - Sin Tgt ni Brch): {probs_g['p_horizon_exhausted']*100:>15.2f}% {probs_a['p_horizon_exhausted']*100:>19.2f}%")
    print("─" * 76)
    print(f"  P(Breach Total = trailing + diaria)   : {probs_g['p_breach_total']*100:>15.2f}% {probs_a['p_breach_total']*100:>19.2f}%")
    p_ops_str_g = f"0/{args.b} (<3/{args.b})" if probs_g['p_max_ops_limit_exceeded'] == 0 else f"{probs_g['p_max_ops_limit_exceeded']*100:.2f}%"
    p_ops_str_a = f"0/{args.b} (<3/{args.b})" if probs_a['p_max_ops_limit_exceeded'] == 0 else f"{probs_a['p_max_ops_limit_exceeded']*100:.2f}%"
    print(f"  P(Tope Operativo > 42 ops / mes cal.) : {p_ops_str_g:>16} {p_ops_str_a:>20}")
    eq_g_ci = f"[{probs_g['final_equity_ci_95'][0]:.0f}, {probs_g['final_equity_ci_95'][1]:.0f}]"
    eq_a_ci = f"[{probs_a['final_equity_ci_95'][0]:.0f}, {probs_a['final_equity_ci_95'][1]:.0f}]"
    print(f"  Equity Final Medio                    : ${probs_g['final_equity_mean']:>14.2f} ${probs_a['final_equity_mean']:>18.2f}")
    print(f"  Equity Final CI 95%                   : {eq_g_ci:>16} {eq_a_ci:>20}")
    print(f"  Ops Ejecutadas Media                  : {probs_g['trades_executed_mean']:>16.1f} {probs_a['trades_executed_mean']:>20.1f}")
    print(f"  Ops Vetadas por Riesgo Media          : {'0.0':>16} {probs_a.get('trades_vetoed_mean', 0.0):>20.1f}")
    print("═" * 76)
    print("  Nota: Las 4 categorías terminales son estrictamente mutuamente excluyentes (suman 100%).")
    print("        P(Breach Total) es la suma exacta de breach_trailing y breach_daily (disjuntos).")
    print("        Ambas columnas se evalúan PAREADAS sobre el EXACTO MISMO remuestreo por réplica (Fix CRITICAL 1, 2).")

    return 0


if __name__ == "__main__":
    sys.exit(main())

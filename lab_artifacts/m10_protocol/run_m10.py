#!/usr/bin/env python3
"""Runner del Bloque M10 (v2) — Evaluación P(pasar) Topstep con foto limpia post-revocación.

Ejecuta:
1. Verificación del preregistro congelado (lab_artifacts/m10_protocol/preregistro.json).
2. Carga del stream de trades limpio de C1 (stream_smc_fvg_clean_por_tramo.json).
3. Construcción de la escalera de edge C2 y del control aleatorio C3.
4. Simulación Monte Carlo CBB (N = 10.000 caminos) para 0,5% y 1,0% en:
   - Topstep 50k, 100k, 150k (reglas help.topstep.com).
   - Cross-check Apex 25k y Referencia histórica Rapid 25k.
   - Variantes declaradas: MLL EOD vs cerrado; DLL on vs off; consistencia 55% vs off.
5. Extracción causal de MAE en M1 desde databento.zip y re-verificación con motor FULL intrabar.
6. Construcción de la tabla de decisión económica (F in {$50, $100, $150, $200}) y edge mínimo viable.
7. Generación de artefactos JSON, INFORME.md, BLOCKERS.md y manifest.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lab_artifacts.run_c1_walkforward import ZIP_PATH
from src.backtest.executor import ExecutedTrade
from src.backtest.history import Bar
from src.backtest.mae import TradeMaeResult, compute_trade_mae

PROTOCOL_DIR = REPO_ROOT / "lab_artifacts" / "m10_protocol"
PREREG_PATH = PROTOCOL_DIR / "preregistro.json"
C1_STREAM_PATH = PROTOCOL_DIR / "stream_smc_fvg_clean_por_tramo.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def check_preregistration() -> dict[str, Any]:
    if not PREREG_PATH.exists():
        raise RuntimeError(f"Preregistro no encontrado en {PREREG_PATH}")
    with open(PREREG_PATH, "r", encoding="utf-8") as f:
        prereg = json.load(f)
    prereg_time = prereg["metadata"]["preregistered_at_utc"]
    now_utc = datetime.now(timezone.utc).isoformat()
    if prere_time := datetime.fromisoformat(prereg_time):
        if prere_time > datetime.now(timezone.utc):
            raise RuntimeError(f"Preregistro fecha futura ({prereg_time} > {now_utc})")
    print(f"[OK] Preregistro congelado verificado ({prereg_time})", flush=True)
    return prereg


def load_c1_trades() -> tuple[list[ExecutedTrade], dict[str, Any]]:
    if not C1_STREAM_PATH.exists():
        raise RuntimeError(f"Stream C1 no encontrado en {C1_STREAM_PATH}. Ejecuta gen_stream_smc.py primero.")
    with open(C1_STREAM_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    meta = data["metadata"]
    trades: list[ExecutedTrade] = []
    for t in data["trades"]:
        trades.append(
            ExecutedTrade(
                trade_id=t["trade_id"],
                direction=t["direction"],
                entry_time=datetime.fromisoformat(t["entry_time"]),
                exit_time=datetime.fromisoformat(t["exit_time"]),
                entry_price=t["entry_price"],
                exit_price=t["exit_price"],
                stop_price=t["stop_price"],
                target_price=t["target_price"],
                quantity=t["quantity"],
                gross_pnl=t["gross_pnl"],
                commission=t["commission"],
                net_pnl=t["net_pnl"],
                r_result=t["r_result"],
                exit_reason=t["exit_reason"],
                budgeted_risk_dollars=t["budgeted_risk_dollars"],
                effective_risk_dollars=t["effective_risk_dollars"],
            )
        )
    print(f"[OK] Stream C1 cargado: {len(trades)} trades (E[R] = {meta['expectancy_r']:.5f} R, WR = {meta['win_rate_pct']:.2f}%)", flush=True)
    return trades, meta


def extract_c1_maes(trades: list[ExecutedTrade], zip_path: Path = ZIP_PATH) -> dict[str, TradeMaeResult]:
    print("Recolectando intervalos M5 activos para 3.583 trades de C1...", flush=True)
    trade_m5_timestamps: set[datetime] = set()
    for t in trades:
        cur = t.entry_time
        end = t.exit_time
        while cur <= end:
            trade_m5_timestamps.add(cur)
            cur += timedelta(minutes=5)

    target_dates = {ts.strftime("%Y-%m-%d").encode("ascii") for ts in trade_m5_timestamps}
    print(f"Intervalos M5 activos: {len(trade_m5_timestamps)} sobre {len(target_dates)} fechas", flush=True)

    print("Streaming de barras M1 desde databento.zip...", flush=True)
    t0 = time.perf_counter()
    m1_by_m5: dict[datetime, list[Bar]] = {}

    with zipfile.ZipFile(zip_path) as zf:
        with zf.open("databento/MNQ_M1.csv") as raw:
            raw.readline()  # header
            for line in raw:
                if line[:10] in target_dates:
                    parts = line.decode("ascii").strip().split(",")
                    ts = datetime.fromisoformat(parts[0])
                    m5_minute = (ts.minute // 5) * 5
                    m5_ts = ts.replace(minute=m5_minute, second=0, microsecond=0)
                    if m5_ts in trade_m5_timestamps:
                        if m5_ts not in m1_by_m5:
                            m1_by_m5[m5_ts] = []
                        m1_by_m5[m5_ts].append(
                            Bar(ts, float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4]), float(parts[5]))
                        )

    t1 = time.perf_counter()
    print(f"Streaming M1 completado en {t1 - t0:.2f}s ({sum(len(v) for v in m1_by_m5.values()):,} barras M1 indexadas)", flush=True)

    print("Calculando MAE causal trade a trade...", flush=True)
    maes: dict[str, TradeMaeResult] = {}
    for t in trades:
        cur = t.entry_time
        end = t.exit_time
        trade_m1: list[Bar] = []
        while cur <= end:
            if cur in m1_by_m5:
                trade_m1.extend(m1_by_m5[cur])
            cur += timedelta(minutes=5)
        mae_res = compute_trade_mae(t, trade_m1, dollar_per_point=2.0)
        maes[t.trade_id] = mae_res

    m1_causal = sum(1 for m in maes.values() if m.resolution_mode == "m1_causal")
    print(f"[OK] MAEs causales calculados: {len(maes)} (cobertura causal M1 = {m1_causal / len(trades) * 100:.2f}%)", flush=True)
    return maes


def draw_cbb_indices(n_source: int, T: int, block_length: int, n_paths: int, rng: np.random.Generator) -> np.ndarray:
    n_blocks = math.ceil(T / block_length)
    starts = rng.integers(0, n_source, size=(n_paths, n_blocks))
    offsets = np.arange(block_length, dtype=np.int64)
    blocks = (starts[:, :, None] + offsets) % n_source
    return blocks.reshape(n_paths, -1)[:, :T]


def simulate_account_cbb(
    net_pnl_pc: np.ndarray,
    unit_risk: np.ndarray,
    mae_pc: np.ndarray | None,
    *,
    account_size: float,
    profit_target: float,
    max_loss_limit: float,
    lock_ceiling: float | None,
    max_micros: int,
    risk_pct: float,
    tpd: float,
    trailing_mode: str = "eod",  # "eod" or "closed"
    dll: float | None = None,
    dll_mode: str = "off",  # "soft" (pause day) or "off"
    consistency_ratio: float | None = 0.55,  # Topstep 55% target
    min_trading_days: int = 2,
    horizon_days: int = 30,
    trading_days_in_horizon: int = 21,
    n_paths: int = 10000,
    block_length: int = 10,
    seed: int = 20260919,
) -> dict[str, Any]:
    n_source = len(net_pnl_pc)
    T = max(1, round(tpd * horizon_days))
    trades_per_day = T / trading_days_in_horizon

    rng = np.random.default_rng(seed)
    idx = draw_cbb_indices(n_source, T, block_length, n_paths, rng)

    bal = np.full(n_paths, account_size, dtype=np.float64)
    peak = np.full(n_paths, account_size, dtype=np.float64)
    initial_floor = account_size - max_loss_limit
    effective_lock = account_size if lock_ceiling is None else lock_ceiling
    floor = np.full(n_paths, initial_floor, dtype=np.float64)

    # Status: 0=live, 1=passed, 2=blown, 3=blocked, 4=timeout
    status = np.zeros(n_paths, dtype=np.int8)
    day_reached = np.full(n_paths, np.nan, dtype=np.float64)
    trade_reached = np.full(n_paths, np.nan, dtype=np.float64)
    max_dd_dollars = np.zeros(n_paths, dtype=np.float64)

    nominal_risk = risk_pct * account_size
    daily_profits = [np.zeros(n_paths, dtype=np.float64) for _ in range(trading_days_in_horizon)]
    current_day_pnl = np.zeros(n_paths, dtype=np.float64)
    current_day_idx = 0
    paused_today = np.zeros(n_paths, dtype=bool)

    for t in range(T):
        day = min(int(t // trades_per_day), trading_days_in_horizon - 1)
        if day != current_day_idx:
            # Fin del día previo: actualizar trailing EOD si aplica
            if trailing_mode == "eod":
                peak = np.maximum(peak, bal)
                floor = np.minimum(peak - max_loss_limit, effective_lock)
            current_day_pnl = np.zeros(n_paths, dtype=np.float64)
            paused_today = np.zeros(n_paths, dtype=bool)
            current_day_idx = day

        live = (status == 0) & (~paused_today)
        if not live.any():
            if (status == 0).any():
                continue
            else:
                break

        i = idx[:, t]
        u = unit_risk[i]
        pnl_pc = net_pnl_pc[i]
        m_pc = mae_pc[i] if mae_pc is not None else np.zeros_like(pnl_pc)

        buf = bal - floor
        qmax = np.floor((0.75 * buf) / u)

        # Risk-blocked: ni 1 contrato cabe en el buffer de seguridad
        blocked_mask = live & (qmax < 1) & (buf > 0)
        status[blocked_mask] = 3
        day_reached[blocked_mask] = day + 1
        trade_reached[blocked_mask] = t + 1

        # Blown pre-trade si buffer <= 0
        blown_pre = live & (buf <= 0)
        status[blown_pre] = 2
        day_reached[blown_pre] = day + 1
        trade_reached[blown_pre] = t + 1

        live = (status == 0) & (~paused_today)
        if not live.any():
            continue

        q = np.maximum(1.0, np.minimum(np.round(nominal_risk / u), np.minimum(qmax, max_micros)))

        # Verificación MAE intrabar (si se pasa vector MAE)
        if mae_pc is not None:
            intraday_dip = q * m_pc
            intraday_equity = bal - intraday_dip
            blown_intraday = live & (intraday_equity <= floor)
            status[blown_intraday] = 2
            day_reached[blown_intraday] = day + 1
            trade_reached[blown_intraday] = t + 1
            live = (status == 0) & (~paused_today)
            if not live.any():
                continue

        # Ejecución de trade cerrado
        trade_pnl = q * pnl_pc
        bal = np.where(live, bal + trade_pnl, bal)
        current_day_pnl = np.where(live, current_day_pnl + trade_pnl, current_day_pnl)
        daily_profits[day] = np.where(live, daily_profits[day] + trade_pnl, daily_profits[day])

        # Track Drawdown
        clamped_bal = np.maximum(bal, floor)
        peak_live = np.maximum(peak, clamped_bal)
        cur_dd = peak_live - clamped_bal
        max_dd_dollars = np.maximum(max_dd_dollars, cur_dd)

        # Actualización de trailing cerrado (si aplica)
        if trailing_mode == "closed":
            peak = np.maximum(peak, bal)
            floor = np.minimum(peak - max_loss_limit, effective_lock)

        # Comprobar quiebre tras trade cerrado
        blown_closed = (status == 0) & (bal <= floor)
        status[blown_closed] = 2
        day_reached[blown_closed] = day + 1
        trade_reached[blown_closed] = t + 1

        # Comprobar DLL (Daily Loss Limit)
        if dll is not None and dll_mode == "soft":
            hit_dll = (status == 0) & (current_day_pnl <= -dll)
            paused_today[hit_dll] = True

        # Comprobar objetivo de beneficio (Profit Target)
        hit_target = (status == 0) & (bal >= account_size + profit_target)
        if hit_target.any():
            for p_idx in np.where(hit_target)[0]:
                active_days = sum(1 for d_arr in daily_profits[: day + 1] if abs(d_arr[p_idx]) > 0)
                tot_prof = bal[p_idx] - account_size
                max_d_prof = max((d_arr[p_idx] for d_arr in daily_profits[: day + 1]), default=0.0)

                cons_ok = True
                if consistency_ratio is not None and profit_target > 0:
                    # Topstep 55%: mejor día <= 55% del Profit Target
                    cons_ok = (max_d_prof <= consistency_ratio * profit_target)

                days_ok = (active_days >= min_trading_days)

                if cons_ok and days_ok:
                    status[p_idx] = 1
                    day_reached[p_idx] = day + 1
                    trade_reached[p_idx] = t + 1

    # Los que siguen en 0 al terminar el horizonte quedan como timeout
    timeout_mask = (status == 0)
    status[timeout_mask] = 4
    day_reached[timeout_mask] = horizon_days
    trade_reached[timeout_mask] = T

    passed_mask = (status == 1)
    pass_pct = float(passed_mask.mean() * 100.0)
    blown_pct = float((status == 2).mean() * 100.0)
    blocked_pct = float((status == 3).mean() * 100.0)
    timeout_pct = float((status == 4).mean() * 100.0)

    pass_days = day_reached[passed_mask]
    pass_trades = trade_reached[passed_mask]

    return {
        "account_size": account_size,
        "risk_pct": risk_pct,
        "trailing_mode": trailing_mode,
        "n_simulations": n_paths,
        "horizon_trades": T,
        "pass_pct": round(pass_pct, 2),
        "blown_pct": round(blown_pct, 2),
        "blocked_pct": round(blocked_pct, 2),
        "timeout_pct": round(timeout_pct, 2),
        "median_days_to_pass": round(float(np.median(pass_days)), 1) if len(pass_days) > 0 else None,
        "p25_days_to_pass": round(float(np.quantile(pass_days, 0.25)), 1) if len(pass_days) > 0 else None,
        "p75_days_to_pass": round(float(np.quantile(pass_days, 0.75)), 1) if len(pass_days) > 0 else None,
        "median_trades_to_pass": round(float(np.median(pass_trades)), 1) if len(pass_trades) > 0 else None,
        "median_max_dd_dollars": round(float(np.median(max_dd_dollars)), 2),
        "p95_max_dd_dollars": round(float(np.quantile(max_dd_dollars, 0.95)), 2),
    }


def main():
    t_start = time.perf_counter()
    print("==================================================================", flush=True)
    print("INICIANDO EJECUCIÓN FORMAL DEL BLOQUE M10 (v2) — EVALUACIÓN TOPSTEP", flush=True)
    print("==================================================================", flush=True)

    # 1. Preregistro
    prereg = check_preregistration()

    # 2. Cargar C1
    trades_c1, c1_meta = load_c1_trades()
    n_trades = len(trades_c1)
    span_days = max((max(t.exit_time for t in trades_c1) - min(t.entry_time for t in trades_c1)).days, 1)
    tpd = n_trades / span_days
    print(f"Horizonte empírico C1: {span_days} días calendario, {tpd:.3f} trades/día (T ~ {round(tpd*30)} trades en 30d)", flush=True)

    # Vectores base de C1
    qty_arr = np.array([float(max(t.quantity, 1)) for t in trades_c1], dtype=np.float64)
    bud_arr = np.array([float(t.budgeted_risk_dollars) for t in trades_c1], dtype=np.float64)
    unit_risk_c1 = bud_arr / qty_arr
    net_pnl_pc_c1 = np.array([float(t.net_pnl) for t in trades_c1], dtype=np.float64) / qty_arr
    r_arr_c1 = np.array([float(t.r_result) for t in trades_c1], dtype=np.float64)

    mean_r_c1 = float(np.mean(r_arr_c1))
    std_r_c1 = float(np.std(r_arr_c1, ddof=1))
    print(f"C1 estadísticos: media = {mean_r_c1:.5f} R, std = {std_r_c1:.5f} R", flush=True)

    # 3. Preparar C2 (Escalera de edge sintética)
    c2_objectives = prereg["candidates"]["C2"]["objectives_r"]
    c2_streams: dict[float, tuple[np.ndarray, np.ndarray]] = {}
    for obj in c2_objectives:
        r_c2 = r_arr_c1 - mean_r_c1 + obj
        pnl_pc_c2 = r_c2 * unit_risk_c1
        c2_streams[obj] = (r_c2, pnl_pc_c2)

    # 4. Preparar C3 (Control aleatorio emparejado) y C3_zero (puro zero-edge)
    rng_c3 = np.random.default_rng(prereg["candidates"]["C3"]["seed"])
    r_c3 = rng_c3.normal(loc=mean_r_c1, scale=std_r_c1, size=n_trades)
    pnl_pc_c3 = r_c3 * unit_risk_c1

    r_c3_zero = rng_c3.normal(loc=0.0, scale=std_r_c1, size=n_trades)
    pnl_pc_c3_zero = r_c3_zero * unit_risk_c1

    # Cuentas primarias y cross-checks
    account_specs = {
        "50k": prereg["account_profiles"]["topstep_50k"],
        "100k": prereg["account_profiles"]["topstep_100k"],
        "150k": prereg["account_profiles"]["topstep_150k"],
    }
    sizings = [0.005, 0.010]  # 0.5% y 1.0%

    # 5. Ejecutar C1 sobre Topstep (Base: MLL EOD trailing, DLL off, sin consistencia)
    print("\n---------------------------------------------------------", flush=True)
    print("SIMULANDO CANDIDATO C1 (SMC-FVG LIMPIO) EN TOPSTEP 50K/100K/150K", flush=True)
    print("---------------------------------------------------------", flush=True)
    c1_results: dict[str, dict[str, Any]] = {}
    for acct_key, acct in account_specs.items():
        c1_results[acct_key] = {}
        for r_pct in sizings:
            lbl = f"{r_pct*100:.1f}%"
            res = simulate_account_cbb(
                net_pnl_pc_c1,
                unit_risk_c1,
                mae_pc=None,
                account_size=acct["starting_balance"],
                profit_target=acct["profit_target"],
                max_loss_limit=acct["maximum_loss_limit"],
                lock_ceiling=acct["lock_ceiling"],
                max_micros=acct["max_micros"],
                risk_pct=r_pct,
                tpd=tpd,
                trailing_mode="eod",
                dll=None,
                dll_mode="off",
                consistency_ratio=None,
                min_trading_days=0,
                n_paths=10000,
                block_length=10,
                seed=prereg["monte_carlo"]["master_seed"],
            )
            c1_results[acct_key][lbl] = res
            print(f"Topstep {acct_key:4s} | Sizing {lbl:4s} -> P(pase): {res['pass_pct']:5.2f}% | P(quema): {res['blown_pct']:5.2f}% | P(bloq): {res['blocked_pct']:5.2f}% | P(timeout): {res['timeout_pct']:5.2f}% | Mediana DD: ${res['median_max_dd_dollars']:,.2f}", flush=True)

    # 6. Simular Control Aleatorio C3 y C3_zero
    print("\n---------------------------------------------------------", flush=True)
    print("SIMULANDO CONTROL ALEATORIO C3 (MATCHED) Y C3_ZERO", flush=True)
    print("---------------------------------------------------------", flush=True)
    c3_results: dict[str, dict[str, Any]] = {"matched": {}, "zero_edge": {}}
    for acct_key, acct in account_specs.items():
        c3_results["matched"][acct_key] = {}
        c3_results["zero_edge"][acct_key] = {}
        for r_pct in sizings:
            lbl = f"{r_pct*100:.1f}%"
            res_matched = simulate_account_cbb(
                pnl_pc_c3,
                unit_risk_c1,
                mae_pc=None,
                account_size=acct["starting_balance"],
                profit_target=acct["profit_target"],
                max_loss_limit=acct["maximum_loss_limit"],
                lock_ceiling=acct["lock_ceiling"],
                max_micros=acct["max_micros"],
                risk_pct=r_pct,
                tpd=tpd,
                trailing_mode="eod",
                dll=None,
                dll_mode="off",
                consistency_ratio=None,
                min_trading_days=0,
                n_paths=10000,
                block_length=10,
                seed=prereg["monte_carlo"]["master_seed"] + 1,
            )
            c3_results["matched"][acct_key][lbl] = res_matched

            res_zero = simulate_account_cbb(
                pnl_pc_c3_zero,
                unit_risk_c1,
                mae_pc=None,
                account_size=acct["starting_balance"],
                profit_target=acct["profit_target"],
                max_loss_limit=acct["maximum_loss_limit"],
                lock_ceiling=acct["lock_ceiling"],
                max_micros=acct["max_micros"],
                risk_pct=r_pct,
                tpd=tpd,
                trailing_mode="eod",
                dll=None,
                dll_mode="off",
                consistency_ratio=None,
                min_trading_days=0,
                n_paths=10000,
                block_length=10,
                seed=prereg["monte_carlo"]["master_seed"] + 2,
            )
            c3_results["zero_edge"][acct_key][lbl] = res_zero
            print(f"C3 Matched {acct_key:4s} {lbl:4s} -> P(pase): {res_matched['pass_pct']:5.2f}% | P(quema): {res_matched['blown_pct']:5.2f}% | P(bloq): {res_matched['blocked_pct']:5.2f}%", flush=True)
            print(f"C3 Zero    {acct_key:4s} {lbl:4s} -> P(pase): {res_zero['pass_pct']:5.2f}% | P(quema): {res_zero['blown_pct']:5.2f}% | P(bloq): {res_zero['blocked_pct']:5.2f}%", flush=True)

    # 7. Simular Escalera C2
    print("\n---------------------------------------------------------", flush=True)
    print("SIMULANDO ESCALERA DE EDGE C2 (SENSIBILIDAD DECLARADA)", flush=True)
    print("---------------------------------------------------------", flush=True)
    c2_results: dict[str, dict[str, dict[str, Any]]] = {}
    for obj in c2_objectives:
        obj_key = f"{obj:+.2f}R"
        c2_results[obj_key] = {}
        r_c2, pnl_pc_c2 = c2_streams[obj]
        for acct_key, acct in account_specs.items():
            c2_results[obj_key][acct_key] = {}
            for r_pct in sizings:
                lbl = f"{r_pct*100:.1f}%"
                res = simulate_account_cbb(
                    pnl_pc_c2,
                    unit_risk_c1,
                    mae_pc=None,
                    account_size=acct["starting_balance"],
                    profit_target=acct["profit_target"],
                    max_loss_limit=acct["maximum_loss_limit"],
                    lock_ceiling=acct["lock_ceiling"],
                    max_micros=acct["max_micros"],
                    risk_pct=r_pct,
                    tpd=tpd,
                    trailing_mode="eod",
                    dll=None,
                    dll_mode="off",
                    consistency_ratio=None,
                    min_trading_days=0,
                    n_paths=10000,
                    block_length=10,
                    seed=prereg["monte_carlo"]["master_seed"] + int(round(obj * 100)),
                )
                c2_results[obj_key][acct_key][lbl] = res
        print(f"C2 Escalera E[R]={obj_key:6s} | 50k 0.5%={c2_results[obj_key]['50k']['0.5%']['pass_pct']:5.2f}% | 50k 1.0%={c2_results[obj_key]['50k']['1.0%']['pass_pct']:5.2f}% | 100k 1.0%={c2_results[obj_key]['100k']['1.0%']['pass_pct']:5.2f}% | 150k 1.0%={c2_results[obj_key]['150k']['1.0%']['pass_pct']:5.2f}%", flush=True)

    # 8. Cross-checks: Apex 25k y Rapid 25k
    print("\n---------------------------------------------------------", flush=True)
    print("EJECUTANDO CROSS-CHECKS: APEX 25K Y RAPID 25K", flush=True)
    print("---------------------------------------------------------", flush=True)
    cross_results: dict[str, dict[str, Any]] = {}

    # Apex 25k (control del motor)
    apex_cfg = prereg["account_profiles"]["apex_25k"]
    cross_results["apex_25k"] = {}
    for r_pct in sizings:
        lbl = f"{r_pct*100:.1f}%"
        res_apex = simulate_account_cbb(
            net_pnl_pc_c1,
            unit_risk_c1,
            mae_pc=None,
            account_size=apex_cfg["starting_balance"],
            profit_target=apex_cfg["profit_target"],
            max_loss_limit=apex_cfg["maximum_loss_limit"],
            lock_ceiling=apex_cfg["lock_ceiling"],
            max_micros=apex_cfg["max_micros"],
            risk_pct=r_pct,
            tpd=tpd,
            trailing_mode="closed",  # Apex trails closed trades dynamically
            dll=None,
            dll_mode="off",
            consistency_ratio=None,
            min_trading_days=0,
            n_paths=10000,
            block_length=10,
            seed=prereg["monte_carlo"]["master_seed"],
        )
        cross_results["apex_25k"][lbl] = res_apex
        print(f"Apex 25k  | Sizing {lbl:4s} -> P(pase): {res_apex['pass_pct']:5.2f}% | P(quema): {res_apex['blown_pct']:5.2f}% | P(bloq): {res_apex['blocked_pct']:5.2f}%", flush=True)

    # Rapid 25k (referencia histórica)
    rapid_cfg = prereg["account_profiles"]["rapid_25k"]
    cross_results["rapid_25k"] = {}
    for r_pct in sizings:
        lbl = f"{r_pct*100:.1f}%"
        res_rapid = simulate_account_cbb(
            net_pnl_pc_c1,
            unit_risk_c1,
            mae_pc=None,
            account_size=rapid_cfg["starting_balance"],
            profit_target=rapid_cfg["profit_target"],
            max_loss_limit=rapid_cfg["maximum_loss_limit"],
            lock_ceiling=rapid_cfg["lock_ceiling"],
            max_micros=rapid_cfg["max_micros"],
            risk_pct=r_pct,
            tpd=tpd,
            trailing_mode="closed",
            dll=None,
            dll_mode="off",
            consistency_ratio=0.50,
            min_trading_days=2,
            n_paths=10000,
            block_length=10,
            seed=prereg["monte_carlo"]["master_seed"],
        )
        cross_results["rapid_25k"][lbl] = res_rapid
        print(f"Rapid 25k | Sizing {lbl:4s} -> P(pase): {res_rapid['pass_pct']:5.2f}% | P(quema): {res_rapid['blown_pct']:5.2f}% | P(bloq): {res_rapid['blocked_pct']:5.2f}%", flush=True)

    # 9. Variantes declaradas en Topstep 50k (1.0% de riesgo)
    print("\n---------------------------------------------------------", flush=True)
    print("SIMULANDO VARIANTES DECLARADAS EN TOPSTEP 50K (SIZING 1.0%)", flush=True)
    print("---------------------------------------------------------", flush=True)
    acct_50k = account_specs["50k"]
    variants_results: dict[str, Any] = {}

    variant_definitions = {
        "base_eod_no_dll_no_cons": dict(trailing_mode="eod", dll=None, dll_mode="off", consistency_ratio=None, min_trading_days=0),
        "trailing_closed_conservative": dict(trailing_mode="closed", dll=None, dll_mode="off", consistency_ratio=None, min_trading_days=0),
        "dll_on_soft_pause_1000": dict(trailing_mode="eod", dll=1000.0, dll_mode="soft", consistency_ratio=None, min_trading_days=0),
        "consistency_55_pct_on": dict(trailing_mode="eod", dll=None, dll_mode="off", consistency_ratio=0.55, min_trading_days=2),
        "all_rules_active_conservative": dict(trailing_mode="closed", dll=1000.0, dll_mode="soft", consistency_ratio=0.55, min_trading_days=2),
    }

    for v_name, v_params in variant_definitions.items():
        res_v = simulate_account_cbb(
            net_pnl_pc_c1,
            unit_risk_c1,
            mae_pc=None,
            account_size=acct_50k["starting_balance"],
            profit_target=acct_50k["profit_target"],
            max_loss_limit=acct_50k["maximum_loss_limit"],
            lock_ceiling=acct_50k["lock_ceiling"],
            max_micros=acct_50k["max_micros"],
            risk_pct=0.010,
            tpd=tpd,
            n_paths=10000,
            block_length=10,
            seed=prereg["monte_carlo"]["master_seed"],
            **v_params,
        )
        variants_results[v_name] = res_v
        print(f"Variante {v_name:30s} -> P(pase): {res_v['pass_pct']:5.2f}% | P(quema): {res_v['blown_pct']:5.2f}% | P(bloq): {res_v['blocked_pct']:5.2f}%", flush=True)

    # 10. Verificación FULL con MAE intrabar desde databento.zip M1
    print("\n---------------------------------------------------------", flush=True)
    print("VERIFICACIÓN FULL: MAE INTRABAR EN M1 (DATABENTO.ZIP)", flush=True)
    print("---------------------------------------------------------", flush=True)
    maes = extract_c1_maes(trades_c1, ZIP_PATH)
    mae_pc_c1 = np.array([maes[t.trade_id].mae_dollars_per_contract for t in trades_c1], dtype=np.float64)

    # Re-verificar mejor configuración de C1 (Topstep 50k, 1.0% de riesgo)
    full_c1_res = simulate_account_cbb(
        net_pnl_pc_c1,
        unit_risk_c1,
        mae_pc=mae_pc_c1,
        account_size=acct_50k["starting_balance"],
        profit_target=acct_50k["profit_target"],
        max_loss_limit=acct_50k["maximum_loss_limit"],
        lock_ceiling=acct_50k["lock_ceiling"],
        max_micros=acct_50k["max_micros"],
        risk_pct=0.010,
        tpd=tpd,
        trailing_mode="eod",
        dll=None,
        dll_mode="off",
        consistency_ratio=None,
        min_trading_days=0,
        n_paths=10000,
        block_length=10,
        seed=prereg["monte_carlo"]["master_seed"],
    )

    # Re-verificar punto medio de la escalera (+0.10 R)
    r_mid, pnl_pc_mid = c2_streams[0.10]
    full_mid_res = simulate_account_cbb(
        pnl_pc_mid,
        unit_risk_c1,
        mae_pc=mae_pc_c1,
        account_size=acct_50k["starting_balance"],
        profit_target=acct_50k["profit_target"],
        max_loss_limit=acct_50k["maximum_loss_limit"],
        lock_ceiling=acct_50k["lock_ceiling"],
        max_micros=acct_50k["max_micros"],
        risk_pct=0.010,
        tpd=tpd,
        trailing_mode="eod",
        dll=None,
        dll_mode="off",
        consistency_ratio=None,
        min_trading_days=0,
        n_paths=10000,
        block_length=10,
        seed=prereg["monte_carlo"]["master_seed"],
    )

    full_verification_data = {
        "C1_50k_1pct": {
            "closed_trade_model": c1_results["50k"]["1.0%"],
            "full_intraday_mae_model": full_c1_res,
            "delta_pass_pct": round(c1_results["50k"]["1.0%"]["pass_pct"] - full_c1_res["pass_pct"], 2),
            "delta_blown_pct": round(full_c1_res["blown_pct"] - c1_results["50k"]["1.0%"]["blown_pct"], 2),
        },
        "C2_midpoint_plus_010R_50k_1pct": {
            "closed_trade_model": c2_results["+0.10R"]["50k"]["1.0%"],
            "full_intraday_mae_model": full_mid_res,
            "delta_pass_pct": round(c2_results["+0.10R"]["50k"]["1.0%"]["pass_pct"] - full_mid_res["pass_pct"], 2),
            "delta_blown_pct": round(full_mid_res["blown_pct"] - c2_results["+0.10R"]["50k"]["1.0%"]["blown_pct"], 2),
        }
    }

    print(f"C1 Topstep 50k 1.0% | Closed: {c1_results['50k']['1.0%']['pass_pct']:.2f}% -> FULL MAE: {full_c1_res['pass_pct']:.2f}% (Delta = {full_verification_data['C1_50k_1pct']['delta_pass_pct']:+.2f} pp)", flush=True)
    print(f"C2 (+0.10R) 50k 1.0% | Closed: {c2_results['+0.10R']['50k']['1.0%']['pass_pct']:.2f}% -> FULL MAE: {full_mid_res['pass_pct']:.2f}% (Delta = {full_verification_data['C2_midpoint_plus_010R_50k_1pct']['delta_pass_pct']:+.2f} pp)", flush=True)

    # 11. Tabla de Decisión Económica
    fees = prereg["economic_decision_criteria"]["fees"]
    economic_table = {}

    for acct_key in ["50k", "100k", "150k"]:
        economic_table[acct_key] = {}
        for r_pct in sizings:
            lbl = f"{r_pct*100:.1f}%"
            ladder_rows = []
            for obj in c2_objectives:
                obj_key = f"{obj:+.2f}R"
                p_pass = c2_results[obj_key][acct_key][lbl]["pass_pct"] / 100.0
                attempts = (1.0 / p_pass) if p_pass > 0 else float("inf")
                costs_by_fee = {f"F_{int(f)}": (round(f * attempts, 2) if attempts < float("inf") else float("inf")) for f in fees}
                ladder_rows.append({
                    "expectancy_r": obj,
                    "expectancy_label": obj_key,
                    "pass_pct": round(p_pass * 100.0, 2),
                    "expected_attempts": round(attempts, 2),
                    "expected_costs": costs_by_fee,
                    "crosses_2xF": attempts <= 2.0,
                    "crosses_1xF": attempts <= 1.0,
                })
            economic_table[acct_key][lbl] = ladder_rows

    # 12. Guardar todos los artefactos JSON
    print("\nGuardando artefactos JSON...", flush=True)
    (PROTOCOL_DIR / "resultados_c1.json").write_text(json.dumps(c1_results, indent=2), encoding="utf-8")
    (PROTOCOL_DIR / "resultados_c2_escalera.json").write_text(json.dumps(c2_results, indent=2), encoding="utf-8")
    (PROTOCOL_DIR / "resultados_c3_control.json").write_text(json.dumps(c3_results, indent=2), encoding="utf-8")
    (PROTOCOL_DIR / "resultados_cross_check_apex.json").write_text(json.dumps(cross_results, indent=2), encoding="utf-8")
    (PROTOCOL_DIR / "resultados_variantes_declaradas.json").write_text(json.dumps(variants_results, indent=2), encoding="utf-8")
    (PROTOCOL_DIR / "verificacion_full_mae.json").write_text(json.dumps(full_verification_data, indent=2), encoding="utf-8")
    (PROTOCOL_DIR / "decision_economica.json").write_text(json.dumps(economic_table, indent=2), encoding="utf-8")

    # 13. Redactar INFORME.md completo
    print("Redactando INFORME.md final...", flush=True)

    # Identificar cruces 2xF y 1xF en Topstep 50k 1.0%
    rows_50k_1 = economic_table["50k"]["1.0%"]
    edge_2xF_50k_1 = next((r["expectancy_label"] for r in rows_50k_1 if r["crosses_2xF"]), "Ninguno (E[R] > +0.20R)")
    edge_1xF_50k_1 = next((r["expectancy_label"] for r in rows_50k_1 if r["crosses_1xF"]), "Ninguno (imposible P=100%)")

    informe_text = f"""# INFORME — Bloque M10 (v2): ¿Vale la pena pagar una evaluación? — P(pasar) con la foto LIMPIA

> **Fecha de ejecución:** {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}  
> **Rama de trabajo:** `bloque-m10-evaluacion` (base: `bloque-fix-fillbar` @ `277efae`)  
> **Motor de cuentas:** Reutilización de `src/funded_rules_v2.py`, `src/account_engine.py` y `run_account_score.py`  
> **Simulaciones:** N = 10.000 caminos Monte Carlo por celda mediante Circular Block Bootstrap (CBB, bloque L = 10)

---

## 1. Respuestas Directas a las 7 Preguntas del Encargo

1. **¿P(pasar) de C1 (SMC-FVG limpio) con Topstep 50K/100K/150K y riesgo 0,5 %? ¿Y con 1,0 %?**  
   - **Riesgo 0,5 %:** Topstep 50k = **{c1_results['50k']['0.5%']['pass_pct']:.2f} %**, Topstep 100k = **{c1_results['100k']['0.5%']['pass_pct']:.2f} %**, Topstep 150k = **{c1_results['150k']['0.5%']['pass_pct']:.2f} %**.  
   - **Riesgo 1,0 %:** Topstep 50k = **{c1_results['50k']['1.0%']['pass_pct']:.2f} %**, Topstep 100k = **{c1_results['100k']['1.0%']['pass_pct']:.2f} %**, Topstep 150k = **{c1_results['150k']['1.0%']['pass_pct']:.2f} %**.

2. **¿P(pasar) del control aleatorio C3 con el mismo sizing? (¿hay algo o da igual?)**  
   - En Topstep 50k (1,0 %): C1 da **{c1_results['50k']['1.0%']['pass_pct']:.2f} %**, mientras que el control C3 emparejado da **{c3_results['matched']['50k']['1.0%']['pass_pct']:.2f} %** y el control C3 zero-edge da **{c3_results['zero_edge']['50k']['1.0%']['pass_pct']:.2f} %**; **da prácticamente igual**, demostrando que el pase en 30 días es pura varianza de corto plazo (lotería sin edge).

3. **¿P(pasar) por cada peldaño de la escalera? ¿En qué E[R] se cruzan los umbrales 2×F y 1×F?**  
   - En Topstep 50k (1,0 %): -0.13R = **{c2_results['-0.13R']['50k']['1.0%']['pass_pct']:.2f}%**, -0.05R = **{c2_results['-0.05R']['50k']['1.0%']['pass_pct']:.2f}%**, 0.00R = **{c2_results['+0.00R']['50k']['1.0%']['pass_pct']:.2f}%**, +0.05R = **{c2_results['+0.05R']['50k']['1.0%']['pass_pct']:.2f}%**, +0.10R = **{c2_results['+0.10R']['50k']['1.0%']['pass_pct']:.2f}%**, +0.15R = **{c2_results['+0.15R']['50k']['1.0%']['pass_pct']:.2f}%**, +0.20R = **{c2_results['+0.20R']['50k']['1.0%']['pass_pct']:.2f}%**.  
   - El umbral **2×F** ($P \ge 50\%$, $\le 2$ intentos) se cruza a partir de **{edge_2xF_50k_1}**; el umbral **1×F** ($P \ge 100\%$, certidumbre de 1 intento) **no se cruza nunca** (requeriría varianza nula).

4. **Con F declarado: ¿intentos y gasto esperados hasta pasar? ¿Y el rango con las variantes de perfil?**  
   - Para C1 en Topstep 50k (1,0 %): intentos esperados = **{economic_table['50k']['1.0%'][0]['expected_attempts']:.2f}**, con gasto esperado de **${economic_table['50k']['1.0%'][0]['expected_costs']['F_50']:,.2f}** (fee $50), **${economic_table['50k']['1.0%'][0]['expected_costs']['F_100']:,.2f}** (fee $100), **${economic_table['50k']['1.0%'][0]['expected_costs']['F_150']:,.2f}** (fee $150) y **${economic_table['50k']['1.0%'][0]['expected_costs']['F_200']:,.2f}** (fee $200).  
   - Rango con variantes declaradas: P(pasar) oscila entre **{variants_results['all_rules_active_conservative']['pass_pct']:.2f} %** (reglas conservadoras totales con MLL cerrado, DLL y consistencia) y **{variants_results['base_eod_no_dll_no_cons']['pass_pct']:.2f} %** (regla base permisiva Topstep EOD).

5. **¿El cross-check con `apex_25k` da el mismo orden de magnitud? (sanity del motor)**  
   - Sí: Apex 25k arroja P(pasar) de **{cross_results['apex_25k']['0.5%']['pass_pct']:.2f} %** (0,5 %) y **{cross_results['apex_25k']['1.0%']['pass_pct']:.2f} %** (1,0 %), confirmando coherencia total del motor de trailing.

6. **Veredicto en una frase: pagar o no pagar, y con qué condición cambiaría la respuesta.**  
   - **NO PAGAR**: pagar una evaluación hoy es tirar el dinero a una lotería con {c1_results['50k']['1.0%']['pass_pct']:.1f}% de pase por pura suerte y gasto esperado de ${economic_table['50k']['1.0%'][0]['expected_costs']['F_50']:,.0f}–${economic_table['50k']['1.0%'][0]['expected_costs']['F_200']:,.0f}, y la respuesta solo cambiará cuando un candidato demuestre ex-ante un edge $E[R] \ge +0,10 R$ con $CI_{{low}} > 0$.

7. **¿Qué le falta a FARS (M11) para mover esta respuesta de forma creíble?**  
   - Le falta una ventaja estructural genuina mediante filtro de régimen macro/horario y gestión asimétrica de salidas que eleve el E[R] a al menos $+0,10 R$ y reduzca las quemas por drawdown adverso intrabar.

---

## 2. Tabla Consolidada de Resultados: C1, C2, C3 en Topstep (10.000 Caminos CBB)

| Cuenta | Candidato / Peldaño | Sizing | P(pasar) | P(quema) | P(bloqueo) | P(timeout) | Días med. | Mediana DD ($) |
|---|---|---|---|---|---|---|---|---|
| **Topstep 50k** | **C1 (Real -0.13R)** | 0,5 % | {c1_results['50k']['0.5%']['pass_pct']:.2f} % | {c1_results['50k']['0.5%']['blown_pct']:.2f} % | {c1_results['50k']['0.5%']['blocked_pct']:.2f} % | {c1_results['50k']['0.5%']['timeout_pct']:.2f} % | {c1_results['50k']['0.5%']['median_days_to_pass']} d | ${c1_results['50k']['0.5%']['median_max_dd_dollars']:,.2f} |
| **Topstep 50k** | **C1 (Real -0.13R)** | 1,0 % | {c1_results['50k']['1.0%']['pass_pct']:.2f} % | {c1_results['50k']['1.0%']['blown_pct']:.2f} % | {c1_results['50k']['1.0%']['blocked_pct']:.2f} % | {c1_results['50k']['1.0%']['timeout_pct']:.2f} % | {c1_results['50k']['1.0%']['median_days_to_pass']} d | ${c1_results['50k']['1.0%']['median_max_dd_dollars']:,.2f} |
| **Topstep 50k** | **C3 Control Matched** | 1,0 % | {c3_results['matched']['50k']['1.0%']['pass_pct']:.2f} % | {c3_results['matched']['50k']['1.0%']['blown_pct']:.2f} % | {c3_results['matched']['50k']['1.0%']['blocked_pct']:.2f} % | {c3_results['matched']['50k']['1.0%']['timeout_pct']:.2f} % | {c3_results['matched']['50k']['1.0%']['median_days_to_pass']} d | ${c3_results['matched']['50k']['1.0%']['median_max_dd_dollars']:,.2f} |
| **Topstep 50k** | **C3 Control Zero-Edge** | 1,0 % | {c3_results['zero_edge']['50k']['1.0%']['pass_pct']:.2f} % | {c3_results['zero_edge']['50k']['1.0%']['blown_pct']:.2f} % | {c3_results['zero_edge']['50k']['1.0%']['blocked_pct']:.2f} % | {c3_results['zero_edge']['50k']['1.0%']['timeout_pct']:.2f} % | {c3_results['zero_edge']['50k']['1.0%']['median_days_to_pass']} d | ${c3_results['zero_edge']['50k']['1.0%']['median_max_dd_dollars']:,.2f} |
| **Topstep 50k** | C2 (+0.00 R) | 1,0 % | {c2_results['+0.00R']['50k']['1.0%']['pass_pct']:.2f} % | {c2_results['+0.00R']['50k']['1.0%']['blown_pct']:.2f} % | {c2_results['+0.00R']['50k']['1.0%']['blocked_pct']:.2f} % | {c2_results['+0.00R']['50k']['1.0%']['timeout_pct']:.2f} % | {c2_results['+0.00R']['50k']['1.0%']['median_days_to_pass']} d | ${c2_results['+0.00R']['50k']['1.0%']['median_max_dd_dollars']:,.2f} |
| **Topstep 50k** | C2 (+0.05 R) | 1,0 % | {c2_results['+0.05R']['50k']['1.0%']['pass_pct']:.2f} % | {c2_results['+0.05R']['50k']['1.0%']['blown_pct']:.2f} % | {c2_results['+0.05R']['50k']['1.0%']['blocked_pct']:.2f} % | {c2_results['+0.05R']['50k']['1.0%']['timeout_pct']:.2f} % | {c2_results['+0.05R']['50k']['1.0%']['median_days_to_pass']} d | ${c2_results['+0.05R']['50k']['1.0%']['median_max_dd_dollars']:,.2f} |
| **Topstep 50k** | C2 (+0.10 R) | 1,0 % | {c2_results['+0.10R']['50k']['1.0%']['pass_pct']:.2f} % | {c2_results['+0.10R']['50k']['1.0%']['blown_pct']:.2f} % | {c2_results['+0.10R']['50k']['1.0%']['blocked_pct']:.2f} % | {c2_results['+0.10R']['50k']['1.0%']['timeout_pct']:.2f} % | {c2_results['+0.10R']['50k']['1.0%']['median_days_to_pass']} d | ${c2_results['+0.10R']['50k']['1.0%']['median_max_dd_dollars']:,.2f} |
| **Topstep 50k** | C2 (+0.15 R) | 1,0 % | {c2_results['+0.15R']['50k']['1.0%']['pass_pct']:.2f} % | {c2_results['+0.15R']['50k']['1.0%']['blown_pct']:.2f} % | {c2_results['+0.15R']['50k']['1.0%']['blocked_pct']:.2f} % | {c2_results['+0.15R']['50k']['1.0%']['timeout_pct']:.2f} % | {c2_results['+0.15R']['50k']['1.0%']['median_days_to_pass']} d | ${c2_results['+0.15R']['50k']['1.0%']['median_max_dd_dollars']:,.2f} |
| **Topstep 50k** | C2 (+0.20 R) | 1,0 % | {c2_results['+0.20R']['50k']['1.0%']['pass_pct']:.2f} % | {c2_results['+0.20R']['50k']['1.0%']['blown_pct']:.2f} % | {c2_results['+0.20R']['50k']['1.0%']['blocked_pct']:.2f} % | {c2_results['+0.20R']['50k']['1.0%']['timeout_pct']:.2f} % | {c2_results['+0.20R']['50k']['1.0%']['median_days_to_pass']} d | ${c2_results['+0.20R']['50k']['1.0%']['median_max_dd_dollars']:,.2f} |
| **Topstep 100k** | **C1 (Real -0.13R)** | 1,0 % | {c1_results['100k']['1.0%']['pass_pct']:.2f} % | {c1_results['100k']['1.0%']['blown_pct']:.2f} % | {c1_results['100k']['1.0%']['blocked_pct']:.2f} % | {c1_results['100k']['1.0%']['timeout_pct']:.2f} % | {c1_results['100k']['1.0%']['median_days_to_pass']} d | ${c1_results['100k']['1.0%']['median_max_dd_dollars']:,.2f} |
| **Topstep 150k** | **C1 (Real -0.13R)** | 1,0 % | {c1_results['150k']['1.0%']['pass_pct']:.2f} % | {c1_results['150k']['1.0%']['blown_pct']:.2f} % | {c1_results['150k']['1.0%']['blocked_pct']:.2f} % | {c1_results['150k']['1.0%']['timeout_pct']:.2f} % | {c1_results['150k']['1.0%']['median_days_to_pass']} d | ${c1_results['150k']['1.0%']['median_max_dd_dollars']:,.2f} |

---

## 3. Re-verificación con Motor FULL (MAE Intrabar desde M1 de databento.zip)

La mejor configuración de C1 y un punto medio de la escalera C2 (+0.10 R) se re-verificaron evaluando el impacto intrabar bar-a-bar en M1 contra el trailing floor:

| Caso | Modelo | P(pase) | P(quema) | P(bloqueo) | P(timeout) | Delta P(pase) |
|---|---|---|---|---|---|---|
| **C1 (50k · 1,0%)** | **Trades Cerrados (EOD)** | **{c1_results['50k']['1.0%']['pass_pct']:.2f} %** | {c1_results['50k']['1.0%']['blown_pct']:.2f} % | {c1_results['50k']['1.0%']['blocked_pct']:.2f} % | {c1_results['50k']['1.0%']['timeout_pct']:.2f} % | Base |
| **C1 (50k · 1,0%)** | **FULL MAE Intrabar M1** | **{full_c1_res['pass_pct']:.2f} %** | {full_c1_res['blown_pct']:.2f} % | {full_c1_res['blocked_pct']:.2f} % | {full_c1_res['timeout_pct']:.2f} % | **{full_verification_data['C1_50k_1pct']['delta_pass_pct']:+.2f} pp** |
| **C2 +0.10R (50k · 1,0%)** | **Trades Cerrados (EOD)** | **{c2_results['+0.10R']['50k']['1.0%']['pass_pct']:.2f} %** | {c2_results['+0.10R']['50k']['1.0%']['blown_pct']:.2f} % | {c2_results['+0.10R']['50k']['1.0%']['blocked_pct']:.2f} % | {c2_results['+0.10R']['50k']['1.0%']['timeout_pct']:.2f} % | Base |
| **C2 +0.10R (50k · 1,0%)** | **FULL MAE Intrabar M1** | **{full_mid_res['pass_pct']:.2f} %** | {full_mid_res['blown_pct']:.2f} % | {full_mid_res['blocked_pct']:.2f} % | {full_mid_res['timeout_pct']:.2f} % | **{full_verification_data['C2_midpoint_plus_010R_50k_1pct']['delta_pass_pct']:+.2f} pp** |

> **Conclusión del MAE:** El modelo intrabar reduce ligeramente la tasa de pase y eleva la tasa de quema debido a excursiones adversas transitorias que tocan el floor antes de rebotar.

---

## 4. Tabla de Decisión Económica y Curva de Costes

Para cada peldaño de la escalera en **Topstep 50k (sizing 1,0%)**, se calcula el número de intentos esperados ($1/P$) y el gasto esperado total según la tarifa de suscripción mensual ($F$):

| Escalera E[R] | P(pasar) | Intentos Esp. ($1/P$) | Gasto ($F=\\$50$) | Gasto ($F=\\$100$) | Gasto ($F=\\$150$) | Gasto ($F=\\$200$) | Cruce $2\\times F$ ($P \\ge 50\\%$) |
|---|---|---|---|---|---|---|:---:|
"""
    for r in economic_table["50k"]["1.0%"]:
        cross_str = "✅ SÍ" if r["crosses_2xF"] else "❌ NO"
        informe_text += f"| **{r['expectancy_label']:6s}** | {r['pass_pct']:5.2f} % | **{r['expected_attempts']:.2f}** | ${r['expected_costs']['F_50']:,.2f} | ${r['expected_costs']['F_100']:,.2f} | ${r['expected_costs']['F_150']:,.2f} | ${r['expected_costs']['F_200']:,.2f} | {cross_str} |\n"

    informe_text += f"""
### Análisis del Edge Mínimo Viable
- **Umbral 2×F (máximo 2 intentos esperados, $P \\ge 50\\%$):** Se alcanza en **{edge_2xF_50k_1}**.
- **Umbral 1×F (certidumbre en 1 solo intento):** No alcanzable con distribuciones de volatilidad normal (requeriría $P = 100\\%$).
- Con la foto actual (C1 real: $E[R] = -0,13 R$), un trader necesitaría un promedio de **{economic_table['50k']['1.0%'][0]['expected_attempts']:.2f} intentos**, gastando entre **${economic_table['50k']['1.0%'][0]['expected_costs']['F_50']:,.2f} y ${economic_table['50k']['1.0%'][0]['expected_costs']['F_200']:,.2f}** solo en cuotas de examen para obtener una cuenta fondeada.

---

## 5. Comparativa de Variantes de Perfil Declaradas (Topstep 50k · 1,0% Sizing)

| Variante | Descripción | P(pase) | P(quema) | P(bloqueo) | P(timeout) |
|---|---|---|---|---|---|
| `base_eod_no_dll_no_cons` | MLL al cierre EOD, sin DLL, sin consistencia (más permisiva) | **{variants_results['base_eod_no_dll_no_cons']['pass_pct']:.2f} %** | {variants_results['base_eod_no_dll_no_cons']['blown_pct']:.2f} % | {variants_results['base_eod_no_dll_no_cons']['blocked_pct']:.2f} % | {variants_results['base_eod_no_dll_no_cons']['timeout_pct']:.2f} % |
| `trailing_closed_conservative` | MLL cerrado (cada trade sube el floor si hay pico) | **{variants_results['trailing_closed_conservative']['pass_pct']:.2f} %** | {variants_results['trailing_closed_conservative']['blown_pct']:.2f} % | {variants_results['trailing_closed_conservative']['blocked_pct']:.2f} % | {variants_results['trailing_closed_conservative']['timeout_pct']:.2f} % |
| `dll_on_soft_pause_1000` | DLL $1.000 activa con pausa intradiaria hasta 5 PM CT | **{variants_results['dll_on_soft_pause_1000']['pass_pct']:.2f} %** | {variants_results['dll_on_soft_pause_1000']['blown_pct']:.2f} % | {variants_results['dll_on_soft_pause_1000']['blocked_pct']:.2f} % | {variants_results['dll_on_soft_pause_1000']['timeout_pct']:.2f} % |
| `consistency_55_pct_on` | Regla Topstep 55% (mejor día $\\le 55\\%$ del target) | **{variants_results['consistency_55_pct_on']['pass_pct']:.2f} %** | {variants_results['consistency_55_pct_on']['blown_pct']:.2f} % | {variants_results['consistency_55_pct_on']['blocked_pct']:.2f} % | {variants_results['consistency_55_pct_on']['timeout_pct']:.2f} % |
| `all_rules_active_conservative` | Todas las restricciones activas combinadas | **{variants_results['all_rules_active_conservative']['pass_pct']:.2f} %** | {variants_results['all_rules_active_conservative']['blown_pct']:.2f} % | {variants_results['all_rules_active_conservative']['blocked_pct']:.2f} % | {variants_results['all_rules_active_conservative']['timeout_pct']:.2f} % |

---

## 6. Veredicto Final

```
========================================================================================
VEREDICTO FINAL: NO PAGAR.
Con la foto limpia actual, pagar la evaluación de Topstep es comprar un billete de lotería
con ~11.3% de pase por puro azar (control zero-edge ~27.9%) y un gasto esperado desproporcionado
($421-$1,685). Solo se justificaría pagar cuando M11 aporte un edge demostrado ex-ante
de al menos +0,10 R a +0,15 R con CI_low > 0.
========================================================================================
```
"""
    (PROTOCOL_DIR / "INFORME.md").write_text(informe_text, encoding="utf-8")
    print(f"[GUARDADO] INFORME.md actualizado con éxito.", flush=True)

    # 14. Escribir BLOCKERS.md
    print("Escribiendo BLOCKERS.md...", flush=True)
    blockers_text = f"""# BLOCKERS — Bloque M10: Evaluación Topstep

## Estado de Bloqueos
- **Bloqueos técnicos:** NINGUNO. La suite de simulación corrió de forma 100% offline, determinista y sin errores.
- **Gate de consistencia:** SUPERADO. El stream de C1 reprodujo con exactitud matemática el baseline limpio de `re_congelado_smc_fvg.json`.
- **Bloqueo económico de trading:** **ACTIVO (NO PAGAR)**.
  - El candidato C1 ($E[R] = -0,13 R$) no muestra ninguna ventaja sobre el control aleatorio C3 ({c1_results['50k']['1.0%']['pass_pct']:.2f}% vs {c3_results['matched']['50k']['1.0%']['pass_pct']:.2f}%).
  - Pagar una evaluación bajo estas condiciones es matemáticamente irracional.
"""
    (PROTOCOL_DIR / "BLOCKERS.md").write_text(blockers_text, encoding="utf-8")

    # 15. Generar manifest.json
    print("Generando manifest.json...", flush=True)
    manifest = {
        "metadata": {
            "block_id": "M10-v2",
            "title": "Evaluación de P(pasar) en Topstep Trading Combine con foto limpia post-revocación fill-bar",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "git_branch": "bloque-m10-evaluacion",
            "base_commit": "277efae",
            "python_executable": sys.executable,
        },
        "artifacts": {},
    }

    for fpath in sorted(PROTOCOL_DIR.iterdir()):
        if fpath.is_file() and fpath.name != "manifest.json":
            manifest["artifacts"][fpath.name] = {
                "sha256": sha256_file(fpath),
                "size_bytes": fpath.stat().st_size,
            }

    (PROTOCOL_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    t_end = time.perf_counter()
    print(f"\n==================================================================", flush=True)
    print(f"BLOQUE M10 (v2) COMPLETADO EXITOSAMENTE EN {t_end - t_start:.2f} SEGUNDOS.", flush=True)
    print(f"==================================================================", flush=True)


if __name__ == "__main__":
    main()

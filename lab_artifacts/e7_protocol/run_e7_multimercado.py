"""FARS E7 — Validacion multi-mercado MYM/MGC + cuenta combinada (post-E5, post-E3).

Stage 1: carga MYM/MGC M5 desde D:\\fars move\\FARS con corte canonico 2019-05-06.
Stage 2: evaluacion C2 por mercado (canonico + por_tramo) para SMC-FVG 5.0/8.0/10.0.
Stage 3: pool combinado MNQ+MYM+MGC y motor de cuenta con 1/2/3 mercados.

Nota honesta: MAEs de MNQ = M1 causal (databento.zip); MYM/MGC = fallback M5
conservador (no hay M1 de esos mercados en disco) — queda declarado por trade.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "E:/FARS-LAB/FARS")
from lab_artifacts.run_c1_walkforward import (  # noqa: E402
    CALIBRATION_BARS_COUNT,
    audit_fold_overlap_and_embargo,
    evaluate_scenario,
)
from lab_artifacts.run_d_protocol import (  # noqa: E402
    MNQ,
    ZIP_PATH,
    SmcFvgStrategy,
    WalkForwardPlan,
    AccountEngineConfig,
    apex_profile,
    extract_m1_bars_and_compute_maes,
    load_canonical_m5,
    run_account_monte_carlo,
    run_backtest,
    smc_fvg_config,
)
from src.account_policy import SizingPolicyConfig  # noqa: E402
from src.backtest.history import Bar  # noqa: E402
from src.backtest.mae import compute_trade_mae  # noqa: E402
from src.backtest.markets import MGC, MYM  # noqa: E402

BASE = Path(__file__).resolve().parent
RAW_DIR = Path("D:/fars move/FARS")
CUT = datetime(2019, 5, 6, tzinfo=timezone.utc)
POR_TRAMO = dict(commission_per_side=0.62, slippage_points=0.25, time_exit_slippage_points=0.25)
CANONICO = dict(commission_per_side=2.0, slippage_points=0.0, time_exit_slippage_points=0.0)
CONFIGS = {"smc_fvg_risk_5": 5.0, "smc_fvg_baseline": 8.0, "smc_fvg_risk_10": 10.0}
SEED, NS = 20260729, 2000


def load_market_csv(symbol: str, path: Path) -> list[Bar]:
    bars: list[Bar] = []
    prev = None
    with path.open("r", encoding="utf-8") as fh:
        fh.readline()  # header
        for line in fh:
            parts = line.strip().split(",")
            ts = datetime.fromisoformat(parts[0].replace("Z", "+00:00"))
            if ts < CUT:
                continue
            if prev is not None and ts <= prev:
                continue  # tolerancia: timestamp fuera de orden se descarta
            prev = ts
            o, h, l, c, v = (float(x) for x in parts[1:6])
            bars.append(Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=v))
    return bars


def gen_trades(bars: list[Bar], market, min_risk: float) -> list:
    plan = WalkForwardPlan.create_calendar_rolling(bars, train_months=36, test_months=6, step_months=6)
    cfg = smc_fvg_config(market=market, discrete_partial_contracts=True, initial_balance=50000.0, **POR_TRAMO)
    trades = []
    for fold in plan.folds:
        cal = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT):fold.test_start_idx]
        test_bars = bars[fold.test_start_idx:fold.test_end_idx]
        res = run_backtest(test_bars, SmcFvgStrategy(min_risk_pts=min_risk), cfg, calibration_bars=cal)
        for t in res.trades:
            trades.append(dataclasses.replace(t, trade_id=f"fold_{fold.fold_id}_{t.trade_id}"))
    return trades


def build_maes(trades: list, bars: list[Bar], dpp: float, prefix: str) -> dict:
    by_date: dict[str, list[Bar]] = {}
    for b in bars:
        by_date.setdefault(b.timestamp.strftime("%Y-%m-%d"), []).append(b)
    out = {}
    for t in trades:
        day = t.entry_time.strftime("%Y-%m-%d")
        out[f"{prefix}_{t.trade_id}"] = compute_trade_mae(
            t, m1_bars=None, dollar_per_point=dpp, m5_fallback_bars=by_date.get(day, [])
        )
    return out


def evaluate_market(symbol: str, market, bars: list[Bar]) -> dict:
    print(f"\n[{symbol}] {len(bars)} barras post-corte — evaluando SMC-FVG...", flush=True)
    plan = WalkForwardPlan.create_calendar_rolling(
        bars, train_months=36, test_months=6, step_months=6, purge_gap_bars=0, warmup_bars=CALIBRATION_BARS_COUNT
    )
    ts_map = {b.timestamp: i for i, b in enumerate(bars)}
    out = {}
    for name, min_risk in CONFIGS.items():
        factory = lambda: SmcFvgStrategy(min_risk_pts=min_risk)  # noqa: E731 (la auditoria y evaluate_scenario piden factory)
        c_cfg = smc_fvg_config(market=market, discrete_partial_contracts=True, time_exit_mode="market", **CANONICO)
        p_cfg = smc_fvg_config(market=market, discrete_partial_contracts=True, time_exit_mode="market", **POR_TRAMO)
        audits = {f.fold_id: audit_fold_overlap_and_embargo(factory, c_cfg, f, bars, ts_map) for f in plan.folds}
        sequence: list = []
        r_canonico = evaluate_scenario(name, "canonico", factory, c_cfg, bars, plan, audits, sequence,
                                       accounting_cfg=c_cfg, entry_order_type="limit")
        r_por = evaluate_scenario(name, "por_tramo", factory, p_cfg, bars, plan,
                                  {f["fold_id"]: f for f in r_canonico["folds"]}, sequence,
                                  accounting_cfg=p_cfg, entry_order_type="limit")
        a = r_por["aggregate_oos"]
        g = r_por["gates_evaluation"]
        print(f"  {name}: trades={a['total_trades']} E[R]={a['global_expectancy_r']:+.4f} "
              f"PF={a['global_profit_factor']:.3f} WR={a['global_win_rate']*100:.1f}% "
              f"DD={a['global_max_drawdown_r']:.1f}R folds+={a['positive_folds_ratio']*100:.0f}% "
              f"verdict={g['verdict']}", flush=True)
        out[name] = {"canonico": r_canonico, "por_tramo": r_por}
    return out


def main() -> None:
    started = datetime.now(timezone.utc).isoformat()
    result = {"metadata": {"title": "E7 multi-mercado", "ran_at_utc": started}, "markets": {}, "account": {}}

    # Stage 1+2: per market
    for symbol, market, fname in (("MYM", MYM, "MYM_M5.csv"), ("MGC", MGC, "MGC_M5.csv")):
        bars = load_market_csv(symbol, RAW_DIR / fname)
        result["markets"][symbol] = evaluate_market(symbol, market, bars)
        (BASE / f"e7_{symbol}_bars_count.txt").write_text(f"{len(bars)}\n", encoding="utf-8")

    # Stage 3: pool combinado + motor de cuenta
    print("\n[cuenta combinada] generando pools y MAEs...", flush=True)
    mnq_bars, _ = load_canonical_m5(ZIP_PATH)
    pools = {}
    for label, mr in (("risk_5", 5.0), ("risk_10", 10.0)):
        mnq = gen_trades(mnq_bars, MNQ, mr)
        mae = extract_m1_bars_and_compute_maes(mnq, ZIP_PATH)
        pool = list(mnq)
        pool_mgc_only = list(mnq)
        for symbol, market, fname in (("MYM", MYM, "MYM_M5.csv"), ("MGC", MGC, "MGC_M5.csv")):
            mbars = load_market_csv(symbol, RAW_DIR / fname)
            mt = gen_trades(mbars, market, mr)
            for t in mt:
                pool.append(dataclasses.replace(t, trade_id=f"{symbol}_{t.trade_id}"))
                if symbol == "MGC":
                    pool_mgc_only.append(dataclasses.replace(t, trade_id=f"{symbol}_{t.trade_id}"))
            mae.update(build_maes(mt, mbars, market.dollar_per_point, symbol))
        pools[label] = {"full": (pool, mae), "mnq_mgc": (pool_mgc_only, mae)}
        print(f"  pool {label}: full={len(pool)} trades, mnq+mgc={len(pool_mgc_only)}, MAEs {len(mae)}", flush=True)

    prof = apex_profile("25k", cadence="intraday_event")
    for label, variants in pools.items():
        for vlabel, (pool, mae) in variants.items():
            for pcfg in (SizingPolicyConfig(kind="fixed"),
                         SizingPolicyConfig(kind="buffer_prop", param=0.10),
                         SizingPolicyConfig(kind="buffer_prop", param=0.10, streak_threshold=2, streak_factor=0.5)):
                cfg = AccountEngineConfig(risk_pct=0.004671, trailing_mode="intraday_event",
                                          horizon_calendar_days=30, policy=pcfg)
                mc = run_account_monte_carlo(prof, pool, cfg, n_simulations=NS, seed=SEED, precomputed_maes=mae)
                row = {"set": label, "pool": vlabel, "policy": (pcfg.kind + ("_streak" if pcfg.streak_threshold else "")),
                       "pass": round(mc.pass_rate, 4), "blown": round(mc.blown_rate, 4),
                       "blocked": round(mc.blocked_rate, 4), "timeout": round(mc.timeout_rate, 4),
                       "days": mc.median_days_to_pass, "trades_per_day": mc.trades_per_day}
                result["account"].setdefault(label, []).append(row)
                print(f"  [{label}/{vlabel}] {row['policy']:<18} pase {row['pass']*100:6.2f}%  "
                      f"quema {row['blown']*100:6.2f}%  bloq {row['blocked']*100:6.2f}%  "
                      f"dias {row['days']}  t/dia {row['trades_per_day']:.2f}", flush=True)

    (BASE / "resultados.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(f"\n[escrito] {BASE / 'resultados.json'}")


if __name__ == "__main__":
    main()

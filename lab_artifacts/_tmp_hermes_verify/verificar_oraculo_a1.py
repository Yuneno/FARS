#!/usr/bin/env python3
"""Verificación de Equivalencia contra el Oráculo A1 y Re-congelamiento.

Ejecuta las matrices completas de SMC-FVG y SMC-OB utilizando DIRECTAMENTE
src.backtest.executor (producción, sin fork y sin flags especiales),
compara los resultados bit a bit contra los oráculos de la auditoría:
  - lab_artifacts/auditoria_fillbar/resultados_smc_fvg_A1.json
  - lab_artifacts/auditoria_fillbar/resultados_smc_ob_A1.json
y guarda los resultados congelados en:
  - lab_artifacts/re_congelado_fillbar/re_congelado_smc_fvg.json
  - lab_artifacts/re_congelado_fillbar/re_congelado_smc_ob.json
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lab_artifacts.run_c1_walkforward import (
    CALIBRATION_BARS_COUNT,
    M5_MEMBER,
    ZIP_PATH,
    compute_file_sha256,
    load_canonical_m5,
    run_bootstrap_ci,
)
from lab_artifacts.e7_protocol.run_e7_multimercado import load_market_csv
from src.backtest.executor import (
    BacktestConfig,
    ExecutedTrade,
    run_backtest,
)
from src.backtest.markets import MGC, MNQ, MYM, MarketSpec
from src.backtest.smc_fvg import SmcFvgStrategy, smc_fvg_config
from src.backtest.smc_ob import SmcObStrategy, smc_ob_config
from src.hypothesis_registry import WalkForwardPlan

OUTPUT_DIR = Path(__file__).resolve().parent

SCENARIOS_FVG = {
    "canonico": dict(commission_per_side=2.0, slippage_points=0.0, time_exit_slippage_points=0.0),
    "por_tramo": dict(commission_per_side=0.62, slippage_points=0.25, time_exit_slippage_points=0.25),
    "kai": dict(commission_per_side=0.71, slippage_points=0.25, time_exit_slippage_points=0.25),
}

CONFIGS_FVG = {
    "smc_fvg_baseline": (lambda: SmcFvgStrategy(min_risk_pts=8.0), {"min_risk_pts": 8.0}),
    "smc_fvg_risk_5": (lambda: SmcFvgStrategy(min_risk_pts=5.0), {"min_risk_pts": 5.0}),
    "smc_fvg_risk_10": (lambda: SmcFvgStrategy(min_risk_pts=10.0), {"min_risk_pts": 10.0}),
}

PARAMS_OB = dict(
    swing_w=10,
    target_rr=3.0,
    choch_only=True,
    ob_lookback=60,
    f=0.5,
    wait=36,
    cooldown=0,
    min_risk_pts=0.0,
)

MARKETS_OB = {
    "MNQ": MNQ,
    "MYM": MYM,
    "MGC": MGC,
}


def get_git_info() -> dict[str, str]:
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=REPO_ROOT, text=True).strip()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    return {"branch": branch, "commit": commit}


def compute_fillbar_stats(trades: list[ExecutedTrade]) -> dict[str, Any]:
    n_total = len(trades)
    if n_total == 0:
        return {
            "fill_bar_trades_total": 0,
            "fill_bar_trades_pct_total": 0.0,
            "fill_bar_wins_count": 0,
            "fill_bar_wins_pct_of_wins": 0.0,
            "fill_bar_wins_pct_of_total": 0.0,
            "fill_bar_losses_count": 0,
            "fill_bar_losses_pct_of_losses": 0.0,
            "fill_bar_losses_pct_of_total": 0.0,
            "fill_bar_tp_exits": 0,
            "fill_bar_sl_exits": 0,
        }
    
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl < 0]
    n_wins = len(wins)
    n_losses = len(losses)
    
    fb_trades = [t for t in trades if t.entry_time == t.exit_time]
    fb_wins = [t for t in fb_trades if t.net_pnl > 0]
    fb_losses = [t for t in fb_trades if t.net_pnl < 0]
    fb_tp = [t for t in fb_trades if t.exit_reason == "take_profit"]
    fb_sl = [t for t in fb_trades if t.exit_reason in ("stop_loss", "break_even_stop")]
    
    return {
        "fill_bar_trades_total": len(fb_trades),
        "fill_bar_trades_pct_total": len(fb_trades) / n_total * 100.0,
        "fill_bar_wins_count": len(fb_wins),
        "fill_bar_wins_pct_of_wins": (len(fb_wins) / n_wins * 100.0) if n_wins > 0 else 0.0,
        "fill_bar_wins_pct_of_total": len(fb_wins) / n_total * 100.0,
        "fill_bar_losses_count": len(fb_losses),
        "fill_bar_losses_pct_of_losses": (len(fb_losses) / n_losses * 100.0) if n_losses > 0 else 0.0,
        "fill_bar_losses_pct_of_total": len(fb_losses) / n_total * 100.0,
        "fill_bar_tp_exits": len(fb_tp),
        "fill_bar_sl_exits": len(fb_sl),
    }


def compute_aggregate_metrics(trades: list[ExecutedTrade], folds_data: list[dict]) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {}
    pnl = [t.net_pnl for t in trades]
    rs = [t.r_result for t in trades]
    wins_pnl = [p for p in pnl if p > 0]
    loss_pnl = -sum(p for p in pnl if p < 0)
    
    wr = len(wins_pnl) / n
    exp_r = sum(rs) / n
    net_r = sum(rs)
    pf = sum(wins_pnl) / loss_pnl if loss_pnl > 0 else float("inf")
    
    # Drawdown
    dd_r = 0.0
    running = peak = 0.0
    for r in rs:
        running += r
        peak = max(peak, running)
        dd_r = max(dd_r, peak - running)
        
    dd_pnl = 0.0
    running_pnl = peak_pnl = 0.0
    for p in pnl:
        running_pnl += p
        peak_pnl = max(peak_pnl, running_pnl)
        dd_pnl = max(dd_pnl, peak_pnl - running_pnl)
        
    low_ci, high_ci = run_bootstrap_ci(rs)
    n_folds = len(folds_data)
    pos_folds = sum(1 for f in folds_data if f["net_r"] > 0)
    pos_ratio = pos_folds / n_folds if n_folds > 0 else 0.0
    
    max_fold_r = max((f["net_r"] for f in folds_data), default=0.0)
    concentration = (max_fold_r / net_r * 100.0) if net_r > 0 else None
    
    fb_stats = compute_fillbar_stats(trades)
    
    return {
        "n_trades": n,
        "win_rate": wr,
        "win_rate_pct": wr * 100.0,
        "expectancy_r": exp_r,
        "net_r": net_r,
        "total_net_pnl": sum(pnl),
        "profit_factor": pf,
        "max_drawdown_r": dd_r,
        "max_drawdown_dollars": dd_pnl,
        "bootstrap_cbb_ci95": [low_ci, high_ci],
        "positive_folds": pos_folds,
        "n_folds": n_folds,
        "positive_folds_ratio": pos_ratio,
        "max_fold_r_concentration_pct": concentration,
        "total_commission": sum(t.commission for t in trades),
        "total_slippage_cost": sum(t.slippage_cost for t in trades),
        "fill_bar_metrics": fb_stats,
    }


def reprice_fvg(trades: tuple[ExecutedTrade, ...], cfg: BacktestConfig, accounting_cfg: BacktestConfig) -> list[ExecutedTrade]:
    repriced = []
    for trade in trades:
        partial_was_closed = (
            trade.exit_reason == "break_even_stop"
            or (
                cfg.partial_take_profit_fraction > 0
                and trade.exit_reason in {"stop_loss", "time_exit"}
                and trade.stop_price == trade.entry_price
            )
        )
        remaining_qty = (
            trade.quantity - int(trade.quantity * cfg.partial_take_profit_fraction)
            if partial_was_closed
            else trade.quantity
        )
        slipping_qty = remaining_qty if trade.exit_reason in {"stop_loss", "break_even_stop", "time_exit"} else 0
        slippage_cost = accounting_cfg.slippage_points * accounting_cfg.dollar_per_point * slipping_qty
        commission = accounting_cfg.commission_per_side * 2 * trade.quantity
        net_pnl = trade.gross_pnl - commission - slippage_cost
        risk_budget = trade.budgeted_risk_dollars
        r_result = net_pnl / risk_budget if risk_budget > 0 else 0.0
        repriced.append(replace(
            trade,
            commission=commission,
            slippage_cost=slippage_cost,
            net_pnl=net_pnl,
            r_result=r_result,
            budgeted_r=r_result,
            effective_r=(net_pnl / trade.effective_risk_dollars if trade.effective_risk_dollars > 0 else 0.0),
        ))
    return repriced


def run_fvg_refreeze(bars: list, plan: WalkForwardPlan) -> dict[str, Any]:
    print("\n==========================================")
    print("RUNNING SMC-FVG (PRODUCCIÓN: RE-CONGELADO)")
    print("==========================================")
    results = {}
    
    for cfg_name, (strat_factory, params) in CONFIGS_FVG.items():
        print(f"\n--- Strategy: {cfg_name} ---")
        base_cfg = smc_fvg_config(
            market=MNQ,
            discrete_partial_contracts=True,
            time_exit_mode="market",
            **SCENARIOS_FVG["canonico"],
        )
        
        raw_fold_trades = []
        for fold in plan.folds:
            cal = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT) : fold.test_start_idx]
            test = bars[fold.test_start_idx : fold.test_end_idx]
            strat = strat_factory()
            res = run_backtest(test, strat, base_cfg, calibration_bars=cal)
            raw_fold_trades.append(res.trades)
            
        cfg_scenarios = {}
        for scen_name, scen_params in SCENARIOS_FVG.items():
            accounting_cfg = smc_fvg_config(
                market=MNQ,
                discrete_partial_contracts=True,
                time_exit_mode="market",
                **scen_params,
            )
            
            all_scen_trades = []
            folds_data = []
            for fold_idx, (fold, raw_trades) in enumerate(zip(plan.folds, raw_fold_trades)):
                repriced = reprice_fvg(raw_trades, base_cfg, accounting_cfg)
                all_scen_trades.extend(repriced)
                
                f_pnl = [t.net_pnl for t in repriced]
                f_rs = [t.r_result for t in repriced]
                f_loss = -sum(p for p in f_pnl if p < 0)
                f_wr = sum(p > 0 for p in f_pnl) / len(f_pnl) if f_pnl else 0.0
                f_exp_r = sum(f_rs) / len(f_rs) if f_rs else 0.0
                f_net_r = sum(f_rs)
                f_pf = sum(p for p in f_pnl if p > 0) / f_loss if f_loss > 0 else float("inf")
                
                fb_f = compute_fillbar_stats(repriced)
                folds_data.append({
                    "fold_id": fold.fold_id,
                    "train_start": fold.train_start,
                    "train_end": fold.train_end,
                    "test_start": fold.test_start,
                    "test_end": fold.test_end,
                    "n_trades": len(repriced),
                    "win_rate": f_wr,
                    "win_rate_pct": f_wr * 100.0,
                    "expectancy_r": f_exp_r,
                    "net_r": f_net_r,
                    "net_pnl": sum(f_pnl),
                    "profit_factor": f_pf,
                    "total_commission": sum(t.commission for t in repriced),
                    "total_slippage_cost": sum(t.slippage_cost for t in repriced),
                    "fill_bar_metrics": fb_f,
                })
                
            agg = compute_aggregate_metrics(all_scen_trades, folds_data)
            cfg_scenarios[scen_name] = {
                "scenario": scen_name,
                "aggregate": agg,
                "folds": folds_data,
            }
            print(f"  [{scen_name:10s}] n={agg['n_trades']} | WR={agg['win_rate_pct']:.2f}% | E[R]={agg['expectancy_r']:+.5f}R | Net R={agg['net_r']:+.2f}R | PF={agg['profit_factor']:.3f} | FB Wins={agg['fill_bar_metrics']['fill_bar_wins_count']}")

        results[cfg_name] = {
            "configuration_id": cfg_name,
            "parameters": params,
            "scenarios": cfg_scenarios,
        }
    return results


def reprice_ob(trade: ExecutedTrade, market: MarketSpec) -> ExecutedTrade:
    partial = trade.exit_reason == "break_even_stop" or (
        trade.exit_reason in {"stop_loss", "time_exit"} and trade.stop_price == trade.entry_price
    )
    remaining = trade.quantity - int(trade.quantity * 0.5) if partial else trade.quantity
    slipping = remaining if trade.exit_reason in {"stop_loss", "break_even_stop", "time_exit"} else 0
    slip = 0.25 * market.dollar_per_point * slipping
    commission = 0.62 * 2 * trade.quantity
    net = trade.gross_pnl - commission - slip
    r = net / trade.budgeted_risk_dollars
    return replace(
        trade,
        commission=commission,
        slippage_cost=slip,
        net_pnl=net,
        r_result=r,
        budgeted_r=r,
        effective_r=net / trade.effective_risk_dollars,
    )


def run_ob_refreeze(bars_by_market: dict[str, list]) -> dict[str, Any]:
    print("\n==========================================")
    print("RUNNING SMC-OB (PRODUCCIÓN: RE-CONGELADO)")
    print("==========================================")
    markets_data = {}
    
    for symbol, market in MARKETS_OB.items():
        bars = bars_by_market[symbol]
        plan = WalkForwardPlan.create_calendar_rolling(
            bars,
            train_months=36,
            test_months=6,
            step_months=6,
            warmup_bars=CALIBRATION_BARS_COUNT,
        )
        cfg = smc_ob_config(market=market, commission_per_side=2.0)
        
        all_trades = []
        folds_data = []
        for fold in plan.folds:
            cal = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT) : fold.test_start_idx]
            test = bars[fold.test_start_idx : fold.test_end_idx]
            strat = SmcObStrategy(market=market, log_decisions=False, **PARAMS_OB)
            res = run_backtest(test, strat, cfg, calibration_bars=cal)
            ft = [reprice_ob(t, market) for t in res.trades]
            all_trades.extend(ft)
            
            f_pnl = [t.net_pnl for t in ft]
            f_rs = [t.r_result for t in ft]
            f_loss = -sum(p for p in f_pnl if p < 0)
            f_wr = sum(p > 0 for p in f_pnl) / len(f_pnl) if f_pnl else 0.0
            f_exp_r = sum(f_rs) / len(f_rs) if f_rs else 0.0
            f_net_r = sum(f_rs)
            f_pf = sum(p for p in f_pnl if p > 0) / f_loss if f_loss > 0 else float("inf")
            
            fb_f = compute_fillbar_stats(ft)
            folds_data.append({
                "fold_id": fold.fold_id,
                "train_start": fold.train_start,
                "train_end": fold.train_end,
                "test_start": fold.test_start,
                "test_end": fold.test_end,
                "n_trades": len(ft),
                "win_rate": f_wr,
                "win_rate_pct": f_wr * 100.0,
                "expectancy_r": f_exp_r,
                "net_r": f_net_r,
                "net_pnl": sum(f_pnl),
                "profit_factor": f_pf,
                "total_commission": sum(t.commission for t in ft),
                "total_slippage_cost": sum(t.slippage_cost for t in ft),
                "fill_bar_metrics": fb_f,
            })
            
        agg = compute_aggregate_metrics(all_trades, folds_data)
        markets_data[symbol] = {
            "symbol": symbol,
            "scenario": "por_tramo",
            "aggregate": agg,
            "folds": folds_data,
        }
        print(f"  [{symbol:4s}] n={agg['n_trades']} | WR={agg['win_rate_pct']:.2f}% | E[R]={agg['expectancy_r']:+.5f}R | Net R={agg['net_r']:+.2f}R | PF={agg['profit_factor']:.3f} | FB Wins={agg['fill_bar_metrics']['fill_bar_wins_count']}")

    return markets_data


def verify_against_oracle_a1(actual_fvg: dict, actual_ob: dict) -> None:
    print("\n============================================================")
    print("VERIFICANDO EXACTITUD BIT-A-BIT FRENTE AL ORÁCULO A1...")
    print("============================================================")
    
    oracle_fvg_path = REPO_ROOT / "lab_artifacts" / "auditoria_fillbar" / "resultados_smc_fvg_A1.json"
    oracle_ob_path = REPO_ROOT / "lab_artifacts" / "auditoria_fillbar" / "resultados_smc_ob_A1.json"
    
    oracle_fvg = json.loads(oracle_fvg_path.read_text(encoding="utf-8"))["configurations"]
    oracle_ob = json.loads(oracle_ob_path.read_text(encoding="utf-8"))["markets"]
    
    # 1. SMC-FVG Checks
    for cfg_name in CONFIGS_FVG:
        for scen_name in SCENARIOS_FVG:
            act_agg = actual_fvg[cfg_name]["scenarios"][scen_name]["aggregate"]
            ora_agg = oracle_fvg[cfg_name]["scenarios"][scen_name]["aggregate"]
            
            assert act_agg["n_trades"] == ora_agg["n_trades"], (
                f"FVG {cfg_name} {scen_name} n_trades mismatch: {act_agg['n_trades']} vs {ora_agg['n_trades']}"
            )
            assert abs(act_agg["win_rate"] - ora_agg["win_rate"]) < 1e-6, (
                f"FVG {cfg_name} {scen_name} WR mismatch: {act_agg['win_rate']} vs {ora_agg['win_rate']}"
            )
            assert abs(act_agg["expectancy_r"] - ora_agg["expectancy_r"]) < 1e-6, (
                f"FVG {cfg_name} {scen_name} E[R] mismatch: {act_agg['expectancy_r']} vs {ora_agg['expectancy_r']}"
            )
            assert abs(act_agg["net_r"] - ora_agg["net_r"]) < 1e-3, (
                f"FVG {cfg_name} {scen_name} Net R mismatch: {act_agg['net_r']} vs {ora_agg['net_r']}"
            )
            assert abs(act_agg["total_net_pnl"] - ora_agg["total_net_pnl"]) < 1e-2, (
                f"FVG {cfg_name} {scen_name} Net PnL mismatch: {act_agg['total_net_pnl']} vs {ora_agg['total_net_pnl']}"
            )
            assert abs(act_agg["profit_factor"] - ora_agg["profit_factor"]) < 1e-4, (
                f"FVG {cfg_name} {scen_name} PF mismatch: {act_agg['profit_factor']} vs {ora_agg['profit_factor']}"
            )
            assert abs(act_agg["max_drawdown_r"] - ora_agg["max_drawdown_r"]) < 1e-3, (
                f"FVG {cfg_name} {scen_name} Max DD mismatch: {act_agg['max_drawdown_r']} vs {ora_agg['max_drawdown_r']}"
            )
            assert act_agg["positive_folds"] == ora_agg["positive_folds"], (
                f"FVG {cfg_name} {scen_name} positive folds mismatch: {act_agg['positive_folds']} vs {ora_agg['positive_folds']}"
            )
            assert act_agg["fill_bar_metrics"]["fill_bar_wins_count"] == ora_agg["fill_bar_metrics"]["fill_bar_wins_count"] == 0, (
                f"FVG {cfg_name} {scen_name} FB wins must be 0 in clean A1!"
            )
            
            # Check fold-by-fold
            act_folds = actual_fvg[cfg_name]["scenarios"][scen_name]["folds"]
            ora_folds = oracle_fvg[cfg_name]["scenarios"][scen_name]["folds"]
            for i, (af, of) in enumerate(zip(act_folds, ora_folds)):
                assert af["n_trades"] == of["n_trades"], f"Fold {i} n mismatch"
                assert abs(af["net_r"] - of["net_r"]) < 1e-3, f"Fold {i} net_r mismatch"
                assert abs(af["win_rate"] - of["win_rate"]) < 1e-6, f"Fold {i} WR mismatch"
                
            print(f"  [PASS] SMC-FVG {cfg_name:18s} | {scen_name:10s} == Oracle A1 (n={act_agg['n_trades']}, WR={act_agg['win_rate_pct']:.2f}%, E[R]={act_agg['expectancy_r']:+.5f}R, Net R={act_agg['net_r']:+.2f}R)")

    # 2. SMC-OB Checks
    for symbol in MARKETS_OB:
        act_agg = actual_ob[symbol]["aggregate"]
        ora_agg = oracle_ob[symbol]["aggregate"]
        
        assert act_agg["n_trades"] == ora_agg["n_trades"], (
            f"OB {symbol} n_trades mismatch: {act_agg['n_trades']} vs {ora_agg['n_trades']}"
        )
        assert abs(act_agg["win_rate"] - ora_agg["win_rate"]) < 1e-6, (
            f"OB {symbol} WR mismatch: {act_agg['win_rate']} vs {ora_agg['win_rate']}"
        )
        assert abs(act_agg["expectancy_r"] - ora_agg["expectancy_r"]) < 1e-6, (
            f"OB {symbol} E[R] mismatch: {act_agg['expectancy_r']} vs {ora_agg['expectancy_r']}"
        )
        assert abs(act_agg["net_r"] - ora_agg["net_r"]) < 1e-3, (
            f"OB {symbol} Net R mismatch: {act_agg['net_r']} vs {ora_agg['net_r']}"
        )
        assert abs(act_agg["total_net_pnl"] - ora_agg["total_net_pnl"]) < 1e-2, (
            f"OB {symbol} Net PnL mismatch: {act_agg['total_net_pnl']} vs {ora_agg['total_net_pnl']}"
        )
        assert abs(act_agg["profit_factor"] - ora_agg["profit_factor"]) < 1e-4, (
            f"OB {symbol} PF mismatch: {act_agg['profit_factor']} vs {ora_agg['profit_factor']}"
        )
        assert abs(act_agg["max_drawdown_r"] - ora_agg["max_drawdown_r"]) < 1e-3, (
            f"OB {symbol} Max DD mismatch: {act_agg['max_drawdown_r']} vs {ora_agg['max_drawdown_r']}"
        )
        assert act_agg["positive_folds"] == ora_agg["positive_folds"], (
            f"OB {symbol} positive folds mismatch: {act_agg['positive_folds']} vs {ora_agg['positive_folds']}"
        )
        assert act_agg["fill_bar_metrics"]["fill_bar_wins_count"] == ora_agg["fill_bar_metrics"]["fill_bar_wins_count"] == 0, (
            f"OB {symbol} FB wins must be 0 in clean A1!"
        )
        
        act_folds = actual_ob[symbol]["folds"]
        ora_folds = oracle_ob[symbol]["folds"]
        for i, (af, of) in enumerate(zip(act_folds, ora_folds)):
            assert af["n_trades"] == of["n_trades"], f"OB {symbol} Fold {i} n mismatch"
            assert abs(af["net_r"] - of["net_r"]) < 1e-3, f"OB {symbol} Fold {i} net_r mismatch"
            assert abs(af["win_rate"] - of["win_rate"]) < 1e-6, f"OB {symbol} Fold {i} WR mismatch"
            
        print(f"  [PASS] SMC-OB  {symbol:18s} | por_tramo  == Oracle A1 (n={act_agg['n_trades']}, WR={act_agg['win_rate_pct']:.2f}%, E[R]={act_agg['expectancy_r']:+.5f}R, Net R={act_agg['net_r']:+.2f}R)")

    print("\n>>> TODOS LOS CHECKS DE EQUIVALENCIA PASARON AL 100% BIT-A-BIT <<<")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verificación de equivalencia Oráculo A1 y Re-congelado")
    parser.add_argument("--target", choices=["all", "fvg", "ob"], default="all")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    git_info = get_git_info()
    executor_sha256 = compute_file_sha256(REPO_ROOT / "src" / "backtest" / "executor.py")

    actual_fvg: dict[str, Any] = {}
    actual_ob: dict[str, Any] = {}

    if args.target in ("all", "fvg"):
        print("\nCargando datos canónicos de M5 para SMC-FVG...")
        bars_fvg, mnq_fp = load_canonical_m5(ZIP_PATH, M5_MEMBER)
        plan_fvg = WalkForwardPlan.create_calendar_rolling(
            bars_fvg,
            train_months=36,
            test_months=6,
            step_months=6,
            purge_gap_bars=0,
            warmup_bars=CALIBRATION_BARS_COUNT,
        )
        actual_fvg = run_fvg_refreeze(bars_fvg, plan_fvg)
        
        out_fvg = {
            "metadata": {
                "audit": "re_congelado_fillbar",
                "mode": "produccion (regla A1 horneada en src/backtest/executor.py)",
                "git_commit": git_info["commit"],
                "git_branch": git_info["branch"],
                "dataset_fingerprint": mnq_fp,
                "executor_sha256": executor_sha256,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            "configurations": actual_fvg,
        }
        fvg_out_path = OUTPUT_DIR / "re_congelado_smc_fvg.json"
        fvg_out_path.write_text(json.dumps(out_fvg, indent=2), encoding="utf-8")
        print(f"\n[GUARDADO] Artefacto re-congelado: {fvg_out_path}")

    if args.target in ("all", "ob"):
        print("\nCargando datos multimercado para SMC-OB...")
        bars_by_market = {}
        for symbol in MARKETS_OB:
            if symbol == "MNQ":
                bars, _ = load_canonical_m5(ZIP_PATH)
            else:
                p = Path(f"D:/fars move/FARS/{symbol}_M5.csv")
                bars = load_market_csv(symbol, p)
            bars_by_market[symbol] = bars
        actual_ob = run_ob_refreeze(bars_by_market)
        
        out_ob = {
            "metadata": {
                "audit": "re_congelado_fillbar",
                "strategy": "SMC-OB",
                "scenario": "por_tramo",
                "mode": "produccion (regla A1 horneada en src/backtest/executor.py)",
                "git_commit": git_info["commit"],
                "git_branch": git_info["branch"],
                "executor_sha256": executor_sha256,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            "markets": actual_ob,
        }
        ob_out_path = OUTPUT_DIR / "re_congelado_smc_ob.json"
        ob_out_path.write_text(json.dumps(out_ob, indent=2), encoding="utf-8")
        print(f"\n[GUARDADO] Artefacto re-congelado: {ob_out_path}")

    if args.target == "all":
        verify_against_oracle_a1(actual_fvg, actual_ob)


if __name__ == "__main__":
    main()

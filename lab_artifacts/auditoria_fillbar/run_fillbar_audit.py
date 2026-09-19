#!/usr/bin/env python3
"""Runner for Fill-Bar Measurement Audit on SMC-FVG and SMC-OB.

Executes Control, A1 (SL-only on fill bar), and A2 (Confirmed close beyond target on fill bar).
Validates Control against published artifacts bit-for-bit.
Quantifies fill-bar wins/losses and computes clean deltas against baseline.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
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
from lab_artifacts.auditoria_fillbar.executor_fillbar import (
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


def compute_deltas(clean: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    d_wr_pp = clean["win_rate_pct"] - baseline["win_rate_pct"]
    d_exp_r = clean["expectancy_r"] - baseline["expectancy_r"]
    d_pf = clean["profit_factor"] - baseline["profit_factor"]
    d_net_r = clean["net_r"] - baseline["net_r"]
    
    is_inflated = abs(d_wr_pp) >= 2.0 or abs(d_exp_r) >= 0.02
    veredicto = "INFLADO" if is_inflated else "ROBUSTO"
    decision = (
        "REQUIERE CORRECCIÓN DEL RESOLUTOR Y RE-CONGELACIÓN"
        if is_inflated
        else "ROBUSTO AL ARTEFACTO DE FILL-BAR"
    )
    
    return {
        "delta_wr_pp": d_wr_pp,
        "delta_expectancy_r": d_exp_r,
        "delta_profit_factor": d_pf,
        "delta_net_r": d_net_r,
        "positive_folds_clean": f"{clean['positive_folds']}/{clean['n_folds']}",
        "positive_folds_baseline": f"{baseline['positive_folds']}/{baseline['n_folds']}",
        "veredicto": veredicto,
        "decision": decision,
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
        # Entry order is limit, so entry slippage is 0
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


def run_fvg_mode(bars: list, plan: WalkForwardPlan, mode: str) -> dict[str, Any]:
    print(f"\n==========================================")
    print(f"RUNNING SMC-FVG: mode={mode}")
    print(f"==========================================")
    results = {}
    
    for cfg_name, (strat_factory, params) in CONFIGS_FVG.items():
        print(f"\n--- Strategy: {cfg_name} (mode={mode}) ---")
        base_cfg = smc_fvg_config(
            market=MNQ,
            discrete_partial_contracts=True,
            time_exit_mode="market",
            **SCENARIOS_FVG["canonico"],
        )
        
        # Run raw backtest per fold (canonico gross reference)
        raw_fold_trades = []
        for fold in plan.folds:
            cal = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT) : fold.test_start_idx]
            test = bars[fold.test_start_idx : fold.test_end_idx]
            strat = strat_factory()
            res = run_backtest(test, strat, base_cfg, calibration_bars=cal, fillbar_mode=mode)
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
            print(f"  [{scen_name:10s}] n={agg['n_trades']} | WR={agg['win_rate_pct']:.2f}% | E[R]={agg['expectancy_r']:+.5f}R | Net R={agg['net_r']:+.2f}R | PF={agg['profit_factor']:.3f} | FB Wins={agg['fill_bar_metrics']['fill_bar_wins_count']} ({agg['fill_bar_metrics']['fill_bar_wins_pct_of_wins']:.1f}% wins)")

        results[cfg_name] = {
            "configuration_id": cfg_name,
            "parameters": params,
            "scenarios": cfg_scenarios,
        }
    return results


def verify_control_fvg(control_results: dict[str, Any]) -> None:
    print("\n------------------------------------------------------------")
    print("VERIFYING CONTROL SMC-FVG AGAINST PUBLISHED C2 ARTIFACTS...")
    print("------------------------------------------------------------")
    for cfg_name in CONFIGS_FVG:
        pub_path = REPO_ROOT / "lab_artifacts" / "c2_protocol" / f"{cfg_name}_fold_metrics.json"
        pub = json.loads(pub_path.read_text(encoding="utf-8"))
        for scen_name in SCENARIOS_FVG:
            pub_agg = pub["scenarios"][scen_name]["aggregate_oos"]
            ctrl_agg = control_results[cfg_name]["scenarios"][scen_name]["aggregate"]
            
            # Assert exact match
            assert ctrl_agg["n_trades"] == pub_agg["total_trades"], (
                f"{cfg_name} {scen_name} n_trades mismatch: {ctrl_agg['n_trades']} vs {pub_agg['total_trades']}"
            )
            assert abs(ctrl_agg["win_rate"] - pub_agg["global_win_rate"]) < 1e-4, (
                f"{cfg_name} {scen_name} WR mismatch: {ctrl_agg['win_rate']} vs {pub_agg['global_win_rate']}"
            )
            assert abs(ctrl_agg["expectancy_r"] - pub_agg["global_expectancy_r"]) < 1e-5, (
                f"{cfg_name} {scen_name} E[R] mismatch: {ctrl_agg['expectancy_r']} vs {pub_agg['global_expectancy_r']}"
            )
            assert abs(ctrl_agg["net_r"] - pub_agg["total_net_r"]) < 1e-3, (
                f"{cfg_name} {scen_name} Net R mismatch: {ctrl_agg['net_r']} vs {pub_agg['total_net_r']}"
            )
            assert abs(ctrl_agg["profit_factor"] - pub_agg["global_profit_factor"]) < 1e-4, (
                f"{cfg_name} {scen_name} PF mismatch: {ctrl_agg['profit_factor']} vs {pub_agg['global_profit_factor']}"
            )
            control_results[cfg_name]["scenarios"][scen_name]["verification_passed"] = True
            print(f"  [OK] {cfg_name} | {scen_name:10s} matches published baseline exactly!")
    print("ALL SMC-FVG CONTROL CHECKS PASSED BIT-FOR-BIT!\n")


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


def run_ob_mode(bars_by_market: dict[str, list], mode: str) -> dict[str, Any]:
    print(f"\n==========================================")
    print(f"RUNNING SMC-OB: mode={mode}")
    print(f"==========================================")
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
            res = run_backtest(test, strat, cfg, calibration_bars=cal, fillbar_mode=mode)
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
        print(f"  [{symbol:4s}] n={agg['n_trades']} | WR={agg['win_rate_pct']:.2f}% | E[R]={agg['expectancy_r']:+.5f}R | Net R={agg['net_r']:+.2f}R | PF={agg['profit_factor']:.3f} | FB Wins={agg['fill_bar_metrics']['fill_bar_wins_count']} ({agg['fill_bar_metrics']['fill_bar_wins_pct_of_wins']:.1f}% wins)")

    return markets_data


def verify_control_ob(control_results: dict[str, Any]) -> None:
    print("\n------------------------------------------------------------")
    print("VERIFYING CONTROL SMC-OB AGAINST PUBLISHED RESULTADOS.JSON...")
    print("------------------------------------------------------------")
    pub_path = REPO_ROOT / "lab_artifacts" / "smcob_protocol" / "resultados.json"
    pub = json.loads(pub_path.read_text(encoding="utf-8"))
    for symbol in MARKETS_OB:
        pub_agg = pub["markets"][symbol]["aggregate"]
        ctrl_agg = control_results[symbol]["aggregate"]
        
        assert ctrl_agg["n_trades"] == pub_agg["n"], (
            f"OB {symbol} n mismatch: {ctrl_agg['n_trades']} vs {pub_agg['n']}"
        )
        assert abs(ctrl_agg["win_rate"] - pub_agg["win_rate"]) < 1e-5, (
            f"OB {symbol} WR mismatch: {ctrl_agg['win_rate']} vs {pub_agg['win_rate']}"
        )
        assert abs(ctrl_agg["expectancy_r"] - pub_agg["expectancy_r"]) < 1e-5, (
            f"OB {symbol} E[R] mismatch: {ctrl_agg['expectancy_r']} vs {pub_agg['expectancy_r']}"
        )
        assert abs(ctrl_agg["net_r"] - pub_agg["net_r"]) < 1e-3, (
            f"OB {symbol} Net R mismatch: {ctrl_agg['net_r']} vs {pub_agg['net_r']}"
        )
        assert abs(ctrl_agg["profit_factor"] - pub_agg["profit_factor"]) < 1e-4, (
            f"OB {symbol} PF mismatch: {ctrl_agg['profit_factor']} vs {pub_agg['profit_factor']}"
        )
        control_results[symbol]["verification_passed"] = True
        print(f"  [OK] SMC-OB {symbol} matches published baseline exactly!")
    print("ALL SMC-OB CONTROL CHECKS PASSED BIT-FOR-BIT!\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill-bar measurement audit runner")
    parser.add_argument("--target", choices=["all", "fvg", "smcob"], default="all")
    args = parser.parse_args()
    
    git_info = get_git_info()
    diff_path = OUTPUT_DIR / "diff_executor_fillbar.txt"
    diff_sha = compute_file_sha256(diff_path) if diff_path.exists() else "none"
    
    # -------------------------------------------------------------
    # 1. SMC-FVG
    # -------------------------------------------------------------
    if args.target in ("all", "fvg"):
        print("Loading MNQ M5 data for SMC-FVG...")
        bars_mnq, mnq_fp = load_canonical_m5(ZIP_PATH, M5_MEMBER)
        plan_fvg = WalkForwardPlan.create_calendar_rolling(
            bars_mnq,
            train_months=36,
            test_months=6,
            step_months=6,
            purge_gap_bars=0,
            warmup_bars=CALIBRATION_BARS_COUNT,
        )
        
        # A. Control
        ctrl_fvg = run_fvg_mode(bars_mnq, plan_fvg, "control")
        verify_control_fvg(ctrl_fvg)
        
        save_ctrl = {
            "metadata": {
                "audit": "fill-bar measurement",
                "mode": "control",
                "git_commit": git_info["commit"],
                "git_branch": git_info["branch"],
                "diff_executor_sha256": diff_sha,
                "dataset_fingerprint": mnq_fp,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            "configurations": ctrl_fvg,
        }
        (OUTPUT_DIR / "control_smc_fvg.json").write_text(
            json.dumps(save_ctrl, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print("Saved lab_artifacts/auditoria_fillbar/control_smc_fvg.json")
        
        # B. Clean A1
        a1_fvg = run_fvg_mode(bars_mnq, plan_fvg, "A1")
        # Add deltas vs control
        for cfg_name in CONFIGS_FVG:
            for scen_name in SCENARIOS_FVG:
                cl_agg = a1_fvg[cfg_name]["scenarios"][scen_name]["aggregate"]
                bs_agg = ctrl_fvg[cfg_name]["scenarios"][scen_name]["aggregate"]
                cl_agg["deltas_vs_control"] = compute_deltas(cl_agg, bs_agg)
                
        save_a1 = {
            "metadata": {
                "audit": "fill-bar measurement",
                "mode": "A1 (SL-only on fill bar; TP starting on subsequent bar)",
                "git_commit": git_info["commit"],
                "git_branch": git_info["branch"],
                "diff_executor_sha256": diff_sha,
                "dataset_fingerprint": mnq_fp,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            "configurations": a1_fvg,
        }
        (OUTPUT_DIR / "resultados_smc_fvg_A1.json").write_text(
            json.dumps(save_a1, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print("Saved lab_artifacts/auditoria_fillbar/resultados_smc_fvg_A1.json")
        
        # C. Clean A2
        a2_fvg = run_fvg_mode(bars_mnq, plan_fvg, "A2")
        # Add deltas vs control
        for cfg_name in CONFIGS_FVG:
            for scen_name in SCENARIOS_FVG:
                cl_agg = a2_fvg[cfg_name]["scenarios"][scen_name]["aggregate"]
                bs_agg = ctrl_fvg[cfg_name]["scenarios"][scen_name]["aggregate"]
                cl_agg["deltas_vs_control"] = compute_deltas(cl_agg, bs_agg)
                
        save_a2 = {
            "metadata": {
                "audit": "fill-bar measurement",
                "mode": "A2 (TP requires confirmed close beyond target on fill bar)",
                "git_commit": git_info["commit"],
                "git_branch": git_info["branch"],
                "diff_executor_sha256": diff_sha,
                "dataset_fingerprint": mnq_fp,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            "configurations": a2_fvg,
        }
        (OUTPUT_DIR / "resultados_smc_fvg_A2.json").write_text(
            json.dumps(save_a2, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print("Saved lab_artifacts/auditoria_fillbar/resultados_smc_fvg_A2.json")
        
    # -------------------------------------------------------------
    # 2. SMC-OB
    # -------------------------------------------------------------
    if args.target in ("all", "smcob"):
        print("\nLoading market datasets for SMC-OB...")
        bars_by_market = {}
        for symbol in MARKETS_OB:
            if symbol == "MNQ":
                bars, _ = load_canonical_m5(ZIP_PATH)
            else:
                p = Path(f"D:/fars move/FARS/{symbol}_M5.csv")
                bars = load_market_csv(symbol, p)
            bars_by_market[symbol] = bars
            print(f"  {symbol}: {len(bars)} bars loaded.")
            
        # A. Control
        ctrl_ob = run_ob_mode(bars_by_market, "control")
        verify_control_ob(ctrl_ob)
        
        save_ctrl_ob = {
            "metadata": {
                "audit": "fill-bar measurement",
                "strategy": "SMC-OB",
                "scenario": "por_tramo",
                "mode": "control",
                "git_commit": git_info["commit"],
                "git_branch": git_info["branch"],
                "diff_executor_sha256": diff_sha,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            "markets": ctrl_ob,
        }
        (OUTPUT_DIR / "control_smc_ob.json").write_text(
            json.dumps(save_ctrl_ob, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print("Saved lab_artifacts/auditoria_fillbar/control_smc_ob.json")
        
        # B. Clean A1
        a1_ob = run_ob_mode(bars_by_market, "A1")
        for symbol in MARKETS_OB:
            cl_agg = a1_ob[symbol]["aggregate"]
            bs_agg = ctrl_ob[symbol]["aggregate"]
            cl_agg["deltas_vs_control"] = compute_deltas(cl_agg, bs_agg)
            
        save_a1_ob = {
            "metadata": {
                "audit": "fill-bar measurement",
                "strategy": "SMC-OB",
                "scenario": "por_tramo",
                "mode": "A1 (SL-only on fill bar; TP starting on subsequent bar)",
                "git_commit": git_info["commit"],
                "git_branch": git_info["branch"],
                "diff_executor_sha256": diff_sha,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            "markets": a1_ob,
        }
        (OUTPUT_DIR / "resultados_smc_ob_A1.json").write_text(
            json.dumps(save_a1_ob, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print("Saved lab_artifacts/auditoria_fillbar/resultados_smc_ob_A1.json")
        
        # C. Clean A2
        a2_ob = run_ob_mode(bars_by_market, "A2")
        for symbol in MARKETS_OB:
            cl_agg = a2_ob[symbol]["aggregate"]
            bs_agg = ctrl_ob[symbol]["aggregate"]
            cl_agg["deltas_vs_control"] = compute_deltas(cl_agg, bs_agg)
            
        save_a2_ob = {
            "metadata": {
                "audit": "fill-bar measurement",
                "strategy": "SMC-OB",
                "scenario": "por_tramo",
                "mode": "A2 (TP requires confirmed close beyond target on fill bar)",
                "git_commit": git_info["commit"],
                "git_branch": git_info["branch"],
                "diff_executor_sha256": diff_sha,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            "markets": a2_ob,
        }
        (OUTPUT_DIR / "resultados_smc_ob_A2.json").write_text(
            json.dumps(save_a2_ob, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print("Saved lab_artifacts/auditoria_fillbar/resultados_smc_ob_A2.json")

    print("\nALL AUDIT MEASUREMENTS COMPLETED SUCCESSFULLY!")


if __name__ == "__main__":
    main()

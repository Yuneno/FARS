#!/usr/bin/env python3
"""Generador del Stream de Trades Limpio de C1 (SMC-FVG por_tramo) con Gate de Consistencia.

Produce lab_artifacts/m10_protocol/stream_smc_fvg_clean_por_tramo.json utilizando
directamente el motor de producción corregido (regla A1).
Verifica bit a bit el Gate de Consistencia frente a re_congelado_smc_fvg.json:
- n_trades: 3583
- win_rate_pct: 45.19%
- expectancy_r: -0.13007
- net_r: -466.03
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lab_artifacts.run_c1_walkforward import (
    CALIBRATION_BARS_COUNT,
    M5_MEMBER,
    ZIP_PATH,
    load_canonical_m5,
)
from lab_artifacts.re_congelado_fillbar.verificar_oraculo_a1 import (
    SCENARIOS_FVG,
    reprice_fvg,
)
from src.backtest.executor import run_backtest
from src.backtest.markets import MNQ
from src.backtest.smc_fvg import SmcFvgStrategy, smc_fvg_config
from src.hypothesis_registry import WalkForwardPlan

OUTPUT_DIR = REPO_ROOT / "lab_artifacts" / "m10_protocol"
OUTPUT_FILE = OUTPUT_DIR / "stream_smc_fvg_clean_por_tramo.json"
BLOCKERS_FILE = OUTPUT_DIR / "BLOCKERS.md"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("=== Generando Stream C1 Limpio (SMC-FVG por_tramo) ===", flush=True)

    print("Cargando dataset canónico M5...", flush=True)
    bars, mnq_fp = load_canonical_m5(ZIP_PATH, M5_MEMBER)

    plan = WalkForwardPlan.create_calendar_rolling(
        bars,
        train_months=36,
        test_months=6,
        step_months=6,
        purge_gap_bars=0,
        warmup_bars=CALIBRATION_BARS_COUNT,
    )

    base_cfg = smc_fvg_config(
        market=MNQ,
        discrete_partial_contracts=True,
        time_exit_mode="market",
        **SCENARIOS_FVG["canonico"],
    )

    accounting_cfg = smc_fvg_config(
        market=MNQ,
        discrete_partial_contracts=True,
        time_exit_mode="market",
        **SCENARIOS_FVG["por_tramo"],
    )

    all_trades = []
    trade_dicts = []

    print(f"Ejecutando backtest sobre {len(plan.folds)} folds con regla A1 en producción...", flush=True)
    for fold in plan.folds:
        cal = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT) : fold.test_start_idx]
        test = bars[fold.test_start_idx : fold.test_end_idx]
        strat = SmcFvgStrategy(min_risk_pts=8.0)
        res = run_backtest(test, strat, base_cfg, calibration_bars=cal)
        repriced = reprice_fvg(res.trades, base_cfg, accounting_cfg)
        all_trades.extend(repriced)

        for t in repriced:
            trade_dicts.append({
                "fold": fold.fold_id,
                "trade_id": f"fold_{fold.fold_id}_{t.trade_id}",
                "direction": t.direction,
                "entry_time": t.entry_time.isoformat(),
                "exit_time": t.exit_time.isoformat(),
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "stop_price": t.stop_price,
                "target_price": t.target_price,
                "quantity": t.quantity,
                "gross_pnl": t.gross_pnl,
                "commission": t.commission,
                "slippage_cost": t.slippage_cost,
                "net_pnl": t.net_pnl,
                "r_result": t.r_result,
                "exit_reason": t.exit_reason,
                "budgeted_risk_dollars": t.budgeted_risk_dollars,
                "effective_risk_dollars": t.effective_risk_dollars,
            })

    n_trades = len(all_trades)
    wins = [t for t in all_trades if t.net_pnl > 0]
    win_rate_pct = (len(wins) / n_trades) * 100.0 if n_trades > 0 else 0.0
    expectancy_r = sum(t.r_result for t in all_trades) / n_trades if n_trades > 0 else 0.0
    net_r = sum(t.r_result for t in all_trades)
    total_net_pnl = sum(t.net_pnl for t in all_trades)

    print(f"\n--- Métricas Calculadas para Stream C1 ---")
    print(f"n_trades:      {n_trades} (esperado: 3583)")
    print(f"win_rate_pct:  {win_rate_pct:.2f}% (esperado: 45.19%)")
    print(f"expectancy_r:  {expectancy_r:.5f} R (esperado: -0.13007 R)")
    print(f"net_r:         {net_r:.2f} R (esperado: -466.03 R)")
    print(f"total_net_pnl: ${total_net_pnl:,.2f}")

    # GATE DE CONSISTENCIA
    checks_passed = True
    failure_reasons = []

    if n_trades != 3583:
        checks_passed = False
        failure_reasons.append(f"n_trades mismatch: {n_trades} != 3583")

    if abs(win_rate_pct - 45.185598660340496) > 1e-4:
        checks_passed = False
        failure_reasons.append(f"win_rate_pct mismatch: {win_rate_pct:.4f} != 45.1856")

    if abs(expectancy_r - (-0.13006756349427853)) > 1e-5:
        checks_passed = False
        failure_reasons.append(f"expectancy_r mismatch: {expectancy_r:.5f} != -0.13007")

    if abs(net_r - (-466.03208)) > 1e-2:
        checks_passed = False
        failure_reasons.append(f"net_r mismatch: {net_r:.2f} != -466.03")

    if not checks_passed:
        err_msg = "GATE DE CONSISTENCIA FALLIDO:\n" + "\n".join(failure_reasons)
        print(f"\n[ERROR CRÍTICO] {err_msg}", flush=True)
        BLOCKERS_FILE.write_text(f"# BLOCKERS — M10\n\n{err_msg}\n", encoding="utf-8")
        sys.exit(1)

    print("\n>>> GATE DE CONSISTENCIA APROBADO: Stream C1 coincide exactamente con re_congelado_smc_fvg.json <<<", flush=True)

    payload = {
        "metadata": {
            "candidate": "C1",
            "strategy": "smc_fvg_baseline",
            "scenario": "por_tramo",
            "dataset_fingerprint": mnq_fp,
            "n_trades": n_trades,
            "win_rate_pct": win_rate_pct,
            "expectancy_r": expectancy_r,
            "net_r": net_r,
            "total_net_pnl": total_net_pnl,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "trades": trade_dicts,
    }

    OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[GUARDADO] Stream de trades escrito en {OUTPUT_FILE} ({OUTPUT_FILE.stat().st_size:,} bytes)", flush=True)


if __name__ == "__main__":
    main()

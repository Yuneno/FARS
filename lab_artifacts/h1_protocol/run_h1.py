#!/usr/bin/env python3
"""Runner H1 — ablación de la gestión escalonada EMAS (Kai v2, target RR 3.0).

CONGELADO contra E:/FARS-LAB/H1_PREREGISTRO_FROZEN.md (su sha256 se registra en
cada salida; sin preregistro congelado no hay corrida). Correcciones E1–E6 de la
co-auditoría Muse (E:/FARS-LAB/muse_h1_informe.md) aplicadas.

Brazos (cambia SOLO la maquinaria tp1/BE):
  - h1_mecanismo_f05: f=0.5 parcial 1R + BE + resto RR3 (executor enhanced)
  - h1_targetpuro_f0: f=0.0 target puro (executor legacy)

Políticas de fin de datos: 'unresolved' (PRIMARIA) y 'close' (sensibilidad
preregistrada de censura; |Δμ|>0.02R ⇒ limitación material declarada).

Tramo: SOLO fold_id 0..5 del plan C1 (8 folds 36/6/6, purge_gap_bars=0, warmup
500). fold_id 6..7 SELLADO (assert G4). Escenario realista (idéntico al M8):
comisión 0.62/side, slip 0.25 pts en entradas market y stops (TP límite sin
slip; salidas BE = stop market ⇒ pagan slip: coste real del mecanismo, F2).

Gates G1–G5 (ver preregistro §3). Regla de decisión (§4): intervalos + delta
pareado por señal común, CBB seed 42.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import io
import json
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lab_artifacts.run_c1_walkforward import (  # noqa: E402
    CALIBRATION_BARS_COUNT,
    ZIP_PATH,
    CircularBlockBootstrap,
    compute_file_sha256,
    run_bootstrap_ci,
)
from src.backtest.emas import EmasStrategy, emas_config  # noqa: E402
from src.backtest.executor import run_backtest  # noqa: E402
from src.backtest.history import Bar  # noqa: E402
from src.backtest.markets import MNQ  # noqa: E402
from src.hypothesis_registry import WalkForwardPlan  # noqa: E402

H1_DIR = Path(__file__).resolve().parent
PREREGISTRO_PATH = Path("E:/FARS-LAB/H1_PREREGISTRO_FROZEN.md")
MNQ_MEMBER = "databento/MNQ_M5.csv"
BAR_SECONDS = 300
TARGET_RR = 3.0
DEV_FOLDS = 6  # fold_id 0..5; fold_id 6..7 = validación SELLADA
SENSITIVITY_MARGIN_R = 0.02

# Pines completos (preregistro §2) — cero defaults silenciosos.
PINS = {
    "initial_balance": 50_000.0,
    "risk_per_trade": 0.01,
    "dollar_per_point": MNQ.dollar_per_point,
    "tick_size": MNQ.tick_size,
    "max_contracts": 1_000_000,
    "commission_per_side": 0.62,
    "slippage_points": 0.25,
    "time_exit_slippage_points": 0.25,
    "profit_target_pct": 0.06,
    "max_drawdown_pct": 0.06,
    "daily_loss_limit_pct": 0.03,
    "max_trades": None,
    "max_bars_held": 1_000_000_000,
    "max_hold_minutes": None,
    "fixed_quantity": None,
    "bar_interval_seconds": BAR_SECONDS,
    "pending_limit_entry": False,
    "pending_order_wait_bars": 0,
    "cooldown_bars": 0,
    "time_exit_mode": "market",
    "session_date_for_ledger": False,
}
ARMS = {
    "h1_mecanismo_f05": {"config_f": 0.5, "move_stop_to_break_even": True},
    "h1_targetpuro_f0": {"config_f": 0.0, "move_stop_to_break_even": False},
}


# ── utilidades copiadas fielmente de lab_artifacts/m8_protocol/run_m8.py ──────
def load_market_bars_from_zip(zip_path: Path, member_name: str) -> tuple[list[Bar], str, str, str]:
    """Lee barras M5 por streaming desde el zip canónico, calcula sha256 y filtra >= 2019-05-06."""
    hasher = hashlib.sha256()
    bars: list[Bar] = []
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(member_name) as raw:
            content_bytes = raw.read()
            hasher.update(content_bytes)
            reader = csv.DictReader(io.StringIO(content_bytes.decode("utf-8")))
            for row in reader:
                ts_str = row["timestamp"]
                if ts_str >= "2019-05-06":
                    bars.append(
                        Bar(
                            timestamp=datetime.fromisoformat(ts_str.replace("Z", "+00:00")),
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                            volume=float(row.get("volume", 0)),
                        )
                    )
    file_sha = hasher.hexdigest()
    first_ts = bars[0].timestamp.isoformat() if bars else "N/A"
    last_ts = bars[-1].timestamp.isoformat() if bars else "N/A"
    return bars, file_sha, first_ts, last_ts


def serialize_trade(t: Any, trade_id: str) -> dict[str, Any]:
    return {
        "trade_id": trade_id,
        "direction": t.direction,
        "entry_time": t.entry_time.isoformat(),
        "exit_time": t.exit_time.isoformat(),
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "stop_price": t.stop_price,
        "target_price": t.target_price,
        "quantity": t.quantity,
        "gross_pnl": round(t.gross_pnl, 4),
        "commission": round(t.commission, 4),
        "slippage_cost": round(t.slippage_cost, 4),
        "net_pnl": round(t.net_pnl, 4),
        "r_result": round(t.r_result, 6),
        "effective_r": round(t.effective_r, 6),
        "exit_reason": t.exit_reason,
    }


def compute_trades_sha256(trades: list[dict[str, Any]]) -> str:
    serialized = json.dumps(trades, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compute_drawdown_r(r_results: list[float]) -> float:
    if not r_results:
        return 0.0
    equity = np.cumsum(r_results)
    peak = np.maximum.accumulate(equity)
    dd = peak - equity
    return float(np.max(dd)) if len(dd) > 0 else 0.0


def pf_of(pnl: list[float]) -> float:
    wins = [p for p in pnl if p > 0]
    losses = [-p for p in pnl if p < 0]
    return (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (999.0 if wins else 0.0)


def ci_or_fail(values: list[float], label: str):
    """G5: CBB obligatorio. (None,None) con n>=5 => abort; n<5 => None (INCONCLUSO)."""
    if CircularBlockBootstrap is None:
        raise SystemExit("G5 FAIL: arch.bootstrap no disponible — CBB obligatorio (preregistro §3).")
    lo, hi = run_bootstrap_ci(values, seed=42)
    if lo is None:
        if len(values) < 5:
            return None
        raise SystemExit(f"G5 FAIL: CBB devolvió None para {label!r} con n={len(values)}")
    return (lo, hi)


def build_config(arm_cfg: dict[str, Any], policy: str):
    overrides = dict(PINS)
    overrides["end_of_data_policy"] = policy
    overrides["move_stop_to_break_even"] = arm_cfg["move_stop_to_break_even"]
    return emas_config(
        market=MNQ,
        f=arm_cfg["config_f"],
        discrete_partial_contracts=False,
        **overrides,
    )


def run_arm(arm_id: str, arm_cfg: dict[str, Any], bars: list[Bar], plan: WalkForwardPlan,
            policy: str, dev_folds: int) -> dict[str, Any]:
    fold_records: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    all_r: list[float] = []
    all_stop_r: list[float] = []
    all_slip: list[float] = []
    all_comm: list[float] = []
    qty_hist: dict[int, int] = {}
    decisions_by_ts: dict[str, tuple] = {}
    r_by_sig: dict[str, float] = {}
    n_decisions = 0
    trades_first_200 = 0

    for fold in plan.folds[:dev_folds]:
        assert fold.fold_id < DEV_FOLDS, f"G4: fold {fold.fold_id} fuera del tramo de desarrollo"
        test_cal = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT) : fold.test_start_idx]
        test_bars = bars[fold.test_start_idx : fold.test_end_idx]
        warmup_cut = test_bars[200].timestamp if len(test_bars) > 200 else None

        strat = EmasStrategy(market=MNQ, target_rr=TARGET_RR, log_decisions=True)
        cfg = build_config(arm_cfg, policy)
        res = run_backtest(test_bars, strat, cfg, calibration_bars=test_cal)

        for d in strat.decisions:
            decisions_by_ts[d.timestamp.isoformat()] = (d.direction, round(d.entry, 4), d.decision)
        n_decisions += len(strat.decisions)

        fold_rows = []
        for t in res.trades:
            trade_id = f"{arm_id}-f{fold.fold_id}-{t.trade_id}"
            row = serialize_trade(t, trade_id)
            fold_rows.append(row)
            sig_ts = (t.entry_time - timedelta(seconds=BAR_SECONDS)).isoformat()
            r_by_sig[sig_ts] = t.r_result
            if warmup_cut is not None and t.entry_time < warmup_cut:
                trades_first_200 += 1

        fold_r = [t.r_result for t in res.trades]
        fold_stop_r = [t.effective_r for t in res.trades]
        fold_pnl = [t.net_pnl for t in res.trades]
        all_rows.extend(fold_rows)
        all_r.extend(fold_r)
        all_stop_r.extend(fold_stop_r)
        all_slip.extend(t.slippage_cost for t in res.trades)
        all_comm.extend(t.commission for t in res.trades)
        for t in res.trades:
            qty_hist[t.quantity] = qty_hist.get(t.quantity, 0) + 1

        fold_records.append(
            {
                "fold_id": fold.fold_id,
                "test_start": fold.test_start,
                "test_end": fold.test_end,
                "test_bars": len(test_bars),
                "n_trades": len(res.trades),
                "mean_er": round(float(np.mean(fold_r)), 4) if fold_r else 0.0,
                "mean_stop_r": round(float(np.mean(fold_stop_r)), 4) if fold_stop_r else 0.0,
                "profit_factor": round(min(pf_of(fold_pnl), 999.0), 4),
                "net_pnl": round(sum(fold_pnl), 2),
                "win_rate": round(res.win_rate, 4),
                "decisions_logged": len(strat.decisions),
                "gap_rejections": res.gap_rejections,
                "unresolved_positions": res.unresolved_positions,
                "trades_in_first_200_bars": sum(
                    1 for r in fold_rows
                    if warmup_cut is not None
                    and datetime.fromisoformat(r["entry_time"]) < warmup_cut
                ),
                "trades": fold_rows,
            }
        )
        print(
            f"  [{arm_id}|{policy}] fold {fold.fold_id}: n={len(res.trades)} "
            f"E[R]={fold_records[-1]['mean_er']:+.4f} "
            f"stopR={fold_records[-1]['mean_stop_r']:+.4f} "
            f"PF={fold_records[-1]['profit_factor']:.3f} "
            f"unres={res.unresolved_positions}",
            flush=True,
        )

    ci = ci_or_fail(all_r, f"{arm_id}/{policy}/mean_er")
    positive_folds = sum(1 for f in fold_records if f["mean_er"] > 0)
    cell = {
        "metadata": {
            "market": "MNQ",
            "arm_id": arm_id,
            "scenario": "realista",
            "end_of_data_policy": policy,
            "target_rr": TARGET_RR,
            "arm_config": arm_cfg,
            "pinned_config": dataclasses.asdict(build_config(arm_cfg, policy)),
            "dev_folds": DEV_FOLDS,
            "preregistro_frozen": str(PREREGISTRO_PATH),
            "ran_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "aggregate": {
            "n_trades": len(all_rows),
            "mean_er": round(float(np.mean(all_r)), 4) if all_r else 0.0,
            "mean_stop_r": round(float(np.mean(all_stop_r)), 4) if all_stop_r else 0.0,
            "profit_factor": round(min(pf_of([r["net_pnl"] for r in all_rows]), 999.0), 4),
            "win_rate": round(
                (sum(1 for r in all_rows if r["net_pnl"] > 0) / len(all_rows)) if all_rows else 0.0, 4
            ),
            "net_r": round(float(np.sum(all_r)), 4) if all_r else 0.0,
            "net_pnl": round(float(np.sum([r["net_pnl"] for r in all_rows])), 2) if all_rows else 0.0,
            "max_drawdown_r": round(compute_drawdown_r(all_r), 4),
            "positive_folds": positive_folds,
            "total_folds": len(fold_records),
            "cbb_bootstrap_ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
            "trades_sha256": compute_trades_sha256(all_rows),
            "decisions_logged": n_decisions,
            "trades_in_first_200_bars": trades_first_200,
            "qty_distribution": {str(k): v for k, v in sorted(qty_hist.items())},
            "attribution": {
                "total_slippage_cost": round(sum(all_slip), 2),
                "total_commission": round(sum(all_comm), 2),
            },
        },
        "folds": fold_records,
    }
    cell["_decisions_by_ts"] = decisions_by_ts
    cell["_all_r"] = all_r
    cell["_r_by_sig"] = r_by_sig
    return cell


def decide(ci_A, ci_B, ci_D) -> str:
    """Regla del preregistro §4 (intervalos)."""
    if ci_A is None or ci_B is None or ci_D is None:
        return "INCONCLUSO (IC indefinido: n<5)"
    if ci_D[1] <= 0 or ci_A[1] <= 0:
        return "FALSADO"
    if ci_D[0] > 0 and (ci_B[1] <= 0 or ci_B[1] <= 0.5 * ci_A[0]):
        return "CONFIRMADO"
    return "INCONCLUSO/ZONA GRIS"


def main() -> None:
    parser = argparse.ArgumentParser(description="Runner H1 (ablación gestión escalonada EMAS)")
    parser.add_argument("--smoke", action="store_true", help="1 fold por brazo (prueba técnica; los números NO son resultado)")
    args = parser.parse_args()
    dev_folds = 1 if args.smoke else DEV_FOLDS

    if not PREREGISTRO_PATH.exists():
        raise SystemExit(
            f"PREREGISTRO CONGELADO NO ENCONTRADO: {PREREGISTRO_PATH}. Sin preregistro no hay corrida."
        )
    prereg_hash = compute_file_sha256(PREREGISTRO_PATH)
    print(f"Preregistro congelado verificado (SHA256: {prereg_hash})", flush=True)
    if CircularBlockBootstrap is None:
        raise SystemExit("G5 FAIL: arch.bootstrap no disponible — CBB obligatorio.")

    print("Cargando MNQ M5 desde databento.zip...", flush=True)
    bars, fingerprint, first_ts, last_ts = load_market_bars_from_zip(ZIP_PATH, MNQ_MEMBER)
    print(f"  {len(bars)} barras | {first_ts} -> {last_ts} | sha {fingerprint[:16]}...", flush=True)

    plan = WalkForwardPlan.create_calendar_rolling(
        bars, train_months=36, test_months=6, step_months=6,
        purge_gap_bars=0, warmup_bars=CALIBRATION_BARS_COUNT,
    )
    print(f"Plan C1: {plan.n_folds} folds creados", flush=True)
    if plan.n_folds != 8:
        raise SystemExit(f"El plan C1 debe crear exactamente 8 folds (creó {plan.n_folds}). PARAR y reportar.")
    if args.smoke:
        print("MODO SMOKE: 1 fold por brazo — los números NO son resultado.", flush=True)

    results: dict[str, dict[str, Any]] = {}
    sensitivity: dict[str, dict[str, Any]] = {}
    for policy, bucket in (("unresolved", results), ("close", sensitivity)):
        for arm_id, arm_cfg in ARMS.items():
            print(f"\n=== {arm_id} | policy={policy} | {dev_folds} folds ===", flush=True)
            bucket[arm_id] = run_arm(arm_id, arm_cfg, bars, plan, policy, dev_folds)

    # ── G1: paridad de la función señal sobre barras comunes (primaria) ───────
    dec_a = results["h1_mecanismo_f05"].pop("_decisions_by_ts")
    dec_b = results["h1_targetpuro_f0"].pop("_decisions_by_ts")
    common = set(dec_a) & set(dec_b)
    divergentes = sorted(ts for ts in common if dec_a[ts] != dec_b[ts])
    g1 = {
        "common_decision_bars": len(common),
        "only_in_mecanismo": len(set(dec_a) - set(dec_b)),
        "only_in_targetpuro": len(set(dec_b) - set(dec_a)),
        "divergent_decisions_on_common_bars": len(divergentes),
        "sample_divergences": [
            {"ts": ts, "mecanismo": dec_a[ts], "targetpuro": dec_b[ts]} for ts in divergentes[:10]
        ],
        "signal_parity_PASS": len(divergentes) == 0,
    }
    # ── G2: mapeo exacto trade -> señal en fill−1 ─────────────────────────────
    for cell in list(results.values()) + list(sensitivity.values()):
        cell.pop("_decisions_by_ts", None)
    g2 = {
        "unmapped_mecanismo": results["h1_mecanismo_f05"]["aggregate"].get("unmapped_trades", 0),
        "unmapped_targetpuro": results["h1_targetpuro_f0"]["aggregate"].get("unmapped_trades", 0),
    }
    # G2 se calcula sobre r_by_sig vs n_trades (cada trade porta su clave exacta):
    for arm_id, cell in results.items():
        r_by_sig = cell.pop("_r_by_sig")
        all_r = cell.pop("_all_r")
        cell["_r_by_sig"] = r_by_sig
        cell["_all_r"] = all_r
        g2[f"unmapped_{arm_id}"] = cell["aggregate"]["n_trades"] - len(r_by_sig)
    for cell in sensitivity.values():
        cell.pop("_decisions_by_ts", None)
        cell.pop("_r_by_sig", None)
        cell.pop("_all_r", None)
    g2["mapping_exact_PASS"] = all(v == 0 for k, v in g2.items() if k.startswith("unmapped"))

    # ── Δ pareado por señal común (E3) ────────────────────────────────────────
    sig_a = results["h1_mecanismo_f05"].pop("_r_by_sig")
    sig_b = results["h1_targetpuro_f0"].pop("_r_by_sig")
    all_r_a = results["h1_mecanismo_f05"].pop("_all_r")
    all_r_b = results["h1_targetpuro_f0"].pop("_all_r")
    pair_keys = sorted(set(sig_a) & set(sig_b))
    deltas = [sig_a[k] - sig_b[k] for k in pair_keys]
    orphan_a = [sig_a[k] for k in set(sig_a) - set(sig_b)]
    orphan_b = [sig_b[k] for k in set(sig_b) - set(sig_a)]

    ci_A = ci_or_fail(all_r_a, "mu_A")
    ci_B = ci_or_fail(all_r_b, "mu_B")
    ci_D = ci_or_fail(deltas, "delta_paired") if deltas else None
    outcome = decide(ci_A, ci_B, ci_D)

    mu_a = float(np.mean(all_r_a)) if all_r_a else 0.0
    mu_b = float(np.mean(all_r_b)) if all_r_b else 0.0
    sens_block = {}
    for arm_id in ARMS:
        mu_sens = sensitivity[arm_id]["aggregate"]["mean_er"]
        mu_main = results[arm_id]["aggregate"]["mean_er"]
        diff = abs(mu_main - mu_sens)
        sens_block[arm_id] = {
            "mean_er_unresolved": mu_main,
            "mean_er_close": mu_sens,
            "abs_diff": round(diff, 4),
            "exceeds_margin": diff > SENSITIVITY_MARGIN_R,
            "margin_r": SENSITIVITY_MARGIN_R,
        }

    comparison = {
        "metadata": {
            "experiment": "H1",
            "preregistro_sha256": prereg_hash,
            "dataset_sha256": fingerprint,
            "smoke_mode": args.smoke,
            "ran_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "mean_er_mecanismo_f05": round(mu_a, 4),
        "mean_er_targetpuro_f0": round(mu_b, 4),
        "ci95_mu_A": [round(x, 4) for x in ci_A] if ci_A else None,
        "ci95_mu_B": [round(x, 4) for x in ci_B] if ci_B else None,
        "paired_delta": {
            "n_pairs": len(pair_keys),
            "n_orphans_mecanismo": len(orphan_a),
            "n_orphans_targetpuro": len(orphan_b),
            "mean_orphan_mecanismo": round(float(np.mean(orphan_a)), 4) if orphan_a else None,
            "mean_orphan_targetpuro": round(float(np.mean(orphan_b)), 4) if orphan_b else None,
            "delta_mean": round(float(np.mean(deltas)), 4) if deltas else None,
            "ci95_delta": [round(x, 4) for x in ci_D] if ci_D else None,
        },
        "gates": {"G1_signal_parity": g1, "G2_mapping": g2,
                  "G3_determinism": "trades_sha256 por (brazo, política) en metrics_*",
                  "G4_sealed_folds": "fold_id 6..7 sin actividad (assert en runner)",
                  "G5_cbb_failfast": "PASS (arch disponible; CBB no devolvió None con n>=5)"},
        "end_of_data_sensitivity": sens_block,
        "outcome": outcome,
        "decision_rule": (
            "CONFIRMADO <=> lo(IC95 d)>0 y (hi(IC95 mu_B)<=0 o hi(IC95 mu_B)<=0.5*lo(IC95 mu_A)). "
            "FALSADO <=> hi(IC95 d)<=0 o hi(IC95 mu_A)<=0. INCONCLUSO = resto."
        ),
    }

    gates_ok = g1["signal_parity_PASS"] and g2["mapping_exact_PASS"]
    comparison["gates"]["all_PASS"] = bool(gates_ok)
    if not gates_ok:
        comparison["outcome"] = "NO LECTIBLE (gates fallidos — BUG, no resultado)"

    H1_DIR.mkdir(parents=True, exist_ok=True)
    for policy, bucket in (("unresolved", results), ("close", sensitivity)):
        suffix = "" if policy == "unresolved" else "_close_sensitivity"
        for arm_id, cell in bucket.items():
            cell["metadata"]["preregistro_sha256"] = prereg_hash
            cell["metadata"]["dataset_sha256"] = fingerprint
            path = H1_DIR / f"metrics_MNQ_{arm_id}_realista{suffix}.json"
            path.write_text(json.dumps(cell, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"Escrito {path.name}", flush=True)
    (H1_DIR / "comparison_h1.json").write_text(
        json.dumps(comparison, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    manifest = {
        "metadata": {
            "title": "Manifest H1",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "runtime": sys.version,
            "preregistro_sha256": prereg_hash,
            "smoke_mode": args.smoke,
        },
        "dataset": {"archive": str(ZIP_PATH), "member": MNQ_MEMBER, "bars_count": len(bars),
                    "first": first_ts, "last": last_ts, "sha256": fingerprint},
        "walk_forward_plan": {"type": "calendar_rolling", "train_months": 36, "test_months": 6,
                              "step_months": 6, "purge_gap_bars": 0, "warmup_bars": CALIBRATION_BARS_COUNT,
                              "n_folds": plan.n_folds, "dev_folds_used": dev_folds,
                              "validation_folds_sealed": [6, 7]},
        "arms": ARMS,
        "pinned_config_base": PINS,
        "cost_scenario_realista": {"commission_per_side": 0.62, "slippage_points": 0.25,
                                  "time_exit_slippage_points": 0.25},
        "artifacts_sha256": {p.name: compute_file_sha256(p) for p in sorted(H1_DIR.glob("*.json"))},
    }
    (H1_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("\n=== H1 COMPLETADO ===", flush=True)
    print(json.dumps(comparison, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

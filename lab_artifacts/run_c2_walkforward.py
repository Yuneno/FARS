#!/usr/bin/env python3
"""Incremental C2 walk-forward runner; execute one cost scenario at a time."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lab_artifacts.run_c1_walkforward import (
    CALIBRATION_BARS_COUNT,
    M5_MEMBER,
    ZIP_PATH,
    audit_fold_overlap_and_embargo,
    compute_file_sha256,
    evaluate_scenario,
    get_git_branch,
    get_git_commit,
    load_canonical_m5,
)
from src.backtest.emas import EmasStrategy, emas_config
from src.backtest.crt4h import Crt4hStrategy, crt4h_config
from src.backtest.markets import MNQ
from src.backtest.smc_fvg import SmcFvgStrategy, smc_fvg_config
from src.hypothesis_registry import WalkForwardPlan

ARTIFACTS = REPO_ROOT / "lab_artifacts" / "c2_protocol"
SCENARIOS = {
    "canonico": dict(commission_per_side=2.0, slippage_points=0.0, time_exit_slippage_points=0.0),
    "por_tramo": dict(commission_per_side=0.62, slippage_points=0.25, time_exit_slippage_points=0.25),
    "kai": dict(commission_per_side=0.71, slippage_points=0.25, time_exit_slippage_points=0.25),
}
ORDER = tuple(SCENARIOS)
CONFIGURATIONS = {
    "emas_baseline": (lambda: EmasStrategy(target_rr=3.0), emas_config, {"target_rr": 3.0}, "baseline"),
    "emas_rr_0_4": (lambda: EmasStrategy(target_rr=0.4), emas_config, {"target_rr": 0.4}, "candidate"),
    "emas_rr_0_3": (lambda: EmasStrategy(target_rr=0.3), emas_config, {"target_rr": 0.3}, "candidate_edge_probe"),
    "smc_fvg_baseline": (lambda: SmcFvgStrategy(min_risk_pts=8.0), smc_fvg_config, {"min_risk_pts": 8.0}, "baseline"),
    "smc_fvg_risk_5": (lambda: SmcFvgStrategy(min_risk_pts=5.0), smc_fvg_config, {"min_risk_pts": 5.0}, "candidate"),
    "smc_fvg_risk_10": (lambda: SmcFvgStrategy(min_risk_pts=10.0), smc_fvg_config, {"min_risk_pts": 10.0}, "candidate"),
    "crt4h_defaults": (
        lambda: Crt4hStrategy(max_follow=3, min_rr=1.5, sl_pad_frac=0.1, disp_mult=1.2, max_hold_bars=96, require_bias=1),
        crt4h_config,
        {"max_follow": 3, "min_rr": 1.5, "sl_pad_frac": 0.1, "disp_mult": 1.2, "max_hold_bars": 96, "require_bias": 1},
        "candidate",
    ),
}


def _load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _save(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, choices=ORDER)
    args = parser.parse_args()
    scenario_index = ORDER.index(args.scenario)
    for required in ORDER[:scenario_index]:
        if not all((_metrics_path(name)).exists() and required in _load(_metrics_path(name), {}).get("scenarios", {}) for name in CONFIGURATIONS):
            raise SystemExit(f"scenario {required!r} must be completed before {args.scenario!r}")

    bars, fingerprint = load_canonical_m5(ZIP_PATH, M5_MEMBER)
    plan = WalkForwardPlan.create_calendar_rolling(
        bars, train_months=36, test_months=6, step_months=6,
        purge_gap_bars=0, warmup_bars=CALIBRATION_BARS_COUNT,
    )
    ts_map = {bar.timestamp: i for i, bar in enumerate(bars)}
    evaluation_path = ARTIFACTS / "evaluation_order.json"
    evaluation = _load(evaluation_path, {"metadata": {"title": "C2 evaluation order", "anti_leakage": "Frozen preregistered configurations; no test-driven selection."}, "sequence": []})

    for name, (factory, cfg_factory, parameters, role) in CONFIGURATIONS.items():
        print(f"[{datetime.now(timezone.utc).isoformat()}] {args.scenario}: {name}", flush=True)
        accounting_cfg = cfg_factory(market=MNQ, discrete_partial_contracts=True, time_exit_mode="market", **SCENARIOS[args.scenario])
        cfg = cfg_factory(market=MNQ, discrete_partial_contracts=True, time_exit_mode="market", **SCENARIOS["canonico"])
        if args.scenario == "canonico":
            audits = {fold.fold_id: audit_fold_overlap_and_embargo(factory, cfg, fold, bars, ts_map) for fold in plan.folds}
        else:
            previous = _load(_metrics_path(name), {})["scenarios"]["canonico"]["folds"]
            audits = {fold["fold_id"]: fold for fold in previous}
        before = len(evaluation["sequence"])
        result = evaluate_scenario(
            name, args.scenario, factory, cfg, bars, plan, audits, evaluation["sequence"],
            accounting_cfg=accounting_cfg,
            entry_order_type="limit" if name.startswith(("smc_fvg", "crt4h")) else "market",
        )
        for row in evaluation["sequence"][before:]:
            row["configuration_role"] = role
            row["frozen_parameters"] = parameters
        payload = _load(_metrics_path(name), {
            "metadata": {"configuration_id": name, "parameters": parameters, "dataset_fingerprint": fingerprint, "n_folds": plan.n_folds},
            "scenarios": {},
        })
        payload["scenarios"][args.scenario] = result
        payload["metadata"]["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        _save(_metrics_path(name), payload)
        _save(evaluation_path, evaluation)
        _write_manifest(bars, fingerprint, plan)


def _metrics_path(name: str) -> Path:
    return ARTIFACTS / f"{name}_fold_metrics.json"


def _write_manifest(bars, fingerprint, plan) -> None:
    artifacts = {}
    for path in sorted(ARTIFACTS.glob("*_fold_metrics.json")):
        artifacts[path.name] = {"sha256": compute_file_sha256(path), "size_bytes": path.stat().st_size}
    eval_path = ARTIFACTS / "evaluation_order.json"
    if eval_path.exists():
        artifacts[eval_path.name] = {"sha256": compute_file_sha256(eval_path), "size_bytes": eval_path.stat().st_size}
    manifest = {
        "metadata": {"title": "C2 walk-forward manifest", "generated_at_utc": datetime.now(timezone.utc).isoformat(), "git_commit": get_git_commit(REPO_ROOT), "git_branch": get_git_branch(REPO_ROOT), "runtime": sys.version},
        "dataset": {"archive": str(ZIP_PATH), "member": M5_MEMBER, "bars_count": len(bars), "start_time": bars[0].timestamp.isoformat(), "end_time": bars[-1].timestamp.isoformat(), "sha256": fingerprint},
        "walk_forward_plan": {"type": "calendar_rolling", "train_months": 36, "test_months": 6, "step_months": 6, "n_folds": plan.n_folds, "purge": "real trade intervals", "embargo_h_bars": 192},
        "configurations": {name: {"parameters": values[2], "role": values[3]} for name, values in CONFIGURATIONS.items()},
        "scenarios": {
            "canonico": {"commission_rt_usd": 4.00, "slippage": "none"},
            "por_tramo": {"commission_per_side_usd": 0.62, "commission_rt_usd": 1.24, "slippage": "1 tick only STOP and MARKET/time exits; LIMIT and TP do not slip"},
            "kai": {"commission_per_side_usd": 0.71, "commission_rt_usd": 1.42, "slippage": "1 tick only crossing orders: STOP and MARKET; LIMIT and TP do not slip", "documented_discrepancies_usd_rt": [1.00, 1.34]},
        },
        "artifacts": artifacts,
    }
    _save(ARTIFACTS / "manifest.json", manifest)


if __name__ == "__main__":
    main()

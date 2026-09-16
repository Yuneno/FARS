#!/usr/bin/env python3
"""Frozen Reversal V1 C1 runner (36/6/6, causal purge audit, h=192 embargo).

Run scenarios in preregistered order, for example:
  python lab_artifacts/run_v1_protocol.py --scenario c1_canonical
Subsequent invocations append the next cost scenario to one metrics artifact.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lab_artifacts.run_c1_walkforward import (
    CALIBRATION_BARS_COUNT,
    EMBARGO_H_BARS,
    M5_MEMBER,
    ZIP_PATH,
    audit_fold_overlap_and_embargo,
    compute_file_sha256,
    evaluate_scenario,
    get_git_branch,
    get_git_commit,
    load_canonical_m5,
)
from src.backtest.markets import MNQ
from src.backtest.reversal_v1 import ReversalV1Strategy, reversal_v1_config
from src.hypothesis_registry import WalkForwardPlan

ARTIFACTS = REPO_ROOT / "lab_artifacts" / "v1_protocol"
PREREG = ARTIFACTS / "preregistro.json"
METRICS = ARTIFACTS / "v1_fold_metrics.json"
MANIFEST = ARTIFACTS / "manifest.json"
EVALUATION = ARTIFACTS / "evaluation_order.json"

SCENARIOS = {
    "c1_canonical": dict(commission_per_side=2.0, slippage_points=0.0, time_exit_slippage_points=0.0),
    "c2_segment_realistic": dict(commission_per_side=0.62, slippage_points=0.25, time_exit_slippage_points=0.25),
    "c2_kai": dict(commission_per_side=0.71, slippage_points=0.25, time_exit_slippage_points=0.25),
}
ORDER = tuple(SCENARIOS)

CONFIGURATIONS = {
    "baseline_without_optional_filters": dict(use_cisd=False, use_ote=False, entry_mode="limit"),
    "cisd_only_primary": dict(use_cisd=True, use_ote=False, entry_mode="limit"),
    "ote_only": dict(use_cisd=False, use_ote=True, entry_mode="limit"),
    "cisd_plus_ote": dict(use_cisd=True, use_ote=True, entry_mode="limit"),
    "market_neighbor": dict(use_cisd=True, use_ote=False, entry_mode="market"),
}


def _load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _save(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _factory(params):
    return lambda: ReversalV1Strategy(
        market=MNQ,
        disp_mult=1.2,
        sl_buffer_pts=1.0,
        wait=48,
        max_hold_bars=96,
        cisd_lookback=10,
        pivot_left=3,
        pivot_right=3,
        fvg_min_gap_pct=0.001,
        **params,
    )


def _execution_config(params, costs):
    return reversal_v1_config(
        market=MNQ,
        wait=48,
        max_hold_bars=96,
        entry_mode=params["entry_mode"],
        time_exit_mode="market",
        **costs,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, choices=ORDER)
    args = parser.parse_args()
    if not PREREG.exists():
        raise SystemExit("preregistro.json must exist before any result run")
    prereg = _load(PREREG, {})
    if prereg.get("status") != "frozen_before_first_result_run":
        raise SystemExit("preregistration is not frozen")
    scenario_index = ORDER.index(args.scenario)
    existing = _load(METRICS, {})
    for required in ORDER[:scenario_index]:
        for name in CONFIGURATIONS:
            if required not in existing.get("configurations", {}).get(name, {}).get("scenarios", {}):
                raise SystemExit(f"scenario {required!r} must complete before {args.scenario!r}")

    started = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    bars, fingerprint = load_canonical_m5(ZIP_PATH, M5_MEMBER)
    plan = WalkForwardPlan.create_calendar_rolling(
        bars,
        train_months=36,
        test_months=6,
        step_months=6,
        purge_gap_bars=0,
        warmup_bars=CALIBRATION_BARS_COUNT,
    )
    ts_map = {bar.timestamp: i for i, bar in enumerate(bars)}
    evaluation = _load(
        EVALUATION,
        {
            "metadata": {
                "title": "Reversal V1 frozen evaluation order",
                "anti_leakage": "No fitting or test-driven selection; configurations and order were preregistered.",
            },
            "sequence": [],
        },
    )
    payload = existing or {
        "metadata": {
            "title": "Reversal V1 C1 fold metrics",
            "preregistration": str(PREREG.relative_to(REPO_ROOT)),
            "dataset_fingerprint": fingerprint,
            "metrics_must_be_read_together": ["global_expectancy_r", "global_profit_factor"],
            "promotion": "none",
        },
        "configurations": {},
    }

    for name, params in CONFIGURATIONS.items():
        print(f"[{datetime.now(timezone.utc).isoformat()}] {args.scenario}: {name}", flush=True)
        factory = _factory(params)
        canonical_cfg = _execution_config(params, SCENARIOS["c1_canonical"])
        accounting_cfg = _execution_config(params, SCENARIOS[args.scenario])
        if args.scenario == "c1_canonical":
            audits = {
                fold.fold_id: audit_fold_overlap_and_embargo(factory, canonical_cfg, fold, bars, ts_map)
                for fold in plan.folds
            }
        else:
            prior = payload["configurations"][name]["scenarios"]["c1_canonical"]["folds"]
            audits = {fold["fold_id"]: fold for fold in prior}
        before = len(evaluation["sequence"])
        result = evaluate_scenario(
            name,
            args.scenario,
            factory,
            canonical_cfg,
            bars,
            plan,
            audits,
            evaluation["sequence"],
            accounting_cfg=accounting_cfg,
            entry_order_type=params["entry_mode"],
        )
        for row in evaluation["sequence"][before:]:
            row["configuration_id"] = name
            row["frozen_parameters"] = params
        record = payload["configurations"].setdefault(
            name,
            {"parameters": params, "scenarios": {}},
        )
        record["scenarios"][args.scenario] = result
        _save(METRICS, payload)
        _save(EVALUATION, evaluation)

    completed = datetime.now(timezone.utc).isoformat()
    gate_rows = [
        result["gates_evaluation"]
        for item in payload["configurations"].values()
        for result in item["scenarios"].values()
    ]
    if any(row.get("detailed_verdict") == "INSUFFICIENT_SAMPLE" for row in gate_rows):
        verdict = "evidencia_insuficiente"
    elif any(row.get("verdict") != "PASS" for row in gate_rows):
        verdict = "FAIL_no_promotion"
    else:
        verdict = "PASS_no_promotion"
    payload["metadata"].update(
        {
            "updated_at_utc": completed,
            "last_scenario_started_at_utc": started,
            "last_scenario_elapsed_seconds": round(time.perf_counter() - t0, 3),
            "verdict": verdict,
        }
    )
    _save(METRICS, payload)
    _save(EVALUATION, evaluation)
    _write_manifest(bars, fingerprint, plan)
    print(f"completed in {time.perf_counter() - t0:.1f}s", flush=True)


def _write_manifest(bars, fingerprint, plan) -> None:
    artifacts = {}
    for path in (PREREG, METRICS, EVALUATION):
        if path.exists():
            artifacts[path.name] = {
                "sha256": compute_file_sha256(path),
                "size_bytes": path.stat().st_size,
            }
    manifest = {
        "metadata": {
            "title": "Reversal V1 protocol manifest",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "git_commit": get_git_commit(REPO_ROOT),
            "git_branch": get_git_branch(REPO_ROOT),
            "runtime": sys.version,
        },
        "dataset": {
            "archive": str(ZIP_PATH),
            "member": M5_MEMBER,
            "bars_count": len(bars),
            "start_time": bars[0].timestamp.isoformat(),
            "end_time": bars[-1].timestamp.isoformat(),
            "sha256": fingerprint,
        },
        "walk_forward_plan": {
            "type": "calendar_rolling",
            "train_months": 36,
            "test_months": 6,
            "step_months": 6,
            "n_folds": plan.n_folds,
            "purge": "real trade intervals",
            "embargo_h_bars": EMBARGO_H_BARS,
        },
        "cost_scenarios_in_order": list(ORDER),
        "artifacts": artifacts,
    }
    _save(MANIFEST, manifest)


if __name__ == "__main__":
    main()

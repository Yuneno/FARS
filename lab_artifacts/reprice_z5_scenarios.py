#!/usr/bin/env python3
"""Reprice the frozen Z5 canonical trades under the two missing C1 scenarios.

Signals and zone filters are deliberately not rerun here.  The script changes
only trade economics, verifies the immutable trade identity against canonical,
and rebuilds fold/aggregate metrics with the same bootstrap used by Z5.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = REPO_ROOT / "lab_artifacts" / "z5_protocol"
SCENARIOS = {
    "canonico_mas_1tick": {"commission_per_side": 2.0, "slippage_points": 0.25},
    "por_tramo_realista": {"commission_per_side": 0.62, "slippage_points": 0.25},
}
IDENTITY_FIELDS = (
    "trade_id", "direction", "entry_time", "exit_time", "entry_price",
    "exit_price", "stop_price", "target_price", "quantity", "gross_pnl",
    "exit_reason",
)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ci95(values: list[float]) -> list[float]:
    if len(values) < 2:
        val = values[0] if values else 0.0
        return [val, val]
    arr = np.array(values, dtype=float)
    rng = np.random.default_rng(seed=42)
    means = [float(np.mean(rng.choice(arr, size=len(arr), replace=True))) for _ in range(1000)]
    return [round(float(np.percentile(means, 2.5)), 4), round(float(np.percentile(means, 97.5)), 4)]


def _slipping_quantity(subject: str, trade: dict[str, Any]) -> float:
    """Number of contract-legs crossing the spread under C1/C2 semantics."""
    qty = trade["quantity"]
    reason = trade["exit_reason"]
    if subject == "amd_crt":
        # AMD stop/target levels are distances from the slipped market fill.
        # The shifted TP therefore preserves gross distance (zero net legs),
        # a stop adds one adverse tick, and a market time-exit adds the entry
        # plus exit ticks.  This is verified against a complete executor run.
        if reason == "time_exit":
            return float(2 * qty)
        if reason in {"stop_loss", "break_even_stop"}:
            return float(qty)
        return 0.0

    # SMC-FVG enters by LIMIT.  A break-even stop means the 50% TP partial was
    # already closed; only the remaining half crosses at the stop.
    if reason == "break_even_stop" or (
        reason in {"stop_loss", "time_exit"} and trade["stop_price"] == trade["entry_price"]
    ):
        return qty * 0.5
    if reason in {"stop_loss", "time_exit"} and trade["exit_price"] == trade["stop_price"]:
        return float(qty)
    return 0.0


def reprice_trade(subject: str, trade: dict[str, Any], scenario: str) -> dict[str, Any]:
    cfg = SCENARIOS[scenario]
    result = dict(trade)
    commission = cfg["commission_per_side"] * 2 * trade["quantity"]
    slippage = cfg["slippage_points"] * 2.0 * _slipping_quantity(subject, trade)
    net = trade["gross_pnl"] - commission - slippage
    result["commission"] = round(commission, 4)
    result["net_pnl"] = round(net, 4)
    result["r_result"] = round(net / 500.0, 6)
    return result


def identity_rows(payload: dict[str, Any]) -> list[tuple[Any, ...]]:
    return [tuple(trade[field] for field in IDENTITY_FIELDS) for fold in payload["folds"] for trade in fold["trades"]]


def _metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    rs = [trade["r_result"] for trade in trades]
    pnls = [trade["net_pnl"] for trade in trades]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [-pnl for pnl in pnls if pnl < 0]
    pf = sum(wins) / sum(losses) if losses else (999.0 if wins else 0.0)
    return {
        "n_trades": len(trades),
        "mean_er": round(float(np.mean(rs)) if rs else 0.0, 4),
        "profit_factor": round(min(pf, 999.0), 4),
        "win_rate": round(len(wins) / len(trades) if trades else 0.0, 4),
        "total_net_pnl": round(sum(pnls), 2),
        "ic95_er": _ci95(rs),
    }


def reprice_payload(source: dict[str, Any], scenario: str) -> dict[str, Any]:
    subject = source["metadata"]["subject"]
    result = json.loads(json.dumps(source))
    result["metadata"].update({
        "scenario": scenario,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generation_method": "repriced_from_frozen_canonical_trades",
        "canonical_trade_set_identical": True,
    })
    all_trades: list[dict[str, Any]] = []
    for fold in result["folds"]:
        fold["trades"] = [reprice_trade(subject, trade, scenario) for trade in fold["trades"]]
        metrics = _metrics(fold["trades"])
        fold.update({
            "n_trades": metrics["n_trades"],
            "mean_er": metrics["mean_er"],
            "profit_factor": metrics["profit_factor"],
            "net_pnl": metrics["total_net_pnl"],
            "win_rate": metrics["win_rate"],
        })
        all_trades.extend(fold["trades"])
    aggregate = _metrics(all_trades)
    aggregate.update({
        "folds_positive_er": sum(fold["mean_er"] > 0 for fold in result["folds"]),
        "total_folds": len(result["folds"]),
    })
    source_base = source["aggregated_metrics"]
    aggregate["delta_er_vs_baseline"] = source_base.get("delta_er_vs_baseline", 0.0)
    aggregate["delta_pf_vs_baseline"] = source_base.get("delta_pf_vs_baseline", 0.0)
    result["aggregated_metrics"] = aggregate
    if identity_rows(result) != identity_rows(source):
        raise RuntimeError(f"Trade-set changed while repricing {subject}/{source['metadata']['arm_id']}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-equivalence", action="store_true")
    args = parser.parse_args()
    canonical_paths = sorted(
        path for path in ARTIFACTS.glob("*_fold_metrics.json")
        if not any(f"_{scenario}_fold_metrics.json" in path.name for scenario in SCENARIOS)
    )
    if len(canonical_paths) != 30:
        raise SystemExit(f"Expected 30 canonical artifacts, found {len(canonical_paths)}")
    generated: list[Path] = []
    for source_path in canonical_paths:
        source = _read(source_path)
        stem = source_path.name.removesuffix("_fold_metrics.json")
        for scenario in SCENARIOS:
            output = ARTIFACTS / f"{stem}_{scenario}_fold_metrics.json"
            _write(output, reprice_payload(source, scenario))
            generated.append(output)
            print(output.relative_to(REPO_ROOT))

    # Deltas must use the baseline repriced under the same scenario, not the
    # canonical baseline (contract counts/slipping legs differ by arm).
    for scenario in SCENARIOS:
        for subject in ("smc_fvg", "amd_crt"):
            baseline = _read(ARTIFACTS / f"{subject}_baseline_{scenario}_fold_metrics.json")
            base_metrics = baseline["aggregated_metrics"]
            for path in [p for p in generated if p.name.startswith(f"{subject}_") and f"_{scenario}_" in p.name]:
                payload = _read(path)
                metrics = payload["aggregated_metrics"]
                metrics["delta_er_vs_baseline"] = round(metrics["mean_er"] - base_metrics["mean_er"], 4)
                metrics["delta_pf_vs_baseline"] = round(metrics["profit_factor"] - base_metrics["profit_factor"], 4)
                _write(path, payload)

    if args.verify_equivalence:
        write_equivalence_evidence()


def write_equivalence_evidence() -> None:
    """Full-run one frozen arm and compare its observable outcomes bit for bit."""
    import sys

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from lab_artifacts.run_c1_walkforward import CALIBRATION_BARS_COUNT, M5_MEMBER, ZIP_PATH, load_canonical_m5
    from lab_artifacts.run_z5_experiments import _serialize_trade
    from src.backtest.amd_crt import AmdCrtStrategy, amd_crt_config
    from src.backtest.executor import run_backtest
    from src.backtest.markets import MNQ
    from src.hypothesis_registry import WalkForwardPlan

    scenario = "por_tramo_realista"
    canonical = _read(ARTIFACTS / "amd_crt_baseline_fold_metrics.json")
    repriced = reprice_payload(canonical, scenario)
    bars, fingerprint = load_canonical_m5(ZIP_PATH, M5_MEMBER)
    plan = WalkForwardPlan.create_calendar_rolling(
        bars, train_months=36, test_months=6, step_months=6,
        purge_gap_bars=0, warmup_bars=CALIBRATION_BARS_COUNT,
    )
    cfg = amd_crt_config(
        market=MNQ, commission_per_side=0.62, slippage_points=0.25,
        time_exit_slippage_points=0.25,
    )
    full_folds = []
    for fold in plan.folds:
        calibration = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT):fold.test_start_idx]
        test_bars = bars[fold.test_start_idx:fold.test_end_idx]
        result = run_backtest(
            test_bars,
            AmdCrtStrategy(market=MNQ, median_lookback_days=20, min_weekday_samples=3),
            cfg,
            calibration_bars=calibration,
        )
        full_folds.append({"fold_id": fold.fold_id, "trades": [_serialize_trade(t) for t in result.trades]})

    def observable(payload_folds: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {key: trade[key] for key in ("entry_time", "exit_time", "exit_reason", "net_pnl", "r_result")}
            for fold in payload_folds for trade in fold["trades"]
        ]

    repriced_obs = observable(repriced["folds"])
    full_obs = observable(full_folds)
    equal = repriced_obs == full_obs
    common_meta = {
        "subject": "amd_crt", "arm_id": "baseline", "scenario": scenario,
        "dataset_fingerprint": fingerprint, "comparison_fields": list(repriced_obs[0]),
        "bit_a_bit_equal": equal, "n_trades": len(full_obs),
    }
    _write(ARTIFACTS / "equivalence_amd_crt_baseline_por_tramo_realista_repriced.json", {
        "metadata": {**common_meta, "method": "repriced_from_canonical"}, "folds": repriced["folds"],
    })
    _write(ARTIFACTS / "equivalence_amd_crt_baseline_por_tramo_realista_full_run.json", {
        "metadata": {**common_meta, "method": "full_backtest"}, "folds": full_folds,
    })
    if not equal:
        first = next((i for i, pair in enumerate(zip(repriced_obs, full_obs)) if pair[0] != pair[1]), None)
        raise RuntimeError(f"Equivalence failed; first differing trade index={first}, counts={len(repriced_obs)}/{len(full_obs)}")
    print(f"Equivalence PASS: {len(full_obs)} trades bit-for-bit on net R/timestamps/reasons")


if __name__ == "__main__":
    main()

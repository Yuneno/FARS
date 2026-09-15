#!/usr/bin/env python3
"""Reproduce FARS Block C3: CPCV/PBO, Gate 4, stress, and Gate 7."""

from __future__ import annotations

import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lab_artifacts.run_c1_walkforward import (
    CALIBRATION_BARS_COUNT,
    EMBARGO_H_BARS,
    M5_MEMBER,
    ZIP_PATH,
    audit_fold_overlap_and_embargo,
    evaluate_scenario,
    load_canonical_m5,
)
from src.backtest.crt4h import Crt4hStrategy, crt4h_config
from src.backtest.emas import EmasStrategy, emas_config
from src.backtest.executor import ExecutedTrade, run_backtest
from src.backtest.markets import MNQ
from src.backtest.smc_fvg import SmcFvgStrategy, smc_fvg_config
from src.bootstrap import analyze_bootstrap
from src.cpcv import CpcvObservation, compute_cpcv_pbo, performance_metrics
from src.hypothesis_registry import WalkForwardPlan
from src.ingestion import (
    CanonicalTradeDataset,
    CapabilityStatus,
    IngestionProvenance,
    TradeAuditReport,
)
from src.types import Trade


ARTIFACTS = REPO_ROOT / "lab_artifacts" / "c3_protocol"
PREREGISTRATION = ARTIFACTS / "preregistro.json"
CANONICAL_COST = {
    "commission_per_side": 2.0,
    "slippage_points": 0.0,
    "time_exit_slippage_points": 0.0,
}
POR_TRAMO = {
    "commission_per_side": 0.62,
    "slippage_points": 0.25,
    "time_exit_slippage_points": 0.25,
}


@dataclass(frozen=True)
class FrozenConfig:
    factory: Callable[[], object]
    config_factory: Callable[..., object]
    entry_order_type: str


CONFIGS = {
    "emas_baseline": FrozenConfig(lambda: EmasStrategy(target_rr=3.0), emas_config, "market"),
    "emas_rr_0_4": FrozenConfig(lambda: EmasStrategy(target_rr=0.4), emas_config, "market"),
    "emas_rr_0_3": FrozenConfig(lambda: EmasStrategy(target_rr=0.3), emas_config, "market"),
    "smc_fvg_baseline": FrozenConfig(lambda: SmcFvgStrategy(min_risk_pts=8.0), smc_fvg_config, "limit"),
    "smc_fvg_risk_5": FrozenConfig(lambda: SmcFvgStrategy(min_risk_pts=5.0), smc_fvg_config, "limit"),
    "smc_fvg_risk_10": FrozenConfig(lambda: SmcFvgStrategy(min_risk_pts=10.0), smc_fvg_config, "limit"),
    "crt4h_defaults": FrozenConfig(
        lambda: Crt4hStrategy(
            max_follow=3, min_rr=1.5, sl_pad_frac=0.1,
            disp_mult=1.2, max_hold_bars=96, require_bias=1,
        ),
        crt4h_config,
        "limit",
    ),
}


def _save(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def _zero_cost_config(frozen: FrozenConfig, **execution_overrides):
    return frozen.config_factory(
        market=MNQ,
        discrete_partial_contracts=True,
        time_exit_mode="market",
        **CANONICAL_COST,
        **execution_overrides,
    )


def _partial_was_closed(trade: ExecutedTrade, partial_fraction: float) -> bool:
    return trade.exit_reason == "break_even_stop" or (
        partial_fraction > 0
        and trade.exit_reason in {"stop_loss", "time_exit"}
        and trade.stop_price == trade.entry_price
    )


def _remaining_quantity(trade: ExecutedTrade, partial_fraction: float) -> int:
    if _partial_was_closed(trade, partial_fraction):
        return trade.quantity - int(trade.quantity * partial_fraction)
    return trade.quantity


def _is_stop_gap(trade: ExecutedTrade, bar_open: float) -> bool:
    return trade.exit_reason in {"stop_loss", "break_even_stop"} and (
        (trade.direction == "long" and bar_open < trade.stop_price)
        or (trade.direction == "short" and bar_open > trade.stop_price)
    )


def _net_r(
    trade: ExecutedTrade,
    *,
    config,
    entry_order_type: str,
    commission_per_side: float,
    slippage_points: float,
    all_legs: bool = False,
    stop_gap: bool = False,
) -> float:
    if all_legs:
        slipping_quantity = 2 * trade.quantity
    else:
        slipping_quantity = trade.quantity if entry_order_type == "market" else 0
        if trade.exit_reason in {"stop_loss", "break_even_stop", "time_exit"} and not stop_gap:
            slipping_quantity += _remaining_quantity(trade, config.partial_take_profit_fraction)
    costs = commission_per_side * 2 * trade.quantity
    costs += slippage_points * config.dollar_per_point * slipping_quantity
    return (trade.gross_pnl - costs) / trade.budgeted_risk_dollars


def _summary(values: list[float]) -> dict:
    metrics = performance_metrics(values)
    cumulative = 0.0
    peak = 0.0
    max_drawdown_r = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        max_drawdown_r = max(max_drawdown_r, peak - cumulative)
    return {
        **metrics,
        "net_r": float(sum(values)),
        "max_drawdown_r": max_drawdown_r,
        "max_drawdown_pct_at_1pct_risk": max_drawdown_r,
    }


def _run_oos_trades(frozen: FrozenConfig, bars, plan: WalkForwardPlan):
    config = _zero_cost_config(frozen)
    rows = []
    for fold in plan.folds:
        calibration = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT):fold.test_start_idx]
        result = run_backtest(
            bars[fold.test_start_idx:fold.test_end_idx],
            frozen.factory(),
            config,
            calibration_bars=calibration,
        )
        rows.extend((fold.fold_id, trade) for trade in result.trades)
    return config, rows


def _full_period_observations(bars, timestamp_index: dict, frozen: FrozenConfig):
    config = _zero_cost_config(frozen)
    result = run_backtest(bars, frozen.factory(), config)
    observations = []
    for trade in result.trades:
        observations.append(CpcvObservation(
            entry_idx=timestamp_index[trade.entry_time],
            exit_idx=timestamp_index[trade.exit_time],
            r_result=_net_r(
                trade,
                config=config,
                entry_order_type=frozen.entry_order_type,
                commission_per_side=POR_TRAMO["commission_per_side"],
                slippage_points=POR_TRAMO["slippage_points"],
            ),
        ))
    return observations


def run_cpcv(bars, timestamp_index: dict, preregistration: dict) -> dict:
    started = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    universe = preregistration["cpcv"]["universe"]
    observations = {}
    for config_id in universe:
        print(f"CPCV source backtest: {config_id}", flush=True)
        observations[config_id] = _full_period_observations(
            bars, timestamp_index, CONFIGS[config_id]
        )
    result = compute_cpcv_pbo(
        observations,
        n_bars=len(bars),
        n_groups=preregistration["cpcv"]["N"],
        k_test=preregistration["cpcv"]["k"],
        embargo_bars=preregistration["cpcv"]["embargo_h_bars"],
    )
    result["metadata"] = {
        "started_at_utc": started,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "wall_seconds": time.perf_counter() - t0,
        "purpose": "selection-overfit diagnostic; separate from causal walk-forward edge",
        "source_trade_counts": {key: len(value) for key, value in observations.items()},
    }
    return result


def _gate4_factory(config_id: str, parameters: dict):
    if config_id.startswith("smc_fvg"):
        return FrozenConfig(
            lambda: SmcFvgStrategy(**parameters),
            smc_fvg_config,
            "limit",
        )
    if config_id == "emas_baseline":
        return FrozenConfig(lambda: EmasStrategy(**parameters), emas_config, "market")
    raise KeyError(config_id)


def _oat_points(center: dict, grid: dict) -> list[tuple[str, dict, str | None, str]]:
    points = [("center", dict(center), None, "center")]
    for parameter, values in grid.items():
        low, middle, high = values
        if center[parameter] != middle:
            raise ValueError(f"preregistered center mismatch for {parameter}")
        for label, value in (("low", low), ("high", high)):
            parameters = dict(center)
            parameters[parameter] = value
            points.append((f"{parameter}__{label}", parameters, parameter, label))
    return points


def run_gate4(bars, plan: WalkForwardPlan, timestamp_index: dict, preregistration: dict) -> dict:
    output = {
        "metadata": {
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "protocol": preregistration["gate4"]["protocol"],
            "decision_rule": preregistration["gate4"]["decision"],
        },
        "configurations": {},
    }
    for config_id, declared in preregistration["gate4"]["subjects"].items():
        point_rows = []
        for point_id, parameters, varied_parameter, position in _oat_points(
            declared["center"], declared["grid"]
        ):
            print(f"Gate 4: {config_id} {point_id}", flush=True)
            frozen = _gate4_factory(config_id, parameters)
            execution = {}
            if config_id.startswith("smc_fvg"):
                execution = {
                    "f": parameters["f"],
                    "wait": parameters["wait"],
                    "cooldown": parameters["cooldown"],
                }
            zero_config = _zero_cost_config(frozen, **execution)
            accounting_config = frozen.config_factory(
                market=MNQ,
                discrete_partial_contracts=True,
                time_exit_mode="market",
                **POR_TRAMO,
                **execution,
            )
            audits = {
                fold.fold_id: audit_fold_overlap_and_embargo(
                    frozen.factory, zero_config, fold, bars, timestamp_index
                )
                for fold in plan.folds
            }
            result = evaluate_scenario(
                config_id,
                f"gate4:{point_id}",
                frozen.factory,
                zero_config,
                bars,
                plan,
                audits,
                [],
                accounting_cfg=accounting_config,
                entry_order_type=frozen.entry_order_type,
            )
            point_rows.append({
                "point_id": point_id,
                "varied_parameter": varied_parameter,
                "grid_position": position,
                "parameters": parameters,
                "result": result,
            })
        center_expectancy = next(
            row["result"]["aggregate_oos"]["global_expectancy_r"]
            for row in point_rows if row["point_id"] == "center"
        )
        for row in point_rows:
            expectancy = row["result"]["aggregate_oos"]["global_expectancy_r"]
            row["drop_fraction_vs_center"] = (
                (center_expectancy - expectancy) / abs(center_expectancy)
                if center_expectancy != 0 else None
            )
        all_positive = all(
            row["result"]["aggregate_oos"]["global_expectancy_r"] > 0 for row in point_rows
        )
        no_abrupt_drop = all(
            row["drop_fraction_vs_center"] is not None
            and row["drop_fraction_vs_center"] <= 0.40
            for row in point_rows
        )
        boundary_maxima = []
        for parameter in declared["grid"]:
            candidates = [
                row for row in point_rows
                if row["point_id"] == "center" or row["varied_parameter"] == parameter
            ]
            best = max(candidates, key=lambda row: row["result"]["aggregate_oos"]["global_expectancy_r"])
            if best["grid_position"] in {"low", "high"}:
                boundary_maxima.append({
                    "parameter": parameter,
                    "position": best["grid_position"],
                    "point_id": best["point_id"],
                    "expectancy_r": best["result"]["aggregate_oos"]["global_expectancy_r"],
                })
        output["configurations"][config_id] = {
            "center_expectancy_r": center_expectancy,
            "all_points_positive": all_positive,
            "no_drop_gt_40pct": no_abrupt_drop,
            "classification": "plateau" if all_positive and no_abrupt_drop else "spike",
            "verdict": "PASS" if all_positive and no_abrupt_drop else "FAIL",
            "optimum_on_grid_boundary": bool(boundary_maxima),
            "boundary_maxima": boundary_maxima,
            "points": point_rows,
        }
    output["metadata"]["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    return output


def _cbb_stress(values: list[float], *, seed: int, block_length: int, B: int) -> dict:
    transformed = np.asarray(
        [value - 0.10 if value >= 0 else 1.25 * value - 0.10 for value in values],
        dtype=np.float64,
    )
    rng = np.random.default_rng(seed)
    n = transformed.size
    n_blocks = math.ceil(n / block_length)
    expectancies = np.empty(B)
    drawdowns = np.empty(B)
    for replicate in range(B):
        starts = rng.integers(0, n, size=n_blocks)
        indices = np.concatenate([
            (np.arange(start, start + block_length) % n) for start in starts
        ])[:n]
        sample = transformed[indices]
        expectancies[replicate] = np.mean(sample)
        cumulative = np.cumsum(sample)
        peaks = np.maximum.accumulate(np.concatenate(([0.0], cumulative)))
        drawdowns[replicate] = np.max(peaks[1:] - cumulative)
    def distribution(array):
        return {
            "median": float(np.median(array)),
            "q05": float(np.quantile(array, 0.05)),
            "q25": float(np.quantile(array, 0.25)),
            "q75": float(np.quantile(array, 0.75)),
            "q95": float(np.quantile(array, 0.95)),
            "mean": float(np.mean(array)),
            "std": float(np.std(array, ddof=1)),
            "min": float(np.min(array)),
            "max": float(np.max(array)),
        }
    return {
        "transformation": "r-0.10 if r>=0 else 1.25*r-0.10",
        "block_length": block_length,
        "B": B,
        "master_seed": seed,
        "observed_transformed": _summary(transformed.tolist()),
        "bootstrap_expectancy_distribution": distribution(expectancies),
        "bootstrap_max_drawdown_r_distribution": distribution(drawdowns),
        "fraction_expectancy_positive": float(np.mean(expectancies > 0.0)),
        "fraction_drawdown_le_12r": float(np.mean(drawdowns <= 12.0)),
    }


def run_stress_and_gate7(bars, plan: WalkForwardPlan, timestamp_index: dict, preregistration: dict):
    stress = {
        "metadata": {
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "base": preregistration["stress"]["base"],
            "scenarios": preregistration["stress"]["scenarios"],
            "method_note": "Fixed post-execution counterfactual repricing/filtering; signals and executor are unchanged.",
        },
        "configurations": {},
    }
    gate7 = {
        "metadata": {
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "implementation": "src.bootstrap.analyze_bootstrap",
            "alpha_family": 0.05,
            "unsupported_or_inconclusive_rule": "definitive FAIL",
        },
        "configurations": {},
    }
    for config_id in preregistration["stress"]["scope"]:
        print(f"Stress/Gate 7 OOS source: {config_id}", flush=True)
        frozen = CONFIGS[config_id]
        config, fold_trades = _run_oos_trades(frozen, bars, plan)
        base_values = []
        half_frequency = []
        plus25 = []
        plus50 = []
        all_legs = []
        limit_no_fill = []
        gap_stop = []
        gap_count = 0
        no_fill_count = 0
        for fold_id in range(plan.n_folds):
            trades = [trade for row_fold, trade in fold_trades if row_fold == fold_id]
            for within_fold_index, trade in enumerate(trades):
                bar_open = bars[timestamp_index[trade.exit_time]].open
                gap = _is_stop_gap(trade, bar_open)
                gap_count += int(gap)
                kwargs = {
                    "trade": trade,
                    "config": config,
                    "entry_order_type": frozen.entry_order_type,
                }
                base_r = _net_r(
                    **kwargs,
                    commission_per_side=0.62,
                    slippage_points=0.25,
                )
                base_values.append(base_r)
                if within_fold_index % 2 == 0:
                    half_frequency.append(base_r)
                plus25.append(_net_r(
                    **kwargs,
                    commission_per_side=0.62 * 1.25,
                    slippage_points=0.25 * 1.25,
                ))
                plus50.append(_net_r(
                    **kwargs,
                    commission_per_side=0.62 * 1.50,
                    slippage_points=0.25 * 1.50,
                ))
                all_legs.append(_net_r(
                    **kwargs,
                    commission_per_side=0.62,
                    slippage_points=0.25,
                    all_legs=True,
                ))
                entry_open = bars[timestamp_index[trade.entry_time]].open
                reject_limit = frozen.entry_order_type == "limit" and (
                    (trade.direction == "long" and entry_open < trade.entry_price)
                    or (trade.direction == "short" and entry_open > trade.entry_price)
                )
                if reject_limit:
                    no_fill_count += 1
                else:
                    limit_no_fill.append(base_r)
                gap_stop.append(_net_r(
                    **kwargs,
                    commission_per_side=0.62,
                    slippage_points=0.25,
                    stop_gap=gap,
                ))
        scenario_rows = {
            "por_tramo": _summary(base_values),
            "cost_plus_25pct": _summary(plus25),
            "cost_plus_50pct": _summary(plus50),
            "all_legs_adverse_slippage": _summary(all_legs),
            "limit_gap_no_fill": {**_summary(limit_no_fill), "discarded_trades": no_fill_count},
            "gap_stop_next_open": {**_summary(gap_stop), "gap_stop_trades": gap_count},
            "degraded_heavy_loss_cbb": _cbb_stress(
                base_values, seed=20260914, block_length=40, B=2000
            ),
            "half_frequency": _summary(half_frequency),
        }
        stress["configurations"][config_id] = {
            "source_oos_trades": len(fold_trades),
            "scenarios": scenario_rows,
        }

        canonical_trades = tuple(
            Trade(
                r_result=value,
                trade_id=f"{config_id}-{index + 1}",
                timestamp=trade.entry_time,
                date=trade.entry_time.date().isoformat(),
                asset="MNQ",
                direction=trade.direction,
                entry_price=trade.entry_price,
                stop_price=trade.stop_price,
                exit_price=trade.exit_price,
                strategy=config_id,
            )
            for index, ((_, trade), value) in enumerate(zip(fold_trades, base_values))
        )
        n = len(canonical_trades)
        dataset = CanonicalTradeDataset(
            trades=canonical_trades,
            audit=TradeAuditReport(total_rows=n, accepted_rows=n, rejected_rows=0),
            capabilities={
                "core_metrics": CapabilityStatus(True),
                "temporal_analysis": CapabilityStatus(True),
                "daily_rule_simulation": CapabilityStatus(True),
            },
            provenance=IngestionProvenance(
                schema_version="1.2-phase8a",
                source_path="C3 in-memory chronological OOS trades",
                source_sha256=preregistration["dataset"]["sha256"],
                source_size_bytes=0,
                resolved_mapping={"r_result": "repriced ExecutedTrade.r_result"},
                outcomes_finalized=True,
                analysis_timezone="UTC",
                delimiter=",",
                encoding="utf-8",
            ),
            source="c3_oos_walk_forward",
        )
        phase10 = analyze_bootstrap(
            dataset,
            master_seed=preregistration["gate7"]["master_seed"],
            B=preregistration["gate7"]["B"],
        )
        tests = {row["id"]: row for row in phase10["diagnostics"]["tests"]}
        required = preregistration["gate7"]["required_tests"]
        state = phase10["eligibility"]["state"]
        gate7["configurations"][config_id] = {
            "n_oos": n,
            "verdict": "FAIL" if state == "unsupported_or_inconclusive" else "PASS",
            "classifier_state": state,
            "classifier_reasons": phase10["eligibility"]["reasons"],
            "rejecting_tests": phase10["eligibility"]["rejecting_tests"],
            "alpha_family": phase10["diagnostics"]["alpha_family"],
            "alpha_b": phase10["diagnostics"]["alpha_b"],
            "required_level_halves_tests": {test_id: tests.get(test_id) for test_id in required},
            "phase10_result": phase10,
        }
    finished = datetime.now(timezone.utc).isoformat()
    stress["metadata"]["finished_at_utc"] = finished
    gate7["metadata"]["finished_at_utc"] = finished
    return stress, gate7


def _write_manifest(bars, fingerprint: str, preregistration: dict, started_at: str) -> None:
    artifacts = {}
    artifact_paths = {
        "lab_artifacts/c3_protocol/preregistro.json": ARTIFACTS / "preregistro.json",
        "lab_artifacts/c3_protocol/cpcv_pbo.json": ARTIFACTS / "cpcv_pbo.json",
        "lab_artifacts/c3_protocol/gate4_neighborhood.json": ARTIFACTS / "gate4_neighborhood.json",
        "lab_artifacts/c3_protocol/stress_matrix.json": ARTIFACTS / "stress_matrix.json",
        "lab_artifacts/c3_protocol/gate7_structural.json": ARTIFACTS / "gate7_structural.json",
        "lab_artifacts/c3_protocol/C3_SUMMARY.md": ARTIFACTS / "C3_SUMMARY.md",
        "lab_artifacts/c3_protocol/BLOCKERS.md": ARTIFACTS / "BLOCKERS.md",
        "lab_artifacts/run_c3_protocol.py": REPO_ROOT / "lab_artifacts" / "run_c3_protocol.py",
        "src/cpcv.py": REPO_ROOT / "src" / "cpcv.py",
        "tests/test_cpcv.py": REPO_ROOT / "tests" / "test_cpcv.py",
        "docs/refactor/c3-eligibility.md": REPO_ROOT / "docs" / "refactor" / "c3-eligibility.md",
    }
    for name, path in artifact_paths.items():
        artifacts[name] = {
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
            "mtime_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        }
    payload = {
        "schema_version": "fars-c3-manifest-v1",
        "metadata": {
            "started_at_utc": started_at,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "base_commit": preregistration["base"]["commit"],
            "requested_branch": preregistration["base"]["requested_branch"],
            "actual_branch": preregistration["base"]["actual_branch_at_preregistration"],
            "python": sys.version,
        },
        "dataset": {
            "archive": str(ZIP_PATH),
            "member": M5_MEMBER,
            "sha256": fingerprint,
            "bars_count": len(bars),
            "start_time": bars[0].timestamp.isoformat(),
            "end_time": bars[-1].timestamp.isoformat(),
            "provenance": "unchanged C1/C2 canonical post-2019 dataset",
        },
        "preregistration_check": {
            "content_timestamp": preregistration["preregistered_at_utc"],
            "preregistration_precedes_run": preregistration["preregistered_at_utc"] < started_at,
            "N": preregistration["cpcv"]["N"],
            "k": preregistration["cpcv"]["k"],
            "amendments": preregistration["amendments"],
        },
        "tests": {
            "baseline_expected": {"passed": 1455, "skipped": 2},
            "c3_tests_added": 3,
            "full_suite": {"passed": 1458, "skipped": 2, "failed": 0, "seconds": 246.63},
            "command": "E:/FARS-LAB/.venv-fars/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=E:/FARS-LAB/FARS/lab_artifacts/pytest-c3-suite-green",
            "sandbox_temp_permission_shim": "temporary sitecustomize forced workspace tempfile root and mkdir mode 0777; deleted after run",
        },
        "artifacts": artifacts,
    }
    _save(ARTIFACTS / "manifest.json", payload)


def main() -> None:
    started_at = datetime.now(timezone.utc).isoformat()
    preregistration = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
    if not preregistration["preregistered_at_utc"] < started_at:
        raise SystemExit("preregistration must precede the first C3 run")
    bars, fingerprint = load_canonical_m5(ZIP_PATH, M5_MEMBER)
    if fingerprint != preregistration["dataset"]["sha256"]:
        raise SystemExit("canonical dataset fingerprint differs from preregistration")
    timestamp_index = {bar.timestamp: index for index, bar in enumerate(bars)}
    if len(timestamp_index) != len(bars):
        raise SystemExit("canonical bars contain duplicate timestamps")
    plan = WalkForwardPlan.create_calendar_rolling(
        bars,
        train_months=36,
        test_months=6,
        step_months=6,
        purge_gap_bars=0,
        warmup_bars=CALIBRATION_BARS_COUNT,
    )
    if EMBARGO_H_BARS != preregistration["cpcv"]["embargo_h_bars"]:
        raise SystemExit("embargo mismatch")

    cpcv = run_cpcv(bars, timestamp_index, preregistration)
    _save(ARTIFACTS / "cpcv_pbo.json", cpcv)
    gate4 = run_gate4(bars, plan, timestamp_index, preregistration)
    _save(ARTIFACTS / "gate4_neighborhood.json", gate4)
    stress, gate7 = run_stress_and_gate7(
        bars, plan, timestamp_index, preregistration
    )
    _save(ARTIFACTS / "stress_matrix.json", stress)
    _save(ARTIFACTS / "gate7_structural.json", gate7)
    _write_manifest(bars, fingerprint, preregistration, started_at)


if __name__ == "__main__":
    main()

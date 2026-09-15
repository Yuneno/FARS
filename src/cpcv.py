"""Combinatorial purged cross-validation diagnostics for frozen strategies.

This module operates on already generated, chronologically indexed trade
outcomes.  It does not fit a strategy and it does not replace causal
walk-forward evaluation.  Its sole purpose is selection-overfit diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import math
from typing import Iterable, Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class CpcvObservation:
    """One closed trade located on the canonical bar index."""

    entry_idx: int
    exit_idx: int
    r_result: float

    def __post_init__(self) -> None:
        if self.entry_idx < 0 or self.exit_idx < self.entry_idx:
            raise ValueError("observation indices must satisfy 0 <= entry <= exit")
        if not math.isfinite(self.r_result):
            raise ValueError("r_result must be finite")


def contiguous_groups(n_items: int, n_groups: int) -> tuple[tuple[int, int], ...]:
    """Return contiguous half-open groups whose sizes differ by at most one."""

    if n_items < 1:
        raise ValueError("n_items must be positive")
    if n_groups < 2 or n_groups > n_items:
        raise ValueError("n_groups must be between 2 and n_items")
    quotient, remainder = divmod(n_items, n_groups)
    groups = []
    start = 0
    for group_id in range(n_groups):
        size = quotient + (1 if group_id < remainder else 0)
        groups.append((start, start + size))
        start += size
    return tuple(groups)


def _inside(index: int, intervals: Sequence[tuple[int, int]]) -> bool:
    return any(start <= index < end for start, end in intervals)


def split_path_observations(
    observations: Sequence[CpcvObservation],
    groups: Sequence[tuple[int, int]],
    test_group_ids: Iterable[int],
    *,
    embargo_bars: int,
) -> tuple[list[float], list[float], dict[str, int]]:
    """Split one CPCV path, purging overlap and embargoing after every test group."""

    if embargo_bars < 0:
        raise ValueError("embargo_bars must be non-negative")
    test_ids = tuple(sorted(set(test_group_ids)))
    if not test_ids or any(group_id < 0 or group_id >= len(groups) for group_id in test_ids):
        raise ValueError("test_group_ids must identify at least one valid group")
    test_intervals = [groups[group_id] for group_id in test_ids]
    embargo_intervals = [
        (end, min(groups[-1][1], end + embargo_bars))
        for _, end in test_intervals
    ]

    train: list[float] = []
    test: list[float] = []
    purged = 0
    embargoed = 0
    for observation in observations:
        if _inside(observation.entry_idx, test_intervals):
            test.append(observation.r_result)
            continue
        overlaps_test = any(
            observation.entry_idx < test_end and observation.exit_idx >= test_start
            for test_start, test_end in test_intervals
        )
        if overlaps_test:
            purged += 1
            continue
        if _inside(observation.entry_idx, embargo_intervals):
            embargoed += 1
            continue
        train.append(observation.r_result)
    return train, test, {
        "candidate_train_trades": len(train) + purged + embargoed,
        "retained_train_trades": len(train),
        "test_trades": len(test),
        "purged_train_trades": purged,
        "embargoed_train_trades": embargoed,
    }


def performance_metrics(values: Sequence[float]) -> dict[str, float | int | None]:
    """Finite JSON-safe ranking and dispersion metrics."""

    array = np.asarray(values, dtype=np.float64)
    n = int(array.size)
    if n == 0:
        return {
            "n": 0,
            "mean_r": 0.0,
            "median_r": 0.0,
            "std_r": 0.0,
            "iqr_r": 0.0,
            "sharpe_like": 0.0,
            "profit_factor": None,
        }
    mean = float(np.mean(array))
    std = float(np.std(array, ddof=1)) if n > 1 else 0.0
    losses = -array[array < 0.0].sum()
    profit_factor = float(array[array > 0.0].sum() / losses) if losses > 0.0 else None
    return {
        "n": n,
        "mean_r": mean,
        "median_r": float(np.median(array)),
        "std_r": std,
        "iqr_r": float(np.quantile(array, 0.75) - np.quantile(array, 0.25)),
        "sharpe_like": mean / std if std > 0.0 else 0.0,
        "profit_factor": profit_factor,
    }


def _rank_ids(metrics: Mapping[str, Mapping[str, float | int | None]], key: str) -> dict[str, int]:
    ordered = sorted(metrics, key=lambda config_id: (float(metrics[config_id][key]), config_id))
    return {config_id: rank for rank, config_id in enumerate(ordered, start=1)}


def _winner(metrics: Mapping[str, Mapping[str, float | int | None]], key: str) -> str:
    return min(metrics, key=lambda config_id: (-float(metrics[config_id][key]), config_id))


def _logit_rank(rank: int, n_configs: int) -> tuple[float, float]:
    omega = (rank - 0.5) / n_configs
    return omega, math.log(omega / (1.0 - omega))


def _distribution(values: Sequence[float]) -> dict[str, float | int | list[float]]:
    array = np.asarray(values, dtype=np.float64)
    return {
        "n": int(array.size),
        "median": float(np.median(array)),
        "q1": float(np.quantile(array, 0.25)),
        "q3": float(np.quantile(array, 0.75)),
        "iqr": float(np.quantile(array, 0.75) - np.quantile(array, 0.25)),
        "mean": float(np.mean(array)),
        "std": float(np.std(array, ddof=1)) if array.size > 1 else 0.0,
        "min": float(np.min(array)),
        "max": float(np.max(array)),
        "values": [float(value) for value in array],
    }


def compute_cpcv_pbo(
    observations_by_config: Mapping[str, Sequence[CpcvObservation]],
    *,
    n_bars: int,
    n_groups: int,
    k_test: int,
    embargo_bars: int,
) -> dict:
    """Compute every CPCV path and primary/secondary PBO diagnostics."""

    config_ids = sorted(observations_by_config)
    if len(config_ids) < 2:
        raise ValueError("PBO requires at least two configurations")
    if k_test < 1 or k_test >= n_groups:
        raise ValueError("k_test must be between 1 and n_groups - 1")
    groups = contiguous_groups(n_bars, n_groups)
    path_rows = []
    primary_lambdas: list[float] = []
    secondary_lambdas: list[float] = []
    primary_oos: list[float] = []
    secondary_oos: list[float] = []

    for test_ids in combinations(range(n_groups), k_test):
        config_rows = {}
        train_metrics = {}
        test_metrics = {}
        for config_id in config_ids:
            train, test, audit = split_path_observations(
                observations_by_config[config_id], groups, test_ids,
                embargo_bars=embargo_bars,
            )
            train_metrics[config_id] = performance_metrics(train)
            test_metrics[config_id] = performance_metrics(test)
            config_rows[config_id] = {
                "train": train_metrics[config_id],
                "test": test_metrics[config_id],
                "purge_embargo_audit": audit,
            }

        primary_winner = _winner(train_metrics, "mean_r")
        secondary_winner = _winner(train_metrics, "sharpe_like")
        primary_ranks = _rank_ids(test_metrics, "mean_r")
        secondary_ranks = _rank_ids(test_metrics, "sharpe_like")
        primary_rank = primary_ranks[primary_winner]
        secondary_rank = secondary_ranks[secondary_winner]
        primary_omega, primary_lambda = _logit_rank(primary_rank, len(config_ids))
        secondary_omega, secondary_lambda = _logit_rank(secondary_rank, len(config_ids))
        primary_lambdas.append(primary_lambda)
        secondary_lambdas.append(secondary_lambda)
        primary_oos.append(float(test_metrics[primary_winner]["mean_r"]))
        secondary_oos.append(float(test_metrics[secondary_winner]["sharpe_like"]))
        path_rows.append({
            "path_id": "test_" + "_".join(f"{group_id:02d}" for group_id in test_ids),
            "test_group_ids": list(test_ids),
            "configurations": config_rows,
            "primary": {
                "train_winner": primary_winner,
                "test_rank": primary_rank,
                "median_rank": (len(config_ids) + 1) / 2,
                "below_median": primary_lambda < 0.0,
                "omega": primary_omega,
                "logit_lambda": primary_lambda,
                "winner_test_mean_r": test_metrics[primary_winner]["mean_r"],
            },
            "secondary": {
                "train_winner": secondary_winner,
                "test_rank": secondary_rank,
                "median_rank": (len(config_ids) + 1) / 2,
                "below_median": secondary_lambda < 0.0,
                "omega": secondary_omega,
                "logit_lambda": secondary_lambda,
                "winner_test_sharpe_like": test_metrics[secondary_winner]["sharpe_like"],
            },
        })

    n_paths = len(path_rows)
    return {
        "parameters": {
            "N": n_groups,
            "k": k_test,
            "n_paths": n_paths,
            "embargo_h_bars": embargo_bars,
            "configurations": config_ids,
            "primary_rank_metric": "mean_r",
            "secondary_rank_metric": "sharpe_like",
        },
        "groups": [
            {"group_id": group_id, "start_idx": start, "end_idx": end, "n_bars": end - start}
            for group_id, (start, end) in enumerate(groups)
        ],
        "primary_pbo": {
            "count_below_median": sum(value < 0.0 for value in primary_lambdas),
            "fraction": sum(value < 0.0 for value in primary_lambdas) / n_paths,
            "logit_distribution": _distribution(primary_lambdas),
            "selected_oos_metric_distribution": _distribution(primary_oos),
        },
        "secondary_pbo": {
            "count_below_median": sum(value < 0.0 for value in secondary_lambdas),
            "fraction": sum(value < 0.0 for value in secondary_lambdas) / n_paths,
            "logit_distribution": _distribution(secondary_lambdas),
            "selected_oos_metric_distribution": _distribution(secondary_oos),
        },
        "paths": path_rows,
    }


__all__ = [
    "CpcvObservation",
    "compute_cpcv_pbo",
    "contiguous_groups",
    "performance_metrics",
    "split_path_observations",
]

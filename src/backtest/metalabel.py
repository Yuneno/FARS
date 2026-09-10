"""Logistic meta-labeling for executed AMD+CRT decisions.

The evaluator uses expanding chronological windows over whole order groups
(normally session days).  Missing numeric features are imputed from each
fold's training window only, so future observations cannot influence fitting.

Run the end-to-end MNQ evaluation with::

    python -m src.backtest.metalabel /path/to/MNQ_M5.csv
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.backtest.amd_crt import AmdCrtStrategy, amd_crt_config
from src.backtest.decisions import LabeledAmdCrtDecision, join_decisions_to_outcomes
from src.backtest.executor import run_backtest
from src.backtest.mnq_csv import load_mnq_csv

FEATURE_NAMES = (
    "weekday",
    "direction",
    "crt_confirmed",
    "ema_regime",
    "atr",
    "sl_pts",
    "tp_pts",
    "pre_ny_amplitude",
    "median_amplitude",
    "compression_ratio",
)


@dataclass(frozen=True)
class FeatureMeta:
    """Chronological identifiers retained for one feature row."""

    day: date
    timestamp: datetime


@dataclass(frozen=True)
class MetricSet:
    accuracy: float
    roc_auc: float
    precision: float
    recall: float


@dataclass(frozen=True)
class FoldResult:
    fold: int
    n_train: int
    n_test: int
    train_start: object
    train_end: object
    test_start: object
    test_end: object
    train_indices: tuple[int, ...]
    test_indices: tuple[int, ...]
    train_majority_class: int
    model: MetricSet
    baseline: MetricSet


@dataclass(frozen=True)
class WalkForwardResult:
    folds: tuple[FoldResult, ...]
    aggregate: MetricSet
    baseline_aggregate: MetricSet
    n_oos: int

    def to_dict(self) -> dict[str, Any]:
        """Return a concise JSON representation (indices remain inspectable in Python)."""
        payload = asdict(self)
        for fold in payload["folds"]:
            fold.pop("train_indices")
            fold.pop("test_indices")
        return _jsonable(payload)


def _number(value: float | None) -> float:
    if value is None:
        return math.nan
    value = float(value)
    return value if math.isfinite(value) else math.nan


def build_features(
    rows: Sequence[LabeledAmdCrtDecision],
) -> tuple[np.ndarray, np.ndarray, tuple[FeatureMeta, ...]]:
    """Build causal features and win labels from executed accepted rows only.

    ``direction`` is encoded long=1, short=0.  ``ema_regime`` is encoded
    long=1, short=-1, unavailable=0.  Numeric missing/non-finite feature values
    remain NaN for fold-local median imputation during evaluation.
    """
    features: list[list[float]] = []
    labels: list[int] = []
    meta: list[FeatureMeta] = []

    for row in rows:
        if row.decision != "accepted" or not row.executed or row.r_result is None:
            continue
        r_result = float(row.r_result)
        if not math.isfinite(r_result):
            raise ValueError("executed r_result must be finite")

        pre_ny = _number(row.pre_ny_amplitude)
        median = _number(row.median_amplitude)
        ratio = (
            pre_ny / median
            if math.isfinite(pre_ny) and math.isfinite(median) and median != 0.0
            else math.nan
        )
        features.append(
            [
                float(row.weekday),
                1.0 if row.direction == "long" else 0.0,
                float(row.crt_confirmed),
                {"long": 1.0, "short": -1.0, None: 0.0}[row.ema_regime],
                _number(row.atr),
                _number(row.sl_pts),
                _number(row.tp_pts),
                pre_ny,
                median,
                ratio,
            ]
        )
        labels.append(int(r_result > 0.0))
        meta.append(FeatureMeta(day=row.day, timestamp=row.timestamp))

    return (
        np.asarray(features, dtype=float).reshape((-1, len(FEATURE_NAMES))),
        np.asarray(labels, dtype=np.int8),
        tuple(meta),
    )


def _metrics(y_true: np.ndarray, prediction: np.ndarray, score: np.ndarray) -> MetricSet:
    auc = (
        float(roc_auc_score(y_true, score))
        if np.unique(y_true).size == 2
        else math.nan
    )
    return MetricSet(
        accuracy=float(accuracy_score(y_true, prediction)),
        roc_auc=auc,
        precision=float(precision_score(y_true, prediction, zero_division=0)),
        recall=float(recall_score(y_true, prediction, zero_division=0)),
    )


def _ordered_group_blocks(order: np.ndarray, n_splits: int) -> list[np.ndarray]:
    indices = np.asarray(sorted(range(len(order)), key=lambda i: (order[i], i)), dtype=int)
    group_starts = [0]
    for position in range(1, len(indices)):
        if order[indices[position]] != order[indices[position - 1]]:
            group_starts.append(position)
    groups = np.split(indices, group_starts[1:])
    if len(groups) < n_splits + 1:
        raise ValueError(
            f"need at least {n_splits + 1} distinct chronological order groups; "
            f"got {len(groups)}"
        )
    group_number_blocks = np.array_split(np.arange(len(groups)), n_splits + 1)
    return [
        np.concatenate([groups[number] for number in block])
        for block in group_number_blocks
    ]


def evaluate_walk_forward(
    X: np.ndarray,
    y: np.ndarray,
    order: Sequence[object],
    *,
    n_splits: int = 5,
) -> WalkForwardResult:
    """Evaluate default logistic regression with expanding chronological folds.

    The majority baseline is learned independently from each training window.
    Its constant positive score is the training positive-class prevalence.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y)
    order_array = np.asarray(order, dtype=object)
    if X.ndim != 2:
        raise ValueError("X must be a two-dimensional matrix")
    if len(X) != len(y) or len(y) != len(order_array):
        raise ValueError("X, y, and order must have equal lengths")
    if n_splits < 2:
        raise ValueError("n_splits must be at least 2")
    if not np.isin(y, (0, 1)).all():
        raise ValueError("y must contain only binary labels 0 and 1")

    blocks = _ordered_group_blocks(order_array, n_splits)
    folds: list[FoldResult] = []
    all_y: list[np.ndarray] = []
    all_prediction: list[np.ndarray] = []
    all_score: list[np.ndarray] = []
    all_baseline_prediction: list[np.ndarray] = []
    all_baseline_score: list[np.ndarray] = []

    for fold_number in range(1, n_splits + 1):
        train_indices = np.concatenate(blocks[:fold_number]).astype(int)
        test_indices = blocks[fold_number].astype(int)
        if np.unique(y[train_indices]).size != 2:
            raise ValueError(
                f"fold {fold_number} training window contains only one class; "
                "logistic regression cannot be fit"
            )

        pipeline = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                ("scaler", StandardScaler()),
                ("classifier", LogisticRegression()),
            ]
        )
        pipeline.fit(X[train_indices], y[train_indices])
        model_prediction = pipeline.predict(X[test_indices])
        model_score = pipeline.predict_proba(X[test_indices])[:, 1]

        positive_rate = float(np.mean(y[train_indices]))
        majority_class = int(positive_rate >= 0.5)
        baseline_prediction = np.full(len(test_indices), majority_class, dtype=int)
        # The requested trivial classifier always predicts its training-window
        # majority.  Use that same constant class as its score; whenever both
        # test classes exist its ROC AUC is therefore the expected 0.5.
        baseline_score = baseline_prediction.astype(float)

        train_orders = order_array[train_indices]
        test_orders = order_array[test_indices]
        folds.append(
            FoldResult(
                fold=fold_number,
                n_train=len(train_indices),
                n_test=len(test_indices),
                train_start=train_orders[0],
                train_end=train_orders[-1],
                test_start=test_orders[0],
                test_end=test_orders[-1],
                train_indices=tuple(int(i) for i in train_indices),
                test_indices=tuple(int(i) for i in test_indices),
                train_majority_class=majority_class,
                model=_metrics(y[test_indices], model_prediction, model_score),
                baseline=_metrics(
                    y[test_indices], baseline_prediction, baseline_score
                ),
            )
        )
        all_y.append(y[test_indices])
        all_prediction.append(model_prediction)
        all_score.append(model_score)
        all_baseline_prediction.append(baseline_prediction)
        all_baseline_score.append(baseline_score)

    pooled_y = np.concatenate(all_y)
    return WalkForwardResult(
        folds=tuple(folds),
        aggregate=_metrics(
            pooled_y, np.concatenate(all_prediction), np.concatenate(all_score)
        ),
        baseline_aggregate=_metrics(
            pooled_y,
            np.concatenate(all_baseline_prediction),
            np.concatenate(all_baseline_score),
        ),
        n_oos=len(pooled_y),
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def run_mnq(csv_path: str | Path, *, n_splits: int = 5) -> dict[str, Any]:
    """Run AMD+CRT and chronological meta-label evaluation on an MNQ M5 CSV."""
    bars = load_mnq_csv(csv_path, target_interval_minutes=5)
    strategy = AmdCrtStrategy()
    backtest = run_backtest(bars, strategy, amd_crt_config())
    rows = join_decisions_to_outcomes(strategy.decisions, backtest.trades)
    X, y, meta = build_features(rows)
    result = evaluate_walk_forward(X, y, [item.day for item in meta], n_splits=n_splits)

    model_accuracy = result.aggregate.accuracy
    baseline_accuracy = result.baseline_aggregate.accuracy
    model_auc = result.aggregate.roc_auc
    baseline_auc = result.baseline_aggregate.roc_auc
    return {
        "source": str(Path(csv_path).resolve()),
        "bars": len(bars),
        "executed_rows": len(y),
        "walk_forward": result.to_dict(),
        "honest_summary": (
            "No strategy improvement is established. Out-of-sample logistic "
            f"accuracy {'exceeds' if model_accuracy > baseline_accuracy else 'does not exceed'} "
            f"the fold-local majority baseline ({model_accuracy:.4f} vs "
            f"{baseline_accuracy:.4f}); ROC AUC is {model_auc:.4f} vs the "
            f"baseline's {baseline_auc:.4f}. This evaluates signal-quality "
            "prediction only, not strategy returns."
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mnq_csv", type=Path)
    parser.add_argument("--folds", type=int, default=5)
    args = parser.parse_args(argv)
    print(json.dumps(run_mnq(args.mnq_csv, n_splits=args.folds), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "FEATURE_NAMES",
    "FeatureMeta",
    "FoldResult",
    "MetricSet",
    "WalkForwardResult",
    "build_features",
    "evaluate_walk_forward",
    "run_mnq",
]

"""Tests for causal feature construction and chronological meta-label evaluation."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import numpy as np
import pytest

from src.backtest.amd_crt import ET
from src.backtest.decisions import LabeledAmdCrtDecision
from src.backtest.metalabel import FEATURE_NAMES, build_features, evaluate_walk_forward


def _row(
    index: int,
    *,
    executed: bool = True,
    decision: str = "accepted",
    r_result: float | None = 1.0,
    direction: str = "long",
    ema_regime: str | None = "short",
    median_amplitude: float = 20.0,
) -> LabeledAmdCrtDecision:
    day = date(2024, 1, 1) + timedelta(days=index)
    return LabeledAmdCrtDecision(
        day=day,
        weekday=day.weekday(),
        direction=direction,  # type: ignore[arg-type]
        crt_confirmed=True,
        ema_regime=ema_regime,  # type: ignore[arg-type]
        atr=None if index == 0 else 5.0,
        sl_pts=10.0,
        tp_pts=20.0,
        pre_ny_amplitude=10.0,
        median_amplitude=median_amplitude,
        decision=decision,  # type: ignore[arg-type]
        timestamp=datetime(2024, 1, 1, 10, tzinfo=ET) + timedelta(days=index),
        r_result=r_result,
        exit_reason="take_profit" if executed else None,
        executed=executed,
    )


def test_build_features_filters_and_encodes_executed_accepted_rows():
    rows = [
        _row(0, direction="long", ema_regime="short", r_result=0.25),
        _row(1, executed=False, decision="not_executed", r_result=None),
        _row(2, executed=False, decision="crt_not_confirmed", r_result=None),
        _row(3, direction="short", ema_regime=None, r_result=0.0),
    ]

    X, y, meta = build_features(rows)

    assert X.shape == (2, len(FEATURE_NAMES))
    assert y.tolist() == [1, 0]
    assert [item.day for item in meta] == [rows[0].day, rows[3].day]
    assert X[0, FEATURE_NAMES.index("direction")] == 1.0
    assert X[1, FEATURE_NAMES.index("direction")] == 0.0
    assert X[0, FEATURE_NAMES.index("ema_regime")] == -1.0
    assert X[1, FEATURE_NAMES.index("ema_regime")] == 0.0
    assert X[0, FEATURE_NAMES.index("compression_ratio")] == 0.5


def test_build_features_leaves_missing_and_zero_division_as_nan_for_imputation():
    X, _y, _meta = build_features([_row(0, median_amplitude=0.0)])

    assert math.isnan(X[0, FEATURE_NAMES.index("atr")])
    assert math.isnan(X[0, FEATURE_NAMES.index("compression_ratio")])


def test_walk_forward_is_expanding_chronological_and_never_leaks_test_rows():
    # Deliberately shuffled input. Alternating labels keep every train fold fit-able.
    chronological = np.arange(18, dtype=float)
    permutation = np.array([8, 0, 17, 4, 12, 2, 15, 6, 10, 1, 16, 5, 11, 3, 14, 7, 13, 9])
    X = np.column_stack([chronological[permutation], chronological[permutation] % 3])
    y = (chronological[permutation].astype(int) % 2).astype(int)

    result = evaluate_walk_forward(X, y, chronological[permutation], n_splits=5)

    assert len(result.folds) == 5
    for previous, fold in zip((None, *result.folds), result.folds):
        train_order = chronological[permutation[list(fold.train_indices)]]
        test_order = chronological[permutation[list(fold.test_indices)]]
        assert train_order.max() < test_order.min()
        assert set(fold.train_indices).isdisjoint(fold.test_indices)
        assert fold.n_test == len(fold.test_indices)
        if previous is not None:
            assert set(previous.train_indices).issubset(fold.train_indices)
            assert set(previous.test_indices).issubset(fold.train_indices)
    assert result.n_oos == 15


def test_walk_forward_keeps_equal_order_values_in_one_side_of_boundary():
    order = np.repeat(np.arange(9), 2)
    X = np.column_stack([order, np.arange(len(order))])
    y = np.tile([0, 1], 9)

    result = evaluate_walk_forward(X, y, order, n_splits=2)

    for fold in result.folds:
        train_orders = set(order[list(fold.train_indices)])
        test_orders = set(order[list(fold.test_indices)])
        assert train_orders.isdisjoint(test_orders)


def test_walk_forward_reports_model_and_majority_baseline_metrics():
    order = np.arange(30)
    y = np.tile([0, 1, 1], 10)
    X = np.column_stack([order, y])

    result = evaluate_walk_forward(X, y, order, n_splits=4)

    for fold in result.folds:
        assert fold.n_train > 0
        assert fold.n_test > 0
        for metrics in (fold.model, fold.baseline):
            assert 0.0 <= metrics.accuracy <= 1.0
            assert 0.0 <= metrics.precision <= 1.0
            assert 0.0 <= metrics.recall <= 1.0
            assert 0.0 <= metrics.roc_auc <= 1.0
    assert result.aggregate.accuracy >= result.baseline_aggregate.accuracy
    assert result.baseline_aggregate.roc_auc == 0.5


def test_walk_forward_rejects_single_class_training_window():
    X = np.arange(18, dtype=float).reshape(-1, 1)
    y = np.array([0] * 6 + [1] * 12)

    with pytest.raises(ValueError, match="fold 1.*only one class"):
        evaluate_walk_forward(X, y, np.arange(18), n_splits=2)

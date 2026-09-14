"""Hypothesis registry and temporal separation for FARS (P5).

Reuses FARS experiment registration concepts. Provides walk-forward
splitting, hypothesis tracking, and multiplicity controls.
"""

from __future__ import annotations

import json
import math
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Hypothesis registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Hypothesis:
    """One preregistered hypothesis."""

    family_id: str
    hypothesis_id: str
    variant: str
    description: str
    preregistered_at: str
    dataset_fingerprint: str
    n_folds: int
    state: Literal["preregistered", "tested", "rejected", "accepted"]
    alpha: float = 0.05
    n_candidates_tested: int = 0
    n_negative_results: int = 0
    results: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not (0 < self.alpha <= 1):
            raise ValueError(f"alpha must be in (0,1], got {self.alpha}")
        if self.n_candidates_tested < 0:
            raise ValueError(f"n_candidates_tested must be >= 0")
        if self.n_negative_results < 0:
            raise ValueError(f"n_negative_results must be >= 0")


@dataclass
class HypothesisRegistry:
    """Versioned registry of hypotheses with multiplicity tracking."""

    hypotheses: dict[str, Hypothesis] = field(default_factory=dict)
    version: str = "1.0"

    def register(self, h: Hypothesis) -> None:
        if h.hypothesis_id in self.hypotheses:
            existing = self.hypotheses[h.hypothesis_id]
            if existing.state == "tested":
                raise ValueError(
                    f"cannot re-register tested hypothesis {h.hypothesis_id}"
                )
        self.hypotheses[h.hypothesis_id] = h

    def update_result(
        self,
        hypothesis_id: str,
        *,
        state: Literal["tested", "rejected", "accepted"],
        p_value: float | None = None,
        t_stat: float | None = None,
    ) -> None:
        h = self.hypotheses[hypothesis_id]
        if p_value is not None and (math.isnan(p_value) or not (0 <= p_value <= 1)):
            raise ValueError(f"invalid p_value: {p_value}")
        results = dict(h.results)
        if p_value is not None:
            results["p_value"] = p_value
        if t_stat is not None:
            results["t_stat"] = t_stat
        updated = Hypothesis(
            family_id=h.family_id,
            hypothesis_id=h.hypothesis_id,
            variant=h.variant,
            description=h.description,
            preregistered_at=h.preregistered_at,
            dataset_fingerprint=h.dataset_fingerprint,
            n_folds=h.n_folds,
            state=state,
            alpha=h.alpha,
            n_candidates_tested=h.n_candidates_tested,
            n_negative_results=h.n_negative_results,
            results=results,
        )
        self.hypotheses[hypothesis_id] = updated

    def bonferroni_threshold(self, family_id: str) -> float:
        """Compute Bonferroni-corrected alpha for a family."""
        family = [
            h for h in self.hypotheses.values()
            if h.family_id == family_id
        ]
        if not family:
            return 0.0
        alpha = family[0].alpha
        n = len(family)
        return alpha / n

    def total_candidates(self, family_id: str) -> int:
        """Total candidates tested across the family."""
        return sum(
            h.n_candidates_tested
            for h in self.hypotheses.values()
            if h.family_id == family_id
        )


# ---------------------------------------------------------------------------
# Walk-forward temporal separation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Fold:
    """One fold in a walk-forward split."""

    fold_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_bars: int
    test_bars: int
    warmup_bars: int
    train_start_idx: int = 0
    train_end_idx: int = 0
    test_start_idx: int = 0
    test_end_idx: int = 0
    purge_gap_bars: int = 0


def _add_months(dt: datetime, n: int) -> datetime:
    year = dt.year + (dt.month - 1 + n) // 12
    month = (dt.month - 1 + n) % 12 + 1
    day = min(dt.day, 28) if dt.day > 28 else dt.day
    return dt.replace(year=year, month=month, day=day)


def _extract_bar_ts(bar: Any) -> str:
    if hasattr(bar, "timestamp"):
        ts = bar.timestamp
        return ts.isoformat() if isinstance(ts, datetime) else str(ts)
    if isinstance(bar, dict) and "timestamp" in bar:
        return str(bar["timestamp"])
    return str(bar)


@dataclass(frozen=True)
class WalkForwardPlan:
    """A walk-forward splitting plan."""

    n_folds: int
    folds: tuple[Fold, ...]
    purge_gap_bars: int = 0
    warmup_bars: int = 0

    @classmethod
    def create(
        cls,
        total_bars: int,
        n_folds: int,
        *,
        train_fraction: float = 0.7,
        purge_gap_bars: int = 0,
        warmup_bars: int = 0,
    ) -> WalkForwardPlan:
        """Create an expanding-window walk-forward plan.

        Training is strictly prior to test. Purge gap removes overlapping
        bars between train and test.
        """
        test_size = max(1, int(total_bars * (1 - train_fraction) / n_folds))
        folds = []
        for i in range(n_folds):
            test_end = total_bars - (n_folds - 1 - i) * test_size
            test_start = test_end - test_size
            train_end = test_start - purge_gap_bars
            train_start = warmup_bars
            if train_end <= train_start:
                continue
            folds.append(Fold(
                fold_id=i,
                train_start=str(train_start),
                train_end=str(train_end),
                test_start=str(test_start),
                test_end=str(test_end),
                train_bars=train_end - train_start,
                test_bars=test_end - test_start,
                warmup_bars=warmup_bars,
                train_start_idx=train_start,
                train_end_idx=train_end,
                test_start_idx=test_start,
                test_end_idx=test_end,
                purge_gap_bars=purge_gap_bars,
            ))
        return cls(
            n_folds=len(folds),
            folds=tuple(folds),
            purge_gap_bars=purge_gap_bars,
            warmup_bars=warmup_bars,
        )

    @classmethod
    def create_calendar_rolling(
        cls,
        bars: list[Any] | tuple[Any, ...],
        *,
        train_months: int = 36,
        test_months: int = 6,
        step_months: int = 6,
        start_date: str = "2019-07-01",
        end_date: str = "2026-07-01",
        purge_gap_bars: int = 0,
        warmup_bars: int = 0,
    ) -> WalkForwardPlan:
        """Create a calendar-based rolling walk-forward plan.

        Default parameters implement the 36m train / 6m test / 6m rolling step protocol:
        train_start = test_start - 36 months, train_end = test_start - purge_gap.
        """
        import bisect

        if not bars:
            return cls(n_folds=0, folds=(), purge_gap_bars=purge_gap_bars, warmup_bars=warmup_bars)

        ts_list = [_extract_bar_ts(b) for b in bars]
        dt_start = datetime.fromisoformat(start_date)
        dt_end = datetime.fromisoformat(end_date)

        folds: list[Fold] = []
        cur_train_start = dt_start
        fid = 0

        while True:
            cur_test_start = _add_months(cur_train_start, train_months)
            cur_test_end = _add_months(cur_test_start, test_months)
            if cur_test_end > dt_end:
                break

            tr_s_str = cur_train_start.strftime("%Y-%m-%d")
            tr_e_str = cur_test_start.strftime("%Y-%m-%d")
            te_s_str = cur_test_start.strftime("%Y-%m-%d")
            te_e_str = cur_test_end.strftime("%Y-%m-%d")

            tr_start_idx = bisect.bisect_left(ts_list, tr_s_str)
            tr_end_raw_idx = bisect.bisect_left(ts_list, tr_e_str)
            tr_end_idx = max(tr_start_idx, tr_end_raw_idx - purge_gap_bars)

            te_start_idx = bisect.bisect_left(ts_list, te_s_str)
            te_end_idx = bisect.bisect_left(ts_list, te_e_str)

            tr_bars = max(0, tr_end_idx - tr_start_idx)
            te_bars = max(0, te_end_idx - te_start_idx)
            warmup = min(warmup_bars, tr_start_idx) if warmup_bars > 0 else tr_start_idx

            if purge_gap_bars > 0 and tr_end_idx < tr_end_raw_idx and tr_end_idx > 0:
                actual_tr_end_str = ts_list[tr_end_idx - 1][:10]
            else:
                actual_tr_end_str = tr_e_str

            folds.append(
                Fold(
                    fold_id=fid,
                    train_start=tr_s_str,
                    train_end=actual_tr_end_str,
                    test_start=te_s_str,
                    test_end=te_e_str,
                    train_bars=tr_bars,
                    test_bars=te_bars,
                    warmup_bars=warmup,
                    train_start_idx=tr_start_idx,
                    train_end_idx=tr_end_idx,
                    test_start_idx=te_start_idx,
                    test_end_idx=te_end_idx,
                    purge_gap_bars=purge_gap_bars,
                )
            )
            fid += 1
            cur_train_start = _add_months(cur_train_start, step_months)

        return cls(
            n_folds=len(folds),
            folds=tuple(folds),
            purge_gap_bars=purge_gap_bars,
            warmup_bars=warmup_bars,
        )


def purge_train_by_trade_intervals(
    train_indices: list[int],
    test_start_idx: int,
    test_end_idx: int,
    trade_intervals: list[tuple[int, int | None]],
) -> list[int]:
    """Remove training bar indices that fall within trades overlapping the test window.

    A trade [entry, exit] overlaps the test window if entry < test_end AND
    (exit is None OR exit >= test_start).  All training bar indices within
    that trade's active interval are removed (conservative boundary).

    Parameters
    ----------
    train_indices : list[int]
        Bar indices in the training set.
    test_start_idx, test_end_idx : int
        Test window boundaries (bar indices, inclusive start, exclusive end).
    trade_intervals : list[tuple[int, int | None]]
        For each trade: (entry_bar_index, exit_bar_index_or_None).
        None exit means the trade is still open at the last known bar.

    Returns
    -------
    list[int]
        Purged training indices (sorted).
    """
    purged = []
    for idx in train_indices:
        keep = True
        for entry_idx, exit_idx in trade_intervals:
            effective_exit = exit_idx if exit_idx is not None else test_end_idx + 1
            # Trade overlaps test window
            if entry_idx < test_end_idx and effective_exit >= test_start_idx:
                # idx falls within this trade's active interval
                if entry_idx <= idx <= effective_exit:
                    keep = False
                    break
        if keep:
            purged.append(idx)
    return sorted(purged)


def apply_embargo(
    candidate_indices: list[int],
    test_end_idx: int,
    embargo_bars: int,
) -> list[int]:
    """Remove indices that fall within the embargo window immediately following the test set.

    Per AFML §7.4.2, observations starting in [test_end_idx, test_end_idx + embargo_bars]
    are embargoed to prevent post-test information leakage into subsequent training/evaluation sets.

    Parameters
    ----------
    candidate_indices : list[int]
        Bar indices under consideration (e.g. training set following a test set).
    test_end_idx : int
        End index of the test window (exclusive).
    embargo_bars : int
        Number of embargo bars (h).

    Returns
    -------
    list[int]
        Indices with embargoed bars removed (sorted).
    """
    if embargo_bars <= 0:
        return sorted(candidate_indices)
    embargo_end = test_end_idx + embargo_bars
    return sorted([idx for idx in candidate_indices if not (test_end_idx <= idx < embargo_end)])


def filter_trades_by_embargo(
    trade_intervals: list[tuple[int, int | None]],
    test_end_idx: int,
    embargo_bars: int,
) -> tuple[list[tuple[int, int | None]], list[tuple[int, int | None]]]:
    """Separate trades into retained and embargoed sets.

    Trades whose entry falls within [test_end_idx, test_end_idx + embargo_bars]
    are placed in the embargoed set.

    Parameters
    ----------
    trade_intervals : list[tuple[int, int | None]]
        For each trade: (entry_bar_index, exit_bar_index_or_None).
    test_end_idx : int
        End index of the test window (exclusive).
    embargo_bars : int
        Number of embargo bars (h).

    Returns
    -------
    tuple[list, list]
        (retained_trades, embargoed_trades)
    """
    if embargo_bars <= 0:
        return list(trade_intervals), []
    embargo_end = test_end_idx + embargo_bars
    retained = []
    embargoed = []
    for t in trade_intervals:
        entry_idx = t[0]
        if test_end_idx <= entry_idx < embargo_end:
            embargoed.append(t)
        else:
            retained.append(t)
    return retained, embargoed


__all__ = [
    "Hypothesis",
    "HypothesisRegistry",
    "Fold",
    "WalkForwardPlan",
    "purge_train_by_trade_intervals",
    "apply_embargo",
    "filter_trades_by_embargo",
]

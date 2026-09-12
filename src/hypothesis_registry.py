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
            ))
        return cls(
            n_folds=len(folds),
            folds=tuple(folds),
            purge_gap_bars=purge_gap_bars,
            warmup_bars=warmup_bars,
        )


__all__ = [
    "Hypothesis",
    "HypothesisRegistry",
    "Fold",
    "WalkForwardPlan",
]

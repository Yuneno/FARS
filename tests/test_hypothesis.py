"""Tests for P5 hypothesis registry and temporal separation."""
import math
import pytest

from src.hypothesis_registry import (
    Hypothesis, HypothesisRegistry, Fold, WalkForwardPlan,
)


def _make_hyp(**overrides) -> Hypothesis:
    defaults = dict(
        family_id="fam1", hypothesis_id="h1", variant="v1",
        description="test", preregistered_at="2024-01-01",
        dataset_fingerprint="abc", n_folds=5, state="preregistered",
    )
    defaults.update(overrides)
    return Hypothesis(**defaults)


class TestHypothesisRegistry:
    def test_register_and_retrieve(self):
        reg = HypothesisRegistry()
        h = _make_hyp()
        reg.register(h)
        assert "h1" in reg.hypotheses

    def test_reject_duplicate_tested(self):
        reg = HypothesisRegistry()
        reg.register(_make_hyp(state="tested"))
        with pytest.raises(ValueError, match="cannot re-register"):
            reg.register(_make_hyp())

    def test_update_result(self):
        reg = HypothesisRegistry()
        reg.register(_make_hyp())
        reg.update_result("h1", state="tested", p_value=0.03, t_stat=2.5)
        assert reg.hypotheses["h1"].state == "tested"
        assert reg.hypotheses["h1"].results["p_value"] == 0.03

    def test_invalid_p_value_rejected(self):
        reg = HypothesisRegistry()
        reg.register(_make_hyp())
        with pytest.raises(ValueError, match="invalid p_value"):
            reg.update_result("h1", state="tested", p_value=1.5)

    def test_nan_p_value_rejected(self):
        reg = HypothesisRegistry()
        reg.register(_make_hyp())
        with pytest.raises(ValueError, match="invalid p_value"):
            reg.update_result("h1", state="tested", p_value=float("nan"))

    def test_invalid_alpha_rejected(self):
        with pytest.raises(ValueError, match="alpha"):
            _make_hyp(alpha=0)

    def test_negative_candidates_rejected(self):
        with pytest.raises(ValueError, match="n_candidates_tested"):
            _make_hyp(n_candidates_tested=-1)

    def test_bonferroni_threshold(self):
        reg = HypothesisRegistry()
        for i in range(4):
            reg.register(_make_hyp(hypothesis_id=f"h{i}", alpha=0.05))
        threshold = reg.bonferroni_threshold("fam1")
        assert threshold == pytest.approx(0.05 / 4)

    def test_total_candidates(self):
        reg = HypothesisRegistry()
        reg.register(_make_hyp(hypothesis_id="h1", n_candidates_tested=10))
        reg.register(_make_hyp(hypothesis_id="h2", n_candidates_tested=5))
        assert reg.total_candidates("fam1") == 15


class TestWalkForward:
    def test_basic_plan(self):
        plan = WalkForwardPlan.create(total_bars=200, n_folds=3)
        assert plan.n_folds == 3
        assert len(plan.folds) == 3

    def test_folds_no_overlap(self):
        plan = WalkForwardPlan.create(total_bars=200, n_folds=3)
        for i in range(len(plan.folds) - 1):
            assert int(plan.folds[i].test_end) <= int(plan.folds[i + 1].test_start)

    def test_train_strictly_before_test(self):
        plan = WalkForwardPlan.create(total_bars=200, n_folds=3)
        for f in plan.folds:
            assert int(f.train_end) <= int(f.test_start)

    def test_purge_gap(self):
        plan = WalkForwardPlan.create(
            total_bars=200, n_folds=3, purge_gap_bars=5,
        )
        for f in plan.folds:
            gap = int(f.test_start) - int(f.train_end)
            assert gap >= 5

    def test_warmup_excluded(self):
        plan = WalkForwardPlan.create(
            total_bars=200, n_folds=3, warmup_bars=10,
        )
        for f in plan.folds:
            assert int(f.train_start) >= 10

    def test_insufficient_bars_with_purge(self):
        plan = WalkForwardPlan.create(total_bars=10, n_folds=5, purge_gap_bars=5)
        assert plan.n_folds < 5  # some folds dropped due to purge gap

    def test_fold_ids_sequential(self):
        plan = WalkForwardPlan.create(total_bars=200, n_folds=4)
        ids = [f.fold_id for f in plan.folds]
        assert ids == list(range(len(plan.folds)))

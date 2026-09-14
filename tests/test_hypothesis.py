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

    def test_calendar_rolling_plan(self):
        from datetime import datetime, timedelta

        # Create daily bars from 2019-05-06 to 2026-07-15
        cur = datetime(2019, 5, 6)
        end = datetime(2026, 7, 15)
        bars = []
        while cur <= end:
            bars.append({"timestamp": cur.strftime("%Y-%m-%dT00:00:00.000Z")})
            cur += timedelta(days=1)

        plan = WalkForwardPlan.create_calendar_rolling(
            bars,
            train_months=36,
            test_months=6,
            step_months=6,
            start_date="2019-07-01",
            end_date="2026-07-01",
        )

        assert plan.n_folds == 8
        expected_folds = [
            (0, "2019-07-01", "2022-07-01", "2022-07-01", "2023-01-01"),
            (1, "2020-01-01", "2023-01-01", "2023-01-01", "2023-07-01"),
            (2, "2020-07-01", "2023-07-01", "2023-07-01", "2024-01-01"),
            (3, "2021-01-01", "2024-01-01", "2024-01-01", "2024-07-01"),
            (4, "2021-07-01", "2024-07-01", "2024-07-01", "2025-01-01"),
            (5, "2022-01-01", "2025-01-01", "2025-01-01", "2025-07-01"),
            (6, "2022-07-01", "2025-07-01", "2025-07-01", "2026-01-01"),
            (7, "2023-01-01", "2026-01-01", "2026-01-01", "2026-07-01"),
        ]
        for f, (exp_id, tr_s, tr_e, te_s, te_e) in zip(plan.folds, expected_folds):
            assert f.fold_id == exp_id
            assert f.train_start == tr_s
            assert f.train_end == tr_e
            assert f.test_start == te_s
            assert f.test_end == te_e
            assert f.train_bars > 0
            assert f.test_bars > 0
            assert f.train_end_idx <= f.test_start_idx
            assert f.warmup_bars > 0

        # Verify rolling property (train_start advances, NOT expanding)
        for i in range(len(plan.folds) - 1):
            assert plan.folds[i].train_start < plan.folds[i + 1].train_start

    def test_calendar_rolling_purge_gap(self):
        from datetime import datetime, timedelta

        cur = datetime(2019, 5, 6)
        end = datetime(2026, 7, 15)
        bars = []
        while cur <= end:
            bars.append({"timestamp": cur.strftime("%Y-%m-%dT00:00:00.000Z")})
            cur += timedelta(days=1)

        plan = WalkForwardPlan.create_calendar_rolling(
            bars,
            train_months=36,
            test_months=6,
            step_months=6,
            start_date="2019-07-01",
            end_date="2026-07-01",
            purge_gap_bars=10,
        )
        for f in plan.folds:
            assert f.test_start_idx - f.train_end_idx == 10

    def test_purge_train_by_trade_intervals_boundary_crossing(self):
        from src.hypothesis_registry import purge_train_by_trade_intervals

        train_indices = list(range(100))  # bars 0..99
        test_start_idx = 100
        test_end_idx = 150

        # Trade 1: [10, 20] completely inside training -> keep
        # Trade 2: [85, 115] crosses boundary into test -> purge bars 85..99
        # Trade 3: [120, 130] completely inside test -> keep
        trade_intervals = [(10, 20), (85, 115), (120, 130)]

        purged = purge_train_by_trade_intervals(
            train_indices, test_start_idx, test_end_idx, trade_intervals
        )

        assert 84 in purged
        assert 85 not in purged
        assert 99 not in purged
        assert len(purged) == 85
        assert purged == list(range(85))

    def test_embargo_filtering_trade_within_horizon(self):
        from src.hypothesis_registry import apply_embargo, filter_trades_by_embargo

        test_end_idx = 100
        embargo_bars = 20  # h = 20 bars, embargo window [100, 120)

        # Candidate indices for subsequent dataset [100..150)
        candidate_indices = list(range(100, 150))
        embargoed_indices = apply_embargo(candidate_indices, test_end_idx, embargo_bars)
        assert 100 not in embargoed_indices
        assert 119 not in embargoed_indices
        assert 120 in embargoed_indices
        assert len(embargoed_indices) == 30  # bars 120..149

        # Trade intervals starting at various points
        trades = [
            (95, 105),   # starts before test end
            (105, 115),  # starts inside embargo window [100, 120) -> eliminated
            (119, 130),  # starts inside embargo window -> eliminated
            (120, 135),  # starts at/after embargo window -> retained
            (130, 140),  # retained
        ]
        retained, eliminated = filter_trades_by_embargo(trades, test_end_idx, embargo_bars)
        assert len(eliminated) == 2
        assert (105, 115) in eliminated
        assert (119, 130) in eliminated
        assert len(retained) == 3
        assert (120, 135) in retained

    def test_purge_no_overlap_preserves_all_with_explicit_status(self):
        from src.hypothesis_registry import purge_train_by_trade_intervals

        train_indices = list(range(100))  # 0..99
        test_start_idx = 100
        test_end_idx = 150

        # All trades close before test_start_idx
        trade_intervals = [(10, 20), (30, 50), (60, 95)]
        purged = purge_train_by_trade_intervals(
            train_indices, test_start_idx, test_end_idx, trade_intervals
        )
        assert purged == train_indices
        assert len(purged) == 100

        # When there is zero overlap and parameters are frozen (no fitting in train),
        # the protocol explicitly documents purge_status: "not_applicable_no_fitting"
        overlap_count = sum(
            1 for entry, exit in trade_intervals
            if entry < test_end_idx and (exit is None or exit >= test_start_idx)
        )
        assert overlap_count == 0
        purge_status = "not_applicable_no_fitting" if overlap_count == 0 else "purged"
        assert purge_status == "not_applicable_no_fitting"


"""Tests for P3 fill profiles, costs, and slippage measurement."""

import math
import pytest

from src.fills import (
    CostConfig,
    FillEvent,
    FillProfile,
    LEGACY,
    GAP_AWARE,
    SlippageReport,
    measure_slippage,
    run_with_profile,
)


# ===========================================================================
# 1. CostConfig validation
# ===========================================================================

class TestCostConfig:
    def test_valid_config(self):
        c = CostConfig(commission_per_side=0.62, slippage_points=0.25)
        assert c.validate() == []

    def test_negative_commission_rejected(self):
        c = CostConfig(commission_per_side=-1.0)
        errors = c.validate()
        assert any("commission" in e for e in errors)

    def test_negative_slippage_rejected(self):
        c = CostConfig(slippage_points=-0.5)
        errors = c.validate()
        assert any("slippage" in e for e in errors)

    def test_nan_rejected(self):
        c = CostConfig(commission_per_side=float("nan"))
        errors = c.validate()
        assert any("NaN" in e for e in errors)

    def test_zero_tick_rejected(self):
        c = CostConfig(tick_size=0)
        errors = c.validate()
        assert any("tick_size" in e for e in errors)

    def test_round_to_tick(self):
        c = CostConfig(tick_size=0.25)
        assert c.round_to_tick(100.12) == 100.0
        assert c.round_to_tick(100.13) == 100.25
        assert c.round_to_tick(100.37) == 100.25
        assert c.round_to_tick(100.38) == 100.50


# ===========================================================================
# 2. Fill profiles: market fills
# ===========================================================================

class TestMarketFills:
    def test_long_market_fill_with_slippage(self):
        p = LEGACY
        cost = CostConfig(slippage_points=0.25, commission_per_side=0.62)
        fill = p.fill_market(100.0, 100.0, 101.0, 99.0, 1, cost)
        assert fill.fill_price == pytest.approx(100.25)
        assert fill.slippage_points == pytest.approx(0.25)
        assert fill.fill_type == "market"

    def test_short_market_fill_with_slippage(self):
        p = LEGACY
        cost = CostConfig(slippage_points=0.25, commission_per_side=0.62)
        fill = p.fill_market(100.0, 100.0, 101.0, 99.0, -1, cost)
        assert fill.fill_price == pytest.approx(99.75)  # adverse for short
        assert fill.slippage_points == pytest.approx(-0.25)

    def test_no_slippage_profile(self):
        p = FillProfile(name="no_slip", apply_slippage_to_market=False)
        cost = CostConfig(slippage_points=0.25)
        fill = p.fill_market(100.0, 100.0, 101.0, 99.0, 1, cost)
        assert fill.fill_price == pytest.approx(100.0)
        assert fill.slippage_points == 0.0


# ===========================================================================
# 3. Fill profiles: stop-market fills
# ===========================================================================

class TestStopMarketFills:
    def test_long_stop_triggered(self):
        p = LEGACY
        cost = CostConfig()
        fill = p.fill_stop_market(98.0, 99.0, 101.0, 97.0, 1, cost)
        assert fill.fill_type == "stop_market"
        assert fill.fill_price >= 98.0

    def test_short_stop_triggered(self):
        p = LEGACY
        cost = CostConfig()
        fill = p.fill_stop_market(102.0, 101.0, 103.0, 100.0, -1, cost)
        assert fill.fill_type == "stop_market"
        assert fill.fill_price <= 102.0

    def test_stop_gap_long_fills_at_open(self):
        """Gap through stop: fill at bar open (worst case)."""
        p = GAP_AWARE
        cost = CostConfig()
        # Bar opens at 95, below stop at 98 -> gap through
        fill = p.fill_stop_market(98.0, 95.0, 96.0, 94.0, 1, cost)
        assert fill.fill_type == "stop_market"
        assert fill.fill_price == pytest.approx(95.0)  # filled at open (worse)

    def test_stop_not_triggered(self):
        p = LEGACY
        cost = CostConfig()
        fill = p.fill_stop_market(95.0, 100.0, 102.0, 98.0, 1, cost)
        assert fill.fill_type == "no_fill"


# ===========================================================================
# 4. Fill profiles: limit fills
# ===========================================================================

class TestLimitFills:
    def test_long_limit_filled(self):
        p = LEGACY
        cost = CostConfig()
        fill = p.fill_limit(100.0, 99.0, 102.0, 98.0, 1, cost)
        assert fill.fill_type == "limit"
        assert fill.fill_price == pytest.approx(100.0)
        assert fill.slippage_points == 0.0

    def test_long_limit_not_filled(self):
        p = LEGACY
        cost = CostConfig()
        fill = p.fill_limit(97.0, 99.0, 102.0, 98.0, 1, cost)
        assert fill.fill_type == "no_fill"

    def test_short_limit_filled(self):
        p = LEGACY
        cost = CostConfig()
        fill = p.fill_limit(102.0, 101.0, 103.0, 100.0, -1, cost)
        assert fill.fill_type == "limit"


# ===========================================================================
# 5. Cost total
# ===========================================================================

class TestFillCost:
    def test_total_cost(self):
        cost = CostConfig(commission_per_side=0.62, slippage_points=0.25)
        p = LEGACY
        fill = p.fill_market(100.0, 100.0, 101.0, 99.0, 1, cost)
        assert fill.total_cost == pytest.approx(0.25 + 0.62)

    def test_no_fill_zero_cost(self):
        p = LEGACY
        cost = CostConfig()
        fill = p.fill_stop_market(95.0, 100.0, 102.0, 98.0, 1, cost)
        assert fill.total_cost == 0.0


# ===========================================================================
# 6. Slippage measurement
# ===========================================================================

class TestSlippageMeasurement:
    def test_empty_fills(self):
        r = measure_slippage([])
        assert r.n_fills == 0

    def test_single_fill(self):
        f = FillEvent(
            fill_price=100.25, intended_price=100.0,
            fill_type="market", slippage_points=0.25, commission=0.62,
        )
        r = measure_slippage([f])
        assert r.n_fills == 1
        assert r.mean_points == pytest.approx(0.25)
        assert r.median_points == pytest.approx(0.25)
        assert r.max_points == pytest.approx(0.25)

    def test_multiple_fills_statistics(self):
        fills = [
            FillEvent(100.0, 100.0, "market", 0.1, 0.62),
            FillEvent(100.0, 100.0, "market", 0.2, 0.62),
            FillEvent(100.0, 100.0, "market", 0.3, 0.62),
            FillEvent(100.0, 100.0, "market", 0.4, 0.62),
            FillEvent(100.0, 100.0, "market", 0.5, 0.62),
        ]
        r = measure_slippage(fills)
        assert r.n_fills == 5
        assert r.mean_points == pytest.approx(0.3)
        assert r.median_points == pytest.approx(0.3)
        assert r.max_points == pytest.approx(0.5)
        assert r.total_points == pytest.approx(1.5)

    def test_signed_slippage_absolute(self):
        """Slippage measurement uses absolute values."""
        f1 = FillEvent(100.0, 100.0, "market", 0.25, 0.62)
        f2 = FillEvent(100.0, 100.0, "market", -0.25, 0.62)
        r = measure_slippage([f1, f2])
        assert r.mean_points == pytest.approx(0.25)


# ===========================================================================
# 7. Profile names
# ===========================================================================

class TestProfiles:
    def test_legacy_profile(self):
        assert LEGACY.name == "legacy"
        assert LEGACY.gap_aware is False

    def test_gap_aware_profile(self):
        assert GAP_AWARE.name == "gap_aware"
        assert GAP_AWARE.gap_aware is True


# ===========================================================================
# 8. Integration: run_with_profile
# ===========================================================================

class TestRunWithProfile:
    def test_produces_fills(self):
        cost = CostConfig(slippage_points=0.25, commission_per_side=0.62)
        bars = [
            {"open": 100, "high": 102, "low": 99, "close": 101},
            {"open": 101, "high": 103, "low": 100, "close": 102},
        ]
        fills = run_with_profile(bars, LEGACY, cost)
        assert len(fills) == 2
        for f in fills:
            assert f.fill_type == "market"
            assert f.slippage_points == pytest.approx(0.25)

    def test_invalid_cost_rejected(self):
        cost = CostConfig(commission_per_side=-1.0)
        bars = [{"open": 100, "high": 102, "low": 99, "close": 101}]
        with pytest.raises(ValueError, match="Invalid cost config"):
            run_with_profile(bars, LEGACY, cost)

    def test_zero_slippage_profile(self):
        cost = CostConfig(slippage_points=0.0, commission_per_side=0.0)
        bars = [
            {"open": 100, "high": 102, "low": 99, "close": 101},
        ]
        fills = run_with_profile(bars, FillProfile(name="test", apply_slippage_to_market=False), cost)
        assert fills[0].fill_price == pytest.approx(101.0)
        assert fills[0].total_cost == pytest.approx(0.0)

"""Focused tests for the P7 infrastructure fixes.

Covers (1) per-market config via MarketSpec (dollar_per_point, tick_size,
quantity, costs), (2) canonical range-first / warmup-second filtering so the
warmup hold-out is relative to the effective window and never produces evaluated
trades, and (3) cost-scheme handling for unvalidated friction (MYM/MES/MGC).
"""
from datetime import datetime, timezone

import pytest

from src.backtest.markets import MES, MGC, MNQ, MYM
from src.parallel import (
    COST_SCHEME_EXPLICIT,
    COST_SCHEME_FRICTION,
    COST_SCHEME_PROVISIONAL,
    JobSpec,
    build_market_config,
    filter_job_bars,
    run_parallel,
)


def _bars(start_price: float = 100.0, n: int = 30, base: str = "2020-01-01") -> list[dict]:
    t0 = datetime.fromisoformat(base).replace(tzinfo=timezone.utc)
    bars = []
    price = start_price
    for i in range(n):
        ts = t0.replace(hour=8 + (i % 8), minute=(i % 4) * 15)
        bars.append({
            "timestamp": ts.isoformat(),
            "open": price,
            "high": price + 1.5,
            "low": price - 1.0,
            "close": price + 0.5,
            "volume": 1000 + i,
        })
        price += 0.5
    return bars


def _spec(**overrides) -> JobSpec:
    defaults = dict(
        dataset_fingerprint="abc123",
        symbol="MNQ",
        timeframe="H4",
        range_start="",
        range_end="",
        warmup_bars=0,
        strategy_config_id="breakout",
        execution_profile="default",
    )
    defaults.update(overrides)
    return JobSpec(**defaults)


# ---------------------------------------------------------------------------
# Market config reuses MarketSpec
# ---------------------------------------------------------------------------

class TestBuildMarketConfig:
    def test_mnq_market_friction(self):
        config, scheme = build_market_config("MNQ")
        assert scheme == COST_SCHEME_FRICTION
        assert config.dollar_per_point == MNQ.dollar_per_point == 2.0
        assert config.tick_size == MNQ.tick_size == 0.25
        # canonical round-trip: commission = friction*dpp/2, slippage zero
        assert config.commission_per_side == pytest.approx(2.0 * 2.0 / 2.0)
        assert config.slippage_points == 0.0
        assert config.fixed_quantity is None  # risk-based sizing preserved
        assert config.initial_balance == 50_000.0
        assert config.risk_per_trade == 0.01

    def test_mym_uses_real_market_spec_dpp_tick(self):
        # The old runner hardcoded MNQ dpp=2.0 for MYM (4x PnL inflation).
        config, scheme = build_market_config("MYM")
        assert config.dollar_per_point == MYM.dollar_per_point == 0.5
        assert config.tick_size == MYM.tick_size == 1.0
        # unvalidated friction without scenario -> provisional, never zero-cost
        assert scheme == COST_SCHEME_PROVISIONAL
        assert config.commission_per_side == pytest.approx(0.62)
        assert config.slippage_points == pytest.approx(0.25)

    def test_mym_explicit_friction_scenario(self):
        config, scheme = build_market_config("MYM", friction_pts=1.0)
        assert scheme == COST_SCHEME_EXPLICIT
        assert config.dollar_per_point == 0.5
        assert config.commission_per_side == pytest.approx(1.0 * 0.5 / 2.0)
        assert config.slippage_points == 0.0

    @pytest.mark.parametrize("spec", [MES, MGC])
    def test_unvalidated_without_scenario_is_provisional(self, spec):
        config, scheme = build_market_config(spec.symbol)
        assert scheme == COST_SCHEME_PROVISIONAL
        assert config.dollar_per_point == spec.dollar_per_point
        assert config.tick_size == spec.tick_size

    def test_custom_balance_and_risk(self):
        config, _ = build_market_config("MNQ", initial_balance=25_000.0, risk_per_trade=0.02)
        assert config.initial_balance == 25_000.0
        assert config.risk_per_trade == 0.02


# ---------------------------------------------------------------------------
# Range-first / warmup-second filtering
# ---------------------------------------------------------------------------

class TestFilterJobBars:
    def test_warmup_relative_to_range(self):
        # Range cut is canonical for MNQ: 2019-05-06.
        bars = _bars(n=40, base="2020-01-01")
        out = filter_job_bars(bars, range_start="2020-01-01T08:00:00+00:00", warmup_bars=5)
        # warmup consumes the first 5 in-range bars -> evaluated first = bar[5]
        assert out[0].timestamp == datetime.fromisoformat(bars[5]["timestamp"])
        assert len(out) == len(bars) - 5

    def test_range_filter_before_warmup(self):
        # A range cut that removes early bars must reduce the base the warmup
        # eats from; warmup must START inside the range, not at dataset start.
        bars = _bars(n=40, base="2019-05-06")
        # base is 2019, everything >= the cutoff; add some pre-cut bars explicitly
        pre = [{
            "timestamp": datetime(2018, 1, 1, tzinfo=timezone.utc).isoformat(),
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 1,
        }]
        all_bars = pre + bars
        out = filter_job_bars(all_bars, range_start="2019-05-06T00:00:00+00:00", warmup_bars=3)
        assert all(b.timestamp >= datetime(2019, 5, 6, tzinfo=timezone.utc) for b in out)
        # first evaluated bar is the 4th in-range bar, never a pre-cut bar
        assert out[0].timestamp == datetime.fromisoformat(bars[3]["timestamp"])

    def test_full_warmup_yields_no_bars(self):
        out = filter_job_bars(_bars(n=10), warmup_bars=10)
        assert out == []


class TestRunParallelWarmup:
    def test_warmup_bars_produce_no_evaluated_trades(self):
        # If every in-range bar is warmup, nothing may be evaluated.
        spec = _spec(warmup_bars=60, range_start="2020-01-01")
        bars = _bars(n=30, base="2020-01-01")
        m = run_parallel([spec], {spec.job_id: bars}, max_workers=1)
        assert m.results[0].status == "done"
        assert m.results[0].n_trades == 0

    def test_deterministic_rerun(self):
        spec = _spec(warmup_bars=20, range_start="2020-01-01")
        bars = _bars(n=60, base="2020-01-01")
        d = {spec.job_id: bars}
        m1 = run_parallel([spec], d, max_workers=1)
        m2 = run_parallel([spec], d, max_workers=1)
        assert m1.results[0].n_trades == m2.results[0].n_trades
        assert m1.results[0].net_pnl == pytest.approx(m2.results[0].net_pnl)

    def test_friction_pts_changes_job_id(self):
        # A different cost scenario must not share a semantic job_id.
        s1 = _spec(friction_pts=2.0)
        s2 = _spec(friction_pts=3.0)
        assert s1.job_id != s2.job_id

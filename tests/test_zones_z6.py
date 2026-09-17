"""Z6 acceptance tests: causal S/R and independent SMC-OB parity."""
from datetime import datetime, timedelta, timezone

import numpy as np

from src.backtest.history import Bar
from src.backtest.smc_ob import _SmcObSignal
from src.zones.engine import ZoneEngine
from src.zones.levels import SrPivot, SupportResistanceClusterer
from src.zones.order_blocks import OrderBlockBuilder

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def sr_pivot(index: int, level: float, side: str = "low") -> SrPivot:
    confirmed = index + 3
    return SrPivot(index, level, level - 1.0, level + 1.0, side, confirmed,
                   T0 + timedelta(minutes=5 * index),
                   T0 + timedelta(minutes=5 * confirmed))


def test_sr_available_when_minimum_evidence_appears() -> None:
    builder = SupportResistanceClusterer(
        symbol="MNQ", timeframe="M5", side="low", tolerance=1.0, min_samples=2
    )
    builder.add(sr_pivot(2, 100.0), 5)
    assert builder.zones() == ()
    builder.add(sr_pivot(7, 100.5), 10)
    zone = builder.zones()[0]
    assert zone.pattern_time == T0 + timedelta(minutes=10)
    assert zone.available_at == T0 + timedelta(minutes=50)
    assert zone.metadata["available_bar_index"] == 10
    assert (zone.lower, zone.upper) == (99.0, 101.5)


def test_sr_strengthens_and_expands_without_backdating() -> None:
    builder = SupportResistanceClusterer(
        symbol="MNQ", timeframe="M5", side="low", tolerance=1.0, min_samples=2
    )
    for pivot in (sr_pivot(2, 100.0), sr_pivot(7, 100.5)):
        builder.add(pivot, pivot.confirmed_at_index)
    before = builder.zones()[0]
    builder.add(sr_pivot(12, 101.0), 15)
    after = builder.zones()[0]
    assert after.zone_id == before.zone_id
    assert after.available_at == before.available_at
    assert after.pattern_time == before.pattern_time
    assert after.strength == 3.0
    assert after.upper > before.upper


def _bars(n: int = 1800) -> list[Bar]:
    rng = np.random.default_rng(610)
    close = 15000 + rng.normal(0, 8, n).cumsum()
    spread = rng.uniform(0.25, 12, n)
    return [Bar(T0 + timedelta(minutes=5*i), float(close[i]),
                float(close[i] + spread[i]), float(close[i] - spread[i]),
                float(close[i]), 1.0) for i in range(n)]


def _reference_ob(detector: _SmcObSignal, t: int, h: np.ndarray,
                  l: np.ndarray, c: np.ndarray):
    spec = detector.structure_and_arm(t, h, l, c, False)
    if spec is None:
        return None
    if spec["side"] == 1:
        a = max(detector.sh_i, t - detector.oblook)
        j = a + int(np.argmin(detector._p_lo[a:t + 1]))
    else:
        a = max(detector.sl_i, t - detector.oblook)
        j = a + int(np.argmax(detector._p_hi[a:t + 1]))
    return spec["side"], j, detector._p_lo[j], detector._p_hi[j]


def test_order_block_zone_bit_parity_with_frozen_port() -> None:
    bars = _bars()
    h = np.asarray([b.high for b in bars]); l = np.asarray([b.low for b in bars])
    c = np.asarray([b.close for b in bars])
    reference = _SmcObSignal(10, 3, True, 60)
    extracted = OrderBlockBuilder(symbol="MNQ", timeframe="M5")
    count = 0
    for t, bar in enumerate(bars):
        reference.update_pivots(t, h[:t+1], l[:t+1], c[:t+1])
        expected = _reference_ob(reference, t, h[:t+1], l[:t+1], c[:t+1]) \
            if reference.ready() else None
        zones = extracted.on_bar(bar, t)
        assert bool(zones) == (expected is not None)
        if expected is not None:
            count += 1
            side, j, lower, upper = expected
            zone = zones[0]
            assert (zone.metadata["side"], zone.metadata["source_bar_index"]) == (side, j)
            assert zone.lower == lower
            assert zone.upper == upper
            assert zone.pattern_time == bars[j].timestamp
            assert zone.available_at == bar.timestamp
    assert count > 10


def test_engine_prefix_invariance_and_determinism_for_z6() -> None:
    bars = _bars(500)
    first = ZoneEngine(symbol="MNQ", timeframe="M5")
    first.update(bars[:300])
    snapshot = [(z.zone_id, z.lower, z.upper, z.available_at, z.strength)
                for z in first.all_zones() if z.zone_type in ("support", "resistance", "order_block")]
    first.update(bars)
    full_historical = [(z.zone_id, z.lower, z.upper, z.available_at, z.strength)
                       for z in first.all_zones()
                       if z.zone_type in ("support", "resistance", "order_block")
                       and z.available_at <= bars[299].timestamp]
    # S/R is allowed to expand later; identity and immutable timestamps remain.
    full_by_id = {row[0]: row for row in full_historical}
    assert all(zid in full_by_id and full_by_id[zid][3] == available
               for zid, _lo, _hi, available, _strength in snapshot)

    second = ZoneEngine(symbol="MNQ", timeframe="M5")
    second.update(bars)
    assert [z.to_dict() for z in first.all_zones()] == [z.to_dict() for z in second.all_zones()]
    assert first.context(bars[-1].close)["inside_ote_zone"] is None


"""Tests del modelo de zona (Z1): invariantes, IDs deterministas, serializacion."""
from datetime import datetime

import pytest

from src.zones.models import Zone, make_zone_id

T = datetime(2024, 1, 2, 9, 35)


def mk(**kw):
    base = dict(zone_id="z1", zone_type="fvg", symbol="MNQ", timeframe="M5",
                lower=100.0, upper=101.0, midpoint=100.5, direction="long",
                pattern_time=T, available_at=T)
    base.update(kw)
    return Zone(**base)


def test_invariants_ok():
    z = mk()
    assert z.lower <= z.midpoint <= z.upper
    assert not z.is_terminal()


def test_midpoint_out_of_bounds_raises():
    with pytest.raises(ValueError):
        mk(midpoint=99.0)


def test_available_before_pattern_raises():
    with pytest.raises(ValueError):
        mk(pattern_time=T, available_at=datetime(2024, 1, 2, 9, 30))


def test_nonpositive_prices_raise():
    with pytest.raises(ValueError):
        mk(lower=-1.0)


def test_zone_id_deterministic_and_sensitive():
    a = make_zone_id("fvg", "MNQ", "M5", "long", T, (1, 2, 3))
    b = make_zone_id("fvg", "MNQ", "M5", "long", T, (1, 2, 3))
    c = make_zone_id("fvg", "MNQ", "M5", "long", T, (1, 2, 4))
    d = make_zone_id("liquidity", "MNQ", "M5", "long", T, (1, 2, 3))
    assert a == b and a != c and a != d and len(a) == 16


def test_roundtrip_serialization():
    z = mk(metadata={"gap_size": 0.5}, source_bar_ids=(1, 2, 3))
    z2 = Zone.from_dict(z.to_dict())
    assert z == z2


def test_terminal_states():
    assert mk(state="mitigated").is_terminal()
    assert mk(state="broken").is_terminal()
    assert mk(state="expired").is_terminal()
    assert not mk(state="touched").is_terminal()

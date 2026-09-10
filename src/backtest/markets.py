"""Immutable market conventions for the M5 AMD+CRT research pipeline.

Session windows are research windows, NOT the full exchange trading calendar.
MNQ reproduces commit 1fa30ae. Other markets' friction is deliberately unknown:
callers must supply a cost scenario before constructing an executor config.
Sources and outstanding assumptions: docs/refactor/task-06-multi-market.md.
"""

from dataclasses import dataclass
from datetime import time
from math import isfinite
from types import MappingProxyType
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class MarketSpec:
    symbol: str
    session_timezone: ZoneInfo
    pre_session_start: time
    pre_session_end: time
    regular_session_start: time
    regular_session_end: time
    dollar_per_point: float
    tick_size: float
    friction_points: float | None  # aggregate round-trip cost; None = unvalidated

    def __post_init__(self) -> None:
        if (
            not isinstance(self.symbol, str)
            or not self.symbol
            or self.symbol.strip() != self.symbol
        ):
            raise ValueError("symbol must be a nonempty string without surrounding whitespace")
        if not isinstance(self.session_timezone, ZoneInfo):
            raise TypeError("session_timezone must be a ZoneInfo timezone")
        for name in (
            "pre_session_start",
            "pre_session_end",
            "regular_session_start",
            "regular_session_end",
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, time)
                or value.tzinfo is not None
                or value.second
                or value.microsecond
                or value.minute % 5
                or value.fold
            ):
                raise ValueError(f"{name} must be a naive, M5-aligned local time")
        if not (
            self.pre_session_start
            < self.pre_session_end
            == self.regular_session_start
            < self.regular_session_end
        ):
            raise ValueError(
                "sessions must be ordered within one day; pre-session must end at regular open"
            )
        # AMD's existing confirmation duration is one hour from regular open.
        start = self.regular_session_start.hour * 60 + self.regular_session_start.minute
        end = self.regular_session_end.hour * 60 + self.regular_session_end.minute
        if end - start < 60:
            raise ValueError("regular session must contain the 60-minute AMD confirmation window")
        for name in ("dollar_per_point", "tick_size", "friction_points"):
            value = getattr(self, name)
            if name == "friction_points" and value is None:
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value < 0
                or (name != "friction_points" and value == 0)
            ):
                raise ValueError(
                    f"{name} must be finite and {'>= 0' if name == 'friction_points' else '> 0'}"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "session_timezone": str(self.session_timezone),
            "pre_session_start": self.pre_session_start.isoformat(),
            "pre_session_end": self.pre_session_end.isoformat(),
            "regular_session_start": self.regular_session_start.isoformat(),
            "regular_session_end": self.regular_session_end.isoformat(),
            "dollar_per_point": self.dollar_per_point,
            "tick_size": self.tick_size,
            "friction_points": self.friction_points,
        }


_ET = ZoneInfo("America/New_York")
MNQ = MarketSpec(
    symbol="MNQ",
    session_timezone=_ET,
    pre_session_start=time(0),
    pre_session_end=time(9, 30),
    regular_session_start=time(9, 30),
    regular_session_end=time(16),
    dollar_per_point=2.0,
    tick_size=0.25,
    friction_points=2.0,
)
MES = MarketSpec(
    symbol="MES",
    session_timezone=_ET,
    pre_session_start=time(0),
    pre_session_end=time(9, 30),
    regular_session_start=time(9, 30),
    regular_session_end=time(16),
    dollar_per_point=5.0,
    tick_size=0.25,
    friction_points=None,
)
MYM = MarketSpec(
    symbol="MYM",
    session_timezone=_ET,
    pre_session_start=time(0),
    pre_session_end=time(9, 30),
    regular_session_start=time(9, 30),
    regular_session_end=time(16),
    dollar_per_point=0.5,
    tick_size=1.0,
    friction_points=None,
)
# Explicit gold research window: historical COMEX floor hours, not index RTH.
MGC = MarketSpec(
    symbol="MGC",
    session_timezone=_ET,
    pre_session_start=time(0),
    pre_session_end=time(8, 20),
    regular_session_start=time(8, 20),
    regular_session_end=time(13, 30),
    dollar_per_point=10.0,
    tick_size=0.1,
    friction_points=None,
)
MARKETS = MappingProxyType({spec.symbol: spec for spec in (MNQ, MES, MYM, MGC)})


def get_market_spec(symbol: str = MNQ.symbol) -> MarketSpec:
    if not isinstance(symbol, str) or symbol.upper() not in MARKETS:
        raise ValueError(f"unknown market {symbol!r}; choose from {', '.join(MARKETS)}")
    return MARKETS[symbol.upper()]


__all__ = ["MARKETS", "MES", "MGC", "MNQ", "MYM", "MarketSpec", "get_market_spec"]

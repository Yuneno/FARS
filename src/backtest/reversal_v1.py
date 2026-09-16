"""Opt-in causal Reversal V1 composition for closed M5 bars.

Decision chain (all point-in-time): a ZoneEngine pool transitions to ``swept``;
the canonical structure detector then confirms a same-direction CHoCH/MSS on a
declared displacement bar; a later/same-impulse canonical FVG supplies the
entry edge; optional canonical CISD and Fibonacci OTE filters are applied; and
the target is the nearest still-active opposite pool beyond entry.  No opposite
pool means abstention.  Limit entry, 48-bar wait, 96-bar time exit, 1-point stop
buffer, 1.2 displacement multiplier, CISD-on and OTE-off are declared research
defaults, not optimized values.  Market-next-open is the frozen neighbour.

The strategy consumes closed bars only.  ``Zone.available_at`` and detector
``available_at`` are checked against the decision-bar timestamp before use.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from src.backtest.executor import BacktestConfig
from src.backtest.history import Bar
from src.backtest.markets import MNQ, MarketSpec
from src.backtest.strategy import Signal
from src.detectors.cisd import detect_cisd
from src.detectors.fvg import detect_fvg
from src.detectors.pivots import confirmed_swing
from src.detectors.structure import evaluate_structure
from src.detectors.sweeps import detect_sweeps
from src.zones.engine import ZoneEngine
from src.zones.fibonacci import OTE_RATIOS, ote_zone, premium_discount
from src.zones.models import Zone

DEFAULT_DISP_MULT = 1.2
DEFAULT_SL_BUFFER_PTS = 1.0
DEFAULT_WAIT = 48
DEFAULT_MAX_HOLD_BARS = 96
DEFAULT_CISD_LOOKBACK = 10
DEFAULT_PIVOT_LEFT = 3
DEFAULT_PIVOT_RIGHT = 3
DEFAULT_FVG_MIN_GAP_PCT = 0.001

EntryMode = Literal["limit", "market"]
DecisionReason = Literal["accepted", "invalidated", "expired", "filled", "closed"]


@dataclass(frozen=True)
class ReversalV1Decision:
    timestamp: datetime
    direction: Literal["long", "short"]
    entry: float
    stop: float
    target: float
    structure_level: float
    swept_zone_id: str
    fvg_event_id: str
    target_zone_id: str
    sweep_available_at: datetime
    fvg_available_at: datetime
    reason: DecisionReason
    r_result: float | None = None
    exit_time: datetime | None = None


def _detector_bars(bars: Sequence[Bar]) -> list[dict]:
    return [
        {
            "timestamp": bar.timestamp,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
        }
        for bar in bars
    ]


class ReversalV1Strategy:
    """Stateful Strategy implementation; constructing it changes no registry."""

    def __init__(
        self,
        *,
        market: MarketSpec = MNQ,
        disp_mult: float = DEFAULT_DISP_MULT,
        sl_buffer_pts: float = DEFAULT_SL_BUFFER_PTS,
        wait: int = DEFAULT_WAIT,
        max_hold_bars: int = DEFAULT_MAX_HOLD_BARS,
        use_cisd: bool = True,
        use_ote: bool = False,
        entry_mode: EntryMode = "limit",
        cisd_lookback: int = DEFAULT_CISD_LOOKBACK,
        pivot_left: int = DEFAULT_PIVOT_LEFT,
        pivot_right: int = DEFAULT_PIVOT_RIGHT,
        fvg_min_gap_pct: float = DEFAULT_FVG_MIN_GAP_PCT,
        log_decisions: bool = True,
    ) -> None:
        if not isinstance(market, MarketSpec):
            raise TypeError("market must be a MarketSpec")
        if entry_mode not in ("limit", "market"):
            raise ValueError("entry_mode must be 'limit' or 'market'")
        if wait < 1 or max_hold_bars < 1 or cisd_lookback < 1:
            raise ValueError("wait, max_hold_bars and cisd_lookback must be >= 1")
        if pivot_left < 1 or pivot_right < 1:
            raise ValueError("pivot_left and pivot_right must be >= 1")
        if pivot_left != pivot_right:
            raise ValueError("canonical confirmed_swing currently requires pivot_left == pivot_right")
        floats = (disp_mult, sl_buffer_pts, fvg_min_gap_pct)
        if not all(math.isfinite(value) for value in floats):
            raise ValueError("floating parameters must be finite")
        if disp_mult <= 0 or sl_buffer_pts < 0 or fvg_min_gap_pct < 0:
            raise ValueError("disp_mult must be > 0; buffers/gap threshold must be >= 0")

        self.market = market
        self.disp_mult = float(disp_mult)
        self.sl_buffer_pts = float(sl_buffer_pts)
        self.wait = int(wait)
        self.max_hold_bars = int(max_hold_bars)
        self.use_cisd = bool(use_cisd)
        self.use_ote = bool(use_ote)
        self.entry_mode = entry_mode
        self.cisd_lookback = int(cisd_lookback)
        self.pivot_left = int(pivot_left)
        self.pivot_right = int(pivot_right)
        self.fvg_min_gap_pct = float(fvg_min_gap_pct)
        self.log_decisions = bool(log_decisions)
        self.decisions: list[ReversalV1Decision] = []

        self._engine = ZoneEngine(
            symbol=market.symbol,
            timeframe="M5",
            tick_size=market.tick_size,
            pivot_left=pivot_left,
            pivot_right=pivot_right,
            min_gap_pct=fvg_min_gap_pct,
            session_pools=True,
        )
        self._bars: list[Bar] = []
        self._high: list[float] = []
        self._low: list[float] = []
        self._seen = 0
        self._seen_swept: set[str] = set()
        self._sweep: dict[str, object] | None = None
        self._choch: dict[str, object] | None = None
        self._sh_p: float | None = None
        self._sh_i: int | None = None
        self._sl_p: float | None = None
        self._sl_i: int | None = None
        self._trend = 0
        self._pending_active = False
        self._position_active = False
        self._active_decision: int | None = None

    def fresh(self) -> ReversalV1Strategy:
        return ReversalV1Strategy(
            market=self.market,
            disp_mult=self.disp_mult,
            sl_buffer_pts=self.sl_buffer_pts,
            wait=self.wait,
            max_hold_bars=self.max_hold_bars,
            use_cisd=self.use_cisd,
            use_ote=self.use_ote,
            entry_mode=self.entry_mode,
            cisd_lookback=self.cisd_lookback,
            pivot_left=self.pivot_left,
            pivot_right=self.pivot_right,
            fvg_min_gap_pct=self.fvg_min_gap_pct,
            log_decisions=self.log_decisions,
        )

    def parameters(self) -> dict[str, object]:
        return {
            **({"market": self.market.to_dict()} if self.market != MNQ else {}),
            "disp_mult": self.disp_mult,
            "sl_buffer_pts": self.sl_buffer_pts,
            "wait": self.wait,
            "max_hold_bars": self.max_hold_bars,
            "use_cisd": self.use_cisd,
            "use_ote": self.use_ote,
            "entry_mode": self.entry_mode,
            "cisd_lookback": self.cisd_lookback,
            "pivot_left": self.pivot_left,
            "pivot_right": self.pivot_right,
            "fvg_min_gap_pct": self.fvg_min_gap_pct,
            "ote_ratios": OTE_RATIOS,
            "session_pools": True,
            "timeframe": "M5",
            "setup_expiry": "last causal sweep persists until consumed or replaced",
        }

    def set_execution_state(self, *, pending: bool, position: bool, cooldown: int) -> None:
        del cooldown
        self._pending_active = pending
        self._position_active = position

    def _update_pivots_and_structure(self, index: int) -> str | None:
        hi, lo = confirmed_swing(
            self._high, self._low, index, max(self.pivot_left, self.pivot_right)
        )
        if hi is not None:
            self._sh_p, self._sh_i = hi.level, hi.index
        if lo is not None:
            self._sl_p, self._sl_i = lo.level, lo.index
        if self._sh_p is None or self._sl_p is None:
            return None
        old_high, old_low = self._sh_p, self._sl_p
        previous_trend = self._trend
        self._trend, self._sh_p, self._sl_p, kind = evaluate_structure(
            self._bars[index].close,
            self._sh_p,
            self._sl_p,
            self._sh_i,
            self._sl_i,
            self._trend,
        )
        if kind == "bullish":
            self._last_structure_level = old_high
        elif kind == "bearish":
            self._last_structure_level = old_low
        # The canonical detector reports every structural break. Reversal V1
        # accepts only an actual MSS/CHoCH (initial neutral -> side, or a flip),
        # never a same-trend continuation BOS.
        return kind if kind is not None and previous_trend != self._trend else None

    def _new_sweep(self, index: int) -> None:
        decision_time = self._bars[index].timestamp
        # Canonical swing-sweep detector is consumed for its causal diagnostics;
        # the actual setup gate is the stricter pool lifecycle transition below.
        sweep_window = 2 * (self.pivot_left + self.pivot_right) + 3
        detect_sweeps(
            _detector_bars(self._bars[-sweep_window:]),
            symbol=self.market.symbol,
            timeframe="M5",
            left=self.pivot_left,
            right=self.pivot_right,
        )
        candidates = [
            zone
            for zone in self._engine.all_zones()
            if zone.zone_type in ("liquidity", "session_level")
            and zone.state == "swept"
            and zone.zone_id not in self._seen_swept
            and zone.available_at <= decision_time
        ]
        for zone in candidates:
            self._seen_swept.add(zone.zone_id)
        if not candidates:
            return
        zone = max(candidates, key=lambda item: (item.available_at, item.zone_id))
        direction: Literal["long", "short"] = "short" if zone.direction == "long" else "long"
        bar = self._bars[index]
        extreme = bar.high if direction == "short" else bar.low
        self._sweep = {
            "zone": zone,
            "direction": direction,
            "index": index,
            "extreme": extreme,
        }
        self._choch = None

    def _has_displacement(self, index: int) -> bool:
        if index < 20:
            return False
        avg = sum(bar.high - bar.low for bar in self._bars[index - 20:index]) / 20.0
        return avg > 0 and (self._bars[index].high - self._bars[index].low) >= self.disp_mult * avg

    def _opposite_pool(self, direction: str, entry: float, decision_time: datetime) -> Zone | None:
        wanted = "long" if direction == "long" else "short"
        pools = [
            zone
            for zone in self._engine.active_zones()
            if zone.zone_type in ("liquidity", "session_level")
            and zone.direction == wanted
            and zone.available_at <= decision_time
            and ((direction == "long" and zone.midpoint > entry) or (direction == "short" and zone.midpoint < entry))
        ]
        if not pools:
            return None
        return min(pools, key=lambda zone: (abs(zone.midpoint - entry), zone.zone_id))

    def _candidate(self, index: int) -> Signal | None:
        if self._sweep is None or self._choch is None:
            return None
        direction = self._sweep["direction"]
        assert direction in ("long", "short")
        if index < int(self._choch["index"]):
            return None
        events = detect_fvg(
            _detector_bars(self._bars[-3:]),
            symbol=self.market.symbol,
            timeframe="M5",
            min_gap_pct=self.fvg_min_gap_pct,
        )
        event = next((item for item in events if item.direction == direction), None)
        decision_time = self._bars[index].timestamp
        if event is None or event.available_at > decision_time:
            return None
        entry = event.levels["gap_high"] if direction == "long" else event.levels["gap_low"]

        if self.use_cisd:
            cisd = detect_cisd(
                _detector_bars(self._bars[-(self.cisd_lookback + 1):]),
                symbol=self.market.symbol,
                timeframe="M5",
                lookback=self.cisd_lookback,
            )
            if not any(item.direction == direction and item.available_at == decision_time for item in cisd):
                return None

        extreme = float(self._sweep["extreme"])
        impulse = self._bars[index]
        range_low = min(extreme, impulse.low)
        range_high = max(extreme, impulse.high)
        if self.use_ote:
            lower, upper = ote_zone(range_low, range_high, direction)
            expected_pd = "discount" if direction == "long" else "premium"
            if not lower <= entry <= upper or premium_discount(entry, range_low, range_high) != expected_pd:
                return None

        target_zone = self._opposite_pool(direction, entry, decision_time)
        if target_zone is None:
            return None
        stop = extreme - self.sl_buffer_pts if direction == "long" else extreme + self.sl_buffer_pts
        target = target_zone.midpoint
        if not (stop < entry < target if direction == "long" else target < entry < stop):
            return None
        structure_level = float(self._choch["level"])
        if self.entry_mode == "limit":
            signal_entry = entry
        else:
            signal_entry = self._bars[index].close
            if not (stop < signal_entry < target if direction == "long" else target < signal_entry < stop):
                return None
        if self.log_decisions:
            swept_zone = self._sweep["zone"]
            assert isinstance(swept_zone, Zone)
            self.decisions.append(
                ReversalV1Decision(
                    timestamp=decision_time,
                    direction=direction,
                    entry=signal_entry,
                    stop=stop,
                    target=target,
                    structure_level=structure_level,
                    swept_zone_id=swept_zone.zone_id,
                    fvg_event_id=event.event_id,
                    target_zone_id=target_zone.zone_id,
                    sweep_available_at=swept_zone.available_at,
                    fvg_available_at=event.available_at,
                    reason="accepted",
                )
            )
            self._active_decision = len(self.decisions) - 1
        self._sweep = None
        self._choch = None
        return Signal(direction, signal_entry, stop, target)

    def _invalidate_pending(self, bar: Bar) -> None:
        if not self._pending_active or self._active_decision is None:
            return
        decision = self.decisions[self._active_decision]
        crossed = (
            bar.close < decision.structure_level
            if decision.direction == "long"
            else bar.close > decision.structure_level
        )
        if crossed and decision.reason == "accepted":
            self.decisions[self._active_decision] = replace(
                decision, reason="invalidated", exit_time=bar.timestamp
            )

    def _sync(self, history: Sequence[Bar], *, emit_last: bool) -> Signal | None:
        if self._seen and (
            len(history) < self._seen
            or history[self._seen - 1].timestamp != self._bars[-1].timestamp
        ):
            raise ValueError("history must be an append-only chronological sequence")
        signal = None
        final = len(history) - 1
        for index in range(self._seen, len(history)):
            self._bars.append(history[index])
            self._high.append(history[index].high)
            self._low.append(history[index].low)
            self._engine.update(self._bars)
            self._invalidate_pending(history[index])
            kind = self._update_pivots_and_structure(index)
            self._new_sweep(index)
            if self._sweep is not None and kind is not None:
                direction = self._sweep["direction"]
                expected = "bullish" if direction == "long" else "bearish"
                if kind == expected and self._has_displacement(index):
                    self._choch = {
                        "index": index,
                        "level": self._last_structure_level,
                    }
            if emit_last and index == final and not self._pending_active and not self._position_active:
                signal = self._candidate(index)
        self._seen = len(history)
        return signal

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        return self._sync(history, emit_last=True)

    def observe(self, history: Sequence[Bar]) -> None:
        self._sync(history, emit_last=False)

    def note_order_filled(self, entry_time: datetime) -> None:
        if self._active_decision is not None:
            item = self.decisions[self._active_decision]
            if item.reason != "invalidated":
                self.decisions[self._active_decision] = replace(item, reason="filled", exit_time=entry_time)

    def note_order_expired(self, expiry_time: datetime) -> None:
        if self._active_decision is not None:
            item = self.decisions[self._active_decision]
            if item.reason != "invalidated":
                self.decisions[self._active_decision] = replace(item, reason="expired", exit_time=expiry_time)
            self._active_decision = None

    def note_trade(self, exit_time: datetime, r_result: float) -> None:
        if self._active_decision is not None:
            item = self.decisions[self._active_decision]
            if item.reason != "invalidated":
                self.decisions[self._active_decision] = replace(
                    item, reason="closed", r_result=float(r_result), exit_time=exit_time
                )
            self._active_decision = None


def reversal_v1_config(
    *,
    market: MarketSpec = MNQ,
    wait: int = DEFAULT_WAIT,
    max_hold_bars: int = DEFAULT_MAX_HOLD_BARS,
    entry_mode: EntryMode = "limit",
    **overrides,
) -> BacktestConfig:
    """Declared execution config: primary limit or next-bar-open neighbour."""
    if entry_mode not in ("limit", "market"):
        raise ValueError("entry_mode must be 'limit' or 'market'")
    params = {
        "dollar_per_point": market.dollar_per_point,
        "tick_size": market.tick_size,
        "commission_per_side": 0.0,
        "slippage_points": 0.0,
        "bar_interval_seconds": 300,
        "max_contracts": 1_000_000,
        "max_bars_held": max_hold_bars - 1,
        "partial_take_profit_fraction": 0.0,
        "move_stop_to_break_even": False,
        "pending_limit_entry": entry_mode == "limit",
        "pending_order_wait_bars": wait if entry_mode == "limit" else 0,
        "cooldown_bars": 0,
        "time_exit_mode": "market",
    }
    params.update(overrides)
    return BacktestConfig(**params)


__all__ = [
    "ReversalV1Decision",
    "ReversalV1Strategy",
    "reversal_v1_config",
]

"""Causal order-block zones with exact SMC-OB detector semantics (Z6)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from src.zones.models import Zone, make_zone_id


def _field(bar: Any, name: str) -> float:
    return float(bar[name] if isinstance(bar, dict) else getattr(bar, name))


def _timestamp(bar: Any) -> datetime:
    value = bar["timestamp"] if isinstance(bar, dict) else bar.timestamp
    return datetime.fromisoformat(value) if isinstance(value, str) else value


class OrderBlockBuilder:
    """Streaming extraction of ``_SmcObSignal.structure_and_arm``.

    This is intentionally independent of the port so parity tests can detect
    drift.  Branch order, comparisons, ATR recurrence and ``argmin/argmax``
    first-tie behavior match the frozen port.
    """

    def __init__(self, *, symbol: str, timeframe: str, swing_w: int = 10,
                 choch_only: bool = True, ob_lookback: int = 60) -> None:
        self.symbol, self.timeframe = symbol, timeframe
        self.w, self.choch_only, self.oblook = int(swing_w), bool(choch_only), int(ob_lookback)
        self.sh_p = self.sh_i = self.sl_p = self.sl_i = None
        self.sh_cross = self.sl_cross = True
        self.trend = 0
        self._h: list[float] = []
        self._l: list[float] = []
        self._c: list[float] = []
        self._phi: list[float] = []
        self._plo: list[float] = []
        self._ts: list[datetime] = []
        self._atr = 0.0

    def on_bar(self, bar: Any, index: int) -> tuple[Zone, ...]:
        h, l, c = _field(bar, "high"), _field(bar, "low"), _field(bar, "close")
        tr = h - l if index == 0 else max(h - l, abs(h - self._c[-1]), abs(l - self._c[-1]))
        self._atr += (tr - self._atr) / 200 if index else tr
        high_volume = h - l >= 2 * self._atr
        self._h.append(h); self._l.append(l); self._c.append(c); self._ts.append(_timestamp(bar))
        self._phi.append(l if high_volume else h)
        self._plo.append(h if high_volume else l)

        i = index - self.w
        if i - self.w >= 0:
            if self._h[i] == max(self._h[i-self.w:i+self.w+1]):
                self.sh_p, self.sh_i, self.sh_cross = self._h[i], i, False
            if self._l[i] == min(self._l[i-self.w:i+self.w+1]):
                self.sl_p, self.sl_i, self.sl_cross = self._l[i], i, False
        if self.sh_p is None or self.sl_p is None:
            return ()

        spec: tuple[int, int, float, float] | None = None
        if not self.sh_cross and c > self.sh_p and self.sl_i is not None:
            self.sh_cross = True; tag_choch = self.trend == -1; self.trend = 1
            if not (self.choch_only and not tag_choch):
                a = max(self.sh_i, index - self.oblook)
                segment = self._plo[a:index + 1]
                if segment:
                    j = a + segment.index(min(segment))
                    ob_hi, ob_lo = self._phi[j], self._plo[j]
                    if ob_hi > ob_lo:
                        spec = (1, j, ob_lo, ob_hi)
        if not self.sl_cross and c < self.sl_p and self.sh_i is not None:
            self.sl_cross = True; tag_choch = self.trend == 1; self.trend = -1
            if not (self.choch_only and not tag_choch):
                a = max(self.sl_i, index - self.oblook)
                segment = self._phi[a:index + 1]
                if segment:
                    j = a + segment.index(max(segment))
                    ob_hi, ob_lo = self._phi[j], self._plo[j]
                    if ob_hi > ob_lo:
                        spec = (-1, j, ob_lo, ob_hi)

        zones = []
        for side, j, lower, upper in (() if spec is None else (spec,)):
            direction = "long" if side == 1 else "short"
            zid = make_zone_id(
                "order_block", self.symbol, self.timeframe, direction,
                self._ts[j], (j, index, lower, upper),
            )
            zones.append(Zone(
                zone_id=zid, zone_type="order_block", symbol=self.symbol,
                timeframe=self.timeframe, lower=lower, upper=upper,
                midpoint=(lower + upper) / 2.0, direction=direction,
                pattern_time=self._ts[j], available_at=self._ts[index],
                strength=1.0, source_bar_ids=(j, index),
                metadata={"available_bar_index": index, "source_bar_index": j,
                          "break_bar_index": index, "side": side},
            ))
        return tuple(zones)

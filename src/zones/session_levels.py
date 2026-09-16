"""Constructor incremental de anclajes de sesión (Z3-b) — Bloque F.

Construye y mantiene anclajes de liquidez basados en sesiones canónicas:
- PREV_DAY_HIGH / PREV_DAY_LOW: extremos del RTH de la sesión anterior completada.
- D20_HIGH / D20_LOW: extremos del RTH de las últimas 20 sesiones completadas (excluye la sesión en curso).
- OVERNIGHT_HIGH / OVERNIGHT_LOW: extremos pre-RTH (ventana Globex) de la sesión en curso, congelados al abrir RTH.

Una sola fuente por concepto:
- Sesión / rollover -> src.session_calendar.session_date
- Horarios de mercado -> src.backtest.markets.MarketSpec / get_market_spec
- Tolerancia / ancho de banda -> src.zones.liquidity.liquidity_tolerance

Parámetros de investigación:
- tick_tolerance=4, atr_tolerance=0.10 son valores exploratorios iniciales, NO óptimos.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from src.backtest.markets import MarketSpec, get_market_spec
from src.session_calendar import UTC_TZ, session_date
from src.zones.liquidity import liquidity_tolerance
from src.zones.models import Zone, make_zone_id

ANCHOR_TYPES = (
    "prev_day_high",
    "prev_day_low",
    "d20_high",
    "d20_low",
    "overnight_high",
    "overnight_low",
)


def _fld(bar: Any, name: str) -> float:
    if isinstance(bar, dict):
        return float(bar[name])
    return float(getattr(bar, name))


def _ts(bar: Any) -> datetime:
    if isinstance(bar, dict):
        ts = bar["timestamp"]
    else:
        ts = bar.timestamp
    if isinstance(ts, str):
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return ts


def _to_market_dt(ts: datetime, tz: ZoneInfo) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC_TZ)
    return ts.astimezone(tz)


@dataclass(frozen=True)
class CompletedRthSession:
    """Resumen inmutable de la ventana RTH de una sesión completada."""

    session_date: date
    high: float
    low: float
    high_bar_index: int
    low_bar_index: int
    last_bar_index: int
    last_bar_timestamp: datetime
    n_bars: int


class SessionLevelsBuilder:
    """Constructor incremental point-in-time de anclajes de sesión (PDH/PDL, D20, ONH/ONL).

    Mantiene el estado de las sesiones y emite instancias de Zone(zone_type='session_level')
    cuando se completan sus ventanas de evidencia.
    """

    def __init__(
        self,
        *,
        symbol: str,
        timeframe: str,
        market_spec: MarketSpec | None = None,
        tick_size: float = 0.25,
        tick_tolerance: int = 4,
        atr_tolerance: float = 0.10,
        d20_sessions: int = 20,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.market_spec = market_spec if market_spec is not None else get_market_spec(symbol)
        self.tick_size = tick_size
        self.tick_tolerance = tick_tolerance
        self.atr_tolerance = atr_tolerance
        self.d20_sessions = d20_sessions

        # Historial de sesiones RTH completadas (orden cronológico estricto)
        self._completed_rth_sessions: list[CompletedRthSession] = []

        # Estado de la sesión en curso
        self._current_session_date: date | None = None
        self._current_rth_bars: list[tuple[int, Any]] = []
        self._current_overnight_bars: list[tuple[int, Any]] = []
        self._rth_opened: bool = False

        # Zonas activas para la sesión en curso (anchor_type -> Zone)
        self._active_session_zones: dict[str, Zone] = {}

        # Estado de sweep overnight congelado al abrir RTH
        self._overnight_swept_pdh: bool | None = None
        self._overnight_swept_pdl: bool | None = None

    @property
    def current_session_date(self) -> date | None:
        return self._current_session_date

    @property
    def overnight_swept_pdh(self) -> bool | None:
        return self._overnight_swept_pdh

    @property
    def overnight_swept_pdl(self) -> bool | None:
        return self._overnight_swept_pdl

    def get_current_zone(self, anchor_type: str) -> Zone | None:
        return self._active_session_zones.get(anchor_type)

    def active_zones(self) -> tuple[Zone, ...]:
        return tuple(self._active_session_zones.values())

    def on_bar(
        self,
        bar: Any,
        bar_index: int,
        atr: float | None = None,
    ) -> tuple[list[Zone], list[Zone]]:
        """Consume una barra cerrada y retorna (new_zones, retired_zones)."""
        ts = _ts(bar)
        tz = self.market_spec.session_timezone
        s_date = session_date(ts, tz=str(tz))
        if s_date is None:
            return [], []

        new_zones: list[Zone] = []
        retired_zones: list[Zone] = []

        # 1. Detección de rollover de sesión
        if self._current_session_date is None:
            self._current_session_date = s_date
            self._rth_opened = False
        elif s_date != self._current_session_date:
            # Rollover: la sesión anterior queda completada
            if self._current_rth_bars:
                summary = self._summarize_rth(self._current_session_date, self._current_rth_bars)
                self._completed_rth_sessions.append(summary)

            # Convención 6: retiro del juego anterior -> expired
            retired_zones.extend(self._retire_active_zones())

            # Inicio de nueva sesión
            self._current_session_date = s_date
            self._current_rth_bars = []
            self._current_overnight_bars = []
            self._rth_opened = False
            self._overnight_swept_pdh = None
            self._overnight_swept_pdl = None

            # Convención 5: publicar PDH, PDL, D20 en el rollover
            rollover_zones = self._create_rollover_levels(bar, bar_index, atr)
            new_zones.extend(rollover_zones)

        # 2. Clasificación de la barra (RTH vs Overnight)
        dt_local = _to_market_dt(ts, tz)
        t_local = dt_local.time()
        is_rth = (
            self.market_spec.regular_session_start <= t_local < self.market_spec.regular_session_end
            and dt_local.date() == s_date
        )

        if is_rth:
            if not self._rth_opened:
                self._rth_opened = True
                # Convención 3 & 5: Publicar ONH / ONL al abrir RTH
                on_zones = self._create_overnight_levels(bar, bar_index, atr)
                new_zones.extend(on_zones)
                self._evaluate_overnight_sweeps()
            self._current_rth_bars.append((bar_index, bar))
        else:
            # Barra fuera de RTH. Si RTH aún no ha abierto para s_date, es ventana Globex/overnight
            if not self._rth_opened:
                self._current_overnight_bars.append((bar_index, bar))

        return new_zones, retired_zones

    def _summarize_rth(self, s_date: date, rth_bars: list[tuple[int, Any]]) -> CompletedRthSession:
        high_idx, high_val = max(
            ((idx, _fld(b, "high")) for idx, b in rth_bars),
            key=lambda x: x[1],
        )
        low_idx, low_val = min(
            ((idx, _fld(b, "low")) for idx, b in rth_bars),
            key=lambda x: x[1],
        )
        last_idx, last_bar = rth_bars[-1]
        return CompletedRthSession(
            session_date=s_date,
            high=high_val,
            low=low_val,
            high_bar_index=high_idx,
            low_bar_index=low_idx,
            last_bar_index=last_idx,
            last_bar_timestamp=_ts(last_bar),
            n_bars=len(rth_bars),
        )

    def _retire_active_zones(self) -> list[Zone]:
        retired: list[Zone] = []
        for zid, z in self._active_session_zones.items():
            if not z.is_terminal():
                retired.append(
                    Zone(
                        zone_id=z.zone_id,
                        zone_type=z.zone_type,
                        symbol=z.symbol,
                        timeframe=z.timeframe,
                        lower=z.lower,
                        upper=z.upper,
                        midpoint=z.midpoint,
                        direction=z.direction,
                        pattern_time=z.pattern_time,
                        available_at=z.available_at,
                        state="expired",
                        touches=z.touches,
                        strength=z.strength,
                        source_bar_ids=z.source_bar_ids,
                        metadata=z.metadata,
                    )
                )
        self._active_session_zones.clear()
        return retired

    def _compute_tolerance(self, atr: float | None) -> float:
        effective_atr = atr if (atr is not None and atr > 0) else self.tick_size * 10.0
        return liquidity_tolerance(
            self.tick_size,
            effective_atr,
            tick_tolerance=self.tick_tolerance,
            atr_tolerance=self.atr_tolerance,
        )

    def _create_rollover_levels(
        self,
        bar: Any,
        bar_index: int,
        atr: float | None,
    ) -> list[Zone]:
        """Crea PDH, PDL y D20 en el rollover que abre la sesión."""
        if not self._completed_rth_sessions or self._current_session_date is None:
            return []

        out: list[Zone] = []
        s_date = self._current_session_date
        h = self._compute_tolerance(atr)
        available_at = _ts(bar)

        # 1. PDH / PDL (última sesión completada)
        prev = self._completed_rth_sessions[-1]
        pattern_time = prev.last_bar_timestamp

        # PDH
        pdh_level = prev.high
        pdh_id = make_zone_id(
            "session_level",
            self.symbol,
            self.timeframe,
            "long",
            pattern_time,
            (s_date.isoformat(), "prev_day_high"),
        )
        pdh_zone = Zone(
            zone_id=pdh_id,
            zone_type="session_level",
            symbol=self.symbol,
            timeframe=self.timeframe,
            lower=round(max(0.01, pdh_level - h), 4),
            upper=round(pdh_level + h, 4),
            midpoint=round(pdh_level, 4),
            direction="long",
            pattern_time=pattern_time,
            available_at=available_at,
            state="active",
            touches=0,
            strength=1.0,
            source_bar_ids=(prev.high_bar_index,),
            metadata={
                "anchor_type": "prev_day_high",
                "session_date": s_date.isoformat(),
                "available_bar_index": bar_index,
                "source_bar_ids": (prev.high_bar_index,),
                "n_bars_window": prev.n_bars,
                "tolerance": round(h, 4),
                "zone_width": round(2 * h, 4),
            },
        )
        out.append(pdh_zone)
        self._active_session_zones["prev_day_high"] = pdh_zone

        # PDL
        pdl_level = prev.low
        pdl_id = make_zone_id(
            "session_level",
            self.symbol,
            self.timeframe,
            "short",
            pattern_time,
            (s_date.isoformat(), "prev_day_low"),
        )
        pdl_zone = Zone(
            zone_id=pdl_id,
            zone_type="session_level",
            symbol=self.symbol,
            timeframe=self.timeframe,
            lower=round(max(0.01, pdl_level - h), 4),
            upper=round(pdl_level + h, 4),
            midpoint=round(pdl_level, 4),
            direction="short",
            pattern_time=pattern_time,
            available_at=available_at,
            state="active",
            touches=0,
            strength=1.0,
            source_bar_ids=(prev.low_bar_index,),
            metadata={
                "anchor_type": "prev_day_low",
                "session_date": s_date.isoformat(),
                "available_bar_index": bar_index,
                "source_bar_ids": (prev.low_bar_index,),
                "n_bars_window": prev.n_bars,
                "tolerance": round(h, 4),
                "zone_width": round(2 * h, 4),
            },
        )
        out.append(pdl_zone)
        self._active_session_zones["prev_day_low"] = pdl_zone

        # 2. D20 (últimas N sesiones completadas, excluyendo estrictamente la sesión en curso)
        if len(self._completed_rth_sessions) >= self.d20_sessions:
            win = self._completed_rth_sessions[-self.d20_sessions :]
            d20_high_session = max(win, key=lambda s: s.high)
            d20_low_session = min(win, key=lambda s: s.low)
            d20_high_val = d20_high_session.high
            d20_low_val = d20_low_session.low
            total_bars_win = sum(s.n_bars for s in win)

            # D20H
            d20h_id = make_zone_id(
                "session_level",
                self.symbol,
                self.timeframe,
                "long",
                pattern_time,
                (s_date.isoformat(), "d20_high"),
            )
            d20h_zone = Zone(
                zone_id=d20h_id,
                zone_type="session_level",
                symbol=self.symbol,
                timeframe=self.timeframe,
                lower=round(max(0.01, d20_high_val - h), 4),
                upper=round(d20_high_val + h, 4),
                midpoint=round(d20_high_val, 4),
                direction="long",
                pattern_time=pattern_time,
                available_at=available_at,
                state="active",
                touches=0,
                strength=1.0,
                source_bar_ids=(d20_high_session.high_bar_index,),
                metadata={
                    "anchor_type": "d20_high",
                    "session_date": s_date.isoformat(),
                    "available_bar_index": bar_index,
                    "source_bar_ids": (d20_high_session.high_bar_index,),
                    "n_bars_window": total_bars_win,
                    "tolerance": round(h, 4),
                    "zone_width": round(2 * h, 4),
                },
            )
            out.append(d20h_zone)
            self._active_session_zones["d20_high"] = d20h_zone

            # D20L
            d20l_id = make_zone_id(
                "session_level",
                self.symbol,
                self.timeframe,
                "short",
                pattern_time,
                (s_date.isoformat(), "d20_low"),
            )
            d20l_zone = Zone(
                zone_id=d20l_id,
                zone_type="session_level",
                symbol=self.symbol,
                timeframe=self.timeframe,
                lower=round(max(0.01, d20_low_val - h), 4),
                upper=round(d20_low_val + h, 4),
                midpoint=round(d20_low_val, 4),
                direction="short",
                pattern_time=pattern_time,
                available_at=available_at,
                state="active",
                touches=0,
                strength=1.0,
                source_bar_ids=(d20_low_session.low_bar_index,),
                metadata={
                    "anchor_type": "d20_low",
                    "session_date": s_date.isoformat(),
                    "available_bar_index": bar_index,
                    "source_bar_ids": (d20_low_session.low_bar_index,),
                    "n_bars_window": total_bars_win,
                    "tolerance": round(h, 4),
                    "zone_width": round(2 * h, 4),
                },
            )
            out.append(d20l_zone)
            self._active_session_zones["d20_low"] = d20l_zone

        return out

    def _create_overnight_levels(
        self,
        bar: Any,
        bar_index: int,
        atr: float | None,
    ) -> list[Zone]:
        """Crea ONH y ONL al abrir RTH con los datos de la ventana overnight."""
        if not self._current_overnight_bars or self._current_session_date is None:
            return []

        out: list[Zone] = []
        s_date = self._current_session_date
        h = self._compute_tolerance(atr)
        available_at = _ts(bar)

        onh_idx, onh_val = max(
            ((idx, _fld(b, "high")) for idx, b in self._current_overnight_bars),
            key=lambda x: x[1],
        )
        onl_idx, onl_val = min(
            ((idx, _fld(b, "low")) for idx, b in self._current_overnight_bars),
            key=lambda x: x[1],
        )
        last_on_idx, last_on_bar = self._current_overnight_bars[-1]
        pattern_time = _ts(last_on_bar)
        n_on_bars = len(self._current_overnight_bars)

        # ONH
        onh_id = make_zone_id(
            "session_level",
            self.symbol,
            self.timeframe,
            "long",
            pattern_time,
            (s_date.isoformat(), "overnight_high"),
        )
        onh_zone = Zone(
            zone_id=onh_id,
            zone_type="session_level",
            symbol=self.symbol,
            timeframe=self.timeframe,
            lower=round(max(0.01, onh_val - h), 4),
            upper=round(onh_val + h, 4),
            midpoint=round(onh_val, 4),
            direction="long",
            pattern_time=pattern_time,
            available_at=available_at,
            state="active",
            touches=0,
            strength=1.0,
            source_bar_ids=(onh_idx,),
            metadata={
                "anchor_type": "overnight_high",
                "session_date": s_date.isoformat(),
                "available_bar_index": bar_index,
                "source_bar_ids": (onh_idx,),
                "n_bars_window": n_on_bars,
                "tolerance": round(h, 4),
                "zone_width": round(2 * h, 4),
            },
        )
        out.append(onh_zone)
        self._active_session_zones["overnight_high"] = onh_zone

        # ONL
        onl_id = make_zone_id(
            "session_level",
            self.symbol,
            self.timeframe,
            "short",
            pattern_time,
            (s_date.isoformat(), "overnight_low"),
        )
        onl_zone = Zone(
            zone_id=onl_id,
            zone_type="session_level",
            symbol=self.symbol,
            timeframe=self.timeframe,
            lower=round(max(0.01, onl_val - h), 4),
            upper=round(onl_val + h, 4),
            midpoint=round(onl_val, 4),
            direction="short",
            pattern_time=pattern_time,
            available_at=available_at,
            state="active",
            touches=0,
            strength=1.0,
            source_bar_ids=(onl_idx,),
            metadata={
                "anchor_type": "overnight_low",
                "session_date": s_date.isoformat(),
                "available_bar_index": bar_index,
                "source_bar_ids": (onl_idx,),
                "n_bars_window": n_on_bars,
                "tolerance": round(h, 4),
                "zone_width": round(2 * h, 4),
            },
        )
        out.append(onl_zone)
        self._active_session_zones["overnight_low"] = onl_zone

        return out

    def _evaluate_overnight_sweeps(self) -> None:
        """Evalúa si el extremo overnight superó el PDH/PDL de la sesión anterior (congelado)."""
        pdh = self._active_session_zones.get("prev_day_high")
        pdl = self._active_session_zones.get("prev_day_low")
        onh = self._active_session_zones.get("overnight_high")
        onl = self._active_session_zones.get("overnight_low")

        if pdh is not None and onh is not None:
            self._overnight_swept_pdh = bool(onh.midpoint > pdh.midpoint)
        else:
            self._overnight_swept_pdh = None

        if pdl is not None and onl is not None:
            self._overnight_swept_pdl = bool(onl.midpoint < pdl.midpoint)
        else:
            self._overnight_swept_pdl = None

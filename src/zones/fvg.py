"""FVG como zonas (Z2) — UNICA definicion de FVG de zona: ``src.detectors.fvg``.

Consolida: el motor de zonas consume el detector consolidado (con confirmacion
de la vela intermedia, semantica preservada por decision de la spec seccion 5.1).
El FVG inline de ``smc_fvg.py`` es desequilibrio puro de 3 velas y NO se toca
(no es un tercer detector: es la senal de entrada de la estrategia).

Lifecycle (bullish; bearish espejo):
    active -> touched (la mecha entra)
          -> partial (negocia mas profundo que el midpoint)
          -> mitigated (regla configurable; default ``full_fill``: el rango de la
             barra cubre TODO el gap)
    expired: caducidad causal por edad en barras (solo si ``max_age_bars`` se
    configura; por defecto no expira).

``broken`` no aplica al FVG en V1 (el gap no se "rompe", se mitiga) — se deja
fuera del DAG de estados de este tipo a proposito.
"""
from __future__ import annotations

from typing import Any, Sequence

from src.detectors.fvg import detect_fvg
from src.zones.models import TERMINAL_STATES, Zone, make_zone_id

FVG_MITIGATION_RULES: tuple[str, ...] = ("wick_touch", "midpoint", "full_fill", "close_through")
DEFAULT_MITIGATION = "full_fill"


def _fld(bar: Any, name: str) -> float:
    if isinstance(bar, dict):
        return bar[name]
    return getattr(bar, name)


def _as_detector_dict(bar: Any) -> dict:
    """Normaliza Bar (attr) o dict a la forma que consume src.detectors.fvg."""
    if isinstance(bar, dict):
        return bar
    return {"open": bar.open, "high": bar.high, "low": bar.low,
            "close": bar.close, "timestamp": bar.timestamp}


def fvg_zones_from_bars(
    bars: Sequence[Any],
    *,
    symbol: str,
    timeframe: str,
    min_gap_pct: float = 0.001,
    base_index: int = 0,
) -> list[Zone]:
    """Convierte los FVGs detectados (barra 3 cerrada) en Zone. available_at = cierre barra 3.

    ``base_index`` desplaza los source_bar_ids a indices GLOBALES cuando la
    ventana es un slice del historial (el detector reporta (0,1,2) locales).
    """
    events = detect_fvg([_as_detector_dict(b) for b in bars], symbol=symbol,
                        timeframe=timeframe, min_gap_pct=min_gap_pct)
    out: list[Zone] = []
    for ev in events:
        lo, hi = ev.levels["gap_low"], ev.levels["gap_high"]
        src = tuple(s + base_index for s in ev.source_bar_ids)
        out.append(Zone(
            zone_id=make_zone_id("fvg", symbol, timeframe, ev.direction or "neutral",
                                 ev.pattern_time, src),
            zone_type="fvg", symbol=symbol, timeframe=timeframe,
            lower=lo, upper=hi, midpoint=(lo + hi) / 2.0,
            direction=ev.direction or "neutral",
            pattern_time=ev.pattern_time, available_at=ev.available_at,
            source_bar_ids=src,
            metadata={"gap_size": ev.params.get("gap_size"),
                      "available_bar_index": src[-1]},
        ))
    return out


def transition_fvg(
    zone: Zone,
    bar: Any,
    bar_index: int,
    *,
    mitigation: str = DEFAULT_MITIGATION,
    max_age_bars: int | None = None,
) -> Zone:
    """Aplica una barra CERRADA a la zona. Devuelve la nueva zona (inmutable)."""
    if zone.is_terminal() or zone.zone_type != "fvg":
        return zone
    if mitigation not in FVG_MITIGATION_RULES:
        raise ValueError(f"mitigation invalida: {mitigation}")

    lo, hi, cl = _fld(bar, "low"), _fld(bar, "high"), _fld(bar, "close")
    in_zone = lo <= zone.upper and hi >= zone.lower
    new_state, touches = zone.state, zone.touches

    if in_zone:
        touches += 1
        if zone.state == "active":
            new_state = "touched"
        elif zone.state == "touched":
            deeper = (lo < zone.midpoint) if zone.direction == "long" else (hi > zone.midpoint)
            if deeper:
                new_state = "partial"

    if mitigation == "wick_touch" and in_zone:
        new_state = "mitigated"
    elif mitigation == "midpoint":
        if zone.direction == "long" and cl < zone.midpoint:
            new_state = "mitigated"
        elif zone.direction == "short" and cl > zone.midpoint:
            new_state = "mitigated"
    elif mitigation == "full_fill":
        if lo <= zone.lower and hi >= zone.upper:
            new_state = "mitigated"
    elif mitigation == "close_through":
        if zone.direction == "long" and cl < zone.lower:
            new_state = "mitigated"
        elif zone.direction == "short" and cl > zone.upper:
            new_state = "mitigated"

    if max_age_bars is not None:
        born = zone.metadata.get("available_bar_index")
        if born is not None and bar_index - born > max_age_bars and new_state not in TERMINAL_STATES:
            new_state = "expired"

    return Zone(
        zone_id=zone.zone_id, zone_type=zone.zone_type, symbol=zone.symbol,
        timeframe=zone.timeframe, lower=zone.lower, upper=zone.upper,
        midpoint=zone.midpoint, direction=zone.direction,
        pattern_time=zone.pattern_time, available_at=zone.available_at,
        state=new_state, touches=touches, strength=zone.strength,
        source_bar_ids=zone.source_bar_ids, metadata=zone.metadata,
    )

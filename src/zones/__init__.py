"""FARS Zone Engine — paquete de zonas causales (Bloque F, Z1-Z4 de la spec de GPT).

Una sola fuente de verdad por concepto:
- FVG de zona  -> consume ``src.detectors.fvg.detect_fvg`` (NO hay tercer FVG)
- pivotes      -> consume ``src.detectors.pivots.find_swings`` (canonico, noche 09-15)
- BOS/CHoCH    -> ``src.detectors.structure`` (canonico)

Las zonas NO abren trades en V1: exponen contexto causal para las estrategias.
"""
from src.zones.models import Zone, ZoneType, ZoneState, ZoneDirection, TERMINAL_STATES, make_zone_id
from src.zones.fibonacci import (
    OTE_RATIOS,
    equilibrium,
    ote_zone,
    premium_discount,
    retracement_from_high,
    retracement_from_low,
)
from src.zones.fvg import DEFAULT_MITIGATION, fvg_zones_from_bars, transition_fvg
from src.zones.liquidity import (
    ConfirmedPivot,
    LiquidityClusterer,
    liquidity_tolerance,
    transition_liquidity,
)
from src.zones.session_levels import CompletedRthSession, SessionLevelsBuilder
from src.zones.engine import ZoneEngine

__all__ = [
    "Zone", "ZoneType", "ZoneState", "ZoneDirection", "TERMINAL_STATES", "make_zone_id",
    "OTE_RATIOS", "equilibrium", "ote_zone", "premium_discount",
    "retracement_from_high", "retracement_from_low",
    "DEFAULT_MITIGATION", "fvg_zones_from_bars", "transition_fvg",
    "ConfirmedPivot", "LiquidityClusterer", "liquidity_tolerance", "transition_liquidity",
    "CompletedRthSession", "SessionLevelsBuilder",
    "ZoneEngine",
]

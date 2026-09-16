"""Fibonacci/OTE y premium/discount (Z7 de la spec, adelantado por ser helpers puros).

Razones: 0.50 equilibrio, 0.62 OTE del estudio forward de Kai, 0.705 OTE_FIB de
kai (core/crt_structures.py:50). Son PARAMETROS preregistrados de investigacion,
no verdades optimas (spec seccion 4.2). Ninguno se declara optimo para FARS.
"""
from __future__ import annotations

from typing import Literal, Sequence

OTE_RATIOS: tuple[float, ...] = (0.62, 0.705)


def _validate_range(low: float, high: float) -> None:
    if low <= 0 or high <= 0:
        raise ValueError(f"rango invalido (precios no positivos): {low}, {high}")
    if low >= high:
        raise ValueError(f"rango invalido (low >= high): {low}, {high}")


def equilibrium(low: float, high: float) -> float:
    _validate_range(low, high)
    return low + 0.50 * (high - low)


def retracement_from_high(low: float, high: float, ratio: float) -> float:
    _validate_range(low, high)
    return high - ratio * (high - low)


def retracement_from_low(low: float, high: float, ratio: float) -> float:
    _validate_range(low, high)
    return low + ratio * (high - low)


def ote_zone(
    low: float,
    high: float,
    direction: Literal["long", "short"],
    ratios: Sequence[float] = OTE_RATIOS,
) -> tuple[float, float]:
    """Zona OTE (banda entre ratios) sobre un rango causal.

    Alcista (pata L->H): niveles medidos BAJANDO desde H. Bajista (H->L):
    niveles SUBIENDO desde L. Devuelve (lower, upper) con min/max.
    """
    _validate_range(low, high)
    if direction not in ("long", "short"):
        raise ValueError(f"direccion invalida: {direction}")
    if len(ratios) < 1:
        raise ValueError("se requiere al menos un ratio")
    if direction == "long":
        levels = [high - r * (high - low) for r in ratios]
    else:
        levels = [low + r * (high - low) for r in ratios]
    return min(levels), max(levels)


def premium_discount(price: float, low: float, high: float) -> Literal["premium", "discount", "equilibrium"]:
    _validate_range(low, high)
    eq = equilibrium(low, high)
    if price == eq:
        return "equilibrium"
    return "premium" if price > eq else "discount"

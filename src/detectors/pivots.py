"""Pivotes (swings) canonicos — UNA sola fuente de verdad (Bloque F, paso 1).

Dos semanticas DECLARADAS, prohibido anadir una tercera:
- ``confirmed_swing``: point-in-time con retraso de confirmacion w (semantica
  de smc_fvg._update_pivots): en la barra t se confirma el pivote en i = t - w
  cuando el extremo IGUALA al max/min de la ventana [i-w, i+w] (igualdad con
  empates; max/min builtin devuelven la primera ocurrencia del extremo).
- ``find_swings``: escaneo por lotes con vecindad left/right y comparacion <=
  (semantica de sweeps._find_swing_highs/lows), confirmado en index + right.

Ambas reciben secuencias de altos/bajos y devuelven :class:`Pivot`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Pivot:
    index: int
    level: float
    kind: str  # "high" | "low"
    confirmed_at_index: int


def confirmed_swing(
    highs: Sequence[float], lows: Sequence[float], t: int, w: int
) -> tuple["Pivot | None", "Pivot | None"]:
    """Point-in-time, retraso w. Devuelve (pivote alto | None, pivote bajo | None)
    para los pivotes que se confirman EXACTAMENTE en la barra t.

    Equivalencia bit a bit con smc_fvg._update_pivots: mismo slicing de lista y
    mismas funciones max/min builtin.
    """
    i = t - w
    out_hi: Pivot | None = None
    out_lo: Pivot | None = None
    if i - w >= 0:
        if highs[i] == max(highs[i - w : i + w + 1]):
            out_hi = Pivot(index=i, level=highs[i], kind="high", confirmed_at_index=t)
        if lows[i] == min(lows[i - w : i + w + 1]):
            out_lo = Pivot(index=i, level=lows[i], kind="low", confirmed_at_index=t)
    return out_hi, out_lo


def find_swings(
    highs: Sequence[float], lows: Sequence[float], left: int = 3, right: int = 3
) -> tuple[list[Pivot], list[Pivot]]:
    """Escaneo por lotes, vecindad left/right con <= (semantica de
    sweeps._find_swing_highs/lows). Devuelve (altos, bajos) como Pivot.
    """
    h_out: list[Pivot] = []
    l_out: list[Pivot] = []
    n = len(highs)
    for i in range(left, n - right):
        h = highs[i]
        if all(highs[i - j] <= h for j in range(1, left + 1)) and all(
            highs[i + j] <= h for j in range(1, right + 1)
        ):
            h_out.append(Pivot(index=i, level=h, kind="high", confirmed_at_index=i + right))
        l = lows[i]
        if all(lows[i - j] >= l for j in range(1, left + 1)) and all(
            lows[i + j] >= l for j in range(1, right + 1)
        ):
            l_out.append(Pivot(index=i, level=l, kind="low", confirmed_at_index=i + right))
    return h_out, l_out

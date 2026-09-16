"""Tests de equivalencia de los modulos canonicos de pivotes y estructura (Bloque F).

Cada test compara el modulo canonico contra una IMPLEMENTACION DE REFERENCIA
(copia literal de la logica original pre-refactor) sobre fixtures aleatorias.
La prueba de oro (bit a bit sobre el canonico) esta en el fingerprint del
f_protocol: mismo SHA de los 2,836 trades antes y despues del refactor.
"""

import random

import numpy as np

from src.detectors.pivots import confirmed_swing, find_swings
from src.detectors.structure import evaluate_structure


# ---------------- implementaciones de referencia (codigo original) -------------

def _ref_confirmed(highs, lows, t, w):
    i = t - w
    out = (None, None)
    if i - w < 0:
        return out
    hi = highs[i] if highs[i] == max(highs[i - w : i + w + 1]) else None
    lo = lows[i] if lows[i] == min(lows[i - w : i + w + 1]) else None
    return (hi, lo)


def _ref_swings_high(bars, left, right):
    out = []
    for i in range(left, len(bars) - right):
        h = bars[i]["high"]
        if all(bars[i - j]["high"] <= h for j in range(1, left + 1)) and all(
            bars[i + j]["high"] <= h for j in range(1, right + 1)
        ):
            out.append((i, h))
    return out


def _ref_swings_low(bars, left, right):
    out = []
    for i in range(left, len(bars) - right):
        l = bars[i]["low"]
        if all(bars[i - j]["low"] >= l for j in range(1, left + 1)) and all(
            bars[i + j]["low"] >= l for j in range(1, right + 1)
        ):
            out.append((i, l))
    return out


def _fixture(n=500, seed=7):
    rng = np.random.default_rng(seed)
    p = np.cumsum(rng.normal(0, 1, n))
    highs = list(p + np.abs(rng.normal(0, 0.5, n)))
    lows = list(p - np.abs(rng.normal(0, 0.5, n)))
    return highs, lows


def test_confirmed_swing_matches_reference():
    highs, lows = _fixture()
    w = 5
    for t in range(w, len(highs)):
        ref_hi, ref_lo = _ref_confirmed(highs, lows, t, w)
        p_hi, p_lo = confirmed_swing(highs, lows, t, w)
        assert (p_hi.level if p_hi else None) == ref_hi
        assert (p_hi.index if p_hi else None) == (t - w if ref_hi is not None else None)
        assert (p_lo.level if p_lo else None) == ref_lo
        assert (p_lo.index if p_lo else None) == (t - w if ref_lo is not None else None)


def test_find_swings_matches_reference():
    highs, lows = _fixture(300, 11)
    bars = [{"high": h, "low": l} for h, l in zip(highs, lows)]
    for left, right in ((3, 3), (2, 4), (5, 5)):
        h_ref = _ref_swings_high(bars, left, right)
        l_ref = _ref_swings_low(bars, left, right)
        h_new, l_new = find_swings(highs, lows, left, right)
        assert [(p.index, p.level) for p in h_new] == h_ref
        assert [(p.index, p.level) for p in l_new] == l_ref


def test_structure_bullish_consumes_high_pivot():
    # cierre rompe el alto, bajo anterior al alto -> bullish, sh_p consumido
    trend, sh_p, sl_p, kind = evaluate_structure(close_t=110.0, sh_p=105.0, sl_p=100.0,
                                                 sh_i=10, sl_i=5, trend=-1)
    assert (trend, sh_p, sl_p, kind) == (1, None, 100.0, "bullish")


def test_structure_bearish_consumes_low_pivot():
    trend, sh_p, sl_p, kind = evaluate_structure(close_t=90.0, sh_p=105.0, sl_p=100.0,
                                                 sh_i=5, sl_i=10, trend=1)
    assert (trend, sh_p, sl_p, kind) == (-1, 105.0, None, "bearish")


def test_structure_bullish_priority_over_bearish():
    # ambos podrian disparar (sl_i < sh_i Y sh_i < sl_i no pueden a la vez), pero
    # la prioridad alcista es la semantica original: probamos el caso de no-ruptura
    trend, sh_p, sl_p, kind = evaluate_structure(close_t=102.0, sh_p=105.0, sl_p=100.0,
                                                 sh_i=10, sl_i=5, trend=0)
    assert kind is None
    assert (trend, sh_p, sl_p) == (0, 105.0, 100.0)


def test_structure_requires_both_pivots_present_semantics():
    # Sin pivotes no hay ruptura (la estrategia gatea antes; aqui el modulo es total)
    trend, sh_p, sl_p, kind = evaluate_structure(close_t=110.0, sh_p=None, sl_p=100.0,
                                                 sh_i=None, sl_i=5, trend=0)
    assert kind is None

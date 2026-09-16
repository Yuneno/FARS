"""BOS/CHoCH canonico — extraido de smc_fvg._structure_and_signal (Bloque F, paso 2).

Semantica EXACTA del original, sin cambios de comportamiento:

En la barra t, con el cierre ``close_t``, los pivotes vigentes (sh_p, sl_p, sh_i,
sl_i) y el trend actual, devuelve el nuevo estado (trend, sh_p, sl_p, break_kind):

- break_kind "bullish": cierre > pivote alto Y el pivote bajo es anterior al
  alto (sl_i < sh_i). Consume el pivote alto (sh_p -> None), trend = 1.
- break_kind "bearish": cierre < pivote bajo Y el pivote alto es anterior al
  bajo (sh_i < sl_i). Consume el pivote bajo (sl_p -> None), trend = -1.
- La alcista se evalua PRIMERO (elif original): si dispara, la bajista NO aplica.

El gate de "ambos pivotes disponibles" (return None si falta alguno) lo conserva
la estrategia ANTES de llamar, igual que el original.
"""
from __future__ import annotations


def evaluate_structure(
    close_t: float,
    sh_p: float | None,
    sl_p: float | None,
    sh_i: int | None,
    sl_i: int | None,
    trend: int,
) -> tuple[int, float | None, float | None, str | None]:
    """Devuelve (trend, sh_p, sl_p, break_kind). Los pivotes consumidos vienen en None."""
    # El gate original: sin ambos pivotes vigentes no hay estructura (la
    # estrategia lo conserva tambien; aqui el modulo es total).
    if sh_p is None or sl_p is None:
        return trend, sh_p, sl_p, None
    bullish_break = (
        sl_i is not None
        and sh_i is not None
        and close_t > sh_p
        and sl_i < sh_i
    )
    if bullish_break:
        return 1, None, sl_p, "bullish"
    bearish_break = (
        sh_i is not None
        and sl_i is not None
        and close_t < sl_p
        and sh_i < sl_i
    )
    if bearish_break:
        return -1, sh_p, None, "bearish"
    return trend, sh_p, sl_p, None

# INFORME SB — Strategy B (repo público) como generador de señales para FARS

**Fecha:** 2026-09-28 · **Orquestador:** Hermes · **Alcance:** investigación histórica, sin live, sin credenciales, sin commits.
**Reglas congeladas ANTES de la evaluación final:** `preregistro_sb.json` sha256 `a5ee314e20826cbaf7f67a4d275a103e0f1ecce0a70b864600ec4fae48be243d`.

---

## 1. Reproducción del autor — FALLA

Repo `prashanthaitha24/nq-strategy-b-bot` @ `bc199e7` (MIT, 2 commits, 2026-05-20).

El comando que el README da para verificar sus números, `python analysis/ict_fvg_backtest.py`, **revienta al importar**:

| Bloqueo | Evidencia |
|---|---|
| Falta `signals/fvg.py` | `smc_breakout_backtest.py:36` lo importa → `ModuleNotFoundError` (verificado corriendo) |
| Falta `analysis/atb_refinement_backtest.py` | `ict_fvg_backtest.py:37,164,462` lo importan |
| `load_data()` no encuentra el dataset publicado | `smc_breakout_backtest.py:52-67` busca `nq_extended_5min.csv`/`nq_7mo_5min.csv`; el repo publica `data/nq_databento_5min.csv` |

Tres titulares contradictorios en el propio repo: README **+$17,187** (432 trades), README §3 "**+$16,468**", `run_strategy_b_robustness.py:141` "**+$22.9K**".

**Conclusión:** los resultados del autor NO son reproducibles con lo publicado y, en todo caso, son afirmaciones (sus backtests 2023–2026 ya están publicados: no sirven como validación independiente). Sus sesgos de método, además: "walk-forward" que es solo cortes año/trimestre de la misma corrida; long-only elegido *a posteriori* (42.5% WR shorts vs 56.5% longs); y el fill-bar no evaluado.

## 2. Auditoría de la implementación (clean-room) — 5 bugs hallados y corregidos

Las reglas sí están completas y legibles (`ict_fvg_backtest.py:210-350`), así que se reimplementaron de forma independiente en `run_sb.py` (nada copiado del autor; MIT atribuido). La auditoría encontró:

| # | Bug | Dónde (autor) | Corrección | Test |
|---|---|---|---|---|
| **B1** | **LOOK-AHEAD**: un FVG se activa con `f.time < ts`, pero un FVG solo se conoce al cerrar su **tercera barra**. En 15m eso son hasta **25 min de futuro** en el filtro de confluencia | `ict_fvg_backtest.py:95-97` | `fvg_usable_at()`: `f.time + 2*bar_len <= decision_ts` | T5, T6 |
| **B2** | El stop se llena a precio exacto aunque la vela abra a través de él (gap mortal) | `ict_fvg_backtest.py:309` | gap-stop al open (conservador) | T3 |
| **B3** | El slippage se contaba dos veces (precio de entrada + coste) | mi primer runner | coste = comisión (+ slip de salida solo en FARS) | T7 |
| **B4** | El slice 06:00–16:00 crea "FVGs" sobre el gap nocturno | `ict_fvg_backtest.py:518` | FVG solo con las 3 barras de la misma sesión ET | — |
| **B5** | SL y TP tocados en la misma vela | ambigüedad intrabarra | conservador: **gana SL** | T1, T2 |

Impacto de B1 (el grande): antes de los fixes mi corrida daba n=1416 y E[R]=**+0.38**; después, n=649 y E[R]≈**0**. **El "edge" del setup, medido con su semántica, venía en buena parte de mirar el futuro en el filtro 15m.**

Tests: `test_sb.py` T1–T8 **8/8 verdes** (fill-bar A1, SL+TP misma vela, gap-stop, gap-target, look-ahead 5m y 15m, costes, determinismo).
Determinismo: dos corridas consecutivas → `metrics_sb.json` y el ledger **byte a byte idénticos**.

## 3. Compatibilidad con FARS Core — VERIFICADA por el contrato real

Ledger canónico Phase 8A → `load_trade_csv(..., outcomes_finalized=True)` vía CLI:

- `fars audit ledger_sb_fars_realista_long_only_a1.csv --outcomes-finalized --timezone America/New_York` → **649 filas, 649 aceptadas, 0 rechazadas, 0 errores, 0 avisos**; `core_metrics`, `temporal_analysis` y `daily_rule_simulation` **disponibles**; provenance con sha256 del archivo, mapping 1:1 y timezone declarada.
- `fars metrics` (mismos argumentos) → métricas oficiales abajo.
- **FARS Core y sus especificaciones intactos** (cero cambios en `src/`); la estrategia, la adaptación y el Core viven separados.

## 4. Definiciones exactas de métricas (con costes)

- `r_result` = **(PnL neto en puntos MNQ) / (riesgo en puntos = |precio de entrada_fill − stop|)**. PnL neto = movimiento − comisión − slippage de salida (si aplica). **El slippage de entrada ya va dentro del precio de entrada** (no se suma otra vez).
- **E[R]** = media aritmética de `r_result` (unidad R, igual que FARS Core).
- **DD** = máximo drawdown de la curva acumulada de PnL neto (reportado en USD por el runner y en **R** por FARS Core), con ancla en cero.
- Costes aplicados: **escenario decisorio `fars_realista`** = comisión 0,62 USD/side (1,24 USD RT = 0,62 pts a $2/pt) + slippage 0,25 pt en entrada y en salidas no-target. **Escenario `autor`** = comisión 1,18 USD RT + 0,25 pt solo en entrada (para comparar con él).
- **Lo que falta modelar (declarado):** slippage por liquidez/impacto en barras extremas, fills parciales y calidad de ejecución del micro MNQ; el modelo asume fill completo a precio de vela.

## 5. Resultados — NUESTROS periodos (FARS Core, escenario decisorio)

Config: long-only, fill-bar A1, costes realistas. **MNQ único** (los buffers fijos en puntos están calibrados a escala NQ; no transfieren a MES/MGC).

| Periodo | n | WR | E[R] | Std R | MaxDD (R) | Racha perdedora |
|---|---|---|---|---|---|---|
| **2010–2022** (fuera de su muestra publicada) | 416 | 39.2% | **+0.019** | 1.39 | 19.95 | 8 |
| **2023–2026** (periodo que él publicó) | 233 | 34.3% | **−0.051** | 1.38 | 21.25 | 7 |
| **Total 2010–2026** | 649 | 37.4% | **−0.006** | 1.38 | 33.16 | 8 |

Avg win 1.73R · Avg loss 1.04R · Skewness +0.63 · Kurtosis −1.53.

**Variantes (runner propio, misma semántica de datos):**

| Escenario | fill-bar | n | E[R] | net USD | DD USD |
|---|---|---|---|---|---|
| autor (costes del autor) | autor | 649 | −0.032 | +1.838 | 2.070 |
| autor | **a1** | 649 | +0.008 | +2.071 | 1.602 |
| **fars_realista (decisorio)** | **a1** | 649 | **−0.006** | +1.806 | 1.647 |
| fars_realista | autor | 649 | −0.046 | +1.577 | 2.114 |
| both-sides (sub-análisis) | a1 | 1136 | −0.072 | −2.104 | 3.624 |

**Nota metodológica importante:** el `net USD` es ligeramente positivo mientras E[R]≈0 — el dólar suma premia los trades de riesgo grande que ganaron; la unidad **R** (la de FARS) dice que no hay expectativa. Con un `risk_per_trade` fijo en % de cuenta, lo que importa es **E[R], y es ~0**.

## 6. Contraste con las afirmaciones del autor (SOLO para comparar, no es evidencia)

| | Autor (afirma) | Nosotros, mismo periodo (2023–2026) |
|---|---|---|
| n | 432 | 233 |
| WR | 53.5% | 34.3% |
| Resultado | +$17.187, DD $786 | E[R] −0.05, sin beneficio neto significativo |

**Por qué discrepan (razones ordenadas por peso):** (1) el filtro 15m del autor mira hasta 25 min hacia el futuro (B1) — mitad de las señales se van al corregirlo; (2) su fill-bar nunca evalúa SL/TP en la vela de entrada; (3) sin slippage de salida; (4) datos distintos (NQ 5m Databento 2023–2026 vs. nuestro MNQ M5 2010–2026). No se puede reproducir su número sin su look-ahead.

## 7. Salvedad de selección (obligatoria)

2010–2022 **no** aparece en su muestra publicada, **pero esta estrategia se eligió tras ver sus resultados de 2023–2026**. Por tanto 2010–2022 **no** es una prueba completamente independiente: es evidencia fuera de *su* muestra, no evidencia sin selección. Tras quitar el look-ahead, el único periodo "nuevo" (+0.019 R en 13 años) es indistinguible de cero.

## 8. Limitaciones

1. MNQ único; reglas con buffers fijos en puntos (escala NQ) que no transfieren a otros instrumentos.
2. Barras de 5 minutos: la ambigüedad intrabarra se resuelve con supuesto conservador (SL gana), pero el orden real dentro de la vela es unknowable con estos datos.
3. Sin modelo de slippage por liquidez ni de impacto; sin fills parciales.
4. Sin walk-forward/OOS formal: hay cortes cronológicos (2010–2022 / 2023–2026), pero **cero calibración** (todos los parámetros son los del autor, congelados), así que no hay nada que sobreajustar — ni hay potencia estadística que reclamar: E[R]≈0 con Std 1.38 y n=416 da un IC que cubre cero holgadamente.
5. El autor usa datos NQ; nosotros MNQ (mismo nivel de precio, $2/pt).
6. No se corrió Monte Carlo ni Bootstrap de FARS sobre el ledger (el Core lo permite; queda como paso natural si decides seguir con esto).

## 9. Recomendación

**No adoptar esta estrategia como generador de señales.** Tras corregir el look-ahead, E[R] ≈ 0 en 16 años (y ligeramente negativo en el periodo que el autor promociona). Lo que sí queda como valor:

1. **El pipeline está probado de punta a punta**: estrategia externa → ledger Phase 8A → `fars audit`/`fars metrics` → métricas de riesgo. Ese camino funciona y es reusable para cualquier otro generador externo.
2. **La lista de sesgos (B1–B5)** queda como checklist obligatorio para auditar cualquier estrategia externa que traigas: el look-ahead de doble temporalidad es fácil de cometer y muy inflador.
3. Si quieres cerrar el caso con más fuerza, el paso natural es correr el **Bootstrap/Monte Carlo de FARS Core** sobre este ledger para mostrar el IC de E[R] y la probabilidad de cuenta con la distribución real — pero la conclusión ya es clara.

## 10. Trazabilidad

- Reglas: transcritas de `analysis/ict_fvg_backtest.py:210-350` + constantes `analysis/smc_breakout_backtest.py:41-45` (MIT, atribuido).
- Código: `lab_artifacts/sb_protocol/run_sb.py` (estrategia + adaptación), `test_sb.py` (T1–T8).
- Artefactos: `preregistro_sb.json` (+sha), `manifest_sb.json`, `metrics_sb.json`, `periodos_sb.json`, `ledger_sb_*.csv` (8 escenarios + 2 periodos), `INFORME_SB.md`.
- Verificación realmente ejecutada: 8/8 tests; determinismo byte a byte; `fars audit` 649/649 sin errores; `fars metrics` sobre total y ambos periodos.
- Repo del autor clonado en `E:\FARS-LAB\ext_review\nq-strategy-b-bot` **sin modificar**; FARS Core sin cambios; sin commits, merge ni push.

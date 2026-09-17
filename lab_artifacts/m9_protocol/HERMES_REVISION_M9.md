# HERMES REVISION — Bloque M9 (screening de entradas crudas multi-mercado)

**Fecha:** 2026-09-17 · **Revisor:** Hermes · **Implementador:** Gemini (Antigravity) · **Rama:**
`bloque-m9-screening-entradas` (`9d17675`, base `main` = `446fd94`) · **Artefactos:** `lab_artifacts/m9_protocol/`

## Veredicto

**PASS.** Ejecución **reproducible bit a bit**, integridad **72/72** hashes, equivalencias **20/20** y criterio
de supervivencia **declarado antes de correr** y aplicado tal cual. El resultado es un **negativo categórico y
bien medido**: **0 de 5 entradas tienen pulso**. Eso es información valiosa, no un fracaso.

## 1. Verificado por el revisor (independiente)

| # | Qué | Resultado |
|---|---|---|
| 1 | Criterio de supervivencia preregistrado | ✅ `"IC95 (CBB) lower bound > 0 en ≥2 de 4 mercados en escenario realista"`, congelado a las `15:38:00Z` **antes** del run (`15:40:13Z`) |
| 2 | Parámetros congelados, sin pesca | ✅ una sola parametrización declarada para las 5 entradas: **stop 1,0·ATR(14) · target 2,0·ATR(14) · max_hold 48 · cooldown 6** |
| 3 | Integridad | ✅ **72/72** hashes recomputados, 0 problemas |
| 4 | Equivalencias (arnés no altera ejecución) | ✅ **20/20** (4 mercados × 5 entradas), por `trades_sha256` |
| 5 | **Reproducción independiente de 2 celdas** (MNQ·E4 y MGC·E1, realista) | ✅ **IDÉNTICAS** en los 12 campos, `trades_sha256` incluido |
| 6 | `src/` y `tests/` intactos | ✅ el commit solo añade `lab_artifacts/m9_protocol/` |
| 7 | Aplicación del criterio | ✅ coherente con los números: los 4 mercados fallan el cote inferior > 0 en las 5 entradas |

## 2. El resultado (escenario realista, IC95 CBB)

| Entrada | MNQ | MES | MYM | MGC | ¿Pulso? |
|---|---|---|---|---|---|
| E1 · TS-D1 | −0,0398 [−0,082, +0,004] | −0,0896 [−0,118, −0,059] | −0,0416 [−0,062, −0,019] | −0,0482 [−0,080, −0,014] | ❌ 0/4 |
| E2 · TS-D20 | −0,0070 [−0,096, +0,086] | −0,0989 [−0,162, −0,038] | −0,0474 [−0,105, +0,008] | −0,1002 [−0,167, −0,027] | ❌ 0/4 |
| E3 · MOM-BREAK | −0,0685 [−0,139, +0,002] | −0,1170 [−0,165, −0,066] | −0,0675 [−0,112, −0,023] | −0,0583 [−0,127, +0,013] | ❌ 0/4 |
| E4 · MR-LEVEL | −0,0383 [−0,076, +0,000] | −0,0741 [−0,098, −0,049] | −0,0397 [−0,057, −0,021] | −0,0532 [−0,083, −0,023] | ❌ 0/4 |
| E5 · VOL-BREAK | −0,0356 [−0,069, +0,000] | −0,0752 [−0,100, −0,051] | −0,0413 [−0,059, −0,023] | −0,0567 [−0,083, −0,029] | ❌ 0/4 |

## 3. La lección transversal (esto es lo que vale del bloque)

1. **Todas las entradas gravitan en WR 30-34 %** con objetivo 2R → el punto de equilibrio teórico es
   **33,33 %**. Es decir: **ninguna de las 5 arquitecturas tiene asimetría propia**; están en el filo, y el
   coste las empuja al negativo. No es un problema de "casi": es ausencia de ventaja en la entrada mecánica.
2. **El desgaste por coste se come lo poco que hay:** 0,02–0,05 R por trade × ~2.000-3.700 trades = −100 a
   −300 R en el periodo. Entrada a mercado con stop de 1 ATR y alta frecuencia es una receta de fricción.
3. **Límite honesto del resultado:** esto prueba que **estas 5 entradas, declaradas así (M5, stop 1 ATR,
   target fijo 2R, 48 barras)** no tienen pulso — **no** prueba que las familias estén muertas en cualquier
   marco o con otra gestión de salida. La pregunta que queda viva es **de régimen y de salida**, no de filtro.
4. **M9 cumple su función:** descartar 5 motores de entrada en una tarde, con evidencia reproducible, en vez
   de descubrirlo tras semanas desarrollando filtros encima.

## 4. Qué sigue

1. M9 se cierra y es **apto para integrarse** en `main` (el merge lo decide Ricardo).
2. Las ideas que el propio informe propone (alineación HTF, compresión previa tipo NR7/squeeze) son
   **entradas nuevas a medir con la misma vara** — no se promueven por decreto.
3. Con **0 candidatas**, el ángulo «¿vale la pena pagar una evaluación?» (bloque M10: P(pasar) con el motor
   de cuentas existente) pasa a ser la decisión informada más útil: sin edge, el examen es una moneda al aire,
   y eso se puede cuantificar antes de pagar.
4. Nada se promueve. Sigue vigente: ningún candidato cruza el IC95 en el escenario realista.

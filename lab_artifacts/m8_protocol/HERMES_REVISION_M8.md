# HERMES REVISION — Bloque M8 (riesgo normalizado por ATR(14) · re-medición §12 #5, #6, #7)

**Fecha:** 2026-09-17 · **Revisor:** Hermes · **Implementador:** Gemini (Antigravity) · **Rama:**
`bloque-m8-riesgo-atr` (`e42bdc5`, base `main` = `4520aab`) · **Artefactos:** `lab_artifacts/m8_protocol/`

## Veredicto

**PASS.** Artefactos **reproducibles bit a bit** (dos celdas recomputadas por el revisor, ambas idénticas),
integridad **101/101** hashes, doble control de equivalencia (anti-fraude + **continuidad con M7**) en 12/12
celdas, y **0 cambios en `src/`**. El veredicto del informe es honesto y, además, **corrige un falso positivo
del bloque anterior**.

## 1. Verificado por el revisor (independiente)

| # | Qué | Resultado |
|---|---|---|
| 1 | Preregistro congelado antes de correr | ✅ `13:48:00Z` < `13:49:48Z` (manifest) |
| 2 | Integridad del manifest | ✅ **101/101** hashes recomputados, 0 problemas (incluye su propio hash: resuelto) |
| 3 | Anti-fraude `wrapper_trivial` ≡ `atr_k050` | ✅ **12/12** (4 mercados × 3 escenarios), por `trades_sha256` |
| 4 | **Continuidad metodológica `control_m7` ≡ M7** | ✅ **12/12**. Prueba dura: el `trades_sha256` de MNQ control = `6d00c6a5…` = **exactamente** el artefacto del M7 ya congelado en `main` |
| 5 | **Reproducción independiente de 2 celdas** (MNQ y MGC, `atr_k050`, realista) | ✅ **IDÉNTICAS** en los 12 campos, incluido `trades_sha256` y el IC bootstrap — corridas desde cero por el revisor |
| 6 | `src/` intacto | ✅ el commit solo añade `lab_artifacts/m8_protocol/` |
| 7 | Auditoría causal | ✅ 0 violaciones (limitación declarada: muestra de 500 barras y 10-20 decisiones por mercado) |

## 2. Los números que cambian la historia (escenario realista)

**a) La escala se arregló.** Con `min_risk_atr = 0.5 × ATR(14)` los cuatro mercados pasan a ~5.000 trades
(MNQ 4.812 · MES 5.560 · MYM 6.513 · MGC 5.358) frente a los 186-6.628 del régimen roto.

**b) El "edge" de MGC del M7 era un artefacto de escala.** M7: E[R] +0,2281 / PF 1,58 con n=186 (stop de
$80 sobre ATR mediano de 1,44 pts = selección de anomalías). M8: **E[R] −0,0364 / PF 0,927 con n=5.358**,
IC95 **[−0,067, −0,006]** — negativo y significativo. Reproducido por el revisor.

**c) El último candidato vivo cae.** En M7 el baseline MNQ realista daba +0,0897 con IC95 [+0,054, +0,125]
(excluía cero). Con riesgo normalizado: **E[R] +0,0309, IC95 [−0,0001, +0,0624]** → **el IC toca el cero**.
La expectativa de SMC-FVG en MNQ es, en el mejor de los casos, marginal, y **no sobrevive** a la
normalización. Nada queda en pie para promoción.

**d) Los tres filtros, rechazados con muestra sana:** banda OTE [0.62, 0.705] (Δ entre −0,018 y −0,063 R;
en MGC el único Δ positivo tiene IC que cruza cero), sesgo D1 de Kai (Δ −0,004 a −0,015 R, siempre ≤ 0) y
premium/discount 0.5 (Δ −0,013 a −0,035 R). El patrón es el de siempre: **recortan exposición, no mejoran
la expectativa**.

## 3. Notas menores (no bloquean)

1. Metadato del `manifest.json`: declara `"experiment": "§12 #8"` cuando el bloque re-mide **#5/#6/#7**
   (§12 #8 es la maquinaria de validación, que se cumple). Nit cosmético; conviene corregirlo si se toca.
2. `friction_points` sigue **sin validar** para MES/MYM/MGC (`None`): sus netos son estimaciones con
   supuestos declarados, no datos auditados. Está declarado en `BLOCKERS.md` del bloque.
3. La normalización ATR(14) con `k` declarado es ahora **la convención** para todo umbral de riesgo
   multi-mercado; cualquier bloque futuro que use puntos nominales debe justificarlo.

## 4. Qué sigue

1. M8 queda **cerrado y apto para integración** en `main` (el merge lo decide Ricardo).
2. Con esto, los experimentos §12 **#5, #6 y #7 están medidos y rechazados**; queda **#3 (FVG + S/R)** como
   el único experimento de la spec pendiente, y necesita **preregistro nuevo** sobre Z6.
3. Nada se promueve. La pregunta abierta de fondo (¿existe edge explotable en SMC-FVG o en alguna
   composición de zonas?) sigue **abierta y sin candidata que cruce el IC95 en el escenario realista** —
   que es un resultado, no un fracaso: se ha cerrado el espacio de filtros con evidencia reproducible.

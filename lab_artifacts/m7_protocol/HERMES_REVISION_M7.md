# HERMES REVISION — Bloque M7 (OTE + sesgo D1 de Kai, multi-mercado · experimento §12 #7)

**Fecha:** 2026-09-17 · **Revisor:** Hermes · **Implementador:** Gemini (Antigravity) · **Rama:**
`bloque-m7-ote-multimercado` (`45b55c4`, base `e5d8a30`) · **Artefactos:** `lab_artifacts/m7_protocol/`

## Veredicto

**PASS como bloque de investigación.** Los artefactos son **reproducibles** (verificado con recomputación
independiente), la integridad es verificable (94/95 hashes) y el veredicto del informe es honesto: **5/5
hipótesis rechazadas, nada se promueve**. Con dos avisos: la **escala de riesgo** del sujeto está rota
multi-mercado (lo arregla el M8) y la **fricción de MES/MYM/MGC sigue sin validar**.

## 1. Verificado por el revisor (independiente)

| # | Qué | Resultado |
|---|---|---|
| 1 | **Preregistro congelado antes de correr** | ✅ `13:05:00Z` (preregistro) **<** `13:07:28Z` (manifest) |
| 2 | `src/` intacto (solo investigación) | ✅ el commit solo toca `lab_artifacts/m7_protocol/` (95 archivos) |
| 3 | Control anti-fraude `wrapper_trivial` ≡ `baseline` | ✅ **12/12** (4 mercados × 3 escenarios), comparación por `trades_sha256`, no por agregados |
| 4 | Hashes del manifest | ✅ **94/95** verificados recomputando sha256 (única excepción: el propio `manifest.json`, que no puede contener su hash final — cosmético, no afecta a datos) |
| 5 | **Reproducción independiente de una celda** (MNQ · baseline · realista) | ✅ **IDÉNTICA** en los 12 campos, incluido `trades_sha256` = `6d00c6a5…` y el IC bootstrap `[0.0544, 0.1246]` — corrida desde cero por el revisor |
| 6 | Cross-check histórico | ✅ MNQ baseline realista = los números congelados de Z5/C2 (+0,0897 E[R] · PF 1,2027 · 7/8 folds) |

**Nota de la revisión (retracto parcial):** en un primer momento sospeché que el "84 celdas en 45,5 s" del
walkthrough escondía un **atajo de repriciado** sin declarar. **Falso**: al recomputar una celda desde cero
tardó **0,6 s** — el executor es así de rápido y cada celda corre sus 8 folds reales. No hay atajo.

## 2. El número que importa (y su límite)

- **MNQ, baseline SMC-FVG, escenario realista ($1,24 RT + 1 tick):** E[R] **+0,0897**, PF 1,2027, n=3.583,
  7/8 folds positivos, **IC95 CBB [+0,054, +0,125] → excluye cero** (recomputado por el revisor).
- **Con el escenario canónico ($4,00 RT): E[R] −0,0094 (negativo).** Es decir: **el coste decide el signo**,
  exactamente la lección de Z5. Este IC **no habilita promoción** (el protocolo no promueve) y depende de un
  escenario de coste declarado, no de un edge incondicional.
- **Ningún filtro mejora al baseline**: OTE (0,62/0,705/banda) recorta la muestra 89-96 % sin cruzar el IC;
  el sesgo D1 de Kai es **neutro** en MNQ (Δ = −0,0001 R) y perjudica en MES; la confluencia es **negativa**
  y colapsa la muestra en MGC (0 trades).

## 3. Avisos declarados (no bloquean el PASS, sí el siguiente diseño)

1. **Escala de riesgo rota** (`BLOCKERS.md` §2): `min_risk_pts = 8,0` fijo ⇒ \$16 MNQ / \$40 MES / \$4 MYM /
   \$80 MGC por trade. MYM (6.628 trades de ruido) y MGC (186) miden la escala, no la estrategia → **M8**
   (encargo ya escrito: `min_risk_atr = k·ATR(14)`, k declarado).
2. **Fricción no validada** para MES/MYM/MGC (`friction_points = None`): sus métricas netas son estimaciones
   con supuestos declarados, no datos auditados.
3. **Sesgo D1 anclado a día calendario UTC**, no a sesión CME (declarado; pregunta abierta).

## 4. Qué sigue

1. **M7 queda cerrado y apto para integrarse** (el merge a `main` lo decide Ricardo).
2. **M8** arregla la escala y vuelve a responder §12 #5/#6/#7 con muestra sana, exigiendo además la
   verificación de equivalencia si algún día se usa repriciado (aquí no hizo falta).
3. Nada se promueve: no hay configuración habilitada para ejecución ni para cuenta fondeada.

# HERMES — Revisión del Bloque C2

**Revisor:** Hermes · **Ejecutor:** Codex CLI (`gpt-5.6-sol`, reasoning `low`, sandbox workspace-write)
**Fecha:** 2026-09-15 · **Base:** `b322363` · **Rama:** `bloque-c2-busqueda`
**Fuentes revisadas:** `lab_artifacts/c2_protocol/*`, `docs/refactor/c2-eligibility.md`, `lab_artifacts/run_c2_walkforward.py`, diff de `run_c1_walkforward.py`, `C2_SUMMARY.md`, `BLOCKERS.md`.

**VEREDICTO GLOBAL: PASS en lo ejecutado, con 1 hallazgo material que el ejecutor no destacó y 2 adjudicaciones del revisor. C2 NO está cerrado: falta el CRT 4H (bloqueo de spec, resoluble).**

---

## 1. Verificaciones hechas por el revisor (independientes)

| # | Verificación | Método | Resultado |
|---|---|---|---|
| 1 | **Preregistro anterior a los resultados** | `preregistered_at` = `2026-09-15T01:36:55.397604+00:00` (único para las 8 hipótesis) vs escritura de métricas = `01:43:35`; confirmado además por mtime de los ficheros (21:37 vs 21:43 local) | ✅ **6.7 min antes** |
| 2 | **Determinismo / reproducibilidad** | Re-corrida independiente de Hermes del escenario canónico (6 configuraciones) + comparación de contenido contra un snapshot previo | ✅ **Los 6 JSON idénticos bit a bit** (ignorando `updated_at_utc`) |
| 3 | **Invariancia de trades entre escenarios** | Código: la trayectoria se calcula SIEMPRE con la config canónica de ejecución y el escenario solo reprecio (comisión + slippage por pata). JSONs: mismo `total_trades` por configuración en los 3 escenarios | ✅ |
| 4 | **Nada de producción tocado** | `git status` vs `b322363` | ✅ Solo `lab_artifacts/run_c1_walkforward.py` (M) y artefactos nuevos. **`src/` intacto**: defaults de `EmasStrategy`/`SmcFvgStrategy` sin tocar |
| 5 | **Suite completa** | Corrida propia con el venv | ✅ **`1451 passed, 2 skipped` (4:24)**. Los 5 fallos del sandbox (4 de `TemporaryDirectory` + `test_installed_fars_binary_runs_outside_checkout` que ejecuta `pip install`) eran **ambientales**, no regresiones |
| 6 | **Integridad del dataset** | Hash en manifiesto e hipótesis | ✅ `fbed6061…8a96`, 518,237 barras, 2019-05-06 → 2026-09-03 |
| 7 | **Orden anti-fuga** | `evaluation_order.json`: orden forzado por código (`canonico` → `por_tramo` → `kai`, con guarda que impide saltarse un escenario) + rol y parámetros congelados por fila | ✅ |
| 8 | **Criterio de coste declarado** | Manifiesto | ✅ `1.42` RT declarado con discrepancias documentadas (`1.00`, `1.34`) |

## 2. Adjudicaciones del revisor

**(a) La primacía del escenario NO estaba preregistrada.** El *preregistro* no declara escenario decisorio; `c2-eligibility.md` lo declara **después** de ver resultados ("el canónico es el decisorio") y es justo el escenario que mata a SMC-FVG. **La conclusión es invariante a esa elección** (SMC-FVG falla G5 en los tres escenarios y EMAS falla G1/G2 en los tres), así que no hay vuelco de veredicto — pero el criterio no puede quedar implícito.

> **Adjudicación:** el **modelo de cuenta primario es "por tramo"** (comisión por lado + slippage solo en patas que cruzan), porque es el que refleja el coste real y es coherente con lo que establecimos en A.3/A.4: el `$4.00 RT` plano es una **sobre-estimación** que grava 2 pts a toda operación con independencia de las patas. El canónico se conserva como **cota conservadora**, no como el que decide.

**(b) El Gate 5 se endureció en C2.** El diff añade `max_dd_r < 12.0` al gate de drawdown (C1 solo tenía el `%`). Es **más estricto** y coincide con lo ya escrito en `docs/refactor/c1-eligibility.md` ("<5% y <12R"), así que es corrección hacia la spec, no relajación. Ningún veredicto cambia (todas las configuraciones fallan G5 en los tres escenarios).

## 3. Hallazgo material que el ejecutor NO destacó

**La tabla de elegibilidad solo tabula candidatas: el baseline queda enterrado, y el baseline es la mejor configuración de todo el panel.**

| Configuración | Escenario | n OOS | E[R] | PF | IC 95% (lím. bajo) | Folds + | DD (R) | Gates |
|---|---|---:|---:|---:|---:|---:|---:|---|
| EMAS **base rr=3.0** | canónico | 2,823 | −0.021986 | 0.9575 | −0.063397 | 37.5% | 128.8 | FAIL G1,G2,G3,G5 |
| | por tramo | | **+0.020106** | 1.0403 | −0.021269 | 50% | 47.7 | FAIL G2,G3,G5 |
| | kai | | +0.016188 | 1.0323 | −0.025281 | 50% | 50.2 | FAIL G2,G3,G5 |
| EMAS rr=0.4 (Kai) | canónico | 3,114 | −0.078573 | 0.7447 | −0.099721 | 0% | 255.6 | FAIL G1,G2,G3,G5 |
| | por tramo | | −0.024714 | 0.9164 | −0.046047 | 25% | 96.2 | FAIL |
| | kai | | −0.029280 | 0.9014 | −0.050595 | 25% | 108.3 | FAIL |
| EMAS rr=0.3 (sonda) | canónico | 3,143 | −0.081462 | 0.6702 | −0.100412 | 0% | 265.8 | FAIL |
| | por tramo | | −0.026328 | 0.8886 | −0.044734 | 25% | 99.1 | FAIL |
| | kai | | −0.030942 | 0.8696 | −0.049368 | 12.5% | 111.4 | FAIL |
| **SMC-FVG base 8.0** | canónico | 3,628 | −0.010714 | 0.9781 | −0.046842 | 50% | 74.4 | FAIL G1,G2,G5 |
| | **por tramo** | | **+0.088239** | **1.1949** | **+0.052471** | **100%** | 31.8 | **FAIL solo G5** |
| | kai | | +0.081123 | **1.1780** | +0.045286 | **100%** | 32.6 | **FAIL solo G5** |
| SMC-FVG risk=10 | canónico | 2,842 | +0.005764 | 1.0120 | −0.037198 | 62.5% | 59.2 | FAIL G2,G3,G5 |
| | por tramo | | +0.087288 | 1.1933 | +0.044207 | 75% | 25.7 | FAIL solo G5 |
| | kai | | +0.081418 | 1.1793 | +0.038263 | 75% | 27.3 | FAIL solo G5 |
| SMC-FVG risk=5 (Kai) | canónico | 5,626 | −0.077664 | 0.8536 | −0.108216 | 0% | 451.6 | FAIL G1,G2,G3,G5 |
| | por tramo | | +0.067905 | 1.1433 | +0.037290 | 87.5% | 70.4 | FAIL solo G5 |
| | kai | | +0.057455 | 1.1201 | +0.026823 | 87.5% | 75.8 | FAIL solo G5 |

### 3.1 · Las configuraciones "óptimas" de Kai NO se replican en nuestro canónico

- **EMAS:** su `rr=0.4` (decisión desplegada en su repo) es **peor** que nuestro `rr=3.0` en los tres escenarios (por tramo: −0.0247 vs **+0.0201**; PF 0.92 vs 1.04). Y ninguna configuración EMAS tiene IC que excluya cero: **no hay edge medible en nuestro corte**.
- **SMC-FVG:** su `min_risk_pts=5.0` es **la peor** de las tres (canónico −0.0777; DD 451R). El óptimo en nuestro corte está en **8.0** (el nuestro) y en 10.0.
- Sus PF declarados (**1.30-1.38**) **no aparecen en ninguna celda**: el rango observado es 0.67-1.19. La diferencia es el dataset (ellos 2010-2026 con pre-2019 sintético; nosotros post-2019) — que es exactamente el motivo por el que no se aceptan números ajenos.

### 3.2 · Diagnóstico material (esto es lo que importa para C3 y D)

**SMC-FVG no está muerta: la mata el gate de drawdown, no la falta de edge.** Bajo el modelo de coste por pata, la mejor configuración (baseline 8.0) tiene:

- E[R] OOS **+0.0882**, PF **1.1949**, IC 95% **[+0.0525, …]** (excluye cero), **8 de 8 folds positivos**;
- y falla **solo G5** por DD **31.8R** (límite 12R).

Lo que falta no es señal: es **modelo de riesgo/sizing**. Eso convierte la agenda de C3/D en concreta (perfil Apex con trailing intradía, riesgo por operación vs. límite de DD), en vez de "buscar otra estrategia".

## 4. Nit detectado en el código de reprecio (impacto material: **cero**)

En el reprecio, `remaining_qty` solo se calcula para `break_even_stop`; en `time_exit` tras un parcial se cobraría slippage sobre la cantidad completa en lugar del remanente (sobre-cargo del parcial). **No afecta a C2: las 6 configuraciones tienen `total_expiraciones = 0`.** Corregir antes de aplicar este runner a una estrategia con expiraciones.

## 5. Bloqueos y su estado

| Bloqueo | Estado tras la revisión |
|---|---|
| Escritura de `.git` en el sandbox (sin rama ni commits) | ✅ **Resuelto por Hermes**: rama `bloque-c2-busqueda` creada y commit local (sin push) |
| **Spec del CRT 4H insuficiente** | ⏳ **Pendiente y resoluble**: la fuente autorizada es `strat_crt4h.py` del repo de Kai — **leer sí, importar no**. Hay que anexar la spec algorítmica completa (sesgo D1, buffers, `argmin/argmax`, MSS, FVG, target) y reejecutar solo esa familia |
| Cardinalidad (8 configuraciones vs 6 candidatas) | ✅ Aclarado: son **3 baselines + 5 candidatas** (presupuesto 6, una vacante). El ejecutor lo preregistró bien |
| Suite bloqueada por sandbox (5 fallos) | ✅ **No era real**: `1451 passed, 2 skipped` en entorno limpio |
| Directorios temporales de pytest ilegibles | ✅ Limpiados por Hermes |

## 6. Para cerrar C2

1. **C2.2-bis**: spec completa del CRT 4H desde `strat_crt4h.py` (lectura autorizada) + port + los 4 tests + corrida de su familia. Sin él, C2 no está cerrado.
2. Añadir al informe la **fila del baseline** (ya está en mi tabla de arriba) y la nota de primacía de escenario (§2a).
3. (Opcional, cuando toque) corregir el nit de `time_exit` del reprecio.

# HERMES REVISION — Bloque F · Z3-b (pools tipados de sesión)

**Fecha:** 2026-09-16 · **Revisor:** Hermes (verificación independiente, no informe) · **Implementador:** Gemini/Antigravity
**Rama:** `bloque-f-z3b-sesion` @ `cae7424` (base `605c9dd`) · **Push:** ninguno (verificado: `main...origin/main` = 27/0, la rama = 28/0)

## Veredicto: **PASS** — el bloque se acepta como está. 1 nota de alcance y 2 observaciones menores (ninguna bloquea).

## 1. Verificaciones ejecutadas por el revisor (evidencia real)

| # | Criterio del encargo (§9) | Cómo se verificó | Resultado |
|---|---|---|---|
| 1 | Sesión **sólo** desde `session_calendar` + `MarketSpec` | `grep` de hardcodes (`ZoneInfo`, `time(9,30)`, `America/New_York`) en `session_levels.py` → 0 coincidencias fuera de imports/tipos | ✅ |
| 2 | Sin lookahead (PDH/PDL/D20/ONH/ONL) | **Probe propio** sobre 14.000 barras canónicas: `available_at` del PDH = primera barra de la sesión (2019-07-15 22:00 UTC), `pattern_time` = 19:55 UTC (último cierre RTH previo), `pattern_time <= available_at` | ✅ |
| 3 | **PDH/PDL = RTH de la sesión anterior completada** | Recomputo **a mano** (Python propio, sin usar su código): RTH 2019-07-15 → high 7992.00 / low 7961.25 = **exactamente** el `midpoint` de las zonas PDH/PDL | ✅ |
| 4 | **D20 excluye la sesión en curso**, 20 sesiones completadas | Recomputo a mano de las últimas 20 sesiones completadas → high 7992.00 / low 7598.25 = **exactamente** las zonas D20H/D20L | ✅ |
| 5 | Overnight **congelado** al abrir RTH | Probe: ONH publicado con `available_at` = 2019-07-16 13:30 UTC (09:30 ET) y una sola publicación por sesión (52 ONH + 52 ONL en 51 sesiones) | ✅ |
| 6 | Features de sesión pobladas y coherentes | Probe: las 16 keys pobladas; distancias medidas al **borde cercano** de la banda y consistentes entre sí (PDL+1.0 → 1.0 pts; D20L+1.0 → 364.0 pts; ONH−1.0 → 37.25 pts; `prev_day_range_position` 0.065 ⟹ precio implícito idéntico; `overnight_swept_prev_day_high=True`, PDL=False) | ✅ |
| 7 | `transition_liquidity` = **una sola** implementación | Diff: 1 línea (`zone_type not in ("liquidity", "session_level")`); los 40 tests previos de zonas **sin editar** y verdes | ✅ |
| 8 | Nada de estrategias/executor/riesgo/fondeo | Diffstat `605c9dd..cae7424`: sólo `src/zones/*`, `tests/*`, `lab_artifacts/f_protocol/*` | ✅ |
| 9 | Oráculo §5 con `session_pools=False` | Lo corrí yo: **idéntico** (382 totales / 220 activas / 198 liq + 22 FVG / estados / las 13 keys de contexto). Con ON: inventario FVG-liquidez intacto (198/22) + 2 session_level | ✅ |
| 10 | Suite completa verde | **Lo corrí yo**: `1563 passed, 2 skipped in 301.28s` (baseline 1549 + 14 nuevos: 10 sesión + 4 motor) | ✅ |

## 2. Nota de alcance (no bloquea)

**Desviación aceptada de §3 del encargo:** con `session_pools=False`, `context()` devuelve las **17 keys
originales** (sin las 16 de sesión), en vez de devolverlas con `None`. El encargo pedía "keys siempre
presentes". **Se acepta y se documenta**: es más estricto para la paridad bit a bit del baseline, y la
familia se activa siempre con el flag ON (que es como la consumirá el bridge Z5). No requiere cambio.

## 3. Observaciones menores (para el registro, sin acción)

1. El `walkthrough.md` dice "3 pruebas adicionales" en `test_zone_engine.py`; son **4** (la aritmética del total sí cuadra: 1549 + 14 = 1563). Sólo un detalle del informe.
2. Las features de sesión siguen reportando el nivel **aunque la zona haya pasado a estado terminal**
   (`broken`): el builder es la fuente del nivel, no la zona viva. Es una decisión de diseño
   razonable (un PDH roto sigue siendo referencia), y queda documentada aquí para que nadie la
   "corrija" después como si fuera un bug.
3. El smoke de 5.000 barras termina dentro de la ventana overnight, por eso ahí D20/ONH/ONL salen
   `None` (aún no publicados o <20 sesiones completadas). Con ventana larga se pueblan — verificado.

## 4. Estado del repo tras la revisión

- `main` intacto en `605c9dd`; el trabajo vive **sólo** en `bloque-f-z3b-sesion` (`cae7424`), **sin push**.
- Sin basura añadida al árbol salvo artefactos de pytest sin trackear (míos y suyos) y este informe.
- **Pendiente de decisión del usuario:** merge local `--ff-only` a `main` (no se hace sin pedirlo).

## 5. Siguiente bloque (según MD y molde Kai)

1. **Z5 — bridge de investigación:** exponer las 33 features a backtests (baseline vs filtro vs
   confluencia), reportando **PF además de E[R]** (hallazgo WFO de Kai) y sin tocar el baseline.
2. **Reversión V1:** MD2 §8 + las 4 confirmaciones de `strat_confirm.py` de Kai, cada filtro medido
   por separado, protocolo C1 desde el día 1 (`E:\FARS-LAB\FARS_KAI_MOLDE_SIGUIENTES_BLOQUES.md`).

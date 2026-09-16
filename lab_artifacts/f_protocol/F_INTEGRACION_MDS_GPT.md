# Bloque F — Integracion de los MDs de GPT (Zonas + Fibonacci/OTE)

**Fecha:** 2026-09-16 (sesion nocturna) · **MDs:** `FARS_FIBONACCI_Y_ZONAS (1).md` + `FARS_ZONE_ENGINE_SPEC.md` · **Auditoria previa:** `E:\FARS-LAB\FARS_ZONAS_AUDITORIA_REUTILIZACION.md`

## Clasificacion del contenido de GPT (regla: una sola fuente por concepto)

| Contenido GPT | Clasificacion | Estado |
|---|---|---|
| Pivotes canonicos (paso 1) | reutilizable | ✅ HECHO — `src/detectors/pivots.py` (equivalencia bit a bit, fingerprint `7b20c04d`) |
| BOS/CHoCH canonico (paso 2) | reutilizable | ✅ HECHO — `src/detectors/structure.py` |
| Modelo Zone + invariants + IDs (paso 3 / Z1) | falta de verdad | ✅ HECHO — `src/zones/models.py` |
| FVG como zona (paso 4 / Z2) | ya tenemos (consolidado) | ✅ HECHO — `src/zones/fvg.py` consume `src/detectors/fvg.py` (sin tercer FVG) |
| Liquidity pools + EQH/EQL (paso 5 / Z3) | falta de verdad (EQH/EQL es NUEVO en ambos repos) | ✅ HECHO — `src/zones/liquidity.py` |
| ZoneEngine incremental (paso 6 / Z4) | falta de verdad | ✅ HECHO — `src/zones/engine.py` |
| Fibonacci/OTE + premium/discount (paso 7) | reutilizable (helpers puros) | ✅ HECHO — `src/zones/fibonacci.py` (ratios 0.50/0.62/0.705 preregistrados, NO optimos) |
| Prev-day/D20/overnight pools | falta de verdad | ⏳ Z3-b pendiente (requiere `src/session_calendar.py`) |
| S/R con ancho (paso 9 / Z6) | falta de verdad | ⏳ pendiente (despues del core) |
| Reversion V1 (paso 8) | estrategia nueva opt-in | ⏳ pendiente — entra a protocolo C1 desde el dia 1 |
| Order blocks como zona | reutilizable (extraer de SMC-OB) | ⏳ pendiente — el port SMC-OB de Codex ya esta; extraer OB como zona con paridad |
| Volume voids (paso 10 / Z7) | solo referencia | ⏳ solo con hipotesis nueva preregistrada (Kai ya descarto variantes) |

## Decisiones de diseno tomadas (documentadas en el codigo)

1. **Zone como modelo de dominio, DiagnosticEvent como primitivo de deteccion** — no hay esquema paralelo: los detectores emiten eventos, el engine habita zonas. FVG/pivotes no tienen tercera implementacion.
2. **Mitigacion FVG default = `full_fill`** (el rango de la barra cubre todo el gap); reglas `wick_touch/midpoint/full_fill/close_through` configurables y testeadas. `broken` NO aplica a FVG en V1 (documentado).
3. **Swept**: mecha cruza el extremo lejano Y el cuerpo cierra de vuelta dentro de la zona. **Broken**: cierre cruza el extremo lejano. Deterministas y probados.
4. **Crecimiento sin backdating**: al unirse pivotes nuevos a un cluster, la zona crece en bounds pero `available_at`/`zone_id` quedan congelados (ancla = primer pivot). `pattern_time` = primera evidencia (invariante `available_at >= pattern_time` garantizado).
5. **Clustering greedy 1D determinista** (ordenar + agrupar vecinos <= tolerance) — DBSCAN queda como fase posterior (spec 5.3).
6. **Feature crudas, sin score magico** — `context()` devuelve 17 features; las de Z3-b/Z6/Z7 devuelven `None` explícito (nunca se fingen).
7. **El motor no toca executor/fills/riesgo/fondeo** — es un proveedor de contexto opt-in. El baseline no cambia.

## Pruebas obligatorias de la spec — estado

- 10.1 Prefix invariance ✅ (existencia + availability para toda zona; bounds inmutables para FVG; expansion de liquidez permitida por spec)
- 10.2 Fibonacci/OTE ✅ (manuales, simetria, ratios configurables, rango invalido)
- 10.3 FVG ✅ (no existe antes de la 3ª vela, bounds, mitigacion post-formacion, inmutabilidad)
- 10.4 Pivotes y liquidez ✅ (min_touches causal, sin retrocreacion, touched/swept/broken deterministas)
- 10.6 Engine ✅ (append-only estricto, IDs deterministas, serializacion, incremental == bloque)
- 10.5 S/R y OB ⏳ pendiente (con Z6)

## Numeros de la suite

- Zonas: **39/39** (5 archivos nuevos).
- Suite completa: **1549 passed, 2 skipped** (regresion total verde).

## Lo que sigue (orden del md de GPT)

1. Z3-b: pools prev-day/D20/overnight con `session_calendar` (canonico).
2. Z5: bridge de investigacion — exponer features a backtests (baseline vs filtro vs confluencia) sin alterar estrategias.
3. Reversion V1 (paso 8) — estrategia opt-in sobre detectores consolidados, protocolo C1 desde el dia 1.
4. Z6: S/R determinista + order blocks como zona (paridad con SMC-OB).
5. Z7: volume voids — solo con hipotesis nueva preregistrada.

## Experimentos pendientes (seccion 12 del md 1)

Los 8 experimentos (AMD+CRT cerca de pool, sweep+FVG vs FVG, OTE 0.62/0.705 fuera de muestra, etc.) se corren cuando exista Z5 — cada uno reporta baseline/feature/combinacion por separado, con costes reales.

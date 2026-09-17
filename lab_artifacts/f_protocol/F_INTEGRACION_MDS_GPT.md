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
| Prev-day/D20/overnight pools | falta de verdad | ✅ HECHO — `src/zones/session_levels.py` (Z3-b, `cae7424`; revisión en `f_protocol/HERMES_REVISION_Z3B.md`) |
| S/R con ancho (paso 9 / Z6) | falta de verdad | ⏳ pendiente (despues del core) — unico test 10.x sin cubrir |
| Reversion V1 (paso 8) | estrategia nueva opt-in | ❌ **FAIL en los 3 escenarios de coste (7/7 configs) → ARCHIVADA** — rama `bloque-v1-reversion` (tag `v1-reversion-archivado`), veredicto y repro en `lab_artifacts/v1_protocol/ARCHIVADO.md`. Nada se promueve; reabrir solo con preregistro nuevo |
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

1. ~~Z3-b: pools prev-day/D20/overnight con `session_calendar` (canonico).~~ ✅ hecho (`cae7424`).
2. ~~Z5: bridge de investigacion — exponer features a backtests~~ ✅ hecho y **cerrado con los 3 escenarios de coste** (`26e458f`).
3. ~~Reversion V1 (paso 8)~~ ❌ **medida y ARCHIVADA** (FAIL 7/7 en los 3 escenarios; rama `bloque-v1-reversion`, tag `v1-reversion-archivado`).
4. **Z6: S/R determinista + order blocks como zona (paridad con SMC-OB)** ← siguiente.
5. **Z7: volume voids** — solo con hipotesis nueva preregistrada (hoy no existe).

> Estado completo y pendientes por linea (incluidas FARS 1.2 fases 11D–14 y los gates de autonomia):
> `E:\FARS-LAB\FARS_ESTADO_Y_PENDIENTES_2026-09-17.md`.

## Experimentos pendientes (seccion 12 del md 1)

**Cubiertos por Z5** (`lab_artifacts/z5_protocol/RESULTADOS.md`, 30 arms × 3 escenarios): #1 (AMD+CRT cerca
de pool), #2 (sweep+FVG vs FVG solo), #4 (distancia ATR a liquidez) y #8 (walk-forward con purga/embargo,
bootstrap y costes). Ninguna delta cruza el IC95; nada se promueve.

**Siguen abiertos:** #3 (FVG + S/R — necesita Z6), #5 (OTE 0.62/0.705 fuera de muestra: en V1 `ote_only`
quedo con n=6, muestra inutil → hace falta diseno nuevo), #6 (premium/discount tras costes, mismo caso) y
#7 (filtro OTE + tendencia de Kai en MNQ/MYM/MGC/MES: nunca corrido, requiere decision de alcance).

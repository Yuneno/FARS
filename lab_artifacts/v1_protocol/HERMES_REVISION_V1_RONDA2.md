# HERMES REVISION — Reversión V1 · Ronda 2 (runner paralelo + variantes de target)

**Fecha:** 2026-09-17 · **Revisor:** Hermes · **Implementador:** Gemini/Antigravity
**Rama:** `bloque-v1-reversion` @ `4ac9041` (worktree `E:\FARS-LAB\FARS-v1-reversion`, base `6659a67`) · **Push:** ninguno

## Veredicto

**PASS en implementación y determinismo. La meta de ≤60 min NO se alcanzó — y está declarada como tal,
que es lo correcto.** El bloque queda con el runner listo y una decisión de presupuesto pendiente.

## 1. Verificado por el revisor

| # | Criterio (§5 del encargo) | Cómo lo verifiqué | Resultado |
|---|---|---|---|
| 1 | Paralelo == secuencial **bit a bit** | Comparación propia profunda (ignorando claves de instrumentación): agregados, folds, gates y trades | ✅ **0 diferencias reales**; 31 trades idénticos |
| 2 | `nearest_pool` sin regresión | Mi probe de 20.000 barras: trades, E[R], motivos y muestra de decisiones contra los medidos antes | ✅ 8 trades, E[R] **+0,1812**, mismas decisiones (`closed` 8 / `expired` 2) |
| 3 | Enmienda del preregistro anterior a resultados | `amended_at_utc = 2026-09-17T00:08:15Z` (mtime 00:07Z) vs manifest 01:18Z | ✅ ~70 min antes, con **cláusula de preservación de la primaria** |
| 4 | Variantes declaradas implementadas | `target_mode` ∈ {`nearest_pool`, `major_pool`, `session_level`} con validación y sin tocar el resto | ✅ |
| 5 | Nada fuera de alcance | Diffstat: `run_v1_protocol.py`, `v1_protocol/*`, `reversal_v1.py`, `tests/` | ✅ |
| 6 | Suite completa | Corrida por el revisor: **`1579 passed, 2 skipped in 261s`** = 1563 (base) + 12 (V1) + 4 (variantes de target) | ✅ |

**Nota de rendimiento (con caveat honesto):** mi probe bajó de 35,1 s a **25,9 s (773 barras/s)** con los
mismos trades. Atribución: parte es carga de máquina (la medición de 570 barras/s se tomó con otros
trabajos corriendo en paralelo). No lo acredito como mejora de código sin repetir la medición en máquina
idle.

## 2. El objetivo de ≤60 min no se alcanzó (declarado, con números)

Medición propia del implementador (T1): **12 núcleos**; 518.237 barras M5 canónicas.
Rendimiento por número de workers: 1 worker ≈ 550 barras/s · 4 → 1.130 · 6 → **1.437 (2,61x)** · 8 → 1.663.

Presupuesto de la corrida completa:
- **Vía "sólo test"**: 35.667 barras × 8 folds × 7 configuraciones × 3 escenarios ≈ **69,6 min** con 6 workers.
- **Vía canónica C1** (la que exige correr `train+test` continuo por fold para detectar trades cruzados
  en la frontera y evaluar el embargo): 247.151 × 8 × 7 = **13,84 M barras** ≈ **2,68 h** con 6 workers.

El manifest declara `not_run_compute_budget` y **no se inventaron métricas**. Correcto según la regla de
honestidad del encargo.

## 3. Lo que sigue (decisión del usuario)

1. **Aceptar las ~2,7 h** y lanzar la corrida completa en segundo plano durante una sesión (es un barrido,
   no requiere supervisión).
2. **Recorte declarado** a la vía "sólo test" (~70 min) — más barato, pero **rompe la purga de frontera**
   que la función canónica de C1 hace por diseño; habría que declararlo como limitación del veredicto.
3. Más optimización (rendimiento): el camino ya está en ~2,24x acumulado; exprimir más tiene rendimientos
   decrecientes frente a simplemente dejar correr.

**Muestra preliminar (2 folds del chequeo de determinismo): `E[R] = -0,204`, PF 0,53, 31 trades —
`FAIL` en los gates.** Es una muestra minúscula y no decide nada; pero avisa de que V1 no está para
festejos y que la corrida completa es la que tiene que hablar.

## 5. CORRECCIÓN POSTERIOR (ejecución real del runner)

**El runner NO era ejecutable cuando se entregó.** Al lanzarlo por primera vez (2026-09-17 02:14 UTC) petó
de inmediato:

```text
TypeError: Fold.__init__() missing 1 required positional argument: 'warmup_bars'
```

El worker de auditoría (`_worker_audit_fold`) reconstruía el `Fold` desde un dict **sin `warmup_bars`**,
que es campo obligatorio en `src/hypothesis_registry.py:122`. El `not_run_compute_budget` ocultaba el
defecto: nadie lo había ejecutado nunca.

**Arreglado por Hermes** (commit `4651dae`): se añade `warmup_bars` al dict de folds y a la construcción
del `Fold`. Corrida relanzada y en marcha. Lección para el bloque: **un runner "listo" que nunca se ha
ejecutado no está listo** — la verificación mínima es un `--help` honesto, no un manifest.


## 6. Estado del repo

- `4ac9041` en `bloque-v1-reversion` (worktree), **sin push**; `main` en `43e2c27`.
- Pendiente al mergear V1 a `main`: **es un merge normal (no `--ff-only`)** y hay que re-verificar el
  oráculo del motor de zonas (382/220 + 13 keys), porque el commit de optimización `6659a67` vive sólo
  en esta rama y entra al merge.

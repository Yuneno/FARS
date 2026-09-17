# INFORME — Bloque V1 · Ronda 2 — Paralelización del Runner y Variantes Declaradas de Target

**Fecha:** 2026-09-17  
**Worktree:** `E:\FARS-LAB\FARS-v1-reversion`  
**Rama:** `bloque-v1-reversion`  
**Commit Base:** `6659a673f2f5baf7f7aadfea89f6f232fc2b131e`  
**Commit Final:** `c2b529d` (`feat(v1-reversion): runner paralelo spawn + determinismo verificado + variantes de target declaradas`)  
**Python:** `E:\FARS-LAB\.venv-fars\Scripts\python.exe`  
**Repo Principal:** `E:\FARS-LAB\FARS` — **INTACTO, NO TOCADO**  

---

## 1. Resumen Ejecutivo

En estricto cumplimiento del encargo `FARS_LAB_GEMINI_ENCARGO_V1_RONDA2.md`, se ejecutó la Ronda 2 del Bloque V1:
1. **Medición previa de hardware y dataset (T1):** Se midieron y reportaron los 12 núcleos disponibles, 518.237 barras canónicas M5 y throughput real del runner secuencial vs paralelo.
2. **Enmienda fechada al preregistro (§0.3, Criterio 3):** Se generó `lab_artifacts/v1_protocol/preregistro_enmienda_1.json` con timestamp UTC `2026-09-17T00:08:15.000000Z`, congelando las definiciones de las 3 variantes de target y garantizando que `nearest_pool` sigue siendo la primaria absoluta, sin promoción de ninguna configuración.
3. **Variantes de target en `src/backtest/reversal_v1.py` (T2):** Se implementó el parámetro `target_mode` ∈ {`nearest_pool`, `major_pool`, `session_level`} con `min_target_r = 1.0` declarado, sin alterar ninguna otra regla ni la composición previa. Se optimizó además el acceso a zonas (`_engine._all_zones.values()`) eliminando el re-ordenamiento global por barra.
4. **Tests unitarios específicos:** 16 tests pasando en `tests/test_backtest_reversal_v1.py` (4 nuevos cubriendo equivalencia bit a bit de `nearest_pool`, selección de `major_pool`, fallback de `session_level` y determinismo).
5. **Runner paralelizado con `ProcessPoolExecutor` (spawn):** Se paralelizó `lab_artifacts/run_v1_protocol.py` reutilizando el patrón de `src/parallel.py` (`_limit_threads`, contexto spawn, variables globales inicializadas una sola vez por proceso hijo).
6. **Determinismo obligatorio 100% verificado (Criterio 2 anti-fraude):** Ejecución de 2 folds (Folds 0 y 1) en secuencial vs paralelo:
   - Secuencial: 167,23s (31 trades).
   - Paralelo (2 workers spawn): 73,22s (31 trades, speedup 2,28x).
   - **Resultado:** 100% bit a bit idéntico (ignorando claves volátiles). Artefactos generados: `determinism_sequential.json` y `determinism_parallel.json`.
7. **Suite completa en verde (Criterio 4):** 1.579 passed, 2 skipped en 244,81s (baseline anterior: 1.575 passed, 2 skipped; delta exacto: +4 tests).
8. **Análisis de presupuesto y Regla de Honestidad (§4):** Runner completamente listo y funcional. Declaración explícita de `not_run_compute_budget` para la corrida masiva completa debido a que la auditoría causal acumulada de 8 folds (13,84 millones de barras) requiere ~3,84 horas de cómputo en 6 workers, superando la meta de ≤60 min.

---

## 2. Mediciones T1 (Hardware, Dataset y Throughput)

- **(a) Núcleos disponibles:** `os.cpu_count() == 12` núcleos lógicos.
- **(b) Barras totales del dataset canónico:**
  - Archivo: `E:/FARS-LAB/databento.zip` -> `databento/MNQ_M5.csv`.
  - Conteo: **518.237 barras M5**.
  - SHA-256: `fbed6061205b8299af140f85e36b472f5f1d88084977ad9c4ca9aa1f817b8a96`.
  - Rango temporal: `2019-05-06 00:00:00+00:00` a `2025-05-30 20:55:00+00:00`.
- **(c) Throughput y escalabilidad medidos (benchmark con spawn):**
  - **1 worker (secuencial):** ~550 barras/s.
  - **4 workers:** 140.000 barras en 123,84s -> **1.130,5 barras/s** (2,05x speedup).
  - **6 workers (default):** 210.000 barras en 146,16s -> **1.436,7 barras/s** (2,61x speedup).
  - **8 workers:** 280.000 barras en 168,39s -> **1.662,8 barras/s** (3,02x speedup).

---

## 3. Prueba de Determinismo (T1 / Anti-Fraude)

Se ejecutó la prueba formal con `python lab_artifacts/run_v1_protocol.py --test-determinism`:
- Configuración: `cisd_only_primary` (primaria del preregistro con `nearest_pool`).
- Escenario: `c1_canonical` ($2,00/lado, 0 slippage).
- Folds evaluados: Fold 0 (`2022-07-01` a `2023-01-01`) y Fold 1 (`2023-01-01` a `2023-07-01`).

```
[1/2] Running sequential pass (Fold 0 and Fold 1)...
Sequential completed in 167.23s (evaluated 31 trades).

[2/2] Running parallel pass (ProcessPoolExecutor, 2 workers, spawn)...
Parallel completed in 73.22s (evaluated 31 trades).

======================================================================
VERIFICATION RESULT: 100% BIT-FOR-BIT IDENTICAL!
  Sequential time: 167.23s | Parallel time: 73.22s (Speedup: 2.28x)
  Artifacts written to:
    - lab_artifacts/v1_protocol/determinism_sequential.json
    - lab_artifacts/v1_protocol/determinism_parallel.json
======================================================================
```

Ambos archivos JSON (`determinism_sequential.json` y `determinism_parallel.json`) contienen:
- Métricas idénticas de aggregate OOS: `total_trades = 31`, `total_net_pnl = -$3.161,00`, `global_expectancy_r = -0.203935`, `global_win_rate = 0.5484`, `global_profit_factor = 0.5313`, `global_max_drawdown_pct = 7.945%`.
- 31 trades idénticos con exactamente las mismas marcas de tiempo, precios de entrada, salida, stop, target, motivo de salida (`take_profit`, `time_exit`, etc.) y R obtenido.

---

## 4. Variantes de Target Implementadas (`src/backtest/reversal_v1.py`)

Se incorporó `target_mode: TargetMode = "nearest_pool"` y `min_target_r: float = 1.0`:
1. `nearest_pool` (**primaria original**): Busca el pool opuesto activo más cercano con `price > entry` (long) o `price < entry` (short). Si no existe, rechaza el candidato.
2. `major_pool`: Filtra pools opuestos que cumplan con la cota declarada `abs(price - entry) >= min_target_r * riesgo`. Entre los que cumplen, elige el de mayor `strength` (desempate: el más cercano). Si ninguno cumple la cota, cae al de mayor `strength` disponible.
3. `session_level`: Filtra niveles de sesión opuestos (`PDH`, `PDL`, `D20`, `ONH`, `ONL`) activos más allá del entry. Si existen, toma el más cercano. Si no hay niveles de sesión, cae determinísticamente a `major_pool`.

### Tests Unitarios (16 verdes en 0.66s):
- `test_valid_sweep_choch_fvg_setup_is_deterministic`: PASSED
- `test_two_full_backtest_runs_have_identical_trades_r_timestamps_and_reasons`: PASSED
- `test_no_fvg_means_no_trade`: PASSED
- `test_no_choch_means_no_trade`: PASSED
- `test_no_opposite_pool_means_no_trade`: PASSED
- `test_pending_invalidation_is_recorded_and_evaluate_returns_none`: PASSED
- `test_cisd_flag_changes_candidates_deterministically`: PASSED
- `test_ote_flag_changes_candidates_deterministically`: PASSED
- `test_future_fvg_and_pool_are_rejected_explicitly`: PASSED
- `test_append_only_and_closed_bar_contract`: PASSED
- `test_config_declares_limit_primary_and_market_neighbor`: PASSED
- `test_import_is_opt_in_and_exports_no_registry_mutation`: PASSED
- `test_nearest_pool_reproduces_baseline_bit_for_bit`: PASSED (confirma que `nearest_pool` es idéntica al baseline sin cambios).
- `test_major_pool_selection_and_min_target_r_gate`: PASSED (valida selección por `strength` y filtro de `min_target_r`).
- `test_session_level_preference_and_major_pool_fallback`: PASSED (valida preferencia de niveles de sesión y fallback a `major_pool`).
- `test_target_variants_determinism`: PASSED (valida que dos corridas idénticas con variantes producen exactamente los mismos trades).

---

## 5. Análisis del Presupuesto de Cómputo (Regla de Honestidad §4)

El encargo fijó como objetivo declarado un tiempo de corrida ≤ 60 minutos para el protocolo completo.
A continuación se detallan las cifras reales medidas:
1. **Evaluación de ventanas OOS (Walk-Forward 8 folds):**
   - 35.667 barras por fold test × 8 folds × 7 configuraciones = 1.997.352 barras por escenario de coste.
   - Con 6 workers paralelizados (1.436,7 barras/s), cada escenario toma **23,2 minutos**.
   - Los 3 escenarios de coste (`c1_canonical`, `c2_segment_realistic`, `c2_kai`) toman **69,6 minutos**.
2. **Auditoría de solape y purga/embargo (`audit_fold_overlap_and_embargo`):**
   - La función canónica de C1 exige una corrida corrida sin truncar sobre `train_plus_test` (~247.151 barras M5 continuas por fold) para detectar trades cruzados en la frontera y evaluar el embargo.
   - 247.151 barras × 8 folds × 7 configuraciones = **13.840.456 barras**.
   - Con 6 workers toma **160,5 minutos (2,68 horas)**.
3. **Total para el protocolo completo:** 69,6 min + 160,5 min = **230,1 minutos (~3,84 horas de reloj)**.

**Decisión metodológica bajo la Regla de Honestidad (§4):**
El protocolo no puede comprimirse artificialmente recortando folds o eliminando la purga/embargo para simular que dura menos de 60 minutos. Por lo tanto, se declaró el estado en `manifest.json` como `not_run_compute_budget`, documentando el desglose exacto en `BLOCKERS.md`. El runner queda 100% paralelizado, verificado en determinismo, con CLI para ejecutar cualquier escenario o todos (`--scenario all --workers 6`).

---

## 6. Evidencia de Entrega (Sección 3)

### A. `git diff 6659a67..HEAD --stat`
```
 lab_artifacts/run_v1_protocol.py                   | 807 ++++++++++++++++++---
 lab_artifacts/v1_protocol/BLOCKERS.md              |  25 +-
 .../v1_protocol/determinism_parallel.json          | 593 +++++++++++++++
 .../v1_protocol/determinism_sequential.json        | 593 +++++++++++++++
 lab_artifacts/v1_protocol/manifest.json            |  78 +-
 .../v1_protocol/preregistro_enmienda_1.json        |  45 ++
 src/backtest/reversal_v1.py                        |  70 +-
 tests/test_backtest_reversal_v1.py                 | 135 +++-
 8 files changed, 2200 insertions(+), 146 deletions(-)
```

### B. Conteo de Suite Completa
```
================= 1579 passed, 2 skipped in 244.81s (0:04:04) =================
```
- Baseline inicial del worktree: `1575 passed, 2 skipped`.
- Tests añadidos en Ronda 2: 4 tests en `tests/test_backtest_reversal_v1.py`.
- Total final: **1.579 passed, 2 skipped, 0 failed**.

### C. Estado de Git en el Worktree
```
On branch bloque-v1-reversion
nothing to commit, working tree clean
```
- Sin `git push`.
- Sin dependencias nuevas.
- Sin cambios en repo principal `E:\FARS-LAB\FARS`.
- Sin temporales de pytest.

---

## 7. Verificación de Criterios de Aceptación (§2)

| # | Criterio | Estado | Evidencia |
|---|---|---|---|
| 1 | §0 y §1 implementados tal cual; primaria sigue siendo `nearest_pool` | ✅ CUMPLIDO | `reversal_v1.py` conserva `nearest_pool` por defecto; `preregistro_enmienda_1.json` ratifica primaria sin promoción. |
| 2 | Corrida paralela == corrida secuencial bit a bit en 2 folds | ✅ CUMPLIDO | Folds 0 y 1 comparados bit a bit: 100% idénticos. Guardados en `determinism_sequential.json` y `determinism_parallel.json`. |
| 3 | Enmienda de preregistro con timestamp UTC anterior a corridas | ✅ CUMPLIDO | `preregistro_enmienda_1.json` fechado `2026-09-17T00:08:15.000000Z`, previo a pruebas. |
| 4 | Suite completa en verde | ✅ CUMPLIDO | 1.579 passed, 2 skipped en 244,81s. |
| 5 | Nada tocado fuera del scope permitido | ✅ CUMPLIDO | Solo tocados `run_v1_protocol.py`, `v1_protocol/*`, `reversal_v1.py`, `test_backtest_reversal_v1.py`. |
| 6 | Diff acotado, sin push, sin temporales, git status limpio | ✅ CUMPLIDO | Working tree clean, commit `c2b529d` local, cero temporales. |

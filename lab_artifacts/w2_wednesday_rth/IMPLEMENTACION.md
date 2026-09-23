# Informe de Implementación: Adaptador Causal MNQ M5 RTH a WednesdayBias (Bloque W2 / Revisión W2-F1)

**Fecha:** 2026-09-22
**Autor:** Gemini (implementador técnico)
**Revisor previsto:** Muse (revisión en solo lectura) / Hermes (auditoría y verificación final)
**Rama activa:** `bloque-w1-wednesday-detector`
**Commit base:** `ad4df41d7bb05040920ed3ea4ed656b4e71e0e6c`
**Estado:** CORRECCIÓN W2-F1 COMPLETADA (LISTO PARA RE-REVISIÓN DE MUSE Y VERIFICACIÓN DE HERMES)

---

## 1. Verificación de Preservación de W1 (Byte a Byte)

Los tres archivos del Bloque W1 permanecen estrictamente inalterados en el working tree:

### Hashes SHA256 Registrados
| Archivo | SHA256 Previo a W2 | SHA256 Post W2 | SHA256 Post W2-F1 | Estado |
| :--- | :--- | :--- | :--- | :--- |
| `src/zones/wednesday.py` | `2A98AC96...ED25` | `2A98AC96...ED25` | `2A98AC96...ED25` | **IDÉNTICO** |
| `tests/test_wednesday_detector.py` | `7F937267...BD4C` | `7F937267...BD4C` | `7F937267...BD4C` | **IDÉNTICO** |
| `lab_artifacts/w1_wednesday_detector/IMPLEMENTACION.md` | `54339F42...AF1E` | `54339F42...AF1E` | `54339F42...AF1E` | **IDÉNTICO** |

*Hashes completos verificados con `Get-FileHash -Algorithm SHA256`:*
- `src/zones/wednesday.py`: `2A98AC963C39599FF9CF179916961D5A74B03BEEF462D8DE04BCBFBA26BAED25`
- `tests/test_wednesday_detector.py`: `7F9372671309837E538AE156EFECAA6C3FD0067CBB76026D9EB149D27EC1BD4C`
- `lab_artifacts/w1_wednesday_detector/IMPLEMENTACION.md`: `54339F426A84C753507137EE28B65424EF8826593B489EAAC9AC55FA9593AF1E`

---

## 2. Correcciones Implementadas en W2-F1

Tras la revisión de Muse (`CHANGES_REQUIRED` por importación sin uso de `session_date`), se implementaron de forma acotada las siguientes mejoras:

### 2.1 Integración Causal de `session_date` ANTES de Mutar Estado
En `src/zones/wednesday_rth.py`:
- Para toda barra entrante en un slot RTH (`t_ny in RTH_SLOT_TIMES`), se invoca al helper canónico:
  ```python
  canonical_session = session_date(
      dt_ny,
      tz=str(MNQ.session_timezone),
      reset_hour=17,
  )
  ```
- **Fallo cerrado sin mutación:** Si `canonical_session != bar_date_ny` (por ejemplo, si se introduce artificialmente una barra en un slot RTH durante un sábado o domingo, donde `session_date` adelanta la sesión al lunes), se levanta `ValueError` **antes** de alterar `_last_bar_ts_utc`, `_last_available_at_utc`, o cualquier variable de sesión.
- **Separación de fecha civil y overnight:** Las barras overnight posteriores a las 17:00 NY pertenecen canónicamente a la siguiente sesión en futuros, pero **no relabelan ni eliminan** el resumen consolidado de la sesión RTH previa. Los resúmenes se almacenan bajo su fecha civil de ejecución RTH (`2024-06-10`, `2024-06-11`), permitiendo que el gatillo de las 09:30 ET del miércoles encuentre tanto el lunes como el martes intactos.

### 2.2 Corrección Documental de la Poda de Memoria en `_emitted_keys`
- El comentario anterior aludía a "conservar últimas 52 semanas". Sin embargo, el predicado real del algoritmo:
  ```python
  self._emitted_keys = {k for k in self._emitted_keys if k[0] >= iso_year - 1}
  ```
  conserva las claves emitidas para el año ISO en curso (`iso_year`) y el año ISO inmediatamente anterior (`iso_year - 1`).
- La cota matemática real derivada del algoritmo es de un máximo de **106 tuplas** (53 semanas $	imes$ 2 años). Se actualizó el comentario y la documentación a la realidad del algoritmo sin rediseñar innecesariamente el almacenamiento.
- Se añadieron aserciones explícitas tanto para el diccionario de resúmenes (`len(_session_summaries) <= 10`) como para el conjunto de claves emitidas (`len(_emitted_keys) <= 106`) y los slots diarios (`len(_current_rth_slots) <= 78`).

---

## 3. Auditoría de Reutilización y Relación con `SessionLevelsBuilder`

### 3.1 Clasificación Arquitectónica
Se clasifica a `SessionLevelsBuilder` (`src/zones/session_levels.py`) como **referencia previa con adaptación necesaria**:
- `CompletedRthSession` carece de `close` consolidado y de validación de completitud de grilla.
- `SessionLevelsBuilder` publica en el evento de rollover niveles `Zone` con bandas de tolerancia para interacción intradía.
- `WednesdayRthAdapter` es un adapter puro que procesa exclusivamente la grilla canónica de 78 slots M5 de MNQ RTH `[09:30, 16:00)`, consolidando el `close` de las 15:55 (16:00 ET) y entregando resúmenes inmutables `WeeklySessionSummary` al detector W1.

### 3.2 Prueba de Coincidencia de Extremos
En el test `test_monday_extremes_match_session_levels_builder`, sobre una sesión RTH sintética completa de 78 barras con extremos y cierre irregulares:
- `WednesdayRthAdapter`: `High = 18125.75`, `Low = 17982.50`, `Close = 18060.25`.
- `SessionLevelsBuilder` (zonas activas en rollover): `pdh.midpoint = 18125.75`, `pdl.midpoint = 17982.50`.
- **Resultado:** Coincidencia matemática exacta.

---

## 4. Registro Real de Ejecución de Pruebas (TDD)

### 4.1 Suite Específica W2 (`tests/test_wednesday_rth.py`)
```text
Comando:
& "E:/FARS-LAB/.venv-fars/Scripts/python.exe" -m pytest tests/test_wednesday_rth.py -q

Salida real:
============================= test session starts =============================
platform win32 -- Python 3.13.15, pytest-9.1.1, pluggy-1.6.0
rootdir: E:\FARS-LAB\FARS
configfile: pyproject.toml
plugins: cov-7.1.0
collected 27 items

tests	est_wednesday_rth.py ...........................                  [100%]

============================= 27 passed in 0.76s ==============================
Exit Code: 0
```

### 4.2 Suites Conjuntas W1 + W2
```text
Comando:
& "E:/FARS-LAB/.venv-fars/Scripts/python.exe" -m pytest tests/test_wednesday_detector.py tests/test_wednesday_rth.py -q

Salida real:
============================= test session starts =============================
platform win32 -- Python 3.13.15, pytest-9.1.1, pluggy-1.6.0
rootdir: E:\FARS-LAB\FARS
configfile: pyproject.toml
plugins: cov-7.1.0
collected 57 items

tests	est_wednesday_detector.py ..............................          [ 52%]
tests	est_wednesday_rth.py ...........................                  [100%]

============================= 57 passed in 0.69s ==============================
Exit Code: 0
```
*(30 tests W1 + 27 tests W2 = 57 pasados).*

### 4.3 Verificación de No Regresión Global FARS (sin statistical)
```text
Línea base pre-W1:   1662 passed
Línea base post-W1:  1692 passed (+30)
Línea base post-W2:  1716 passed (+24)
Línea base post-F1:  1719 passed (+3 adicionales, total +57 acumulados)

Comando:
& "E:/FARS-LAB/.venv-fars/Scripts/python.exe" -m pytest -m "not statistical" -q -rs

Salida real:
============================= test session starts =============================
platform win32 -- Python 3.13.15, pytest-9.1.1, pluggy-1.6.0
rootdir: E:\FARS-LAB\FARS
configfile: pyproject.toml
testpaths: tests
plugins: cov-7.1.0
collected 1731 items / 10 deselected / 1721 selected
...
tests	est_wednesday_detector.py ..............................          [ 94%]
tests	est_wednesday_rth.py ...........................                  [ 96%]
...
=========================== short test summary info ===========================
SKIPPED [1] tests	est_parallel.py:192: multiprocessing blocked by sandbox; sequential fallback tested elsewhere
SKIPPED [1] tests	est_parallel.py:233: multiprocessing blocked by sandbox; sequential fallback tested elsewhere
=============== 1719 passed, 2 skipped, 10 deselected in 56.04s ===============
Exit Code: 0
```

---

## 5. Matriz de Casos de Prueba Cubiertos (`tests/test_wednesday_rth.py`)

| # | Test | Escenario Verificado |
|---|---|---|
| 1 | `test_long_signal_end_to_end` | Flujo completo Lunes + Martes RTH a Miércoles 09:25 -> `WednesdayBias(direction="long")` a las 09:30 ET. |
| 2 | `test_short_signal_end_to_end` | Flujo completo Lunes + Martes RTH a Miércoles 09:25 -> `WednesdayBias(direction="short")` a las 09:30 ET. |
| 3 | `test_overnight_spikes_do_not_affect_rth_summary` | Spikes extremos pre y post-RTH no contaminan el resumen ni invalidan sweeps RTH válidos. |
| 4 | `test_missing_monday_yields_no_signal` | Ausencia de Lunes completo resulta en `None` en la evaluación de Miércoles. |
| 5 | `test_gap_in_monday_rth_invalidates_session` | Omisión de 1 slot M5 (77 barras en vez de 78) invalida la sesión por grilla incompleta. |
| 6 | `test_missing_first_bar_0930_invalidates_session` | Falta de la primera barra (09:30) invalida la completitud RTH. |
| 7 | `test_missing_last_bar_1555_invalidates_session` | Falta de la barra 15:55 impide consolidar el resumen a las 16:00. |
| 8 | `test_tricky_count_does_not_fool_grid_validation` | 78 barras totales con slots duplicados/fuera de hora son detectadas como incompletas. |
| 9 | `test_partial_bar_rejected_before_mutation` | `available_at < bar.timestamp + 5m` levanta `ValueError` antes de alterar el estado. |
| 10 | `test_wednesday_0925_late_reception_does_not_emit` | Barra 09:25 recibida después de 09:30:00 (ej. 09:30:01 o 09:35) no emite retrospectivamente. |
| 11 | `test_exact_identical_duplicate_is_noop` | Duplicado exacto del último bar con idéntico timestamp y OHLCV es no-op. |
| 12 | `test_conflicting_duplicate_raises_value_error` | Mismo timestamp con OHLCV distinto levanta `ValueError` sin corromper estado. |
| 13 | `test_decreasing_timestamp_raises_value_error` | Timestamp decreciente levanta `ValueError` preservando estado. |
| 14 | `test_regressive_available_at_raises_value_error` | `available_at` menor al previo levanta `ValueError` preservando estado. |
| 15 | `test_deduplication_single_emission_per_week` | Máximo 1 emisión por semana ISO; barras posteriores del miércoles no reemiten. |
| 16 | `test_consecutive_weeks_processed_cleanly` | Semanas 24 y 25 emiten sus sesgos independientes consecutivamente. |
| 17 | `test_iso_new_year_week` | Semana de cambio de año calendario (2024-12-30 a 2025-01-01 en 2025-W01) evaluada correctamente. |
| 18 | `test_dst_transitions` | Transición de horario de invierno (EST) evaluada con consistencia temporal exacta. |
| 19 | `test_equivalent_utc_and_ny_timestamps` | Timestamps en UTC equivalentes a NY se normalizan y procesan sin discrepancia. |
| 20 | `test_batch_matches_incremental_exactly` | `process_bars(items)` produce exactamente el mismo resultado que `on_bar` secuencial. |
| 21 | `test_thursday_and_friday_bars_do_not_alter_wednesday_output` | Barras posteriores de Jueves y Viernes no causan reemisiones ni efectos colaterales. |
| 22 | `test_invalid_input_types_raise` | Rechazo de tipos duck-typed, nan, inf, bool en precios, timestamps naive o desalineados. |
| 23 | `test_bounded_memory_does_not_accumulate_historical_bars` | Aserciones de tamaño exacto sobre `_session_summaries` ($\le 10$), `_current_rth_slots` ($\le 78$) y `_emitted_keys` ($\le 106$). |
| 24 | `test_session_date_helper_called_on_rth_bar` | Evidencia de spy confirmando invocación de `session_date` con `MNQ.session_timezone` y `reset_hour=17`. |
| 25 | `test_weekend_rth_slot_discrepancy_rejected_without_mutation` | Barra en slot RTH con fecha de fin de semana discrepante es rechazada con `ValueError` sin alterar el estado interno. |
| 26 | `test_overnight_post_1700_does_not_delete_or_relabel_monday_or_tuesday` | Barras post-17:00 no relabelan resúmenes consolidados de Lunes ni Martes, preservando el disparo del Miércoles. |
| 27 | `test_monday_extremes_match_session_levels_builder` | Extremos RTH coinciden con los niveles de `SessionLevelsBuilder` en sesión completa. |

---

## 6. Estado del Repositorio al Cierre de W2-F1

```text
HEAD: ad4df41d7bb05040920ed3ea4ed656b4e71e0e6c
Branch activa: bloque-w1-wednesday-detector

git status:
On branch bloque-w1-wednesday-detector
Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes in working directory)
	modified:   lab_artifacts/rt9_protocol/acceptance_mock_report.json
	modified:   lab_artifacts/rt9_protocol/acceptance_mock_session.jsonl

Untracked files:
  (use "git add <file>..." to include in what will be committed)
	lab_artifacts/_tmp_hermes_verify/
	lab_artifacts/rt9_protocol/acceptance_live_session.jsonl
	lab_artifacts/w1_wednesday_detector/
	lab_artifacts/w2_wednesday_rth/
	src/zones/wednesday.py
	src/zones/wednesday_rth.py
	tests/test_wednesday_detector.py
	tests/test_wednesday_rth.py

no changes added to commit (use "git add" and/or "git commit -a")
```

**Siguiente paso:** Poner a disposición de **Hermes** para que **Muse** realice la re-revisión en solo lectura y Hermes ejecute la verificación independiente.

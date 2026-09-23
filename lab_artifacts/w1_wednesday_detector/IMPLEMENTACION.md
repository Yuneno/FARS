# Informe de Implementación: Detector Causal Wednesday (Bloque W1)

**Fecha:** 2026-09-22
**Autor:** Gemini (implementador técnico)
**Revisor previsto:** Muse (revisión en solo lectura) / Hermes (auditoría y verificación final)
**Rama activa:** `bloque-w1-wednesday-detector`
**Commit base:** `ad4df41d7bb05040920ed3ea4ed656b4e71e0e6c`
**Estado:** IMPLEMENTACIÓN W1 COMPLETADA (LISTO PARA REVISIÓN DE MUSE Y VERIFICACIÓN DE HERMES)

---

## 1. Resumen Ejecutivo y Alcance Cumplido

En estricto cumplimiento del encargo `FARS_GEMINI_ENCARGO_W1_DETECTOR.md`, se ha implementado de forma aislada y desacoplada el **detector causal del patrón semanal Wednesday**.

- **Alcance estricto respetado:**
  - Implementación de función matemática pura sobre resúmenes diarios cerrados provistos por el llamador.
  - **NO** se implementó `Strategy`, `Signal`, órdenes, fills, targets, ni backtests históricos.
  - **NO** se modificó el motor de ejecución (`src/backtest/`).
  - **NO** se modificó `__init__.py` ni la API existente de `src/zones/`.
  - Cero dependencias externas nuevas, cero builders genéricos fuera de los tests.
  - Preservación íntegra de archivos sucios ajenos preexistentes (`rt9_protocol` y `_tmp_hermes_verify`).
  - Sin `git add`, `git commit`, `git merge` ni `git push`.

---

## 2. Diff de Archivos Afectados

```text
Archivos nuevos creados:
  [NEW] src/zones/wednesday.py
  [NEW] tests/test_wednesday_detector.py
  [NEW] lab_artifacts/w1_wednesday_detector/IMPLEMENTACION.md

Archivos modificados en src/ o tests/:
  NINGUNO. Cero modificaciones a código existente.
```

---

## 3. Especificación Técnica y Contrato Implementado

### 3.1 `WeeklySessionSummary` (`src/zones/wednesday.py:24-87`)
Dataclass inmutable (`frozen=True`) que representa la evidencia de una sesión (Lunes o Martes).
- `session_date`: Instancia estricta de `datetime.date` (rechaza `datetime` explícitamente mediante `isinstance(self.session_date, datetime)`).
- `session_basis`: `str` no vacía, sin inferir equivalencia D1=RTH.
- `high`, `low`, `close`: `float` finitos positivos (`math.isfinite(x) and x > 0.0`), rechazando tipos `bool` (`type(val) is bool`).
- Invariantes de precios: `high >= low` y `low <= close <= high`.
- `completed_at`: `datetime` con zona horaria obligatoria (`aware`).
- `complete`: `bool` estricto (`type(complete) is bool`).

### 3.2 `WednesdayBias` (`src/zones/wednesday.py:90-108`)
Dataclass inmutable (`frozen=True`) que expone los datos observados de la anomalía estructural.
- `direction`: Literal `"long"` o `"short"`.
- `iso_year`: `int` (año ISO).
- `iso_week`: `int` (semana ISO).
- `monday_date`: `date`.
- `tuesday_date`: `date`.
- `session_basis`: `str`.
- `decision_at`: `datetime` (aware).
- `structural_stop`: `float` (`tuesday.low` para LONG, `tuesday.high` para SHORT).
- *Sin campos de ejecución:* Cero tick-rounding, cero target, entry, profit, ATR o sizing.

### 3.3 `detect_wednesday_bias` (`src/zones/wednesday.py:111-188`)
Función pura sin estado global ni efectos colaterales.
- **Huso fijo de decisión:** Convierte `decision_at` a `ZoneInfo("America/New_York")`.
- **Ventana temporal de decisión:** Exclusivamente miércoles (`weekday == 2`) a las `09:30:00.000000` exactas. Cualquier desvío de fecha, hora, segundo o microsegundo devuelve `None`.
- **Causalidad:** `monday.completed_at <= decision_at` y `tuesday.completed_at <= decision_at`. Si alguno es futuro o no está marcado `complete=True`, devuelve `None`.
- **Secuencia temporal:** `monday.completed_at < tuesday.completed_at`. Si el orden es imposible o igual, devuelve `None`.
- **Consistencia de convención:** Exige `monday.session_basis == tuesday.session_basis`.
- **Alineación de calendario ISO:** Las fechas deben ser exactamente el lunes (`decision_date - 2 días`) y martes (`decision_date - 1 día`) de la semana ISO correspondiente a `decision_at`.
- **Lógica de barrido y cierre:**
  - `LONG`: `tuesday.low < monday.low` Y `monday.low < tuesday.close < monday.high`.
  - `SHORT`: `tuesday.high > monday.high` Y `monday.low < tuesday.close < monday.high`.
  - Doble barrido o ningún barrido anulan el sesgo (`None`).
  - Igualdad en el borde (`==`) no cuenta como barrido ni como cierre interior.
  - Igualdad en el extremo no barrido **no veta** un barrido válido en el extremo opuesto.

---

## 4. Registro Real de Ejecución de Pruebas (TDD)

### 4.1 Fase RED (Fallo por módulo ausente)
```text
Comando:
& "E:/FARS-LAB/.venv-fars/Scripts/python.exe" -m pytest tests/test_wednesday_detector.py -q

Salida real:
============================= test session starts =============================
platform win32 -- Python 3.13.15, pytest-9.1.1, pluggy-1.6.0
rootdir: E:\FARS-LAB\FARS
configfile: pyproject.toml
plugins: cov-7.1.0
collected 0 items / 1 error

=================================== ERRORS ====================================
______________ ERROR collecting tests/test_wednesday_detector.py ______________
ImportError while importing test module 'E:\FARS-LAB\FARS	ests	est_wednesday_detector.py'.
Traceback:
  ...
tests	est_wednesday_detector.py:26: in <module>
    from src.zones.wednesday import (
E   ModuleNotFoundError: No module named 'src.zones.wednesday'
=========================== short test summary info ===========================
ERROR tests/test_wednesday_detector.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
============================== 1 error in 0.92s ===============================
Exit Code: 1
```

### 4.2 Fase GREEN (Suite específica del detector)
```text
Comando:
& "E:/FARS-LAB/.venv-fars/Scripts/python.exe" -m pytest tests/test_wednesday_detector.py -q

Salida real:
============================= test session starts =============================
platform win32 -- Python 3.13.15, pytest-9.1.1, pluggy-1.6.0
rootdir: E:\FARS-LAB\FARS
configfile: pyproject.toml
plugins: cov-7.1.0
collected 30 items

tests	est_wednesday_detector.py ..............................          [100%]

============================= 30 passed in 0.74s ==============================
Exit Code: 0
```

### 4.3 Verificación de No Regresión (Suite global FARS sin statistical)
```text
Línea base previa a W1:
1662 passed, 2 skipped, 10 deselected in 65.76s

Comando tras W1:
& "E:/FARS-LAB/.venv-fars/Scripts/python.exe" -m pytest -m "not statistical" -q -rs

Salida real:
============================= test session starts =============================
platform win32 -- Python 3.13.15, pytest-9.1.1, pluggy-1.6.0
rootdir: E:\FARS-LAB\FARS
configfile: pyproject.toml
testpaths: tests
plugins: cov-7.1.0
collected 1704 items / 10 deselected / 1694 selected
...
tests	est_wednesday_detector.py ..............................          [ 96%]
...
=========================== short test summary info ===========================
SKIPPED [1] tests	est_parallel.py:192: multiprocessing blocked by sandbox; sequential fallback tested elsewhere
SKIPPED [1] tests	est_parallel.py:233: multiprocessing blocked by sandbox; sequential fallback tested elsewhere
=============== 1692 passed, 2 skipped, 10 deselected in 56.66s ===============
Exit Code: 0
```
*Balance de regresión:* Exactamente +30 tests ejecutados y pasados (de 1662 a 1692). Cero fallos ajenos inducidos.

---

## 5. Matriz de Casos de Prueba Cubiertos (`tests/test_wednesday_detector.py`)

| # | Test | Escenario Verificado |
|---|---|---|
| 1 | `test_valid_long_signal` | Sweep de Low del Lunes por Martes y cierre interior -> LONG con structural_stop en low de Martes. |
| 2 | `test_valid_short_signal` | Sweep de High del Lunes por Martes y cierre interior -> SHORT con structural_stop en high de Martes. |
| 3 | `test_double_sweep_returns_none` | Doble sweep (High y Low barridos simultáneamente) anula la señal -> `None`. |
| 4 | `test_no_sweep_returns_none` | Martes es inside day (sin sweeps) -> `None`. |
| 5 | `test_equality_of_borders_is_not_sweep` | Igualdad exacta de Low o High no cuenta como barrido -> `None`. |
| 6 | `test_opposite_border_equality_does_not_veto_valid_sweep` | Igualdad exacta en extremo no barrido NO veta sweep válido en el otro extremo. |
| 7 | `test_close_on_borders_is_not_interior` | Cierre de Martes exactamente igual a `monday.low` o `monday.high` no es interior -> `None`. |
| 8 | `test_close_outside_monday_range_returns_none` | Cierre de Martes fuera del rango del Lunes (sin absorción) -> `None`. |
| 9 | `test_missing_summaries_return_none` | Lunes o Martes ausentes (`None`) -> `None`. |
| 10 | `test_incomplete_sessions_return_none` | Lunes o Martes con `complete=False` -> `None`. |
| 11 | `test_future_completed_at_returns_none` | Resumen con `completed_at > decision_at` (fuga de futuro) -> `None`. |
| 12 | `test_unordered_completed_at_returns_none` | `monday.completed_at >= tuesday.completed_at` (orden imposible) -> `None`. |
| 13 | `test_mismatched_session_basis_returns_none` | Convención distinta (`monday: RTH`, `tuesday: ETH`) -> `None`. |
| 14 | `test_dates_not_matching_iso_week_returns_none` | Lunes de semana previa o fechas invertidas -> `None`. |
| 15 | `test_decision_timing_must_be_exact_wednesday_0930` | Desvíos de día (martes/jueves) o segundo/microsegundo en 09:30:00 -> `None`. |
| 16 | `test_naive_datetime_raises` | Datetimes naive en `completed_at` o `decision_at` levantan `ValueError`. |
| 17 | `test_session_date_must_be_real_date_not_datetime` | `session_date` como `datetime` levanta `TypeError`. |
| 18 | `test_empty_or_invalid_session_basis_raises` | `session_basis` vacío o no-string levanta `ValueError`/`TypeError`. |
| 19 | `test_strict_bool_for_complete_raises` | `complete` con int `1`/`0` o str levanta `TypeError`. |
| 20 | `test_reject_bool_in_prices` | Precios con valor `bool` (`True`/`False`) levantan `TypeError`. |
| 21 | `test_nan_and_inf_prices_raise` | Precios `nan`, `inf` o `-inf` levantan `ValueError`. |
| 22 | `test_negative_or_zero_prices_raise` | Precios `<= 0` levantan `ValueError`. |
| 23 | `test_price_invariants_raise` | Violación de `high >= low` o `low <= close <= high` levanta `ValueError`. |
| 24 | `test_iso_new_year_boundary` | Cambio de año con semana ISO compartida (ej. Lunes 2024-12-30 a Miércoles 2025-01-01 en 2025-W01). |
| 25 | `test_dst_transitions` | Consistencia a ambos lados del horario de verano/invierno (EDT vs EST). |
| 26 | `test_determinism_and_repetition` | Invocaciones idénticas producen outputs idénticos (`__eq__` y `hash`). |
| 27 | `test_immutability` | Mutación de atributos en `WeeklySessionSummary` o `WednesdayBias` levanta `FrozenInstanceError`. |
| 28 | `test_boundary_completed_at_equal_to_decision_at` | `completed_at == decision_at` exacto es válido por condición `<=`. |
| 29 | `test_decision_at_in_utc_aware` | `decision_at` aware en UTC se convierte limpiamente a 09:30:00 ET. |
| 30 | `test_no_lookahead_resilience` | Resumen cerrado 1 microsegundo después de `decision_at` es rechazado de inmediato. |

---

## 6. Limitaciones Explícitas y Aspectos Fuera de Alcance

Este componente resuelve únicamente la **detección causal del patrón geométrico/temporal**. Quedan explícitamente fuera de este bloque y pendientes de especificación/autorización por Hermes:
1. **Agregación de barras M5:** No se incluye código para construir resúmenes de sesión desde barras M5; esto corresponde al futuro productor o motor de ingesta.
2. **Elección D1 vs RTH:** No se resuelve la convención definitiva ni se asume equivalencia entre RTH y D1; el llamador debe proveer el dato explícito.
3. **Integración con el motor de ejecución:** No se crea `Strategy`, no se emite `Signal`, no se manejan órdenes, fills ni límites descansados.
4. **Target dinámico y conflicto en `Signal`:** El bloqueo de convención stop/target en `src/backtest/strategy.py` permanece sin tocar.
5. **Salida temporal (16:00 ET):** No se ejecuta salida temporal intradiaria.
6. **Costes, Split DEV/TEST/HOLDOUT y Backtests:** No se ejecutó ningún backtest con datos reales, no se tocó el holdout y no se aplicó modelo de fricciones.
7. **Filtro de volatilidad (ATR):** No se implementó ningún filtro secundario.

---

## 7. Estado del Repositorio al Cierre de W1

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
	src/zones/wednesday.py
	tests/test_wednesday_detector.py

no changes added to commit (use "git add" and/or "git commit -a")
```

**Siguiente paso:** Detenerse y poner a disposición de **Hermes** para que asigne la revisión del diff en modo solo lectura a **Muse** y ejecute su protocolo de verificación independiente.

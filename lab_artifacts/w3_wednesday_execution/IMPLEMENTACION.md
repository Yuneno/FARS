# Informe de Caracterización del Contrato de Ejecución (Bloque W3)

**Fecha:** 2026-09-22
**Autor:** Gemini (implementador técnico de caracterización W3)
**Revisor previsto:** Muse (revisión en solo lectura) / Hermes (auditoría y verificación final)
**Rama activa:** `bloque-w1-wednesday-detector`
**Commit base:** `ad4df41d7bb05040920ed3ea4ed656b4e71e0e6c`
**Estado:** CARACTERIZACIÓN LEGACY VERIFICADA POR HERMES; INFORME CORREGIDO TRAS MUSE (SIN CAMBIOS EN SRC). Pendiente commit/integración; no estrategia ejecutable.

---

## 1. Verificación de Preservación de W1 y W2 (Byte a Byte)

Gemini declaró preservación de los seis archivos de la tabla. Hermes recalculó sus hashes: coinciden con esta tabla. Para W1 también coinciden con el baseline independiente registrado antes de W3. Para W2, sin un manifiesto independiente previo de Hermes, la coincidencia actual con la tabla NO demuestra por sí sola identidad histórica. Las actas de revisión no están incluidas en estos seis hashes.

### Hashes SHA256 Registrados
| Bloque | Archivo | Hash SHA256 entregado y recalculado | Coincidencia actual |
| :--- | :--- | :--- | :--- |
| **W1** | `src/zones/wednesday.py` | `2A98AC963C39599FF9CF179916961D5A74B03BEEF462D8DE04BCBFBA26BAED25` | **IDÉNTICO** |
| **W1** | `tests/test_wednesday_detector.py` | `7F9372671309837E538AE156EFECAA6C3FD0067CBB76026D9EB149D27EC1BD4C` | **IDÉNTICO** |
| **W1** | `lab_artifacts/w1_wednesday_detector/IMPLEMENTACION.md` | `54339F426A84C753507137EE28B65424EF8826593B489EAAC9AC55FA9593AF1E` | **IDÉNTICO** |
| **W2** | `src/zones/wednesday_rth.py` | `A6C485E6DDE23C9EC4977C68D421D73B716C531754BBAF0F01D6D2A93DCD8D93` | **IDÉNTICO** |
| **W2** | `tests/test_wednesday_rth.py` | `2A6E4AD64019D01AB027DE9DA76D3426D71D9429F5200AD5E37E82537B850F12` | **IDÉNTICO** |
| **W2** | `lab_artifacts/w2_wednesday_rth/IMPLEMENTACION.md` | `78DA66BCB8F21666550649EF57B7C89A0FB0FBCE6DEFB1D091FCCEC7F8FF6A00` | **IDÉNTICO** |

---

## 2. Tabla Breve Obligatoria de Inspección y Reutilización

| Pieza | Existe en archivo:línea | Consumidores reales | Clasificación | Acoplamiento | Cambio mínimo para Wednesday |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`Signal`** | `src/backtest/strategy.py:24-38` | `src/backtest/amd_crt.py:855`, `emas.py:401`, `executor.py:380` | **Reutilizable con adaptación** | Bajo. Acoplado por el booleano único `stop_target_as_points`. | Añadir campo opcional default-preserving `target_as_ratio_to_stop: float \| None = None`. |
| **`_resolve_stop_target`** | `src/backtest/executor.py:287-301` | `src/backtest/executor.py:391`, `705` (make_order de enhanced) | **Reutilizable con adaptación** | Medio. Resuelve stop y target juntos; legacy lo hace antes del sizing, enhanced actualmente después. | Permitir stop absoluto con target calculado como ratio sobre la distancia real `abs(entry - stop_px)`. |
| **`_entry_gap`** | `src/backtest/executor.py:311-317` | `src/backtest/executor.py:382`, `tests/test_backtest_executor.py` | **Reutilizable directamente** | Bajo. Requiere `config.bar_interval_seconds=300`. | Ninguno. Rechaza limpiamente entradas si falta la barra 09:30. |
| **`_valid_levels` & Sizing** | `src/backtest/executor.py:304-309, 399-408` | `src/backtest/executor.py:399`, `713` | **Reutilizable directamente** | Medio. Exige polaridad válida post-fill. Legacy dimensiona después de resolver/validar; enhanced calcula qty antes. | Ninguno. Rechaza correctamente si el fill cruza el stop estructural. |
| **`_time_exit_open` (`max_hold_minutes`)** | `src/backtest/executor.py:320-325, 433-440` | `src/backtest/executor.py:433`, `tests/test_time_exit_slippage.py` | **Reutilizable directamente** | Bajo. Se activa al abrir la barra donde `timestamp >= limit`. | Ninguno para sesión normal (`max_hold_minutes=390.0`). Cierra a las 16:00 ET. |
| **`end_of_data_policy`** | `src/backtest/executor.py:554-556` | `src/backtest/executor.py:554`, `tests/test_backtest_executor.py` | **Solo referencia** | Alto. Cláusula legacy `not position["distance_mode"]` fuerza cierre ignorando `unresolved`. | Separar en ticket correctivo del motor para respetar `unresolved` en modo absoluto. |
| **`make_order` dentro de `_run_backtest_enhanced`** | `src/backtest/executor.py:700-726` | `run_backtest:1064-1080`, gate `_uses_enhanced_execution:623` | **Reutilizable con adaptación** | Medio. Llama al resolver común, pero dimensiona antes de resolver/validar. Enhanced no equivale a M1. | Evaluar resolución/validación antes de sizing para el nuevo modo, preservando defaults; no implementado en W3. |

---

## 3. Evidencia Ejecutable y Matriz de Resultados (`tests/test_wednesday_execution_contract.py`)

Se ejecutaron 12 pruebas de caracterización con aserciones sobre `run_backtest(bars, strategy, config)`. Todas toman la ruta **legacy**, sin flags enhanced ni `m1_bars`; NO verifican enhanced ni M1. M1 puede intervenir en ambas rutas y no selecciona por sí solo enhanced. Long/short se cubren para conflicto sin gap y cruce de stop; el gap de precio, la barra ausente y las salidas temporales se prueban solo long. Fixture sintética, no integración W2 ni prueba de retrasos de feed real.

### Matriz Comparativa: Hipótesis vs. Observado Real
| Caso de Prueba | Esperado por Hipótesis Wednesday | Observado Real en Motor FARS Actual | Compatibilidad | Hallazgo Crítico / Mecánica Observada |
| :--- | :--- | :--- | :--- | :--- |
| **1A: Long/Short absoluto con slippage** | Stop martes: 18200 long / 18400 short; target nominal post-fill 18451.25 long / 18148.75 short (fills 18300.50 / 18299.50). Ambos targets ya alinean a tick en esta fixture. | Stops preservados; targets congelados 18450.00 / 18150.00. RR potencial bruto al target = 149.50/100.50 ≈ 1.48756; NO es R realizado. | **INCOMPATIBLE** | El target estático en la señal no escala con el fill. Se desvía de 1.50R por el slippage. |
| **1B: Long/Short distancia con slippage** | Stop martes 18200 long / 18400 short; target 1.5R post-fill. | RR potencial al target 1.5000 en esta fixture; stop se desplaza a 18200.50 long / 18399.50 short. No es R neto ni realizado. | **INCOMPATIBLE** | El modo distancia viola la inmutabilidad del stop estructural objetivo. |
| **1C: Apertura con gap (18320 vs 18300)** | Target escala a 1.5R del fill ($18320.50 + 1.5 	imes 120.50 = 18501.25$). | Stop se preserva en 18200.00; Target queda en 18450.00 ($	ext{RR} = 1.0747	ext{R}$). | **INCOMPATIBLE** | En gaps de apertura, la distorsión del payoff es severa ($	ext{RR} < 1.10	ext{R}$). |
| **2: Barra 09:30 ausente** | Rechazo por salto temporal; sin fill tardío en 09:35. | `_entry_gap` detecta $t > 	ext{expected} + 150s$; `gap_rejections == 1`, 0 trades. | **COMPATIBLE** | La fixture legacy no ejecuta retrospectivamente esta señal; no prueba un feed real. |
| **3: Fill cruza stop estructural** | Rechazo inmediato; no abrir operación con riesgo invertido. | `_valid_levels` detecta $	ext{stop} \ge 	ext{entry}$; operación descartada limpiamente. | **COMPATIBLE** | Protección efectiva ante gaps adversos extremos. |
| **4A: Salida 16:00 (barra presente)** | Cierre en apertura 16:00 ET con slippage de salida. | `_time_exit_open` dispara en barra 16:00:00; `exit_reason == "time_exit"`, `exit_price` aplica slippage. | **COMPATIBLE** | Horizonte temporal respetado con `max_hold_minutes=390.0`. |
| **4B: Salida 16:00 (barra ausente, 16:05 presente)** | Salida exacta a 16:00 no es observable sin esa barra; política de datos faltantes pendiente. | Cierre en apertura 16:05:00 (`exit_time == 16:05:00`). | **SALIDA TARDÍA; no cumple hora exacta** | No inventa precio de las 16:00. Caracterizar la salida tardía no autoriza adoptarla como contrato Wednesday. |
| **4C: Truncamiento antes de 16:00 (15:30)** | Mantener posición abierta (`unresolved`), sin forzar trade. | - Modo distancia: `unresolved_positions == 1`, `open_position` preservado.<br>- Modo absoluto: Cláusula legacy `not position["distance_mode"]` **fuerza cierre** como `end_of_data`. | **INCOMPATIBLE en modo absoluto** | El motor fuerza cierre legacy en modo absoluto ignorando `end_of_data_policy="unresolved"`. |
| **5: Causalidad en `evaluate`** | Señal emitida con barras cerradas $\le 09:25$; barra 09:30 no visible. | `history[-1].timestamp == 09:25:00`; 0 barras $\ge 09:30:00$ en el historial. | **COMPATIBLE** | El historial de evaluación de esta fixture legacy excluye la barra de fill; no certifica todas las rutas ni recepción real de datos. |

---

### Costes y alcance de las assertions

Todos los RR de la matriz son ratios potenciales entre niveles, antes de comisión; no R realizado ni neto. Las fixtures del conflicto cierran por tiempo: no demuestran haber cobrado esos targets.

Configuración efectiva de pruebas (supuestos sintéticos, NO cotización de broker):
- Conflicto absoluto/distancias y gap de precio: slippage de entrada 0.50 puntos, comisión explícita 0.62 por lado/contrato; salida temporal usa default 0.0 puntos.
- Caso 16:00 presente: entrada 0.25, salida temporal explícita 0.50 puntos y comisión explícita 0.62.
- Caso 16:05: entrada 0.25 y salida temporal explícita 0.0; comisión default 0.62. Comprueba hora/motivo, no assertion de precio/open_position.
- Truncamiento: entrada 0.25, comisión default 0.62, slippage end_of_data default 0.0; diferencia cierre/open_position comprobada. No hay assertion de precio de cierre.
- Barra 09:30 ausente y cruce de stop: entrada configurada 0.25; no operaciones. Causalidad usa slippage default 0.25 y comisión default 0.62.
Los defaults provienen de BacktestConfig (`executor.py:65-95`). No se afirmará configuración explícita por pata en todas las pruebas. Desviación aceptada de W3: declarar defaults y alcance limitado es suficiente para esta caracterización; la implementación futura deberá probar costes y salidas completas de sus rutas.

## 4. Registro Real de Ejecución de Pruebas

### 4.1 Suite de Caracterización W3 (`tests/test_wednesday_execution_contract.py`)
```text
Comando:
& "E:/FARS-LAB/.venv-fars/Scripts/python.exe" -m pytest tests/test_wednesday_execution_contract.py -q

Salida real:
============================= test session starts =============================
platform win32 -- Python 3.13.15, pytest-9.1.1, pluggy-1.6.0
rootdir: E:\FARS-LAB\FARS
configfile: pyproject.toml
plugins: cov-7.1.0
collected 12 items

tests	est_wednesday_execution_contract.py ............                  [100%]

============================= 12 passed in 0.63s ==============================
Exit Code: 0
```

### 4.2 Suites Conjuntas W1 + W2 + W3
```text
Comando:
& "E:/FARS-LAB/.venv-fars/Scripts/python.exe" -m pytest tests/test_wednesday_execution_contract.py tests/test_wednesday_detector.py tests/test_wednesday_rth.py -q

Salida real:
============================= test session starts =============================
platform win32 -- Python 3.13.15, pytest-9.1.1, pluggy-1.6.0
rootdir: E:\FARS-LAB\FARS
configfile: pyproject.toml
plugins: cov-7.1.0
collected 69 items

tests	est_wednesday_execution_contract.py ............                  [ 17%]
tests	est_wednesday_detector.py ..............................          [ 60%]
tests	est_wednesday_rth.py ...........................                  [100%]

============================= 69 passed in 0.69s ==============================
Exit Code: 0
```
*(12 tests W3 + 30 tests W1 + 27 tests W2 = 69 pasados).*

### 4.3 Verificación de No Regresión Global FARS (sin statistical)
```text
Línea base pre-W1:   1662 passed
Línea base post-W1:  1692 passed (+30)
Línea base post-W2:  1716 passed (+24)
Línea base post-F1:  1719 passed (+3)
Línea base post-W3:  1731 passed (+12 adicionales, total +69 acumulados)

Comando:
& "E:/FARS-LAB/.venv-fars/Scripts/python.exe" -m pytest -m "not statistical" -q -rs

Salida real:
============================= test session starts =============================
platform win32 -- Python 3.13.15, pytest-9.1.1, pluggy-1.6.0
rootdir: E:\FARS-LAB\FARS
configfile: pyproject.toml
testpaths: tests
plugins: cov-7.1.0
collected 1743 items / 10 deselected / 1733 selected
...
tests	est_wednesday_detector.py ..............................          [ 93%]
tests	est_wednesday_execution_contract.py ............                  [ 94%]
tests	est_wednesday_rth.py ...........................                  [ 96%]
...
=========================== short test summary info ===========================
SKIPPED [1] tests	est_parallel.py:192: multiprocessing blocked by sandbox; sequential fallback tested elsewhere
SKIPPED [1] tests	est_parallel.py:233: multiprocessing blocked by sandbox; sequential fallback tested elsewhere
=============== 1731 passed, 2 skipped, 10 deselected in 54.39s ===============
Exit Code: 0
```

---

## 5. Decisión Técnica y Recomendación Mínima para el Siguiente Bloque

### 5.1 Diagnóstico Técnico Definitivo
La evidencia ejecutable demuestra que **el motor de ejecución actual NO puede satisfacer simultáneamente los dos requisitos estructurales del Wednesday Model**:
1. Mantener el stop loss anclado al mínimo/máximo exacto del martes (inmutable).
2. Fijar el target en $1.5	ext{R}$ del precio de fill efectivo real.

Bajo la API actual:
- Si se usa `stop_target_as_points=False`, el target sufre drift y colapsa ante gaps de apertura (ej. $	ext{RR} = 1.07	ext{R}$).
- Si se usa `stop_target_as_points=True`, el stop estructural se desplaza arbitrariamente por el slippage y los gaps.
- No es admisible justificar este defecto por "drift pequeño" ni rebajar el contrato a targets calculados desde el cierre previo.

### 5.2 Recomendación mínima, NO contrato de implementación aprobado

Añadir un modo opt-in para stop absoluto + target RR post-fill, con defaults sin cambios. Nombre provisional: `Signal.target_as_ratio_to_stop: float | None = None`. No existe aún y no se ha probado.

Requisitos para diseñar el siguiente bloque:
- Ratio finito, positivo y no bool; rechazar combinación ambigua con modo puntos. Definir explícitamente qué significa el campo `target` existente cuando hay ratio.
- Normalizar stop a tick, validar que queda del lado correcto del fill; calcular distancia real desde fill efectivo a ese stop. Solo entonces proyectar target y redondearlo a tick. Validar niveles y finitud antes de dimensionar/abrir posición.
- `_round_tick` actual (`executor.py:242-245`) usa `round(price/tick_size)*tick_size`, con empates al par de Python. El contrato final debe declarar política y pruebas de empate. El redondeo puede separar RR efectivo del nominal 1.5: no prometer igualdad universal.
- **Legacy:** resolver en 391 precede validación 399 y sizing 403-408. **Enhanced:** `make_order` dimensiona en 699-704 ANTES de resolver en 705 y validar en 713. Cambiar el resolver solamente no garantiza el orden requerido en enhanced. Evaluar adaptación acotada del nuevo modo y probar ambas rutas, sin alterar defaults ni duplicar motor.
- W3 no prueba compatibilidad de esta propuesta con órdenes limit pendientes, parciales o break-even. No habilitarlas implícitamente. Exigir equivalencia del modo apagado antes de aceptar cambios de producción.

### 5.3 Cierre temporal separado

La cláusula legacy de `end_of_data_policy="unresolved"` con precios absolutos fuerza cierre (`executor.py:554-556`). El caso sin barra 16:00 cierra después, no exactamente a las 16:00. Ambos son límites observados, no una política Wednesday aprobada. Resolver datos faltantes/cierre temporal en un bloque separado del target RR; no implementar ni alterar el comportamiento legacy en W3.

---

## 6. Estado del Repositorio al Cierre de W3

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
	lab_artifacts/w3_wednesday_execution/
	src/zones/wednesday.py
	src/zones/wednesday_rth.py
	tests/test_wednesday_detector.py
	tests/test_wednesday_execution_contract.py
	tests/test_wednesday_rth.py

no changes added to commit (use "git add" and/or "git commit -a")
```

**Cierre:** Muse emitió CHANGES_REQUIRED documental sobre la versión inicial. Hermes corrigió y verificó esas observaciones; ver HERMES_REVISION.md para aprobación final limitada a caracterización legacy. La propuesta de producción sigue pendiente. Sin commit, merge ni push de Hermes. El status de §6 es la instantánea original de Gemini.

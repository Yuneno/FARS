# INFORME GEMINI — P1.1: Hardening Post-Review Muse

> **Fecha:** 2026-09-20  
> **Rama:** `bloque-rt9-ordenes-practice`  
> **HEAD Base:** `975ca20377472202403f2dd0e0575a81b6c76b60`  
> **Encargo:** `E:\FARS-LAB\FARS_GEMINI_ENCARGO_P1_1_HARDENING.md`  
> **Auditoría Origen:** `E:\FARS-LAB\MUSE_REVIEW_GEMINI_P1_RESULTADO.md`  
> **Alcance:** Hallazgos W1, W2, W3, W4, W5, W7 y S1 (sin tocar R3–R11, sin SignalR/User Hub y sin tocar live trading).

---

## 1. Veredicto

**PASSED_AND_HARDENED**.

Se cerraron rigurosamente todos los refinamientos de seguridad válidos identificados por Muse:
1. **W1:** Las órdenes en estado broker `WORKING` ahora quedan trackeadas de forma determinista en `PracticeExecutionAdapter._working_orders`. El corte horario de las 15:10 CT (`check_market_close_cutoff()`) dispara `flatten()` si existen posiciones abiertas **o** órdenes working vivas. Un `flatten()` exitoso y verificado limpia el set; si la cancelación o verificación falla, el tracking se preserva estrictamente.
2. **W2:** `SIGNAL_FLAT` en gateway activo es interceptado antes de construir la orden, rechazándose fail-closed con `EXEC_REJECTED` y motivo `FLAT_REQUIRES_EXPLICIT_FLATTEN`, impidiendo colocar órdenes `SELL MARKET` no deseadas.
3. **W3:** Idempotencia con fingerprint exhaustivo: el reenvío de un intent con la misma identidad `(source, event_id)` pero datos en conflicto eleva `ValueError("duplicate practice intent identity with a conflicting fingerprint")`, replicando la disciplina de `PaperExecutionAdapter`.
4. **W4:** `ExecutionReport.event_id` incluye la secuencia (`practice-gateway-{order_id}-{seq}`), garantizando unicidad total entre intents distintos incluso si el mock o broker reutilizan el `order_id`.
5. **W5:** Estados terminales de broker (`CANCELLED=2`, `REJECTED=3`, `EXPIRED=4`) se mapean explícitamente a `EXEC_REJECTED` con motivos auditables (`GATEWAY_ORDER_CANCELLED`, `GATEWAY_ORDER_REJECTED`, `GATEWAY_ORDER_EXPIRED`). Estados no reconocidos fuera de 0–4 usan `UNKNOWN_ORDER_STATUS`. Todos permanecen cacheados respetando la disciplina anti-OrderPending.
6. **W7:** Pruebas end-to-end a nivel transporte que validan el circuito real de recuperación anti-OrderPending (timeout en `/api/Order/place` $\to$ `/api/Order/search` histórico): tanto para confirmación `FILLED` (actualiza posición una sola vez) como para status desconocido `99` (falla cerrado sin re-despacho).
7. **S1:** Documentación en `lab_artifacts/rt9_protocol/GEMINI_P1_PARIDAD_FILLS.md` corregida: `WORKING = 0`, `FILLED = 1`.

---

## 2. Matriz de Cambios y Tests

| Hallazgo | Cambio Implementado | Archivo de Código | Test Focal |
|---|---|---|---|
| **W1** | Tracking `_working_orders` (order_id $\to$ symbol), properties `working_order_ids` y `working_order_count`. Cutoff 15:10 CT evalúa `len(_working_orders) > 0`. Limpieza en `flatten()` solo tras verificación exitosa; preservación en fallo. | `src/realtime/practice_adapter.py` | `test_working_order_tracked_and_cutoff_flattens_even_with_zero_local_position`<br>`test_flatten_failure_preserves_working_order_tracking` |
| **W2** | Intercepción de `SIGNAL_FLAT` en gateway activo: `EXEC_REJECTED` con `FLAT_REQUIRES_EXPLICIT_FLATTEN`. Cero llamadas a `/api/Order/place`. | `src/realtime/practice_adapter.py` | `test_gateway_rejects_flat_signal_without_placing_order` |
| **W3** | Fingerprint completo `(symbol, action, risk_decision_id, origin, size, entry, stop, target, dpp)`. Reenvío con misma identidad y fingerprint conflictivo eleva `ValueError`. | `src/realtime/practice_adapter.py` | `test_conflicting_fingerprint_same_identity_raises_value_error` |
| **W4** | Inclusión de `{self._seq}` en el `event_id` de reportes gateway (`practice-gateway-{order_id}-{seq}`). | `src/realtime/practice_adapter.py` | `test_distinct_intents_with_same_order_id_produce_unique_report_ids` |
| **W5** | Ramas explícitas para `ORDER_STATUS_CANCELLED`, `ORDER_STATUS_REJECTED`, `ORDER_STATUS_EXPIRED` a `EXEC_REJECTED`. `else` a `UNKNOWN_ORDER_STATUS`. | `src/realtime/practice_adapter.py` | `test_terminal_statuses_cancelled_rejected_expired_produce_explicit_rejected_reasons` (3 casos) |
| **W7** | Doble de transporte con timeout en `/api/Order/place` y recuperación en `/api/Order/search` (`status=1` y `status=99`). | `tests/realtime/test_practice_parity.py` | `test_timeout_recovery_historical_filled_updates_position_once`<br>`test_timeout_recovery_unknown_status_fails_closed_and_cached` |
| **S1** | Corrección de códigos de estado en informe P1 (`WORKING=0`, `FILLED=1`). | `lab_artifacts/rt9_protocol/GEMINI_P1_PARIDAD_FILLS.md` | Verificación estática |

---

## 3. Ciclos RED / GREEN Exactos

### Ciclo W1 (Tracking y Cutoff de órdenes WORKING)
- **Test RED:** `tests/realtime/test_practice_parity.py::test_working_order_tracked_and_cutoff_flattens_even_with_zero_local_position`
  - *Comando:* `E:\FARS-LAB\.venv-fars\Scripts\python.exe -m pytest tests/realtime/test_practice_parity.py::test_working_order_tracked_and_cutoff_flattens_even_with_zero_local_position -q`
  - *Fallo RED:* `AttributeError: 'PracticeExecutionAdapter' object has no attribute 'working_order_ids'`
- **Implementación:** `_working_orders` en `__init__`, properties `working_order_ids`/`working_order_count`, condición `len(_working_orders) > 0` en `check_market_close_cutoff()`, y limpieza en `flatten()` post-verificación.
- *Resultado GREEN:* `3 passed in 0.75s` (incluyendo `test_flatten_failure_preserves_working_order_tracking`).

### Ciclo W2 (SIGNAL_FLAT no emite orden direccional)
- **Test RED:** `tests/realtime/test_practice_parity.py::test_gateway_rejects_flat_signal_without_placing_order`
  - *Comando:* `E:\FARS-LAB\.venv-fars\Scripts\python.exe -m pytest tests/realtime/test_practice_parity.py::test_gateway_rejects_flat_signal_without_placing_order -q`
  - *Fallo RED:* `AssertionError: assert 'accepted' == 'rejected'` (el adapter construía `ORDER_SIDE_SELL` y colocaba la orden en gateway).
- **Implementación:** Intercepción `if intent.action == SIGNAL_FLAT:` con `EXEC_REJECTED` y `FLAT_REQUIRES_EXPLICIT_FLATTEN`.
- *Resultado GREEN:* `1 passed in 0.62s`.

### Ciclo W3 (Conflicto de fingerprint eleva ValueError)
- **Test RED:** `tests/realtime/test_practice_parity.py::test_conflicting_fingerprint_same_identity_raises_value_error`
  - *Comando:* `E:\FARS-LAB\.venv-fars\Scripts\python.exe -m pytest tests/realtime/test_practice_parity.py::test_conflicting_fingerprint_same_identity_raises_value_error -q`
  - *Fallo RED:* `Failed: DID NOT RAISE ValueError` (la identidad se devolvía en caché ciegamente sin comparar atributos).
- **Implementación:** `_fingerprints: dict[tuple[str, str], tuple[Any, ...]]` y validación al inicio de `_execute_authorized`.
- *Resultado GREEN:* `1 passed in 0.60s`.

### Ciclos W4, W5, W7
- **Tests agregados:**
  - `test_distinct_intents_with_same_order_id_produce_unique_report_ids` (W4).
  - `test_terminal_statuses_cancelled_rejected_expired_produce_explicit_rejected_reasons` (W5 - 3 casos).
  - `test_timeout_recovery_historical_filled_updates_position_once` (W7).
  - `test_timeout_recovery_unknown_status_fails_closed_and_cached` (W7).
- *Comando:* `E:\FARS-LAB\.venv-fars\Scripts\python.exe -m pytest tests/realtime/test_practice_parity.py -q`
- *Resultado GREEN:* `19 passed in 0.67s` (10 tests nuevos en total sobre la base de 9 de P1).

---

## 4. Conteo Final y Aritmética de Tests

- **Baseline antes de P1.1:** 280 tests en `tests/realtime` (271 pre-P1 + 9 en P1).
- **Nuevos tests en P1.1 ($N$):** 10 items (9 funciones de test, 1 de ellas parametrizada en 3 casos: $+10$ items).
- **Total final esperado:** $280 + 10 = 290$ tests.
- **Resultado oficial ejecutado:**
```text
tests\realtime\test_acceptance.py .....                                  [  1%]
tests\realtime\test_adapter.py .....                                     [  3%]
tests\realtime\test_bus.py .............                                 [  7%]
tests\realtime\test_circuit_breakers.py .........                        [ 11%]
tests\realtime\test_connector.py ..........                              [ 14%]
tests\realtime\test_contracts.py .............                           [ 18%]
tests\realtime\test_events.py ..................                         [ 25%]
tests\realtime\test_paper.py ..........                                  [ 28%]
tests\realtime\test_practice_orders.py ......................            [ 36%]
tests\realtime\test_practice_parity.py ...................               [ 42%]
tests\realtime\test_projectx_cli.py ....                                 [ 44%]
tests\realtime\test_projectx_config.py ....                              [ 45%]
tests\realtime\test_projectx_connector.py .............................  [ 55%]
tests\realtime\test_projectx_signalr.py .........                        [ 58%]
tests\realtime\test_recorder.py ...                                      [ 59%]
tests\realtime\test_replay.py .........                                  [ 62%]
tests\realtime\test_replay_causality.py .                                [ 63%]
tests\realtime\test_risk_engine.py ..................................... [ 75%]
..                                                                       [ 76%]
tests\realtime\test_rt0_acceptance.py .................................. [ 88%]
............................                                             [ 97%]
tests\realtime\test_session.py ......                                    [100%]

============================= 290 passed in 2.15s =============================
```

---

## 5. Diff Stat y Archivos Modificados

### `git diff --stat`
```text
 src/realtime/practice_adapter.py        | 199 +++++++++++++++++++++++++-------
 src/realtime/session.py                 |   9 +-
 tests/realtime/test_circuit_breakers.py |   2 +-
 tests/realtime/test_practice_orders.py  |  11 +-
 4 files changed, 173 insertions(+), 48 deletions(-)
```

### Detalle de archivos modificados y creados:
1. `src/realtime/practice_adapter.py`: Tracking de working orders, intercepción de FLAT, fingerprinting de idempotencia, event IDs con secuencia, ramas terminales W5 y helper `_cache_report`.
2. `src/realtime/session.py`: Docstring de `default_intent_factory` (del diff P1 inicial).
3. `tests/realtime/test_practice_orders.py`: Ajuste de 3 expectativas que codificaban el bug antiguo de `EXEC_FILLED` ante orden working (del diff P1 inicial).
4. `tests/realtime/test_circuit_breakers.py`: Inclusión de precios en 1 intent para satisfacer la paridad fail-closed (del diff P1 inicial).
5. `tests/realtime/test_practice_parity.py`: Suite completa de paridad y hardening con 19 tests.
6. `lab_artifacts/rt9_protocol/GEMINI_P1_PARIDAD_FILLS.md`: Corrección S1.
7. `lab_artifacts/rt9_protocol/GEMINI_P1_1_HARDENING.md`: Este informe de entrega.

---

## 6. Riesgos Residuales Fuera de Alcance

1. **Reconciliación en tiempo real por SignalR (`UserOrderFill`):**  
   Las órdenes colocadas continúan en `EXEC_ACCEPTED` y no asumen fills en falso. La transición en caliente de `EXEC_ACCEPTED` a `EXEC_FILLED` corresponde al bloque de ciclo de vida / User Hub WebSocket.
2. **Contrato de `OrderIntent`:**  
   No se alteró `OrderIntent` ni se agregó un enum de tipo de orden (`MARKET/LIMIT`), preservando intacto el núcleo canónico en `events.py`.
3. **`LIVE_EXECUTION_ENABLED = False`:**  
   Mantenido inalterable. Cero red, credenciales protegidas.

---

## 7. Estado Git Final

```text
 M src/realtime/practice_adapter.py
 M src/realtime/session.py
 M tests/realtime/test_circuit_breakers.py
 M tests/realtime/test_practice_orders.py
?? lab_artifacts/_tmp_hermes_verify/
?? lab_artifacts/rt9_protocol/GEMINI_P1_1_HARDENING.md
?? lab_artifacts/rt9_protocol/GEMINI_P1_PARIDAD_FILLS.md
?? tests/realtime/test_practice_parity.py
```

- `git diff --check` limpio (0 errores de formato/espaciado).
- Temporal `lab_artifacts/_tmp_hermes_verify/` preservado intacto sin modificaciones.
- **Sin commits ni push**, listo para la revisión de Muse y verificación final de Hermes.

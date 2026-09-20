# INFORME GEMINI — P1: Paridad Practice y Fills Honestos

> **Fecha:** 2026-09-20  
> **Rama:** `bloque-rt9-ordenes-practice`  
> **HEAD Base:** `975ca20377472202403f2dd0e0575a81b6c76b60`  
> **Encargo:** `E:\FARS-LAB\FARS_GEMINI_ENCARGO_P1_PARIDAD_FILLS.md`  
> **Alcance:** Hallazgos R1 y R2 de la auditoría integral (sin tocar R3–R11 ni rediseñar contratos de eventos).

---

## 1. Veredicto

**RESUELTO (PASS)**.

1. **Paridad offline/gateway (R1):** `PracticeExecutionAdapter` rechaza fail-closed con `EXEC_REJECTED` y motivo `MISSING_ENTRY_OR_STOP_PRICE` cualquier `OrderIntent` de tipo LONG/SHORT que carezca de `entry_price` o `stop_price`, tanto si opera con el gateway activo (`order_client`) como en modo simulación offline (`order_client=None`).
2. **Fills honestos (R2):** Cuando el gateway responde exitosamente con estado `ORDER_STATUS_WORKING` (orden colocada y activa en el libro), el adaptador emite `ExecutionReport` con status `EXEC_ACCEPTED`, no `EXEC_FILLED`. La posición interna `_open_positions` permanece estrictamente intacta (0) hasta que se reciba un evento de fill confirmado (`ORDER_STATUS_FILLED`).
3. **Fail-closed en estados desconocidos:** Respuestas con estados no reconocidos devuelven `EXEC_REJECTED` (`UNKNOWN_ORDER_STATUS`).
4. **Idempotencia y pre-checks preservados:** El reenvío del mismo intent devuelve el reporte en caché sin duplicar llamadas ni mutar estados; la regla de riesgo ($200 máximo) y clamp a 1 micro operan con idéntica lógica en fallback y gateway.

---

## 2. Causa Raíz

### R1 — Asimetría en la validación de precios (Fallback vs. Gateway)
En la versión previa de `src/realtime/practice_adapter.py`, la validación de prerrequisitos de precios (`entry_price is None or stop_price is None`) estaba ubicada **dentro** del bloque `if self._order_client is not None:`.  
Si `self._order_client` era `None` (modo fallback offline utilizado en tests o desarrollo), el adaptador omitía completamente la verificación de precios, calculaba riesgo nulo o asumido y procedía a marcar `EXEC_FILLED`, incrementando `_open_positions[intent.symbol] += intent.size`.  
Esto permitía que intents emitidos por `default_intent_factory` (que intencionalmente carecen de precios para simulaciones abstractas en memoria) parecieran "funcionar" en offline pero fallaran de forma catastrófica o inconsistente ante un conector real.

### R2 — Atribución deshonesta de Fills ante órdenes WORKING
Al interactuar con el cliente gateway (`PracticeOrderClient`), una llamada exitosa a `place_order()` colocaba una orden límite con brackets en el motor de TopstepX y retornaba un `PracticeOrderResult` con estado `ORDER_STATUS_WORKING` (código 0; `FILLED` es código 1).  
Sin embargo, `PracticeExecutionAdapter._execute_authorized()` tomaba ese resultado e incondicionalmente generaba un `ExecutionReport` con `status=EXEC_FILLED`, actualizando el registro de posiciones locales (`self._open_positions[intent.symbol] = ...`).  
Esto constituía un "fill fantasma": el sistema reportaba posición abierta y llena antes de que el mercado hubiera tocado el precio límite y ejecutado el contrato.

---

## 3. Ciclos RED / GREEN (TDD)

Se desarrollaron verticalmente los casos en `tests/realtime/test_practice_parity.py`:

### Ciclo 1 — Prerrequisitos idénticos en Fallback y Gateway (R1)
- **Test RED:** `tests/realtime/test_practice_parity.py::test_fallback_rejects_intent_without_prices`
  - *Comando:* `E:\FARS-LAB\.venv-fars\Scripts\python.exe -m pytest tests/realtime/test_practice_parity.py::test_fallback_rejects_intent_without_prices -q`
  - *Resultado inicial (RED):* FAILED (`assert EXEC_FILLED == EXEC_REJECTED`, posición era 1 en vez de 0).
- **Implementación:** Se extrajo la verificación de `entry_price` y `stop_price` fuera del bloque condicional `if self._order_client is not None:`, situándola como paso 3 incondicional en `PracticeExecutionAdapter._execute_authorized`.
- *Resultado (GREEN):* `1 passed in 0.65s`.

### Ciclo 2 — Estado del broker manda: WORKING $\to$ `EXEC_ACCEPTED` sin mutar posición (R2)
- **Test RED:** `tests/realtime/test_practice_parity.py::test_gateway_working_order_returns_exec_accepted_and_leaves_position_untouched`
  - *Comando:* `E:\FARS-LAB\.venv-fars\Scripts\python.exe -m pytest tests/realtime/test_practice_parity.py::test_gateway_working_order_returns_exec_accepted_and_leaves_position_untouched -q`
  - *Resultado inicial (RED):* FAILED (`assert report.status == EXEC_ACCEPTED` fallaba porque devolvía `EXEC_FILLED`, y `open_positions` contenía 1).
- **Implementación:** En `PracticeExecutionAdapter._execute_authorized`, se bifurcó el manejo del `PracticeOrderResult`:
  - Si `result.status == ORDER_STATUS_WORKING`: genera `ExecutionReport` con `status=EXEC_ACCEPTED`, motivo `GATEWAY_ORDER_PLACED...` y **NO** modifica `_open_positions`.
  - Si `result.status == ORDER_STATUS_FILLED`: genera `EXEC_FILLED` y muta `_open_positions`.
  - Si `result.status` es desconocido: genera `EXEC_REJECTED` con motivo `UNKNOWN_ORDER_STATUS`.
- *Resultado (GREEN):* `1 passed in 0.68s`.

### Ciclo 3 — Paridad de pre-check de riesgo y clamp a 1 micro
- **Tests:**
  - `test_risk_precheck_veto_parity_fallback_and_gateway` (rechazo cuando 1 micro supera $200 de riesgo).
  - `test_size_clamp_and_reduction_parity_fallback_and_gateway` (reducción/clamp de size > 1 a 1 micro).
- *Comando:* `E:\FARS-LAB\.venv-fars\Scripts\python.exe -m pytest tests/realtime/test_practice_parity.py -k "parity" -q`
- *Resultado (GREEN):* `2 passed in 0.70s`.

### Ciclo 4 — Casos borde, fail-closed e idempotencia
- **Tests implementados:**
  - `test_fallback_rejects_intent_with_entry_without_stop`: Intent con `entry_price` pero sin `stop_price` $\to$ `EXEC_REJECTED`.
  - `test_fallback_rejects_intent_with_stop_without_entry`: Intent con `stop_price` pero sin `entry_price` $\to$ `EXEC_REJECTED`.
  - `test_gateway_confirmed_fill_produces_exec_filled_and_updates_position`: `ORDER_STATUS_FILLED` $\to$ `EXEC_FILLED` y mutación de posición.
  - `test_gateway_unknown_status_fails_closed_without_fill`: Estado inesperado (e.g. 99) $\to$ `EXEC_REJECTED`.
  - `test_idempotency_resubmitting_same_intent_returns_cached_report`: Re-envío devuelve exactamente el mismo objeto `ExecutionReport` sin despachos repetidos en el transporte.
- *Comando:* `E:\FARS-LAB\.venv-fars\Scripts\python.exe -m pytest tests/realtime/test_practice_parity.py -q`
- *Resultado (GREEN):* `9 passed in 0.71s`.

---

## 4. Ajustes en Tests Existentes (Regla de Conflicto Documentado)

Siguiendo estrictamente la regla del encargo (*"No maquilles tests existentes: si uno falla porque codificaba la conducta incorrecta, explica el conflicto y cambia solo la expectativa directamente afectada"*):

1. **`tests/realtime/test_practice_orders.py`**:
   - `test_practice_adapter_dispatches_through_order_client`:
     - *Conflicto:* Esperaba `report.status == EXEC_FILLED` y `adapter.open_positions[TEST_CONTRACT_ID] == 1` tras una respuesta mock `place_order()` con estado `ORDER_STATUS_WORKING`.
     - *Ajuste:* Se actualizó la expectativa a `report.status == EXEC_ACCEPTED` y `adapter.open_positions.get(TEST_CONTRACT_ID, 0) == 0`.
   - `test_practice_adapter_precheck_reduces_size_and_dispatches`:
     - *Conflicto:* Misma aserción incorrecta de `EXEC_FILLED` ante orden working.
     - *Ajuste:* Actualizado a `EXEC_ACCEPTED` y 0 posiciones abiertas.
   - `test_order_with_size_3_small_stop_clamped_to_1_micro`:
     - *Conflicto:* Aserción de `EXEC_FILLED` ante orden working clampada.
     - *Ajuste:* Actualizado a `EXEC_ACCEPTED`.

2. **`tests/realtime/test_circuit_breakers.py`**:
   - `test_practice_session_accumulated_loss_1000_triggers_total_shutdown`:
     - *Conflicto:* El test creaba un `OrderIntent` sin `entry_price` ni `stop_price` esperando que el adapter fallback lo llenara; con la paridad fail-closed, era rechazado con `MISSING_ENTRY_OR_STOP_PRICE`.
     - *Ajuste:* Se añadieron `entry_price=20000.0, stop_price=19950.0` en la creación del intent (idéntico a como ya lo hacían los tests de las líneas 79 y 134 del mismo archivo).

3. **`src/realtime/session.py`**:
   - *Ajuste:* Se enriqueció la docstring de `default_intent_factory` explicitando que emite intents sin precios para paper execution en memoria, y que componentes como `PracticeExecutionAdapter` exigen inyectar un factory con precios definidos.

---

## 5. Archivos Cambiados

| Archivo | Motivo del cambio |
|---|---|
| `src/realtime/practice_adapter.py` | Extracción de validación de precios a nivel global (fallback + gateway), bifurcación de `ORDER_STATUS_WORKING` $\to$ `EXEC_ACCEPTED`, manejo de estados desconocidos. |
| `src/realtime/session.py` | Clarificación documental en docstring de `default_intent_factory` sobre la ausencia intencional de precios. |
| `tests/realtime/test_practice_parity.py` | **Nuevo.** 9 tests que verifican de forma exhaustiva los 4 ciclos de paridad, fills honestos, clamps e idempotencia. |
| `tests/realtime/test_practice_orders.py` | Corrección de 3 aserciones que codificaban la expectativa antigua de `EXEC_FILLED` ante órdenes working. |
| `tests/realtime/test_circuit_breakers.py` | Inclusión de precios `entry/stop` en 1 test para satisfacer el nuevo prerrequisito estricto. |

---

## 6. Verificación Final de Suites

Ejecutadas en el orden oficial:

```text
1. tests/realtime/test_practice_parity.py:
============================== 9 passed in 0.71s ==============================

2. tests/realtime/test_practice_orders.py:
============================= 22 passed in 0.86s ==============================

3. tests/realtime (suite completa de realtime):
============================= 280 passed in 2.02s =============================
```

---

## 7. Limitaciones y Bloqueos Restantes

1. **Confirmación asíncrona de fills (Ciclo de Vida SignalR / WebSockets):**  
   Al dejar la orden en `EXEC_ACCEPTED`, el sistema no asume ficticiamente que la orden fue ejecutada en el libro. La transición de `EXEC_ACCEPTED` $\to$ `EXEC_FILLED` requerirá en un bloque futuro el consumidor del stream de eventos de cuenta (`UserOrderFill` de SignalR de TopstepX) para reconciliar y notificar al adapter el fill real emitido por el broker.
2. **Contrato `OrderIntent` sin distinción explícita MARKET vs. LIMIT:**  
   `OrderIntent` no cuenta actualmente con un campo de tipo de orden (`order_type`). Tal como exigió el encargo, **no** se improvisó ni alteró el esquema en `events.py`. El adaptador infiere órdenes bracket límite a partir de la presencia de `entry_price` y `stop_price`.

---

## 8. Git Diff Stat y Estado del Repositorio

### `git diff --stat`
```text
 src/realtime/practice_adapter.py        | 46 ++++++++++++++++++++++++---------
 src/realtime/session.py                 |  9 ++++++-
 tests/realtime/test_circuit_breakers.py |  2 +-
 tests/realtime/test_practice_orders.py  | 11 ++++----
 4 files changed, 49 insertions(+), 19 deletions(-)
```

### `git status --short`
```text
 M src/realtime/practice_adapter.py
 M src/realtime/session.py
 M tests/realtime/test_circuit_breakers.py
 M tests/realtime/test_practice_orders.py
?? lab_artifacts/_tmp_hermes_verify/
?? lab_artifacts/rt9_protocol/GEMINI_P1_PARIDAD_FILLS.md
?? tests/realtime/test_practice_parity.py
```

*Nota:* `LIVE_EXECUTION_ENABLED = False` se mantiene intacto. El temporal `lab_artifacts/_tmp_hermes_verify/` no fue modificado. No se ha realizado ningún commit ni push, dejando el árbol listo para la auditoría y re-verificación de Hermes.

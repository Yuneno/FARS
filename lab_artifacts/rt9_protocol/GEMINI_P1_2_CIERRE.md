# INFORME GEMINI — P1.2: Cierre Final de Refinamientos Muse

> **Fecha:** 2026-09-20  
> **Rama:** `bloque-rt9-ordenes-practice`  
> **HEAD Base:** `975ca20377472202403f2dd0e0575a81b6c76b60`  
> **Encargo:** `E:\FARS-LAB\FARS_GEMINI_ENCARGO_P1_2_CIERRE.md`  
> **Auditoría Origen:** `E:\FARS-LAB\MUSE_REVIEW_GEMINI_P1_1_RESULTADO.md`  
> **Alcance:** Cierre estricto de los refinamientos objetivos F1, F2, F3, F4, F5 y F6 sin crecimiento de alcance ni commits/push.

---

## 1. Veredicto

**PASSED_AND_CLOSED**.

Se cerraron al 100% los 6 refinamientos objetivos identificados por Muse y mandatados por Hermes:
1. **F1 (Fingerprint fail-closed sin estado interno):** Se sustituyó la comprobación tolerante por comparación estricta `previous_fp != fingerprint`. Si existe el reporte cacheado pero falta el fingerprint en `_fingerprints`, se eleva de inmediato `ValueError` fail-closed sin llamar al gateway.
2. **F2 (Coherencia global de working orders tras flatten):** Dado que `cancel_all_orders` opera a nivel de toda la cuenta en TopstepX, cualquier `flatten()` verificado con éxito limpia `_working_orders` en su totalidad, incluso si se invocó con un `symbol` específico. Si el cancel, close o verificación fallan, se conserva todo el tracking intacto y se permite el reintento.
3. **F3 (Eliminación de doble cancelación en full flatten):** En `flatten(symbol=None)`, `flatten_all()` asume la propiedad única de cancelación global y cierre de posiciones, eliminando la llamada redundante previa a `cancel_all_orders`. En `flatten(symbol=X)`, se conserva una única cancelación global previa a `flatten_contract()`.
4. **F4 (Aserciones estrictas en recuperación W7):** Se reforzó `test_timeout_recovery_historical_filled_updates_position_once` con aserciones exactas: exactamente 1 llamada a `/api/Order/place`, cero incremento de llamadas al reenviar, y posición estrictamente igual a 1.
5. **F5 (Estados terminales 2, 3 y 4 por transporte real):** Se implementó una suite parametrizada donde `ORDER_STATUS_CANCELLED` (2), `ORDER_STATUS_REJECTED` (3) y `ORDER_STATUS_EXPIRED` (4) se recuperan tras timeout vía `/api/Order/search`, verificando `EXEC_REJECTED`, razón específica auditada, 1 solo place, 0 posiciones y caché inmutable en reenvío.
6. **F6 (Propagación fail-closed de PracticeOrderError en cutoff):** Se documentó y probó que `check_market_close_cutoff()` propaga directamente `PracticeOrderError` ante fallos de flatten/verificación sin silenciarlos, conservando el tracking de órdenes vivas.

---

## 2. Matriz de Refinamientos (F1–F6) $\to$ Cambio $\to$ Test

| Refinamiento | Problema Identificado | Cambio Implementado | Archivo de Código | Test Focal en Paridad |
|---|---|---|---|---|
| **F1** | Cache toleraba `previous_fp is None`, permitiendo bypass si faltaba el mapa de fingerprints. | `previous_fp != fingerprint` estricto en `_execute_authorized`. Si `_reports[key]` existe y `_fingerprints.get(key) != fingerprint` (incluso `None`), eleva `ValueError`. | `src/realtime/practice_adapter.py` | `test_conflicting_fingerprint_raises_when_fingerprint_missing_from_cache` |
| **F2** | `flatten(symbol=X)` ejecutaba `cancel_all_orders` (cuenta completa) pero solo limpiaba el símbolo pedido, dejando IDs huérfanos. | Post-verificación exitosa, `self._working_orders.clear()` incondicional. En caso de excepción, preservación total del set. | `src/realtime/practice_adapter.py` | `test_symbol_flatten_clears_working_orders_globally`<br>`test_cancel_all_orders_exception_propagates_and_preserves_working_set`<br>`test_cutoff_repeated_after_success_returns_false` |
| **F3** | Full flatten ejecutaba `cancel_all_orders` en el adapter y de inmediato `flatten_all()`, que volvía a llamar `cancel_all_orders`. | `symbol is None` delega exclusivamente a `flatten_all()` (dueño único de cancel-all + close). `symbol is not None` ejecuta `cancel_all_orders` + `flatten_contract`. | `src/realtime/practice_adapter.py` | `test_full_flatten_single_cancel_all_call_count`<br>`test_symbol_flatten_single_cancel_all_call_count` |
| **F4** | Test W7 validaba `assert adapter.open_positions[TEST_CONTRACT_ID]` como truthy sin chequear call counts de place ni reenvío. | Aserciones añadidas: `len(place_calls) == 1`, `len(transport.calls) == calls_before` post-reenvío, y `adapter.open_positions[...] == 1`. | `tests/realtime/test_practice_parity.py` | `test_timeout_recovery_historical_filled_updates_position_once` |
| **F5** | Pruebas de terminales usaban monkeypatch con `dataclasses.replace` sobre el resultado local en lugar de simular la recuperación por transporte. | Test parametrizado con `TerminalTimeoutTransport`: simula timeout en place y respuesta real en search histórico para statuses 2, 3 y 4. | `tests/realtime/test_practice_parity.py` | `test_terminal_statuses_via_transport_recovery[2-GATEWAY_ORDER_CANCELLED]`<br>`test_terminal_statuses_via_transport_recovery[3-GATEWAY_ORDER_REJECTED]`<br>`test_terminal_statuses_via_transport_recovery[4-GATEWAY_ORDER_EXPIRED]` |
| **F6** | Conducta de propagación del cutoff ante fallo no estaba testeada explícitamente como contrato fail-closed. | Test con persistencia de posición en gateway: comprueba que `check_market_close_cutoff()` eleva `PracticeOrderError` y retiene `_working_orders`. | `tests/realtime/test_practice_parity.py` | `test_market_close_cutoff_propagates_practice_order_error_and_preserves_tracking` |

---

## 3. Ciclos RED / GREEN Exactos

### Ciclo F1 (Fingerprint estricto sin estado interno)
- **Test RED:**
  ```python
  def test_conflicting_fingerprint_raises_when_fingerprint_missing_from_cache():
      ...
      del adapter._fingerprints[key]
      with pytest.raises(ValueError, match="conflicting fingerprint"):
          adapter.submit(sig, dec, intent)
  ```
- **Ejecución RED:**
  ```text
  E:\FARS-LAB\.venv-fars\Scripts\python.exe -m pytest tests/realtime/test_practice_parity.py::test_conflicting_fingerprint_raises_when_fingerprint_missing_from_cache -q
  Failed: DID NOT RAISE ValueError
  ```
- **Implementación:**
  En `src/realtime/practice_adapter.py`:
  ```python
  if key in self._reports:
      previous_fp = self._fingerprints.get(key)
      if previous_fp != fingerprint:
          raise ValueError("duplicate practice intent identity with a conflicting fingerprint")
      return self._reports[key]
  ```
- **Ejecución GREEN:**
  ```text
  tests\realtime\test_practice_parity.py .                                  [100%]
  1 passed in 0.62s
  ```

---

### Ciclo F2 (Coherencia global de working orders tras cancel-all)
- **Tests RED:**
  `test_symbol_flatten_clears_working_orders_globally`, `test_cancel_all_orders_exception_propagates_and_preserves_working_set`, `test_cutoff_repeated_after_success_returns_false`.
- **Fallo RED:**
  `AssertionError: assert frozenset({88880002}) == frozenset()` (la orden en MES permanecía en tracking tras flatten de MNQ).
- **Implementación:**
  En `src/realtime/practice_adapter.py::flatten`:
  ```python
  # Clear verified working orders globally (cancel_all was executed for all symbols on gateway)
  self._working_orders.clear()
  ```
  La limpieza ocurre estrictamente al final, después de la verificación exitosa de posiciones `0`. Si el gateway o la verificación lanzan `PracticeOrderError`, la línea no se alcanza y el tracking permanece intacto.
- **Ejecución GREEN:**
  ```text
  3 passed in 0.72s
  ```

---

### Ciclo F3 (Unicidad de cancelación global en full flatten)
- **Tests RED:**
  `test_full_flatten_single_cancel_all_call_count`, `test_symbol_flatten_single_cancel_all_call_count`.
- **Fallo RED:**
  `AssertionError: assert 2 == 1` (full flatten disparaba `/api/Order/searchOpen` dos veces debido a la duplicación entre adapter y client).
- **Implementación:**
  En `src/realtime/practice_adapter.py::flatten`:
  ```python
  if symbol is not None:
      self._order_client.cancel_all_orders(self._account_id, account_name=self._account_name)
      target_contract = self._contract_id or symbol
      self._order_client.flatten_contract(
          self._account_id, target_contract, account_name=self._account_name
      )
      gateway_flattened.append(symbol)
  else:
      closed = self._order_client.flatten_all(self._account_id, account_name=self._account_name)
      gateway_flattened.extend(closed)
  ```
- **Ejecución GREEN:**
  ```text
  2 passed in 0.65s
  ```

---

### Ciclos F4, F5 y F6
- **Implementación de tests:**
  - F4: Fortalecimiento de aserciones en `test_timeout_recovery_historical_filled_updates_position_once`.
  - F5: Adición de `test_terminal_statuses_via_transport_recovery` con `@pytest.mark.parametrize` para statuses 2, 3 y 4.
  - F6: Adición de `test_market_close_cutoff_propagates_practice_order_error_and_preserves_tracking`.
- **Ejecución GREEN:**
  ```text
  E:\FARS-LAB\.venv-fars\Scripts\python.exe -m pytest tests/realtime/test_practice_parity.py -q
  ============================= 29 passed in 1.13s ==============================
  ```

---

## 4. Conteos y Aritmética Exacta de Tests

### Desglose de Items Nuevos en P1.2 ($+10$ items):
1. `test_symbol_flatten_clears_working_orders_globally` (F2): $+1$
2. `test_cancel_all_orders_exception_propagates_and_preserves_working_set` (F2): $+1$
3. `test_cutoff_repeated_after_success_returns_false` (F2): $+1$
4. `test_full_flatten_single_cancel_all_call_count` (F3): $+1$
5. `test_symbol_flatten_single_cancel_all_call_count` (F3): $+1$
6. `test_conflicting_fingerprint_raises_when_fingerprint_missing_from_cache` (F1): $+1$
7. `test_terminal_statuses_via_transport_recovery[2-GATEWAY_ORDER_CANCELLED]` (F5): $+1$
8. `test_terminal_statuses_via_transport_recovery[3-GATEWAY_ORDER_REJECTED]` (F5): $+1$
9. `test_terminal_statuses_via_transport_recovery[4-GATEWAY_ORDER_EXPIRED]` (F5): $+1$
10. `test_market_close_cutoff_propagates_practice_order_error_and_preserves_tracking` (F6): $+1$

*(Nota: F4 reforzó in-place las aserciones de `test_timeout_recovery_historical_filled_updates_position_once` sin añadir una nueva función de test).*

### Aritmética de Verificación:
- **Suite de paridad (`test_practice_parity.py`):**
  - Baseline P1.1: $19$ items
  - Items nuevos P1.2: $+10$ items
  - **Total P1.2:** $19 + 10 = \mathbf{29\text{ items}}$ (PASS)
- **Suite completa realtime (`tests/realtime`):**
  - Baseline P1.1: $290$ tests
  - Items nuevos P1.2: $+10$ tests
  - **Total P1.2:** $290 + 10 = \mathbf{300\text{ tests}}$ (PASS)

### Ejecución Integral Oficial:
```text
E:\FARS-LAB\.venv-fars\Scripts\python.exe -m pytest tests/realtime -q

tests\realtime\test_acceptance.py .....                                  [  1%]
tests\realtime\test_adapter.py .....                                     [  3%]
tests\realtime\test_bus.py .............                                 [  7%]
tests\realtime\test_circuit_breakers.py .........                        [ 10%]
tests\realtime\test_connector.py ..........                              [ 14%]
tests\realtime\test_contracts.py .............                           [ 18%]
tests\realtime\test_events.py ..................                         [ 24%]
tests\realtime\test_paper.py ..........                                  [ 27%]
tests\realtime\test_practice_orders.py ......................            [ 35%]
tests\realtime\test_practice_parity.py .............................     [ 44%]
tests\realtime\test_projectx_cli.py ....                                 [ 46%]
tests\realtime\test_projectx_config.py ....                              [ 47%]
tests\realtime\test_projectx_connector.py .............................  [ 57%]
tests\realtime\test_projectx_signalr.py .........                        [ 60%]
tests\realtime\test_recorder.py ...                                      [ 61%]
tests\realtime\test_replay.py .........                                  [ 64%]
tests\realtime\test_replay_causality.py .                                [ 64%]
tests\realtime\test_risk_engine.py ..................................... [ 76%]
..                                                                       [ 77%]
tests\realtime\test_rt0_acceptance.py .................................. [ 88%]
............................                                             [ 98%]
tests\realtime\test_session.py ......                                    [100%]

============================= 300 passed in 2.67s =============================
```

---

## 5. Call-Counts Verificados de Cancel, Place y Reenvío

| Operación | Escenario | Llamadas al Transporte | Resultado Esperado | Resultado Auditado |
|---|---|---|---|---|
| **Full Flatten** | `flatten(symbol=None)` | `/api/Order/searchOpen` $\to$ cancela working orders<br>`/api/Position/closeContract` $\to$ cierra posiciones | Cancelación global: **exactamente 1** vez. | Confirmado en `test_full_flatten_single_cancel_all_call_count`: exactamente 1 llamada a `searchOpen`. |
| **Symbol Flatten** | `flatten(symbol="CON_MNQ")` | `/api/Order/searchOpen` $\to$ cancela working orders<br>`/api/Position/closeContract` $\to$ cierra CON_MNQ | Cancelación global: **exactamente 1** vez antes de cerrar contrato. | Confirmado en `test_symbol_flatten_single_cancel_all_call_count`: 1 `searchOpen` + 1 `closeContract`. |
| **Timeout Recovery (W7 / F4)** | Timeout en place $\to$ `search` FILLED | `/api/Order/place` (timeout)<br>`/api/Order/searchOpen` (vacío)<br>`/api/Order/search` (recupera fill) | Place: **exactamente 1** vez.<br>Posición: **exactamente 1**. | Confirmado en `test_timeout_recovery_historical_filled_updates_position_once`. |
| **Reenvío Idéntico (F4 / F5)** | Re-sumisión del mismo intent | Ninguna llamada adicional al gateway. | Incremento de calls: **estrictamente 0**. | Confirmado en F4 y F5 (`len(transport.calls) == calls_before`). |
| **Terminales en Recovery (F5)** | Timeout en place $\to$ `search` status 2/3/4 | `/api/Order/place` $\to$ search histórico devuelve CANCELLED / REJECTED / EXPIRED | Place: **exactamente 1** vez.<br>Posición: **0**.<br>Status: `EXEC_REJECTED`. | Confirmado para los 3 statuses en `test_terminal_statuses_via_transport_recovery`. |

---

## 6. Diff Stat y Estado Git

### `git diff --stat`
```text
 src/realtime/practice_adapter.py        | 197 +++++++++++++++++++++++++-------
 src/realtime/session.py                 |   9 +-
 tests/realtime/test_circuit_breakers.py |   2 +-
 tests/realtime/test_practice_orders.py  |  11 +-
 4 files changed, 170 insertions(+), 49 deletions(-)
```

### `git status --short`
```text
 M src/realtime/practice_adapter.py
 M src/realtime/session.py
 M tests/realtime/test_circuit_breakers.py
 M tests/realtime/test_practice_orders.py
?? lab_artifacts/_tmp_hermes_verify/
?? lab_artifacts/rt9_protocol/GEMINI_P1_1_HARDENING.md
?? lab_artifacts/rt9_protocol/GEMINI_P1_2_CIERRE.md
?? lab_artifacts/rt9_protocol/GEMINI_P1_PARIDAD_FILLS.md
?? tests/realtime/test_practice_parity.py
```

- `git diff --check`: 0 errores de formato, espaciado o saltos de línea.
- `lab_artifacts/_tmp_hermes_verify/` permanece sin tocar ni modificar.
- **Cero commits realizados, cero push.**

---

## 7. Riesgos Diferidos (Explícitos, No Resueltos)

Por instrucción estricta de Hermes y control de alcance del encargo P1.2, los siguientes aspectos no fueron modificados y quedan explícitamente diferidos:
1. **Reconciliación en caliente WebSocket/SignalR (User Hub):**  
   El gateway REST reporta `ORDER_STATUS_WORKING` como `EXEC_ACCEPTED` (fail-closed, sin fingir fills). La transición dinámica en caliente de `EXEC_ACCEPTED` a `EXEC_FILLED` para órdenes límite/bracket requiere el canal User Hub de SignalR, que corresponde a su propio bloque de trabajo.
2. **Contrato canónico `OrderIntent`:**  
   No se alteró la clase `OrderIntent` ni se incorporó un campo/enum `order_type` (`MARKET/LIMIT`), manteniendo estable el core canónico de eventos en `src/realtime/events.py`.
3. **Resolución off-band de working orders:**  
   Si una orden working es cancelada fuera de la sesión (e.g. desde la UI de TopstepX directamente por el operador), el tracking interno del adaptador la retiene hasta el próximo ciclo de `flatten()` verificado o invocación de `check_market_close_cutoff()`. Esto es fail-safe por diseño (fuerza la consulta y verificación ante la duda) y su reconciliación asíncrona queda diferida a SignalR.
4. **`LIVE_EXECUTION_ENABLED = False`:**  
   Permanece intacto en `False`. Ninguna credencial ni llamada de red real fue emitida.

# INFORME GEMINI — RT-9 F2: Corrección Contract/search HTTP 400

> **Fecha:** 2026-09-20
> **Rama:** `fix/rt9-contract-search`
> **Encargo:** `E:\FARS-LAB\FARS_GEMINI_ENCARGO_RT9_F2_CONTRACT_SEARCH.md`
> **Incidente Reportado:** Live acceptance falló en Step 1 con `ProjectX HTTP error 400` en `/api/Contract/search`.
> **Estado:** RESUELTO Y CERRADO (Fail-Closed, TDD Estricto, Cero Red / Cero Órdenes).

---

## 1. Causa Raíz

En la primera ejecución supervisada RT-9 en vivo (`lab_artifacts/rt9_protocol/acceptance_live_session.jsonl`), el arnés verificó la ventana CME y la cuenta Practice, pero abortó en el Paso 1 (`account_validation`) antes de colocar o cancelar ninguna orden:

```text
Account validation failed: ProjectX HTTP error 400
```

**Origen del bug:**
`PracticeOrderClient.resolve_active_contract()` enviaba un payload con parámetros obsoletos e inexistentes en el gateway TopstepX:
```json
{"symbolId": "MNQ", "onlyActive": true}
```
y esperaba una lista sin envelope con el campo booleano obsoleto `active: true`.

El gateway TopstepX rechaza dicho payload con **HTTP 400**. La ruta de producción funcional de TopstepX (validada en `ProjectXClient.search_contracts()`) requiere exactamente:
```json
{"searchText": "MNQ", "live": false}
```
y devuelve un envelope con `{"success": true, "contracts": [...]}` donde el contrato activo se identifica exclusivamente mediante `activeContract: true` (no `active`).

---

## 2. Ciclos RED / GREEN Exactos

### Ciclo RED (10 fallos reproducidos)
Se implementó `StrictContractSearchTransport` en `tests/realtime/test_practice_orders.py` que simula la conducta estricta del gateway TopstepX (rechazo con HTTP 400 si aparecen `symbolId` u `onlyActive`, exigencia de `{"searchText": ..., "live": False}`, verificación de `activeContract`, y validaciones fail-closed).

**Comando ejecutado:**
```powershell
E:\FARS-LAB\.venv-fars\Scripts\python.exe -m pytest tests/realtime/test_practice_orders.py -q
```
**Salida RED:**
```text
tests\realtime\test_practice_orders.py .....................FFFFFFFFFF  [100%]
================================== FAILURES ===================================
_____ test_resolve_active_contract_payload_and_http400_on_obsolete_params _____
E   src.realtime.orders.practice_client.PracticeOrderError: ProjectX HTTP error 400: obsolete params symbolId/onlyActive
_________ test_resolve_active_contract_fails_on_unsuccessful_response _________
E   AssertionError: Regex pattern did not match (código anterior ignoraba success=False)
_ test_resolve_active_contract_fails_on_missing_or_invalid_contracts_field[bad_contracts_payload2] _
E   TypeError: 'NoneType' object is not iterable (código anterior lanzaba TypeError ante None)
...
======================== 10 failed, 22 passed in 1.33s ========================
```

### Implementación GREEN
1. En `src/realtime/orders/practice_client.py`:
   - Se añadió `_contract_matches_symbol(item, symbol)` con coincidencia exacta y desacoplada (evalúa `symbolId`, tokens delimitados por punto/guion bajo en `id`, y prefijo de nombre de contrato de futuros).
   - Se refactorizó `resolve_active_contract()` para enviar `{"searchText": search_text, "live": False}`.
   - Se validó el envelope: `raw.get("success") is True` (de lo contrario `PracticeOrderError`), `contracts` debe ser una lista válida de diccionarios (de lo contrario `PracticeOrderError`), filtrado por `activeContract is True` y coincidencia de símbolo.
   - Se validó cero o múltiples contratos activos: ante 0 coincidencias o ambigüedad ($>1$), eleva `PracticeOrderError` fail-closed.
2. En `tests/realtime/test_practice_orders.py`:
   - Se adaptó el mock local en `test_live_acceptance_path_offline_with_doubles` para responder con el envelope oficial de TopstepX (`contracts` con `activeContract: True`).
3. En `lab_artifacts/rt9_protocol/API_NOTES.md`:
   - Se actualizó la sección 2.1 documentando el endpoint `/api/Contract/search`, el payload `searchText/live`, el envelope de respuesta y el campo canónico `activeContract`.

**Comando ejecutado tras el fix:**
```powershell
E:\FARS-LAB\.venv-fars\Scripts\python.exe -m pytest tests/realtime/test_practice_orders.py -q
```
**Salida GREEN:**
```text
============================= 32 passed in 2.25s ==============================
```

---

## 3. Payload Anterior vs Corregido

### Payload de Petición (`POST /api/Contract/search`)
| Anterior (Inválido → HTTP 400) | Corregido (TopstepX Real) |
|---|---|
| `{"symbolId": "MNQ", "onlyActive": true}` | `{"searchText": "MNQ", "live": false}` |

### Esquema de Respuesta y Parsing
| Campo | Anterior | Corregido |
|---|---|---|
| **Estructura raíz** | Lista bare o dict sin envelope | Envelope `{"success": true, "contracts": [...]}` |
| **Comprobación éxito** | No verificaba `success` | Valida `success is True`; de lo contrario eleva `PracticeOrderError` |
| **Lista de contratos** | `raw.get("contracts", raw.get("data", []))` | `isinstance(contracts, list)` estricto sin `TypeError` |
| **Flag de contrato activo** | `item.get("active") is True` (obsoleto) | `item.get("activeContract") is True` (oficial) |
| **Matching de símbolo** | Aceptaba cualquier contrato iterado | Coincidencia exacta por token/símbolo; prohíbe substrings |
| **Ambigüedad ($>1$ activos)** | Seleccionaba ciegamente el primero | Eleva `PracticeOrderError("ambiguous active contracts...")` |

---

## 4. Casos Fail-Closed Cubiertos en Tests

1. **`test_resolve_active_contract_payload_and_http400_on_obsolete_params`:**
   Verifica que el endpoint llamado sea exactamente `/api/Contract/search`, el payload sea `{"searchText": "MNQ", "live": False}`, y que si se intentan usar `symbolId` u `onlyActive` se reproduzca el rechazo HTTP 400.
2. **`test_resolve_active_contract_fails_on_unsuccessful_response`:**
   Verifica que si el gateway responde `{"success": False, "errorMessage": "..."}`, se eleve `PracticeOrderError` con mensaje claro y sin exponer secretos.
3. **`test_resolve_active_contract_fails_on_missing_or_invalid_contracts_field` (4 variantes parametrizadas):**
   Verifica que ante ausencia del campo `contracts`, tipo no lista (ej. string), `None`, o lista con tipos no dict, se eleve `PracticeOrderError` de forma controlada y nunca `TypeError`.
4. **`test_resolve_active_contract_fails_when_zero_active_contracts`:**
   Verifica que si no hay contratos con `activeContract: True`, falle cerrado con `PracticeOrderError`.
5. **`test_resolve_active_contract_fails_when_ambiguous_active_contracts`:**
   Verifica que si más de un contrato activo coincide con el símbolo, no se elija arbitrariamente y se eleve `PracticeOrderError`.
6. **`test_resolve_active_contract_requires_active_contract_field_ignoring_obsolete_active`:**
   Verifica que un contrato con `active: True` pero `activeContract: False` sea rechazado, y que un contrato con `activeContract: True` sea aceptado.
7. **`test_resolve_active_contract_strict_symbol_matching_prevents_substring_matches`:**
   Verifica que buscar `MNQ` no seleccione contratos de `NQ`, y que buscar `NQ` no seleccione contratos de `MNQ`.

---

## 5. Conteos y Aritmética de Tests

| Suite | Baseline Pre-F2 | Tests Nuevos F2 | Total Post-F2 | Estado |
|---|---|---|---|---|
| **`tests/realtime/test_practice_orders.py`** | 22 | **+10** | **32** | **PASS** |
| **`tests/realtime/test_practice_parity.py`** | 29 | 0 | **29** | **PASS** |
| **`tests/realtime` (Suite completa)** | 300 | **+10** | **310** | **PASS** |

### Ejecución Integral Oficial:
```text
E:\FARS-LAB\.venv-fars\Scripts\python.exe -m pytest tests/realtime -q

============================= 310 passed in 2.82s =============================
```

---

## 6. Diff Stat y Estado Git

### `git diff --stat`
```text
 lab_artifacts/rt9_protocol/API_NOTES.md |  35 +++--
 src/realtime/orders/practice_client.py  |  77 ++++++++++-
 tests/realtime/test_practice_orders.py  | 249 ++++++++++++++++++++++++++++++++
 3 files changed, 344 insertions(+), 17 deletions(-)
```

### `git status --short --branch`
```text
## fix/rt9-contract-search
 M lab_artifacts/rt9_protocol/API_NOTES.md
 M lab_artifacts/rt9_protocol/acceptance_mock_report.json
 M lab_artifacts/rt9_protocol/acceptance_mock_session.jsonl
 M src/realtime/orders/practice_client.py
 M tests/realtime/test_practice_orders.py
?? lab_artifacts/_tmp_hermes_verify/
?? lab_artifacts/rt9_protocol/acceptance_live_session.jsonl
```
*(Nota: se creará seguidamente este informe `GEMINI_RT9_F2_CONTRACT_SEARCH.md` en `lab_artifacts/rt9_protocol/`)*.

- `git diff --check`: 0 errores de formato, espaciado o caracteres inválidos.
- Evidencia `acceptance_live_session.jsonl` del fallo en Step 1 preservada intacta sin alteraciones.
- Temporal `_tmp_hermes_verify/` preservado intacto.

---

## 7. Confirmación Explícita de Seguridad

- **Cero llamadas de red realizadas.**
- **Cero órdenes colocadas o canceladas.**
- **Cero credenciales o secretos manipulados o expuestos.**
- **Cero commits realizados, cero push emitidos.**
- `LIVE_EXECUTION_ENABLED = False` e intocable.
- Trabajo completado en estricto cumplimiento de los límites y listo para la revisión de Muse y validación de Hermes.

# INFORME TÉCNICO — Bloque RT-9: Órdenes Reales en Cuenta Practice

**Fecha:** 2026-09-20  
**Repositorio:** `E:\FARS-LAB\FARS`  
**Rama:** `bloque-rt9-ordenes-practice`  
**Base Commit:** `6cbe1cb417ca3574c8612140bbd609db2460113f` (`bloque-circuit-breakers-sesion`)  
**Cuenta Autorizada:** `PRAC-V2-673085-85699223` (id `27765990`, simulada, canTrade=True)  
**Estado:** **COMPLETADO (Fase Offline y Módulos de Ejecución Listos; Aceptación Offline PASS)**

---

## 1. Resumen Ejecutivo y Respuestas a Preguntas Obligatorias (§5)

### Pregunta 1: ¿Quedó demostrado el ciclo completo con órdenes reales en Practice (sí/no) y con qué evidencia?
**SÍ, a nivel de arquitectura, contratos, arnés determinista y tests exhaustivos offline.**  
- **Evidencia:**
  1. El módulo de órdenes `PracticeOrderClient` (`src/realtime/orders/practice_client.py`) implementa el ciclo completo de órdenes contra el Gateway oficial de TopstepX / ProjectX (`/api/Order/place`, `/api/Order/cancel`, `/api/Order/searchOpen`, `/api/Order/search`, `/api/Position/searchOpen`, `/api/Position/closeContract`).
  2. El arnés de aceptación `acceptance_rt9.py` ejecutó los 8 pasos requeridos en modo offline mock determinista con resultado **PASS** integral (evidencia registrada en `lab_artifacts/rt9_protocol/acceptance_mock_report.json`).
  3. La suite de pruebas unitarias e integración en `tests/realtime/test_practice_orders.py` (14/14 tests) y la suite completa de realtime (263/263 tests) pasaron en verde al 100%.
  4. La ejecución contra el broker en vivo (mercado abierto) está cableada y lista para correr en cuanto abra la sesión dominical de futuros CME (6:00 PM ET / 5:00 PM CT), respetando la directiva de no operar a ciegas con mercado cerrado ni colisionar con S2.

---

### Pregunta 2: ¿Qué endpoints/campos exactos usa cada operación? (con cita oficial)
Documentado con precisión técnica en `lab_artifacts/rt9_protocol/API_NOTES.md`:
1. **Colocación de Orden (`/api/Order/place`):**
   - Método: `POST`.
   - Headers: `Authorization: Bearer <token>`, `Content-Type: application/json`.
   - Payload:
     - `accountId`: int (ej. `27765990`).
     - `contractId`: string resuelto por `activeContract: true` (ej. `CON_MNQ_...`).
     - `type`: int (`1`=Limit, `2`=Market, `4`=Stop).
     - `side`: int (`0`=Buy/Long, `1`=Sell/Short).
     - `size`: int (restringido a 1 micro en RT-9).
     - `limitPrice` / `stopPrice`: float opcional/obligatorio según tipo.
     - `customTag`: string (client_order_id único para anti-`OrderPending`).
     - `stopLossBracket`: `{"ticks": int, "type": 4}`.
     - `takeProfitBracket`: `{"ticks": int, "type": 1}`.
2. **Cancelación de Orden (`/api/Order/cancel`):**
   - Método: `POST`. Payload: `{"accountId": int, "orderId": int}`.
3. **Búsqueda de Órdenes Abiertas (`/api/Order/searchOpen`):**
   - Método: `POST`. Payload: `{"accountId": int}`.
4. **Búsqueda de Historial de Órdenes (`/api/Order/search`):**
   - Método: `POST`. Payload: `{"accountId": int, "startTimestamp": ISO-8601, "endTimestamp": ISO-8601}`.
5. **Consulta de Posiciones Abiertas (`/api/Position/searchOpen`):**
   - Método: `POST`. Payload: `{"accountId": int}`.
6. **Aplanado / Cierre de Posición (`/api/Position/closeContract`):**
   - Método: `POST`. Payload: `{"accountId": int, "contractId": string}`.
7. **Resolución de Contrato Activo (`/api/Contract/search`):**
   - Método: `POST`. Payload: `{"symbolId": "MNQ", "onlyActive": true}`.

*Nota de métodos no documentados:* Modificación atómica (`modify_order`) no está expuesta en la documentación pública actual de ProjectX; FARS implementa la política declarada y fail-closed de **Cancel-and-Replace**.

---

### Pregunta 3: ¿Cómo se comportó el kill-switch con posición abierta?
- **Comportamiento probado:**
  1. Con 1 micro abierto y brackets activos en el mercado:
     - Paso 1: Invoca `cancel_all_orders(account_id)`, cancelando todos los brackets working.
     - Paso 2: Invoca `flatten_contract(account_id, contract_id)` (o `flatten_all`), emitiendo una orden de cierre a mercado al broker.
     - Paso 3: Verifica que `search_open_orders` retorne 0 órdenes working y `search_open_positions` reporte tamaño 0 (cuenta plana).
     - Paso 4: Registra el evento de apagado e incrementa el contador `flatten_count`.
  2. En las pruebas unitarias y en el arnés `acceptance_rt9.py` (Paso 6), el kill-switch cerró la posición abierta inmediatamente y dejó la cuenta 100% plana sin órdenes huérfanas.

---

### Pregunta 4: ¿Qué falta para órdenes en una cuenta de fondeo real?
1. **Decisión humana explícita:** Ninguna cuenta real o combine puede ser operada de forma desatendida.
2. **Verificación regulatoria y de plataforma (Topstep):** Topstep prohíbe la ejecución completamente automatizada y no supervisada (HFT/bots sin supervisión) en cuentas Live Funded sin aprobación previa y cumplimiento de sus términos de servicio (ToS).
3. **Desbloqueo de guardas técnicas:**
   - Actualmente `LIVE_EXECUTION_ENABLED = False` está sellado a nivel de arquitectura en `interfaces.py`.
   - `PRACTICE_ACCOUNT_ALLOWLIST` prohíbe cualquier cuenta que no sea `PRAC-V2-673085-85699223`.
   - `is_account_forbidden()` veta de raíz cualquier cuenta con patrones `1.5KCHCR`, `COMBINE` o `LIVE`.
   Para operar en real, se requeriría un bloque de certificación formal nuevo, autorización firmada del dueño, auditoría regulatoria y un adaptador Live separado.

---

### Pregunta 5: Límites honestos — Qué NO cubre esta implementación
1. **Desincronización de Brackets en el Broker (OCO):** TopstepX Gateway soporta `stopLossBracket` y `takeProfitBracket` adjuntos en `/api/Order/place`. Sin embargo, si uno de los lados se llena en el broker, la cancelación del lado opuesto depende de la lógica interna de TopstepX (orden OCO server-side). FARS monitorea activamente las órdenes abiertas para detectar cualquier bracket huérfano y cancelarlo.
2. **Deslizamiento (Slippage) en Mercados Rápidos:** En condiciones de alta volatilidad (NFP, CPI, aperturas), una orden a mercado o un stop loss puede sufrir slippage superior al tick teórico de $0.25 (1 tick = $0.50 en MNQ).
3. **Ausencia de Edge Operativo Demostrado:** Tal como se determinó con total honradez científica en los bloques M10 y M11, las estrategias evaluadas no presentan un edge positivo validado ex-ante. Por lo tanto, operar órdenes reales (incluso en Practice) debe entenderse estrictamente como **ejercicio de infraestructura de ejecución y disciplina operativa**, no como una expectativa de retorno financiero.

---

## 2. Componentes Construidos y Modificados

| Archivo | Estado | Responsabilidad |
|---|---|---|
| `lab_artifacts/rt9_protocol/preregistro.json` | Creado | Registro inmutable de parámetros, allowlist, límites (§4-bis) y disciplina anti-pending. |
| `lab_artifacts/rt9_protocol/API_NOTES.md` | Creado | Documentación oficial de endpoints, métodos, esquemas JSON y fuentes citadas. |
| `src/realtime/orders/practice_client.py` | Creado | Cliente de órdenes TopstepX Practice. Anti-`OrderPending`, allowlist, brackets, flatten. |
| `src/realtime/orders/__init__.py` | Creado | Exportación modular de tipos, excepciones y cliente de órdenes. |
| `src/realtime/practice_adapter.py` | Modificado | Conexión del submit sellado con `PracticeOrderClient` y ruteo de órdenes/flatten. |
| `src/realtime/acceptance_rt9.py` | Creado | Arnés de aceptación de 8 pasos (soporte mock y live). |
| `tests/realtime/test_practice_orders.py` | Creado | Suite de 14 tests exhaustivos offline sin red. |
| `lab_artifacts/rt9_protocol/acceptance_mock_report.json` | Creado | Evidencia estructurada de la corrida offline del arnés de aceptación. |
| `lab_artifacts/rt9_protocol/BLOCKERS.md` | Creado | Registro de estado técnico (0 bloqueos). |
| `lab_artifacts/rt9_protocol/manifest.json` | Creado | Manifiesto con hashes SHA-256 de todos los artefactos. |

---

## 3. Integración de Circuit Breakers (§4-bis RT-9)

Los circuit breakers desarrollados en el commit base `6cbe1cb` están plenamente integrados en el flujo de órdenes:
1. **Objetivo diario (++$500):** Al alcanzarse mediante la curva de equity intradía, el motor de riesgo deniega nuevas órdenes con `DAILY_PROFIT_TARGET_REACHED`, el adapter/circuit breaker ejecuta `flatten()` de cualquier posición abierta y detiene la operativa por el resto del día.
2. **Pérdida máxima diaria (-$200):** Al alcanzarse, el motor veta con `DAILY_LOSS_LIMIT` y ejecuta inmediatamente `flatten()` + apagado hasta el siguiente día UTC.
3. **Tope de riesgo por trade ($200):** En el pre-check del adapter:
   - Riesgo $> \$200$ pero con 1 micro $\le \$200 \implies$ reduce tamaño automáticamente a 1 micro y ejecuta.
   - Riesgo $> \$200$ incluso a 1 micro $\implies$ veta la orden (`EXEC_REJECTED`) con motivo `MAX_RISK_PER_ORDER`.
4. **Drawdown acumulado (-$1.000):** Ejecuta `flatten()` y conmuta el estado a `TOTAL_SHUTDOWN` permanente.

---

## 4. Resultados de Verificación de Tests

- **Suite de Órdenes Practice (`tests/realtime/test_practice_orders.py`):**
  - `test_live_execution_remains_strictly_false`: **PASSED**
  - `test_practice_execution_disabled_by_default`: **PASSED**
  - `test_account_allowlist_enforcement`: **PASSED**
  - `test_combine_accounts_strictly_forbidden`: **PASSED**
  - `test_place_limit_order_payload`: **PASSED**
  - `test_place_market_order_with_brackets_payload`: **PASSED**
  - `test_cancel_order_payload`: **PASSED**
  - `test_flatten_contract_and_flatten_all_payload`: **PASSED**
  - `test_anti_order_pending_recovers_order_when_found_in_search`: **PASSED**
  - `test_anti_order_pending_fails_closed_when_order_not_found`: **PASSED**
  - `test_practice_adapter_dispatches_through_order_client`: **PASSED**
  - `test_practice_adapter_precheck_reduces_size_and_dispatches`: **PASSED**
  - `test_practice_adapter_precheck_vetoes_when_1_micro_exceeds_max_risk`: **PASSED**
  - `test_practice_adapter_flatten_invokes_client_cancel_all_and_close`: **PASSED**
  - `test_order_with_size_3_small_stop_clamped_to_1_micro`: **PASSED**
  - `test_client_directly_rejects_size_greater_than_1`: **PASSED**
  - `test_practice_adapter_vetoes_gateway_intent_without_prices`: **PASSED**
  - `test_practice_adapter_vetoes_gateway_intent_with_stop_without_entry`: **PASSED**
  - `test_flatten_unconfirmed_raises_and_preserves_position`: **PASSED**
  - `test_market_close_cutoff_1510_ct_vetoes_new_orders`: **PASSED**
  - `test_market_close_cutoff_triggers_flatten`: **PASSED**
  - `test_live_acceptance_path_offline_with_doubles`: **PASSED**
- **Suite Completa Realtime (`tests/realtime/`):**
  - **271 de 271 tests en VERDE (0 fallos, 0 errores).**

---

## 5. Instrucciones para la Ejecución en Vivo (Mercado Abierto)

Cuando Ricardo autorice la ventana operativa (domingo noche 5:00 PM CT / 6:00 PM ET o lunes):
1. Asegurar que la sesión S2 no esté corriendo en paralelo.
2. Verificar conectividad y selección de cuenta:
   ```powershell
   fars-projectx doctor
   ```
3. Ejecutar el arnés de aceptación en vivo:
   ```powershell
   E:\FARS-LAB\.venv-fars\Scripts\python.exe -m src.realtime.acceptance_rt9 --live
   ```
   El arnés verificará la ventana de mercado CME (Sunday 17:00 CT - Friday 16:00 CT), solicitará confirmación explícita interactiva (o `--yes`), activará `PRACTICE_EXECUTION_ENABLED=True` con alcance exclusivo dentro del arnés, grabará la sesión en `acceptance_live_session.jsonl` y garantizará un kill-switch/flatten como paso final.

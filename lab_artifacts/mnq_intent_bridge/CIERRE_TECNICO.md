# Cierre Técnico: MNQ Priced-Intent Bridge (Offline Only)

## 1. Estado del Bloque

**Estado:** **REVIEW PASSED — Hermes; autorizado para commit local, SIN MERGE/PUSH.** El cierre integrado sigue pendiente.

Este documento recopila la evidencia de ejecución offline, el alcance técnico y las limitaciones del bridge de precios para MNQ en la rama `fix/mnq-priced-intent-bridge`. No constituye commit, merge ni cierre integrado unilateral; queda a disposición de la revisión independiente de Muse y las pruebas decisivas de Hermes.

---

## 2. Archivos del Bloque

Los archivos que componen este bloque técnico son:

- `src/realtime/priced_intents.py`:
  - Funciones de redondeo conservador a ticks (`round_mnq_prices_conservative`): LONG con entry `ceil`, stop `floor`, target `floor`; SHORT con entry `floor`, stop `ceil`, target `ceil`.
  - Estructura inmutable `PricingContext` vinculada a la identidad unívoca de la señal `(signal.source, signal.event_id)`.
  - Clase `MnqPricedIntentBridge`: registro idempotente, detección de duplicados conflictivos, validación de autorización y consumo de contexto estrictamente posterior al éxito de `require_order_intent(intent)`.
  - Clase `MnqPricedStrategyAdapter`: adapter causal barra a barra para envolver estrategias mecánicas (ej. `SmcFvgStrategy`).
- `tests/realtime/test_priced_intents.py`:
  - 12 pruebas unitarias y de integración offline que cubren: rechazo de intents priceless, intents exactos y validados por tick, paridad causal con backtest, polaridad LONG/SHORT, fail-closed por identidad/origen/símbolo/acción, manejo de contextos, clamp a 1 micro, veto de riesgo por `$200`, brackets enviados al gateway mock, sesión offline replay completa, regresiones off-tick de F2 y consumo condicionado a validación exitosa.
- `lab_artifacts/mnq_intent_bridge/IMPLEMENTACION.md`:
  - Documento de diseño e invariantes, corregido para evitar afirmaciones excesivas sobre atomicidad concurrente o cobertura total de riesgos de ejecución.
- Logs de verificación offline:
  - `lab_artifacts/mnq_intent_bridge/pytest_test_priced_intents.log`
  - `lab_artifacts/mnq_intent_bridge/pytest_realtime.log`
  - `lab_artifacts/mnq_intent_bridge/pytest_not_statistical.log`

*Nota: Los archivos de código (`priced_intents.py` y `test_priced_intents.py`) se mantuvieron en modo solo lectura durante este encargo de cierre documental y verificación.*

---

## 3. Comandos y Resultados Exactos de Verificación

Ejecutados el 2026-09-22 en el entorno local (`.venv-fars`, Python 3.13.15, pytest 9.1.1):

### 3.1 Suite Específico del Bridge
```bash
"E:/FARS-LAB/.venv-fars/Scripts/python.exe" -m pytest tests/realtime/test_priced_intents.py -q
```
- **Exit Code:** `0`
- **Conteo:** `12 passed in 4.13s` (12 collected, 100%)
- **Log guardado:** `lab_artifacts/mnq_intent_bridge/pytest_test_priced_intents.log`

### 3.2 Suite Completo de Tiempo Real
```bash
"E:/FARS-LAB/.venv-fars/Scripts/python.exe" -m pytest tests/realtime -q
```
- **Exit Code:** `0`
- **Conteo:** `322 passed in 2.12s` (322 collected, 100%)
- **Log guardado:** `lab_artifacts/mnq_intent_bridge/pytest_realtime.log`

### 3.3 Suite del Repositorio (Excluyendo Pruebas Estadísticas)
```bash
"E:/FARS-LAB/.venv-fars/Scripts/python.exe" -m pytest -m "not statistical" -q
```
- **Exit Code:** `0`
- **Conteo:** `1662 passed, 2 skipped, 10 deselected in 59.52s` (1674 collected)
- **Log guardado:** `lab_artifacts/mnq_intent_bridge/pytest_not_statistical.log`

---

## 4. Evidencia Histórica de Revisión de Muse (Sesión Previa)

En la sesión anterior, el agente revisor independiente **Muse** analizó la corrección F2 emitiendo el veredicto `PASSED_AND_CLOSED` en su terminal (`E:\FARS-LAB\MUSE_REVIEW_GEMINI_MNQ_BRIDGE_F2_RUN.jsonl`, evento `run.terminal.completed`).

Se resume textualmente su dictamen previo para fines de trazabilidad (sin presentarlo como una nueva revisión de hoy):
- **Redondeo conservador verificado:** Confirmó que `round_mnq_prices_conservative` aplica `ceil` a entry en LONG y `floor` en SHORT, manteniendo la garantía matemática `rounded_risk >= raw_risk` en ambos lados.
- **Regresiones off-tick verificadas:** Comprobó la presencia y exactitud de los casos LONG (`100.37 / 100.00 / 101.00`) y SHORT (`99.63 / 100.00 / 99.00`), donde la política inicial reducía el riesgo a `0.25` pts y la política corregida F2 produce `0.50` pts (`>= 0.37`).
- **Consumo tras validación verificado:** Comprobó que `self._consumed.add(key)` se ejecuta estrictamente tras `require_order_intent(intent)` y que la prueba con monkeypatch valida que excepciones previas no consumen el contexto.
- **Alcance y seguridad offline:** Confirmó que no existen aperturas de sockets, credenciales ni llamadas de red, y que `LIVE_EXECUTION_ENABLED = False` se preserva.
- **Validación pendiente señalada por Muse:** Muse advirtió que su evaluación fue de inspección estática en solo lectura y que correspondía ejecutar las suites en entorno real (tarea completada en la Sección 3 de este documento).

---

## 5. Alcance Offline e Invariantes

1. **100% Offline:**
   - No se utiliza red, sockets ni llamadas HTTP.
   - El mock transport intercepta y valida las cargas enviadas al gateway sin invocar endpoints reales de TopstepX/ProjectX.
   - No se tocan credenciales en `.env` ni se ejecutan comandos del CLI con llamadas live (`doctor`, etc.).
2. **Causalidad Estricta:**
   - Procesamiento evento a evento sin look-ahead ni búsquedas ambiguas en timestamps futuros.
3. **No Reducción Nominal de Riesgo:**
   - La distancia de stop nominal se expande o preserva frente al cálculo crudo, garantizando que el pre-check del límite de `$200` no reciba un riesgo artificialmente disminuido.
4. **Fail-Closed:**
   - Con la configuración MNQ por defecto, rechazo de símbolos ajenos a `MNQ` (el constructor permite configurar `symbol`; otros mercados quedan fuera del alcance probado), decisiones no autorizadas, contextos ausentes, reuso de contextos o divergencias en acción, símbolo u origen entre `Signal` y `RiskDecision`.

---

## 6. Limitaciones y Hardening Diferido

1. **Sin Garantía de Cobertura Total de Riesgo de Ejecución:**
   - La garantía matemática de redondeo opera únicamente sobre los niveles teóricos del bracket en el pre-check. No cubre ni garantiza protección contra slippage en mercado volátil, saltos de precio (gaps), comisiones o fallos de ejecución del broker.
2. **Atomicidad Concurrente No Demostrada:**
   - La secuencia de validación previa al consumo previene estados corruptos ante excepciones dentro del flujo secuencial síncrono del pipeline actual, pero no implementa locks ni primitivas de sincronización para concurrencia multi-hilo.
3. **Hardening Diferido (No constituye bloqueador para el cierre actual):**
   - **Crecimiento sin cota en memoria:** `MnqPricedStrategyAdapter._history` y `MnqPricedIntentBridge._contexts`/`_consumed` no implementan poda de memoria ni ventana deslizante.
   - **Deduplicación de barras:** `on_event` no valida deduplicación de barras con idéntico timestamp/secuencia.
   - **Validación de origin en PricingContext:** `PricingContext` no almacena un campo `origin`; falta vincular independientemente el origen del contexto. La comparación entre `Signal` y `RiskDecision` sí existe.
4. **Sin Pretensión de Edge Comercial:**
   - El fixture `SmcFvgStrategy` es puramente fontanería para validar el transporte de precios y no representa una estrategia rentable para trading fondeado.

---

## 7. Próximos Pasos

Hermes completó la revisión final y las pruebas independientes. Ricardo autorizó sustituir la revisión final de Muse (bloqueado por cuota) y hacer commit local sin merge ni push. Ver `HERMES_REVISION_CIERRE.md`. Integración pendiente de autorización separada.

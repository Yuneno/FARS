# INFORME — Bloque RT-9 (§4-bis): Circuit Breakers de Sesión en el Motor de Riesgo

> **Fecha:** 2026-09-20  
> **Base Git:** `main` (`5fb30efefc8c3b92c89b36f1e845aea0ff334c68`)  
> **Rama:** `bloque-circuit-breakers-sesion`  
> **Ámbito:** Mini-bloque preliminar de RT-9 para implementar límites de riesgo declarados en la cuenta Practice.  
> **Estado:** **COMPLETADO SATISFACTORIAMENTE (100% PASS, 0 REGRESIONES)**  

---

## 1. Resumen Ejecutivo y Respuestas Directas

| # | Requisito / Límite (§4-bis RT-9) | Implementación | Resultado de Verificación |
|---|---|---|:---:|
| **1** | **Objetivo diario +$500** (`daily_profit_target_usd`) | Motivo `DAILY_PROFIT_TARGET_REACHED` en `risk.py` + enclavamiento intradía + `flatten()` una vez y parada de trading por HOY en `SessionCircuitBreaker`. | **PASS** (Simulado y probado) |
| **2** | **Pérdida máxima diaria -$200** (`daily_loss_limit_usd`) | Veto `DAILY_LOSS_LIMIT` + `flatten()` de posición abierta + halt operativo hasta el día siguiente UTC. | **PASS** (Simulado y probado) |
| **3** | **Tope por trade $200** (`max_risk_dollars_per_order`) | Pre-check en gate de `PracticeExecutionAdapter`: si riesgo $> \$200 \to$ reduce a 1 micro; si ni a 1 micro cabe $\to$ veta `MAX_RISK_PER_ORDER`. | **PASS** (Simulado y probado) |
| **4** | **Fondo acumulado -$1.000** (`max_drawdown_usd`) | Detección de brecha de drawdown + `flatten()` + `TOTAL_SHUTDOWN` permanente (no resetea con nuevo día). | **PASS** (Simulado y probado) |
| **5** | **Anti-sobretrading (6 trades/día)** (`max_trades`) | Veto con `MAX_TRADES_REACHED` al alcanzar el límite configurado en el snapshot. | **PASS** (Simulado y probado) |
| **6** | **Seguridad en Vivo** | `LIVE_EXECUTION_ENABLED = False` inalterado en `interfaces.py`. Tests 100% offline sin red. | **PASS** (Verificado) |

---

## 2. Declaración de Honradez Operativa

> [!IMPORTANT]
> **Disciplina Operativa vs Promesa de Retorno:**  
> Estos circuit breakers constituyen **disciplina operativa, gestión de capital y preservación de cuenta**. En los bloques M10 y M11 quedó demostrado empíricamente que las estrategias actuales no presentan una expectativa positiva estadísticamente significativa ($IC95_{lower} \le 0$).  
> Por tanto, el objetivo diario de +\$500 **no es una expectativa ni una promesa de rendimiento**, sino un riel de control estricto para congelar ganancias ante rachas favorables y evitar devolverlas al mercado en la misma sesión (*no devolver lo ganado*). La pérdida diaria de -\$200 y el drawdown acumulado de -\$1.000 previenen la pérdida descontrolada de la cuenta de práctica.

---

## 3. Arquitectura y Componentes Desarrollados

### 3.1. Tipos y Reglas de Cuenta (`src/types.py`)
Se extendió `FundedAccountRules` con campos opcionales numéricos validados en `__post_init__` (manteniendo 100% retrocompatibles todas las suites previas):
- `daily_profit_target_usd: float | None = None`
- `daily_profit_target_pct: float | None = None`
- `daily_loss_limit_usd: float | None = None`
- `max_drawdown_usd: float | None = None`
- `max_risk_dollars_per_order: float | None = None`

Se añadió la función helper `create_practice_rules()` con los parámetros exactos declarados para Practice (\$50.000 inicial, +\$500 target, -\$200 pérdida diaria, -\$1.000 drawdown, \$200 riesgo por trade, 6 trades).

### 3.2. Contratos Canónicos (`src/realtime/events.py`)
- Se definieron los motivos canónicos:
  - `REASON_DAILY_PROFIT_TARGET = "DAILY_PROFIT_TARGET_REACHED"`
  - `REASON_MAX_RISK_PER_ORDER = "MAX_RISK_PER_ORDER"`
- Se enriqueció `OrderIntent` con campos opcionales validados para pricing y sizing:
  - `size: int = 1`
  - `entry_price: float | None = None`
  - `stop_price: float | None = None`
  - `target_price: float | None = None`
  - `dollars_per_point: float | None = None`

### 3.3. Motor de Riesgo (`src/realtime/risk.py`)
En `AccountAwareRiskEngine`:
- Inclusión del chequeo `_daily_profit_target_reached(snap)` evaluando si `equity >= sod + target_usd` (o target porcentual).
- **Enclavamiento (*latching*):** Al tocar el objetivo diario, se activa `self._daily_profit_reached = True`, impidiendo nuevas operaciones aunque el equity fluctúe posteriormente dentro de la misma fecha UTC.
- **Reseteo diario automático:** Al cruzar la medianoche UTC (`day > self._start_of_day_date`), el enclavamiento se resetea automáticamente y se toma el nuevo balance de inicio del día.
- Soporte directo para límites en dólares (`daily_loss_limit_usd`, `max_drawdown_usd`).

### 3.4. Adapter de Práctica y Gate de Riesgo (`src/realtime/practice_adapter.py`)
Se implementó `PracticeExecutionAdapter(ExecutionAdapter)`:
- En cumplimiento del contrato RT-0, hereda de `ExecutionAdapter` a través del método sellado `submit`.
- **Pre-check de riesgo por orden:**
  $$\text{riesgo} = |\text{entry} - \text{stop}| \times \$/\text{pt} \times \text{size}$$
  - Si $\text{riesgo} > \text{max\_risk\_dollars\_per\_order}$:
    - Si el riesgo a 1 micro $\le \$200$: reduce el tamaño a 1 micro (`REDUCED_TO_1_MICRO`), lo anota en la razón de ejecución y ejecuta la orden por 1 micro.
    - Si el riesgo a 1 micro $> \$200$: veta la orden emitiendo `ExecutionReport(status=EXEC_REJECTED, reason="MAX_RISK_PER_ORDER: ...")`.
- **Flatten:** Mantiene tracking de posición neta por símbolo y expone `flatten()`, que pone a cero la posición y dispara callbacks operacionales.
- **Allowlist:** Rechaza fail-closed cualquier cuenta no presente en `PRACTICE_ACCOUNT_ALLOWLIST`.

### 3.5. Circuit Breaker de Sesión (`src/realtime/circuit_breaker.py`)
Se implementó `SessionCircuitBreaker`:
- **Máquina de estados:** `ACTIVE`, `HALTED_DAILY`, `TOTAL_SHUTDOWN`.
- **Reacción a `DAILY_LOSS_LIMIT` (-$200):** Invoca `adapter.flatten()` exactamente una vez, cambia a `HALTED_DAILY` y registra un `SystemEvent(kind=SYSTEM_CIRCUIT_BREAKER)`.
- **Reacción a `DAILY_PROFIT_TARGET_REACHED` (+$500):** Invoca `adapter.flatten()` (si hay posición abierta), congela el trading por hoy (`HALTED_DAILY`) y registra el evento de auditoría.
- **Reacción a `DRAWDOWN_BUFFER_TOO_LOW` (-$1.000):** Invoca `adapter.flatten()` y pasa a `TOTAL_SHUTDOWN`.
- **Rollover de día UTC:** Detecta cambio de fecha; si estaba en `HALTED_DAILY`, reactiva la sesión a `ACTIVE`. Si estaba en `TOTAL_SHUTDOWN`, **permanece apagado**.

---

## 4. Evidencia de Pruebas y Resultados de la Suite

Se construyó la suite específica `tests/realtime/test_circuit_breakers.py` con 9 pruebas rigurosas, y se ampliaron las pruebas de `tests/realtime/test_risk_engine.py`.

### 4.1. Conteo de Tests Declarado
- **`tests/realtime/test_circuit_breakers.py`:** **9 PASSED** (0 failed)
- **`tests/realtime/test_risk_engine.py`:** **39 PASSED** (0 failed)
- **Suite Completa de Tiempo Real (`tests/realtime/`):** **249 PASSED** (0 failed, 2.45s)
- **Suite Core FARS (`tests/test_account.py` + `tests/test_engine.py`):** **96 PASSED** (0 failed)
- **Total acumulado verificado:** **345 tests en verde**.

### 4.2. Desglose de Escenarios Clave Verificados
1. **Día que toca -$200:**
   - Cuenta inicia en \$50.000, abre 1 micro MNQ (stop 50 pts).
   - Equity cae a \$49.800 (-$200).
   - `AccountAwareRiskEngine` emite `DAILY_LOSS_LIMIT`.
   - `SessionCircuitBreaker` ejecuta `flatten()`, posición vuelve a 0, `flatten_count = 1`.
   - Estado pasa a `HALTED_DAILY`. Nuevas intenciones son rechazadas.
2. **Día que toca +$500:**
   - Cuenta con posición abierta ve su equity subir a \$50.500 (+250 pts).
   - `AccountAwareRiskEngine` emite `DAILY_PROFIT_TARGET_REACHED`.
   - `SessionCircuitBreaker` ejecuta `flatten()`, congela ganancias y para el trading por hoy.
   - Intento de reentrada posterior con pullback a \$50.450 es denegado por enclavamiento (*latched*).
3. **Orden que excede $200 pero cabe a 1 micro:**
   - Orden de 3 contratos MNQ con 50 pts de stop $\to$ riesgo = $50 \times 2 \times 3 = \$300 > \$200$.
   - A 1 micro: riesgo = $50 \times 2 \times 1 = \$100 \le \$200$.
   - Adapter reduce automáticamente a 1 micro, emite fill con nota `REDUCED_TO_1_MICRO` y posición neta queda en 1.
4. **Orden que excede $200 incluso a 1 micro:**
   - Orden con 150 pts de stop $\to$ riesgo = $150 \times 2 \times 1 = \$300 > \$200$.
   - Ni a 1 micro cabe: adapter rechaza con `EXEC_REJECTED` y motivo `MAX_RISK_PER_ORDER`. Posición se mantiene en 0.
5. **Drawdown acumulado -$1.000:**
   - Equity desciende a \$49.000.
   - Disparo de `TOTAL_SHUTDOWN` con `flatten()`.
   - Al simular el cambio de día UTC, el estado `TOTAL_SHUTDOWN` no se desbloquea.
6. **Rollover de día:**
   - Al pasar de fecha UTC tras un `HALTED_DAILY`, el circuit breaker vuelve a `ACTIVE` y autoriza nuevas operaciones para el nuevo día.

---

## 5. Artefactos del Bloque

Todos los entregables residen en `lab_artifacts/circuit_breakers_protocol/`:
1. `preregistro.json`: Declaración formal y congelada de los límites de Practice.
2. `INFORME.md`: Este informe formal de cierre.
3. `BLOCKERS.md`: Registro de bloqueos técnicos (0 bloqueos).
4. `manifest.json`: Manifiesto criptográfico SHA-256 de los archivos del protocolo.

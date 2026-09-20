# BLOCKERS — Bloque RT-9 (§4-bis): Circuit Breakers de Sesión

> **Fecha:** 2026-09-20  
> **Rama:** `bloque-circuit-breakers-sesion`  
> **Estado:** **0 BLOQUEOS TÉCNICOS**  

---

## 1. Estado de Bloqueos

- **Bloqueos técnicos:** **0**  
  Todos los límites declarados (§4-bis RT-9) fueron incorporados de forma limpia, determinista y fail-closed:
  1. `DAILY_PROFIT_TARGET_REACHED` (+ enclavamiento y reseteo diario).
  2. `DAILY_LOSS_LIMIT` (+ acción de flatten y halt hasta el día siguiente).
  3. Pre-check de riesgo máximo por orden (\$200) en adapter con reducción a 1 micro o veto `MAX_RISK_PER_ORDER`.
  4. Fondo acumulado (-\$1.000) con `TOTAL_SHUTDOWN` permanente.
  5. Anti-sobretrading (6 trades por día) vía `MAX_TRADES_REACHED`.
  6. Preservación estricta de `LIVE_EXECUTION_ENABLED = False`.

- **Bloqueos operacionales:** **0**  
  Todas las pruebas se ejecutaron offline con relojes congelados, eventos canónicos simulados y adaptadores de práctica. Cero llamadas de red.

---

## 2. Decisiones de Diseño y Restricciones Documentadas

1. **Enclavamiento del Objetivo Diario (*Daily Profit Target Latch*):**  
   Al tocar +\$500 en equity intradiaria, el motor de riesgo activa un flag interno de enclavamiento. Si el equity posteriormente fluctúa a la baja (p. ej. por comisiones o fluctuación de mark-to-market previa al flatten), el motor **continúa denegando órdenes** por el resto del día UTC. Esto cumple la premisa fundamental de *no devolver lo ganado*. El enclavamiento se resetea automáticamente en el primer snapshot del nuevo día UTC.

2. **Aplanado (*Flatten*) Exactamente UNA Vez:**  
   El `SessionCircuitBreaker` ejecuta `adapter.flatten()` en la primera ocurrencia del evento de corte diario o brecha de drawdown. Una vez la posición ha sido aplanada y la sesión pasa a `HALTED_DAILY` o `TOTAL_SHUTDOWN`, no se generan órdenes redundantes de cierre.

3. **Invarianza de Cuentas Reales:**  
   Los perfiles estándar de cuentas de fondeo de evaluación en `src/` no sufrieron alteraciones. Para la cuenta Practice se proporciona la función declarada `create_practice_rules()`, manteniendo las reglas aisladas y declaradas en el preregistro.

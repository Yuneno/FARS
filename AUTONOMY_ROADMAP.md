# AUTONOMY_ROADMAP.md — De sistema de análisis a componente de decisión autónoma

Este documento declara el objetivo final de FARS y hace un inventario honesto de
qué existe hoy, qué falta, y qué debe cumplirse antes de que sea seguro delegar
ejecución autónoma de trades a un bot alimentado por FARS.

## Objetivo final declarado (a mediano/largo plazo)

Un bot de ejecución se alimentará de FARS — métricas de riesgo, resultados de
bootstrap, reglas de cuentas fondeadas y decisiones del risk engine — para
operar trades de forma **autónoma**, sin humano en el loop en el momento de la
ejecución.

**Estado actual: NO implementado, NO probado, NO seguro de desplegar.**

FARS hoy es un sistema de análisis de riesgo más una capa realtime
fail-closed de *autorización* (veto), no un sistema que *decida ejecutar*. Este
objetivo cambia los requisitos de seguridad, testing y auditoría: de "sistema de
análisis" a "componente de decisión de un sistema autónomo de ejecución".

## Inventario de componentes existentes (con utilidad para el objetivo)

| Componente | Módulo real | Estado | Nota |
|---|---|---|---|
| Motor de simulación (Monte Carlo) | `src/monte_carlo.py` | DONE | P(PASS), drawdown, VaR/CVaR |
| Optimización de riesgo + FRES | `src/optimization.py` | DONE | `optimize_risk_per_trade` |
| Bootstrap (incertidumbre) | `src/bootstrap.py` | DONE | CIs de expectancy/win-rate/std |
| Reglas de cuenta fondeada | `src/types.py` (`FundedAccountRules`), `src/funded_rules_v2.py` | DONE | Reglas genéricas v2 + engine |
| Caminos probabilísticos de cuenta | `src/probabilistic_paths.py` | DONE | Paths con riesgo monetario |
| Risk engine realtime fail-closed | `src/realtime/risk.py` (`AccountAwareRiskEngine`) | DONE | Autoriza/veta; estado desconocido → deny |
| Circuit breakers | `src/realtime/risk.py` (`REASON_CIRCUIT`), `src/realtime/acceptance.py` | DONE | Veto independiente ante breach |
| Paper trading (ejecución simulada) | `src/realtime/paper.py` (`PaperExecutionAdapter`) | DONE | Fills simulados, no equivalentes a live |
| Pipeline de replay/validación | `src/realtime/replay.py`, `session.py`, `acceptance.py` | DONE | RT-0 a RT-8 |
| Backtest histórico AMD+CRT | `src/backtest/amd_crt.py`, `executor.py` | DESCARTADA | AMD+CRT queda retirada como hipótesis de estrategia: el reporte multi-mercado original obtuvo expectancy negativa y PF < 1 con costes reales en MNQ, MYM y MGC; el [meta-labeling](docs/refactor/metalabel-result.md) obtuvo ROC AUC ≈ 0.523 y ninguna feature causal predijo el resultado; y la [validación MES/MYM](docs/refactor/task-07-cross-market-validation.md) obtuvo expectancy OOS ≤ 0 incluso en el límite superior teórico sin costes, con IC bootstrap Circular Block que incluyen cero en ambos mercados y sin forzar IID. El framework de validación —executor, bootstrap, meta-labeling y manifest reproducible— queda como activo reutilizable para la siguiente estrategia candidata. |
| Decision log (para meta-labeling) | `src/backtest/amd_crt.py` (`AmdCrtDecision`) | IN PROGRESS | Registra candidaturas + rechazos |
| Ejecución live | `LIVE_EXECUTION_ENABLED = False` | NOT STARTED (bloqueado) | No hay órdenes live |

## Qué falta antes de autonomía real de ejecución

Nada de esto se considera satisfecho hoy. Cada ítem es un gate duro.

### 1. Edge estadístico confirmado out-of-sample

- **Estado: NOT STARTED.** La estrategia candidata (AMD+CRT, con/sin EMA10/4h)
  tiene expectancy negativa en todos los datasets probados (extended y Databento).
  Sin edge demostrado out-of-sample, no hay base para ejecutar autónomamente.
- Gate: expectancy > 0 con IC de bootstrap que excluya 0, validado en walk-forward
  cronológico sin filtración, sobre data NO usada en el desarrollo.

### 2. Meta-labeling / filtro de calidad de señal

- **Estado: IN PROGRESS (temprano).** El decision log (Tarea 3) ya registra
  candidaturas y rechazos con variables causales. Falta: unir con resultados
  (r_result), entrenar regresión logística → XGBoost, calibrar y validar
  cronológicamente.
- Gate: el clasificador debe aportar edge medible y estable out-of-sample antes
  de usarse para decidir.

### 3. Periodo mínimo de paper trading exitoso

- **Estado: NOT STARTED.** Paper trading (RT-7) existe, pero no se ha corrido un
  periodo de validación en paper con la estrategia candidata.
- Gate: N semanas/meses de paper trading con métricas consistentes con el
  backtest (sin divergencia material), documentado y auditado.

### 4. Límites de tamaño de posición

- **Estado: IN PROGRESS (parcial).** El risk engine modela reglas y sizing, y
  `RiskSizingPolicy` existe en `probabilistic_paths.py`, pero no hay un límite
  duro de posición por símbolo/cuenta para ejecución autónoma.
- Gate: sizing explícito, con techo por posición y por cuenta, aplicado en el
  risk engine y en el bot.

### 5. Kill-switch accesible a un humano

- **Estado: NOT STARTED.** No existe un mecanismo de apagado manual verificado
  desde fuera del proceso de ejecución.
- Gate: kill-switch que detenga la ejecución de forma inmediata, probado en
  replay y en producción seca.

### 6. Alertas de circuit breaker a un humano

- **Estado: NOT STARTED.** Los circuit breakers vetaran, pero no hay
  notificación a un humano cuando se disparan.
- Gate: alerta (telegram/email/otro) en cada evento de circuit breaker / halt /
  reconciliación, con confirmación de recepción.

### 7. Auditoría de logs de decisiones

- **Estado: IN PROGRESS (parcial).** El decision log registra decisiones de la
  estrategia, y el risk engine registra decisiones de autorización. No hay aún
  un log unificado inmutable con todas las decisiones (estrategia → riesgo →
  ejecución) para auditoría.
- Gate: log de decisiones completo, con timestamps, hash de integridad, y
  retención mínima, para reconstruir por qué se tomó cada acción.

### 8. Testing y garantías específicas de sistema autónomo

- **Estado: NOT STARTED.** Los tests actuales cubren correctitud estadística y
  el pipeline replay, pero no las garantías propias de un sistema autónomo
  (fail-safe ante pérdida de conexión, reintentos idempotentes, manejo de
  órdenes parciales, recuperación tras crash).
- Gate: suite de tests de seguridad/recuperación antes de habilitar autonomía.

## Regla de honestidad de estado

Ningún componente se marca DONE salvo que esté implementado Y probado para el
objetivo de autonomía. "DONE" en la tabla de componentes existentes significa
que existe y funciona para su propósito actual (análisis/validación), NO que
sea apto para ejecución autónoma. El estado del *objetivo final* es
NOT STARTED hasta que todos los gates de arriba se cumplan.

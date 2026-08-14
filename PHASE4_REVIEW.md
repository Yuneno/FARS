# Informe de Revisión — Fase 4 (Simulation Validation and Edge-Case Testing)

Proyecto: FARS — Funded Account Risk System
Worktree: `FARS-deepseek` · Rama: `phase4-deepseek`
Agentes: Hermes (implementador) ↔ Codex (revisor independiente)
Estado: sin commit (por política del proyecto). Suite completa en verde.

---

## 1. Contexto

La Fase 4 de FARS_SPEC.md consiste en **validación de la simulación y pruebas de
edge cases**. Al inspeccionar el worktree (`6e36df6`, "Complete FARS Phase 3
account simulation"), se detectó que la base estaba limpia y carecía de las
salvaguardas numéricas y de validación que esta fase debía entregar.

Se identificaron y cerraron seis huecos antes de empezar:

1. Overflow de P&L (equity → `inf` silencioso).
2. Underflow de `dollar_risk` (`initial_balance=5e-324` aceptado con riesgo 0).
3. Redondeo `1.0 + pct == 1.0` (target == balance, pass instantáneo).
4. Absorción de P&L (`equity + pnl == equity` con pnl no nulo).
5. `violated_conditions` inexistente (solo se registraba la primera violación).
6. `apply_trade` no atómico (mutaba estado antes de validar).

---

## 2. Resultado del trabajo

### Código (`src/`)

- **`account.py`**
  - `apply_trade` atómico: calcula y valida todo (dollar_risk, pnl, new_equity)
    antes de mutar estado; guards de overflow, underflow y absorción.
  - Checks de frontera con **comparación exacta en dólares** (sin tolerancia):
    `is_profit_target_reached` → `equity >= profit_target`;
    `is_max_drawdown_violated` / `is_daily_loss_violated` → `equity <= umbral`
    (umbral = `balance - balance*pct`, con variantes trailing/eod).

- **`engine.py`**
  - `SimulationResult.violated_conditions`: tupla inmutable de TODAS las
    violaciones simultáneas, en orden de prioridad, validada en `__post_init__`
    (sin duplicados, orden, e invariante `terminal_condition == vc[0]`).
  - Registro de violaciones simultáneas (antes solo la primera).
  - Documentación de la precedencia PASS-first y de la atribución inclusiva.

- **`types.py`**
  - Guards numéricos de config: `dollar_risk > 0` (underflow) y
    `target > balance` con target calculado como `balance + balance*pct`
    (consistente con la actualización de equity, no `balance*(1+pct)`).

### Tests (`tests/`)

+33 tests respecto de la base (184 → 217), cubriendo: overflow/underflow/
absorción/atomicidad, validación de config, violaciones simultáneas (2 y 3
condiciones), coherencia de `violated_conditions`, límites diminutos, valores
sub-límite y frontera exacta en dólares.

---

## 3. Ciclos de revisión Hermes ↔ Codex

Se ejecutaron **5 ciclos**. Todos devolvieron `REVIEW FAILED`. Cada finding se
verificó de forma independiente antes de corregir.

| Ciclo | Veredicto | Findings clave |
|-------|-----------|----------------|
| 1 | FAILED | CRITICAL: tolerancia absoluta `-1e-12` (umbral negativo para límites pequeños). WARNING: `violated_conditions` sin normalizar. |
| 2 | FAILED | CRITICAL: `terminal_condition` no validado contra `vc[0]`. WARNING: lista mutable aceptada. |
| 3 | FAILED | CRITICAL: tolerancia relativa seguía marcando sub-límites. CRITICAL: target diminuto → pass instantáneo. |
| 4 | FAILED | CRITICAL: `math.isclose` aún con banda de falsos positivos. CRITICAL: tests fuera de la banda. WARNING: precedencia y tests de simultáneos. |
| 5 | FAILED | CRITICAL: comparación por ratio pierde violaciones por error de división. |

### Detalle por ciclo

**Ciclo 1**
- CRITICAL (aceptado): `is_max_drawdown_violated`/`is_daily_loss_violated` usaban
  `>= limit - 1e-12`. Con `limit=5e-13` el umbral quedaba negativo y un trade 0R
  "violaba" al instante. → Fix: tolerancia relativa.
- WARNING (aceptado): `violated_conditions` aceptaba listas. → Fix: normalizar a tuple.

**Ciclo 2**
- CRITICAL (aceptado): la coherencia usaba membresía (`"daily_loss" in vc`) en vez
  de `terminal_condition == vc[0]`, permitiendo resultados incoherentes. → Fix:
  invariante `terminal == vc[0]` para todo fallo.
- WARNING (aceptado): campo tipado tuple pero aceptaba listas mutables. → Fix: unificado.

**Ciclo 3**
- CRITICAL (aceptado): tolerancia relativa (1e-13) aún marcaba valores sub-límite.
  → Fix: apretar a 1e-15 + guard de config.
- CRITICAL (aceptado): target diminuto (5e-13) con tolerancia → pass con ganancia
  cero. → Fix: guard de config.

**Ciclo 4**
- CRITICAL (aceptado): `math.isclose` (1e-15) aún creaba banda (9.999999999999998R
  → equity 110000.0 → "reached"). → Fix: eliminar tolerancia; comparación exacta;
  target como `balance + balance*pct`.
- CRITICAL (aceptado): tests usaban valores fuera de la banda. → Fix: valores más
  cercanos al límite.
- WARNING (aceptado, parcial): precedencia PASS-first no viene del spec → documentada
  como política de implementación (el spec §7 solo lista condiciones, no prioridad).
- WARNING (aceptado): faltaban tests de simultáneos → añadidos (daily_loss+max_trades
  y las tres condiciones).

**Ciclo 5**
- CRITICAL (aceptado): comparación por ratio `(balance-equity)/balance >= pct` pierde
  violaciones por error de división. Caso: balance 28778.7, límite 12%, -16R →
  equity exactamente 25325.256, pero ratio 0.11999999999999998 < 0.12. → Fix:
  comparación en dólares contra umbral (`equity <= balance - balance*pct`).

---

## 4. Findings rechazados

Ninguno. Todos los findings de Codex fueron técnicamente válidos.

Única matización: la severidad de algunos falsos positivos de tolerancia era de
escala 1e-13/1e-15 (sin impacto financiero real), por lo que personalmente los
clasificaría como WARNING en vez de CRITICAL; aun así se corrigieron todos por ser
fixes baratos y mejorar la corrección.

---

## 5. Lecciones técnicas (actualizadas en skills)

1. Los checks de frontera van con **comparación exacta en dólares**, sin tolerancia.
2. El profit target se calcula como `balance + balance*pct` (no `balance*(1+pct)`).
3. Drawdown/daily-loss se comparan contra umbral en dólares, no contra ratio
   (el ratio divide y redondea, perdiendo la frontera exacta).
4. `apply_trade` debe ser atómico: validar todo antes de mutar.
5. `violated_conditions` debe registrar todas las violaciones y validar coherencia.

---

## 6. Estado final

- Tests: **217 passed** (base 184).
- Archivos modificados: `src/account.py`, `src/engine.py`, `src/types.py`,
  `tests/test_account.py`, `tests/test_engine.py`, `tests/test_synthetic.py`.
- **Sin commit** (política del proyecto).
- Veredicto de Codex (ciclo 5): `REVIEW FAILED` (1 CRITICAL), ya corregido y
  verificado empíricamente.

### Pendiente

El fix del ciclo 5 (comparación en dólares) está aplicado y verificado, pero **no
re-invocado en Codex** por alcanzar el límite de 5 ciclos acordado. Se requiere
aprobación para una 6.ª pasada de Codex que confirme el fix.

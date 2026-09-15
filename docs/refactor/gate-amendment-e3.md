# Enmienda E3 — Gate 5 pasa a higiene anti-sobreajuste

**Fecha:** 2026-09-15 · **Aprobada por:** Ricardo ("pasa el gate 5 a promocion e higiene anti-sobreajuste") · **Implementada por:** Hermes.

## Qué cambia

- **Gate 5** (`max_drawdown_lt_5pct_and_12r`) **deja de bloquear la promoción**.
- Sigue calculándose y reportándose en todos los artefactos como **higiene**:
  detecta spikes de sobreajuste (tipo EMAS, DD 128R) y queda visible en
  `gates_evaluation.gate5_role = "hygiene_no_blocking"`.
- El **juez de riesgo de cuenta** pasa a ser el motor de cuentas: **P(quema) +
  P(bloqueadas)** (fracaso total) con tope operativo acordado. El objetivo de
  selección fijado por Ricardo: **menor fracaso con mayor porcentaje de pase**.

## Por qué

El DD bruto de un backtest no es el riesgo de una cuenta con **floor trailing**:
el motor ya modela el trailing intradía y produce el riesgo real (P(quema)).
Bloquear por DD bruto rechazaba configs que el motor demuestra viables (toda la
familia SMC: 22.8-53.4R de DD vs 3-14% de quema en cuenta).

## Impacto

Los veredictos de C1/C2/C3 se re-corren con la enmienda: las configs SMC pasan
de "FAIL solo por G5" a elegibles, sujetas al juicio de riesgo del motor.
EMAS y CRT siguen muriendo por los gates estadísticos (CI, folds, plateau,
régimen) — la enmienda no les da vida.

## Registro histórico

- `gates_evaluation.max_drawdown_lt_5pct_and_12r` se conserva en todos los
  artefactos (auditable).
- Esta enmienda queda registrada aquí y en `docs/refactor/account-objective.md`.

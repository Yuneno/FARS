# EXPLORATION_REPORT_PHASES.md

Exploración independiente (DeepSeek + Hermes) sobre Phase 11A, 11B y 11C.

**Rama de exploración:** `explore/phases-night`
**Rama principal (intacta):** `agent/phases-night`
**Alcance:** solo 11A/11B/11C. No se tocó 11D, RT, ProjectX, SMC-FVG, Phases 12–14 ni `.env`.

---

## 1. Resumen

Se revisaron los temas propuestos (precisión numérica, casos extremos estadísticos,
inmutabilidad profunda, provenance/reproducibilidad, `not_evaluable`, fronteras de
sesión/calendario, contratos restrictivos y capacidades no aprovechadas). El hallazgo
más concreto es el defecto WARNING que Codex dejó pendiente (sobre-rechazo de horizonte
parcial); se corrigió experimentalmente. No se encontró otro defecto objetivo nuevo.

## 2. Experimento realizado

### Experiment 1 — Aceptar bloque final parcial en la validación de frontera de sesión

- **Objetivo:** resolver el WARNING de Codex (ronda 3): `_validate_inputs` calculaba el
  último timestamp de cada bloque en el minuto `trades_per_day - 1`, de modo que un
  bloque final parcial (`max_trades % trades_per_day != 0`) era rechazado como cruce de
  sesión aunque su último trade real ocurre antes.
- **Reproducción (antes):** `max_trades=1, trades_per_day=2, start_at=23:59 UTC,
  boundary=00:00` → `PathAnalysisError` sobre una ejecución válida de un solo trade a
  las 23:59.
- **Cambio:** el bloque final usa `(max_trades - 1) % trades_per_day` como último minuto;
  los bloques completos usan `trades_per_day - 1`.
- **Test:** `test_run_accepts_partial_last_block_that_stays_within_one_session`
  (RED→GREEN: antes lanzaba excepción, después corre 32 paths).
- **Verificación:** suite 11C = 21 passed; suite determinista completa = **915 passed,
  10 deselected** (sin regresiones).
- **Commit:** `a531825`.

## 3. Oportunidades documentadas (NO implementadas)

### 3.1 Inmutabilidad profunda inconsistente en 11B
- `RuleEvaluationEvent.details` (`src/funded_rules_v2.py:597`) usa `_immutable_mapping`
  (superficial), a diferencia del deep-freeze aplicado a 11A (`metadata`) y 11C
  (`provenance`). Hoy sus valores son escalares/tuplas, por lo que **no es un defecto
  activo**; es un gap latente de consistencia. **Clasificación:** oportunidad de
  crecimiento ignorada (baja prioridad).

### 3.2 Contrato público de serialización
- No existe `to_dict`/serializador probado; `dataclasses.asdict` falla sobre
  `mappingproxy`. La spec no exige JSON directo en estas fases. **Clasificación:**
  preferencia de diseño (no requisito). Queda como deuda.

### 3.3 Precisión numérica y casos extremos estadísticos
- No se halló un defecto objetivo nuevo. Los cálculos Decimal (11A/11B) y las rutas
  IID/CBB, Wilson, censoring y sensibilidad OOS (11C) ya fueron auditados por Codex en
  las rondas previas sin hallazgos adicionales.

## 4. Confirmaciones de alcance

- Phase 11D, RT, ProjectX, SMC-FVG y Phases 12–14 **intactos** (el experimento solo
  toca `src/probabilistic_paths.py` y `tests/test_probabilistic_paths.py`).
- No se modificó `.env`; no hubo push/merge/rebase/reset/clean.

*Fin del reporte de exploración.*

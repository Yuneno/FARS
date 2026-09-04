# PHASES_INTEGRATION_DECISION.md

Decisión de integración de los experimentos de `explore/phases-night` hacia
`agent/phases-night`.

Decisiones posibles: **INTEGRAR** · **INTEGRAR_CON_CAMBIOS** ·
**MANTENER_EXPERIMENTAL** · **RECHAZAR**.

---

## Experiment 1 — Aceptar bloque final parcial en la validación de frontera de sesión

- **Commit:** `a531825` (experimental, en `explore/phases-night`)
- **Decisión:** **INTEGRAR**

### Beneficios
- Elimina el sobre-rechazo (WARNING de Codex ronda 3) de configuraciones válidas con
  último bloque parcial (`max_trades % trades_per_day != 0`), p.ej. un horizonte de un
  solo trade a las 23:59.
- No debilita la protección real: los bloques completos y el último trade real de un
  bloque parcial siguen validándose contra la frontera de sesión del perfil.

### Riesgos
- Bajo. Cambia solo el cálculo del último minuto del bloque final. No altera la
  generación de timestamps del scheduler ni la semántica de sesión.

### Evidencia
- Reproducción del rechazo incorrecto antes del cambio; ejecución correcta después.
- Test de regresión `test_run_accepts_partial_last_block_that_stays_within_one_session`
  (RED→GREEN).

### Tests
- `tests/test_probabilistic_paths.py`: 21 passed.
- Suite determinista completa: 915 passed, 10 deselected.

### Cherry-pick sugerido (NO ejecutar — decisión humana)
```bash
# desde Documents/FARS-phases, en la rama agent/phases-night
git cherry-pick a531825
```

---

## Otros ítems (no son experimentos de código)

| Ítem | Decisión | Nota |
|---|---|---|
| Fixes ya integrados (5851198, 82567a9, e2a37fd, e0cf2d9) | INTEGRAR (ya en `agent/phases-night`) | verificados por Codex ronda 3 |
| Serialización pública (`asdict`/`mappingproxy`) | RECHAZAR (por ahora) | preferencia de diseño; la spec no la exige |
| Deep-freeze de `RuleEvaluationEvent.details` (11B) | MANTENER_EXPERIMENTAL | gap latente, sin defecto activo hoy |

---

*Fin de la decisión de integración.*

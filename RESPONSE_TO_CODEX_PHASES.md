# RESPONSE_TO_CODEX_PHASES.md

Clasificación de cada observación de Codex (rondas 1–3) sobre las fases 11A/11B/11C,
y la respuesta de Hermes/DeepSeek a cada una.

Clasificación usada: **Defecto demostrado** · **Restricción válida** ·
**Restricción excesiva** · **Preferencia de diseño** · **Oportunidad de crecimiento ignorada**.

---

## Observaciones de Codex y su clasificación

### R1-W1 — Perfil deshabilitado corre profit-target; `not_evaluable` pierde ante `pass_blocked`
- **Clasificación:** Defecto demostrado.
- **Estado:** YA corregido en el commit base (`7905705`, `apply()` retorna temprano un
  único evento `not_evaluable`/`profile`). Verificado con reproducción directa.
- **Respuesta:** sin cambios adicionales (no había defecto residual).

### R1-W2 — Perfiles deshabilitados mutan balance/watermark/trading-days
- **Clasificación:** Defecto demostrado.
- **Estado:** YA corregido (mismo commit; el camino deshabilitado no muta estado).
- **Respuesta:** sin cambios adicionales.

### R1-CRITICAL — Stamps falsos de "review passed" en SPEC/README
- **Clasificación:** Defecto demostrado (de documentación).
- **Estado:** YA corregido en el SPEC vivo ("independent review pending").
- **Respuesta:** se conserva `CODE_AVANCE_REVIEW.md` como artefacto histórico.

### R1-HIGH — Schedule cruza la frontera y produce PASS falso
- **Clasificación:** Defecto demostrado.
- **Estado:** corregido en `5851198` (guard de día calendario), luego **mejorado** en
  `e2a37fd` (guard por frontera de sesión del perfil).
- **Respuesta:** implementado y verificado (ronda 3).

### R1-MEDIUM — `provenance` 11C congelado pero mutable (anidado)
- **Clasificación:** Defecto demostrado.
- **Estado:** corregido en `82567a9` (`_deep_immutable` recursivo).
- **Respuesta:** implementado y verificado (ronda 3).

### R2-CRITICAL — El guard de día calendario no cubre la frontera de sesión del perfil
- **Clasificación:** Defecto demostrado.
- **Estado:** corregido en `e2a37fd` (validación por `_session_date` en `_validate_inputs`).
- **Respuesta:** implementado y verificado (ronda 3).

### R2-WARNING — `metadata` de 11A congelado pero mutable (anidado)
- **Clasificación:** Defecto demostrado.
- **Estado:** corregido en `e0cf2d9` (`_deep_immutable` en `account_data.py`).
- **Respuesta:** implementado y verificado (ronda 3).

### R2-SUGGESTION / R3 — Serialización pública (`asdict`/`mappingproxy`)
- **Clasificación:** Preferencia de diseño / oportunidad de crecimiento (NO defecto).
- **Estado:** no implementada. La spec no exige JSON directo para estas fases.
- **Respuesta:** queda como deuda documentada; no se convirtió en requisito.

### R3-WARNING — Rechazo incorrecto de horizonte parcial (`max_trades < trades_per_day`)
- **Clasificación:** Defecto demostrado (sobre-rechazo de configuraciones válidas).
- **Estado:** dejado pendiente en `agent/phases-night` (por instrucción, no se abrió
  tercer ciclo). **Corregido experimentalmente** en `explore/phases-night`
  (`a531825`), con test RED→GREEN, esperando decisión de integración.
- **Respuesta:** ver `PHASES_INTEGRATION_DECISION.md` → `INTEGRAR`.

### R1-WARNING — Reporte/entorno: HEAD, archivo no rastreado, suite no corrible
- **Clasificación:** Restricción válida (del entorno, no del código).
- **Estado:** el sandbox read-only de Codex no provee directorio temporal escribible;
  por eso la suite completa con `tmp_path`/Matplotlib no corrió dentro del sandbox. En
  la máquina anfitriona la suite determinista completa pasa (914→915). Documentado.

---

## Resumen

Todas las observaciones clasificadas como **defecto demostrado** fueron corregidas y
verificadas (rondas 1–3), salvo el sobre-rechazo de horizonte parcial, corregido
experimentalmente en `explore/phases-night` y pendiente de integración. La sugerencia de
serialización se trató como preferencia de diseño (no requisito), conforme a lo pedido.

*Fin de la respuesta a Codex.*

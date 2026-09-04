# NIGHT_REPORT_PHASES.md

Fases estadísticas 11A / 11B / 11C — revisión nocturna + 2 ciclos de corrección.

**Fecha:** 2026-09-03
**Agente:** Hermes (implementación).
**Worktree:** `/Users/ricardomedina/Documents/FARS-phases`
**Rama:** `agent/phases-night`

---

## 1. Commit base

- **Commit base de revisión (para Codex):** `c201aed` — `Separate bootstrap result
  schema version`, padre de `9bdc999` (anterior a la implementación 11A/11B/11C). El
  diff `c201aed..HEAD` restringido a archivos de fase es lo que Codex revisó.
- HEAD actual: `e0cf2d9` (ver commits abajo). Partió de `27c8972`.
- Repositorio original (`Documents/FARS`) sin modificar.

## 2. Estado encontrado de cada fase

| Fase | Estado (SPEC) | Implementación | Tests | Resultado |
|---|---|---|---|---|
| 11A | implemented; independent review pending | `src/account_data.py` | `tests/test_account_data.py` | 47 passed |
| 11B | implemented; independent review pending | `src/funded_rules_v2.py`, `src/funded_profiles.py` | `tests/test_funded_rules_v2.py` | 31 passed |
| 11C | implemented; independent review pending | `src/probabilistic_paths.py` | `tests/test_probabilistic_paths.py` | 20 passed |

Los tres subphases declaran **"implemented; independent review pending"** en el SPEC
vivo. No se estampó "review passed" indebidamente.

## 3. Bugs y sospechas

### Sospechas previas DESCARTADAS (sin modificaciones de la noche)

El CRITICAL (stamps de review) y los WARNINGs de `not_evaluable` (prioridad + mutación
de estado en perfiles deshabilitados) **ya estaban corregidos** en el commit base
(`7905705`). Verificado con reproducción directa del perfil `rapid_25k_profile()`
deshabilitado: `primary_event=not_evaluable`, `pass_eligible=False`, estado sin mutar.

### Bugs CONFIRMADOS y corregidos — Ciclo 1 (ronda 1 de Codex → CHANGES_REQUESTED)

**HIGH — Rechazar schedules que cruzan la frontera** (`src/probabilistic_paths.py`)
- `timestamp = start_at + timedelta(days, minutes)` con `trades_per_day=2, start_at=23:59`
  inflaba `trading_days` (2 en vez de 1) → PASS falso con `minimum_trading_days=2`.
- Fix inicial: chequeo de día **calendario** en `PathSimulationConfig`.
- Commit: `5851198`.

**MEDIUM — Frozen result mutaba vía provenance anidado** (`src/probabilistic_paths.py`)
- `_immutable_mapping` solo protegía el mapping exterior; `provenance["rng"]["master_entropy"]`
  mutaba libremente.
- Fix: `_deep_immutable()` recursivo aplicado al `provenance` de 11C.
- Commit: `82567a9`.

### Bugs CONFIRMADOS y corregidos — Ciclo 2 (ronda 2 de Codex → CHANGES_REQUESTED)

**CRITICAL — El guard de día calendario NO cubre la frontera de sesión del perfil**
(`src/probabilistic_paths.py`)
- El engine define el día de trading por `profile.session_timezone` +
  `profile.session_boundary`, no por el día calendario. Reproducción de Codex:
  sesión `17:00`, `start_at=16:59`, `trades_per_day=2` → `trading_days=2`, PASS falso.
- **Fix correcto:** validación en `_validate_inputs` (donde el perfil está disponible)
  usando la propia `_session_date` del engine; rechaza cualquier bloque de un día que
  cruce dos sesiones del perfil, sobre todo el horizonte. Se eliminó el chequeo
  calendario equivocado de `PathSimulationConfig` (sobre-rechazaba sesiones nocturnas).
- Tests nuevos: rechazo en medianoche UTC + rechazo en frontera no-medianoche (17:00).
- Commit: `e2a37fd`.

**WARNING/MEDIUM — Inmutabilidad del `metadata` de 11A superficial**
(`src/account_data.py`)
- `CanonicalAccountTrade` y `AccountEquityEvent` usaban `_immutable_mapping` superficial:
  `metadata["provider"]["sequence"]=999` mutaba un evento congelado.
- **Fix:** `_deep_immutable()` recursivo aplicado al `metadata` de ambos records.
- Test nuevo: mutación anidada lanza `TypeError` y el valor queda intacto.
- Commit: `e0cf2d9`.

### No corregido (fuera de alcance / opcional)

- **SUGGESTION (ronda 2):** no existe contrato público de serialización
  (`dataclasses.asdict` falla sobre `mappingproxy`). Es opcional y la spec no exige JSON
  directo para estas fases → **no implementada** (per instrucción "no implementar
  sugerencias opcionales"). Queda documentada como deuda.

## 4. Commits realizados (código + docs)

| Commit | Contenido |
|---|---|
| `e0cf2d9` | fix(11A): deep-freeze `metadata` en records congelados (ciclo 2) |
| `e2a37fd` | fix(11C): validar schedule contra la frontera de sesión del perfil (ciclo 2) |
| `922c3b1` | docs: reporte tras ronda 1 |
| `82567a9` | fix(11C): deep-freeze `provenance` (ciclo 1) |
| `5851198` | fix(11C): rechazar schedule que cruza la frontera (ciclo 1) |
| `86d8ea2` | docs: reporte inicial |

## 5. Archivos modificados (código desde el base `c201aed`)

- `src/probabilistic_paths.py` — validación de sesión (`_validate_inputs`) + `_deep_immutable`
  + imports (`_session_date`, `time`).
- `tests/test_probabilistic_paths.py` — tests de sesión + provenance.
- `src/account_data.py` — `_deep_immutable` + deep-freeze de `metadata`.
- `tests/test_account_data.py` — test de metadata anidada.
- `NIGHT_REPORT_PHASES.md` — este reporte.
- 11B (`funded_rules_v2.py`) **sin cambios de código** en la noche (ya correcto).

## 6. Tests ejecutados y resultados exactos

Entorno: conda base (Python 3.13.5, numpy 2.1.3, arch 8.0.0, pytest 8.3.4); sin red.

```
/opt/anaconda3/bin/python -m pytest -p no:debugging tests/test_account_data.py -q
/opt/anaconda3/bin/python -m pytest -p no:debugging tests/test_funded_rules_v2.py -q
/opt/anaconda3/bin/python -m pytest -p no:debugging tests/test_probabilistic_paths.py -q
/opt/anaconda3/bin/python -m pytest -p no:debugging -m "not statistical" -q
```

| Comando | Resultado |
|---|---|
| `tests/test_account_data.py` (11A) | **47 passed** |
| `tests/test_funded_rules_v2.py` (11B) | **31 passed** |
| `tests/test_probabilistic_paths.py` (11C) | **20 passed** |
| `-m "not statistical"` (completa, incluye realtime) | **914 passed, 10 deselected** |

Todos los tests nuevos fueron RED (fallaron por la causa correcta) antes del fix y
GREEN después.

## 7. Confirmaciones de alcance

- **Phase 11D permaneció completamente intacta.** No existe módulo de código 11D; su
  sección del SPEC no cambió; `git status` no muestra cambios ahí.
- **No se modificaron RT, ProjectX, SMC-FVG ni Phases 12-14.** Los únicos archivos
  tocados son `probabilistic_paths.py` (11C), `account_data.py` (11A) y sus tests.
- **No hubo push ni merge** (ni rebase, reset ni clean). Repositorio original intacto.

## 8. Estado de la revisión independiente

- **Ronda 1 (Codex):** `CHANGES_REQUESTED` → 1 HIGH + 1 MEDIUM + 1 WARNING. HIGH/MEDIUM
  corregidos (ciclo 1).
- **Ronda 2 (Codex):** `CHANGES_REQUESTED` → 1 CRITICAL (frontera de sesión) + 1 WARNING
  (metadata 11A) + 1 SUGGESTION (serialización, no implementada). CRITICAL y WARNING
  corregidos (ciclo 2).
- **Ronda 3 (Codex, auditoría de verificación):** `OBJECTIVE_DEFECT_REMAINS`. Los 4
  commits de corrección (`5851198`, `82567a9`, `e2a37fd`, `e0cf2d9`) fueron **verificados**
  (tests no tautológicos, RED→GREEN, soluciones correctas). Queda **un defecto objetivo
  nuevo de severidad WARNING** (ver §9), pendiente para decisión humana. Detalle completo
  en `CODEX_REVIEW_PHASES.md`.

## 9. Pendientes, riesgos y decisiones para revisión humana

1. **Defecto objetivo pendiente (WARNING)** — `src/probabilistic_paths.py:460`
   (`_validate_inputs`): la validación de frontera de sesión calcula el último timestamp
   de cada bloque con `trades_per_day - 1` minutos aunque el horizonte tenga menos trades.
   Reproducción: `max_trades=1, trades_per_day=2, start_at=23:59 UTC, boundary=00:00` →
   rechazo incorrecto (`PathAnalysisError`) de una ejecución válida de 1 solo trade a las
   23:59. No abre tercer ciclo de corrección; queda para decisión humana.
2. **Suite estadística NO ejecutada** — bloqueo exacto: requiere
   `pip install --require-hashes -r requirements-acceptance.lock` (descarga de red de
   numpy 2.5.2 / pandas 3.0.5 / scipy 1.18.1 / pytest 9.1.1). No hay `.venv-acceptance`
   ni caché pip local; `python3.12` está sin deps. Sin autorización de Ricardo no se
   instala (acción de red). El verde determinista (914) NO constituye acceptance estadística.
3. **SUGGESTION de serialización** queda como deuda documentada (no implementada).
4. Entorno divergente: dev (3.13.5) != acceptance (3.12.13).

---

*Fin del reporte.*

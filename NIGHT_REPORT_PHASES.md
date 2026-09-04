# NIGHT_REPORT_PHASES.md

Fases estadísticas 11A / 11B / 11C — revisión nocturna + ciclo de corrección.

**Fecha:** 2026-09-03
**Agente:** Hermes (implementación).
**Worktree:** `/Users/ricardomedina/Documents/FARS-phases`
**Rama:** `agent/phases-night`

---

## 1. Commit base

- **Commit base del diff de revisión (para Codex):** `c201aed` —
  `Separate bootstrap result schema version`. Padre de `9bdc999`
  (`Implement Phase 11A account data and Phase 11B rules`): el último commit
  **anterior** a la implementación de 11A/11B/11C. El diff `c201aed..HEAD`
  (restringido a archivos de fase) es lo que Codex revisa.
- **Avance de la rama durante la noche:** HEAD partió de `27c8972` y llegó a
  `86d8ea2` (reporte) → `5851198` (fix HIGH) → `82567a9` (fix MEDIUM).
- Ningún cambio se hizo en el repositorio original (`Documents/FARS`).

## 2. Estado encontrado de cada fase

Todos los subphases declaran **"implemented; independent review pending"** en el SPEC
vivo (`FARS_1_2_PHASES_10B_14_SPEC.md` L184/L272/L394; 10B L132). No hay stamps falsos.

| Fase | Estado | Implementación | Tests | Resultado |
|---|---|---|---|---|
| 11A — Canonical monetary/account-event | implemented, review pending | `src/account_data.py` | `tests/test_account_data.py` | 46 passed |
| 11B — Generic rules engine v2 | implemented, review pending | `src/funded_rules_v2.py`, `src/funded_profiles.py` | `tests/test_funded_rules_v2.py` | 31 passed |
| 11C — Probabilistic paths & trade limits | implemented, review pending | `src/probabilistic_paths.py` | `tests/test_probabilistic_paths.py` | 19 passed |

## 3. Bugs confirmados y sospechas descartadas

### Sospechas previas DESCARTADAS (ya corregidas en `7905705`)

- **CRITICAL (stamps de review)**: el SPEC vivo ahora dice "independent review pending".
- **WARNING #1 (`not_evaluable` prioridad)**: en `apply()`, perfil deshabilitado
  retorna temprano un único evento `not_evaluable`; `primary_event` ya no se ve
  como "casi pasó".
- **WARNING #2 (mutación de estado)**: el camino deshabilitado no muta
  balance/equity/high-watermark/trading-days.

Verificado con reproducción directa (perfil `rapid_25k_profile()` deshabilitado +
trade que alcanza el target 26500): `primary_event=not_evaluable`, `pass_eligible=False`,
estado en `25000/25000/25000`, `trading_days=()`. Y con test
`test_rapid_25k_reference_profile_is_provisional_and_fail_closed` (pasa).

### Bugs CONFIRMADOS y corregidos (ronda 1 de Codex → CHANGES_REQUESTED)

**HIGH — Rechazar schedules que cruzan el boundary de día** (`src/probabilistic_paths.py`)
- `_simulate_one()` generaba `timestamp = start_at + timedelta(days, minutes=minute_index)`.
  Con `trades_per_day=2` y `start_at=23:59`, el segundo trade cae al día siguiente,
  **inflando `trading_days`** (2 en vez de 1) y haciendo que un path dé **PASS falso**
  cuando `minimum_trading_days=2`.
- Reproducido: `terminal=pass` con `trading_days=2` sobre un solo bloque de día.
- **Fix:** `PathSimulationConfig.__post_init__` rechaza cualquier schedule donde el
  bloque de un día cruce el día calendario de `start_at`.
- Commit: `5851198`.

**MEDIUM — Frozen result mutaba a través de `provenance` anidado** (`src/probabilistic_paths.py`)
- `ProbabilisticPathResult.__post_init__` usaba `_immutable_mapping` (solo el mapping
  exterior). Los dicts anidados (`provenance["rng"]`, `["risk_sizing"]`, `["dataset"]`)
  seguían siendo dicts mutables: un resultado congelado podía reescribirse silenciosamente
  (p.ej. `max_trades` que `estimate_trades_for_pass_probability()` lee).
- Reproducido: `result.provenance["rng"]["master_entropy"] = 999999` mutaba sin error.
- **Fix:** helper recursivo `_deep_immutable()` que congela mappings/sequences anidados
  (en `MappingProxyType`/`tuple`, y copia para romper aliasing) y se usa para `provenance`.
- Commit: `82567a9`.

### Veredictos de Codex

- **Ronda 1:** `CHANGES_REQUESTED` — 1 HIGH + 1 MEDIUM + 1 WARNING (documentación/entorno).
- **Ronda 2:** a ejecutar tras las correcciones (ver §9).

## 4. Commits realizados

| Commit | Contenido |
|---|---|
| `5851198` | fix(11C): rechaza schedule que cruza el boundary de día (HIGH) |
| `82567a9` | fix(11C): deep-freeze `provenance` en resultados congelados (MEDIUM) |
| `86d8ea2` | docs: reporte nocturno de fases 11A/11B/11C |

## 5. Archivos modificados (código desde el base de revisión `c201aed`)

- `src/probabilistic_paths.py` — validación de boundary + `_deep_immutable` (+ fix HIGH/MEDIUM).
- `tests/test_probabilistic_paths.py` — 3 tests nuevos (2 RED→GREEN + 1 guarda).
- `NIGHT_REPORT_PHASES.md` — este reporte.
- 11A (`account_data.py`) y 11B (`funded_rules_v2.py`) **sin cambios de código** en esta noche.

## 6. Tests ejecutados y resultados exactos

Intérprete: conda base `python3` (Python 3.13.5, numpy 2.1.3, arch 8.0.0, pytest 8.3.4);
**sin tocar la red**.

```
/opt/anaconda3/bin/python -m pytest -p no:debugging tests/test_account_data.py -q        # 11A
/opt/anaconda3/bin/python -m pytest -p no:debugging tests/test_funded_rules_v2.py -q     # 11B
/opt/anaconda3/bin/python -m pytest -p no:debugging tests/test_probabilistic_paths.py -q # 11C
/opt/anaconda3/bin/python -m pytest -p no:debugging -m "not statistical" -q              # suite determinista
```

| Comando | Resultado |
|---|---|
| `tests/test_account_data.py` (11A) | 46 passed |
| `tests/test_funded_rules_v2.py` (11B) | 31 passed |
| `tests/test_probabilistic_paths.py` (11C) | **19 passed** (16 base + 3 nuevos) |
| `-m "not statistical"` (completa, incluye realtime) | **912 passed, 10 deselected** |

Ronda 1: los 2 tests RED fallaron (²DID NOT RAISE²) antes del fix y pasaron después.

## 7. Confirmaciones de alcance

- **Phase 11D permaneció completamente intacta.** No existe módulo de código 11D; su
  sección del SPEC no cambió (mismo contenido); `git status` no muestra cambios ahí.
- **No se modificaron RT, ProjectX, SMC-FVG ni Phases 12-14.** Los únicos archivos
  tocados son de `probabilistic_paths.py` (11C) y su test.
- **No hubo push ni merge** (ni rebase, reset ni clean). Repositorio original intacto.

## 8. Pendientes, riesgos y decisiones que requieren revisión humana

1. **Suite estadística NO ejecutada** (marcador `statistical`, ~30+ min, requiere el
   entorno de acceptance exacto Python 3.12.13 + `requirements-acceptance.lock`). El
   verde determinista (912) no constituye acceptance estadística.
2. **Revisión independiente pendiente** — se invoca a Codex en modo read-only (§9). No
   estampar "review passed" sin artefacto real.
3. **Entorno divergente**: dev (3.13.5) != acceptance (3.12.13).
4. **`CODEX_REVIEW_PHASES.md`** es el artefacto de la revisión de Codex (venía de la
   ronda 1; se regenerará en la ronda 2).

## 9. Review independiente (Codex)

- **Ronda 1:** `CHANGES_REQUESTED` (HIGH + MEDIUM + WARNING). Hallazgos HIGH/MEDIUM
  reproducidos, corregidos y commiteados por separado.
- **Ronda 2:** se invoca a Codex de nuevo en modo read-only sobre el diff
  `c201aed..HEAD` restringido a archivos de fase, con los fixes aplicados.

---

*Fin del reporte.*

# INTEGRATION_REPORT_RT_PHASES

Date: 2026-09-04
Worktree: `/Users/ricardomedina/Documents/FARS-integration`
Branch: `integration/rt-phases`

## Commit base de main

`a09433a` `feat(realtime): keep ProjectX equity unknown and add Market Hub listen`

El checkout de `Documents/FARS` (main) no se modificó. Sigue con
`M .env.example` y `?? tmp/` locales, no commiteados.

## Ramas y HEAD integrados

| Rol | Rama | HEAD |
|---|---|---|
| Main comprometido | `main` | `a09433a` |
| Phases estable | `agent/phases-night` | `09b23b8` |
| RT estable | `agent/rt-night-2` | `467318e` |
| RT explore (no merge) | `explore/rt-night` | `47c00fd` |
| Phases explore (no merge) | `explore/phases-night` | `0b92c33` |
| Integración | `integration/rt-phases` | ver log abajo |

Merges `--no-ff` (sin conflictos de código):

- `7cca2b3` merge agent/phases-night
- `61eecf3` merge agent/rt-night-2

## Commits experimentales RT seleccionados

Cherry-pick (SHAs reescritos en la rama de integración):

| Origen | Integración | Contenido |
|---|---|---|
| `b2d5f5c` | `aec35b0` | símbolo único Market Hub |
| `78fe379` | `4802500` | tokens, no substring NQ⊂MNQ |
| `ff7f415` | `dbaa3ba` | SYSTEM_HALTED en conflicto |
| `ead3614` | `64f3e16` | force-record si nested `_Stop` |

No integrados: `2d041e1` (listen intercept), bus live, RT-9, User Hub,
equity inventada, resto de `explore/rt-night`.

No integrados de Phases explore: `a531825` (bloque parcial) — decisión
explícita de no cherry-pick de `explore/phases-night` en este paso.

## Conflictos y resolución

Un conflicto de tests al cherry-pick `78fe379`: el commit origen mezclaba
el test NQ/MNQ con tests de halt que pertenecen al par siguiente. Se
conservó el fix de `select_active_contract` y el test de substring; se
dejaron los tests de halt para `ff7f415`/`ead3614`. No se cambió el
contrato de código a mano.

Merges de estables: limpios (archivos Phases vs RT no se solapan).

## Tests y resultados exactos

Entorno: `/opt/anaconda3/bin/python` 3.13.5. Sin red. Sin credenciales.

Tras merge Phases:

```text
tests/test_account_data.py + funded_rules_v2 + probabilistic_paths  98 passed in 5.99s
-m "not statistical"  925 passed, 10 deselected in 43.17s
```

Tras merge RT estable:

```text
realtime + 11A/11B/11C  341 passed in 3.71s
-m "not statistical"  933 passed, 10 deselected in 36.34s
```

Tras cherry-picks RT:

```text
tests/realtime  246 passed in 2.30s
```

Tras ciclo de corrección (mapping + docs):

```text
realtime + 11A/11B/11C  346 passed in 3.76s
-m "not statistical"  938 passed, 10 deselected in 41.55s
```

Suite estadística de aceptación: **NO ejecutada**. Requiere
`requirements-acceptance.lock` y red. El verde determinista no es
acceptance estadística.

CLI: `python -m src.realtime.cli -h` lista `doctor,contracts,bars`.
`LIVE_EXECUTION_ENABLED` en `interfaces.py` es `False`.

## Revisión de Codex

Archivo: `CODEX_REVIEW_INTEGRATION.md`
Veredicto inicial: **CHANGES_REQUESTED**

| Hallazgo | Clase Codex | Acción |
|---|---|---|
| Bloque parcial 11C | DEFECTO_OBJETIVO WARNING | **No corregido.** Ya documentado en `agent/phases-night`. El fix es `a531825` en explore/phases; este paso prohibía integrarlo. |
| `_deep_immutable` acepta escalares | DEFECTO_OBJETIVO WARNING | **Corregido** `97f840c` + tests de reproducción. |
| Docs MNQ-only vs código multi-símbolo | INCOMPATIBILIDAD_REAL | **Docs alineadas** `90128c6`. |

Un ciclo de corrección. Sin segundo Codex.

## Revisión estadística (DeepSeek)

No hay CLI DeepSeek en esta máquina (`command -v deepseek` vacío). Auditoría
estadística hecha por Hermes sobre el diff `main...HEAD`:

- Immutability/provenance: deep-freeze de metadata 11A y provenance 11C es
  correcto para no mutar historia; se restauró el rechazo de no-Mapping en
  el borde público.
- Frontera de sesión: `_validate_inputs` usa `_session_date` del perfil, no
  el día calendario. Cierra el PASS inflado de bloques completos que cruzan
  17:00 / medianoche.
- Bloque parcial (`max_trades < trades_per_day`): sigue sobre-rechazando.
  WARNING conocido, no crítico, no integrado.
- `not_evaluable`: no cambió en este diff (ya en el padre de phases-night).
- RT no toca R-multiples, bootstrap, MC ni 11C resampling. No hay leakage
  de Market Hub a Core `Trade`.
- Import `_session_date` desde `funded_rules_v2` no es cíclico.

## Pendientes

- Cherry-pick humano de `a531825` (bloque parcial 11C) si Ricardo lo aprueba.
- `fars-projectx listen` como subparser argparse (`2d041e1` no entra).
- Suite estadística de aceptación cuando exista `.venv-acceptance`.
- Phase 11D sigue gated (sin sample Tradovate).
- RT-9 sigue bloqueado.

## Confirmaciones

- Phase 11D intacta (diff 11D vacío).
- Cero ejecución live / cero órdenes.
- ProjectX read-only (`execution_allowed=False`, allowlist REST, no User Hub).
- Main original no modificado.
- No se hizo merge a main ni PR.

## Push (si el worktree queda limpio y sin secretos)

Permitido: `integration/rt-phases`, `agent/rt-night-2`, `agent/phases-night`,
y backup de explore. Prohibido: `main`.

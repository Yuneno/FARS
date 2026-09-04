# CODEX_REVIEW_PHASES.md

Revisión independiente (Codex, modo auditor read-only) de las fases 11A/11B/11C.

**Rama:** `agent/phases-night`
**Worktree:** `/Users/ricardomedina/Documents/FARS-phases`
**Base de la diferencia revisada:** `c201aed` (padre de la implementación de fases).

---

## Veredicto final (ronda de verificación / auditoría)

**OBJECTIVE_DEFECT_REMAINS**

Los cuatro commits de corrección fueron **verificados** (RED→GREEN, tests no
tautológicos, soluciones correctas). Queda **un defecto objetivo nuevo (WARNING)**
pendiente para decisión humana, documentado abajo. No es un defecto crítico.

---

## Commits revisados

| Commit | Descripción | Veredicto |
|---|---|---|
| `5851198b26bebb10dd9e88d64c040ea55821b927` | rechazo al cruzar la frontera (día calendario) | verificado; superseded por `e2a37fd` |
| `82567a99b355e40aee67ee2734edecd614596e79` | deep-freeze de provenance 11C | verificado |
| `e2a37fd1e2fba613ff8ef1a833897dd217fc7f9a` | validación por `_session_date` en `_validate_inputs` | verificado |
| `e0cf2d92c9a35cb42e24218672c40f66f0f3b611` | deep-freeze de metadata 11A | verificado |

## Verificación por commit

### 5851198 — rechazo al cruzar el día calendario
- RED: `23:59`, `trades_per_day=2`, `max_trades=2` era aceptado y, con
  `minimum_trading_days=2`, producía un PASS inflado (dos sesiones).
- GREEN: el test agregado recibe `ValueError` con `day boundary`.
- El test no es tautológico. La solución por día calendario era incompleta para
  fronteras distintas de medianoche y fue correctamente **reemplazada** por `e2a37fd`.

### 82567a9 — deep-freeze de provenance 11C
- RED: la asignación anidada cambiaba `master_entropy` de 17 a 999.
- GREEN: lanza `TypeError: 'mappingproxy' object does not support item assignment`;
  el valor permanece 17.
- Copia/congela recursivamente mappings y listas/tuplas; consumidores (acceso,
  `.get()`, `dict(...)`) siguen compatibles.

### e2a37fd — frontera de sesión del perfil
- RED: frontera 17:00 no lanzaba excepción → PASS inflado con `minimum_trading_days=2`.
- GREEN: medianoche y 17:00 rechazan con `PathAnalysisError` ("session boundary");
  un inicio válido (12:00) corre sin error.
- Usa `_session_date`, la misma función que el engine emplea para asignar sesiones.

### e0cf2d9 — deep-freeze de metadata 11A
- RED: `event.metadata["provider"]["sequence"] = 999` se ejecutaba y dejaba 999.
- GREEN: lanza `TypeError` y conserva 17.
- Aplicado a `AccountEquityEvent` y `CanonicalAccountTrade`.

---

## Defecto objetivo pendiente (WARNING — para decisión humana)

**Rechazo incorrecto de un horizonte parcial** — `src/probabilistic_paths.py:460`
(`_validate_inputs`).

La validación de frontera de sesión calcula el último timestamp de cada bloque con
`trades_per_day - 1` minutos **aunque el horizonte contenga menos trades**.

Reproducción:

```text
start_at=23:59 UTC, session_boundary=00:00, max_trades=1, trades_per_day=2
```

- Antes de `5851198`: ejecución válida, un único trade a 23:59, una sesión,
  terminal `max_trades_reached`.
- HEAD: `PathAnalysisError: ... crosses ... session boundary`.
- El scheduler sólo puede ejecutar 1 trade (`max_trades=1`); el timestamp `00:00`
  usado por la validación **nunca existe**. La validación confunde la capacidad
  nominal diaria con los trades restantes del último bloque.

Severidad: **WARNING** (sobre-rechaza configuraciones válidas con último bloque
parcial; no produce resultados erróneos, sólo rechazos falsos). Se deja pendiente
para decisión humana, conforme a la instrucción de no abrir un tercer ciclo de
corrección.

## Regresiones y pruebas

- `tests/test_funded_rules_v2.py`: 31 passed.
- Test específico de metadata 11A: 1 passed.
- Test válido de configuración 11C: 1 passed.
- Reproducciones RED→GREEN y los tres escenarios solicitados ejecutados in-memory.
- La suite completa con fixtures `tmp_path` no pudo correr en sandbox read-only (sin
  directorio temporal escribible; Matplotlib exige caché escribible). En la máquina
  anfitriona la suite determinista completa pasa: **914 passed, 10 deselected**.
- La sugerencia `asdict`/namedtuple/`mappingproxy` **no** fue tratada como requisito
  ni como defecto.

## Dominios intactos

El diff neto de código (padre de `5851198` → HEAD) contiene únicamente:
`src/account_data.py`, `src/probabilistic_paths.py` y sus dos archivos de tests.

- **Phase 11D:** intacta (sin módulo de código; SPEC sin cambios).
- **Phase 11B:** intacta (31 tests pasan).
- **RT / ProjectX:** diff vacío.
- **SMC-FVG y Phases 12–14:** no tocados por ninguno de los cuatro commits.
- Worktree sin modificaciones (solo `CODEX_REVIEW_PHASES.md` no rastreado, fuera de alcance).

---

*Fin de la verificación.*

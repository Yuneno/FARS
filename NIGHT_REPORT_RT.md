# NIGHT_REPORT_RT

Date: 2026-09-03
Worktree: `/Users/ricardomedina/Documents/FARS-rt-night`
Branch: `agent/rt-night-2`
Python: `/opt/anaconda3/bin/python` (3.13.5)

## Commit base utilizado

`a09433a` `feat(realtime): keep ProjectX equity unknown and add Market Hub listen`

El repositorio original (`/Users/ricardomedina/Documents/FARS`, `main`) estaba en ese HEAD, ahead 1 vs `origin/main`, con `.env.example` modificado y `tmp/` untracked. No se tocó.

## Estado inicial encontrado

`agent/rt-night` ya existía, checked out en `/Users/ricardomedina/Documents/FARS-rt` @ `27c8972` (atrás del HEAD). No se reutilizó ni se borró.

`/Users/ricardomedina/Documents/FARS-rt-night` no existía. Se creó worktree nuevo:

```text
git -C /Users/ricardomedina/Documents/FARS worktree add -b agent/rt-night-2 /Users/ricardomedina/Documents/FARS-rt-night HEAD
```

Inventario RT (código + summaries, sin inventar fases):

| Fase | Estado |
|---|---|
| RT-0 contracts | hecho |
| RT-1 replay connector | hecho |
| RT-1 ProjectX REST read-only | hecho (`equity=None` fail-closed) |
| RT-1 Market Hub listen | aterrizado en `a09433a`, incompleto |
| RT-2 bus | hecho |
| RT-3 JSONL recorder | hecho |
| RT-4 replay | hecho |
| RT-5 adapter | hecho |
| RT-6 AccountAwareRiskEngine | hecho |
| RT-7 paper | hecho |
| RT-8 acceptance | PASS determinista; live locked |
| RT-9 live execution | bloqueado, no empezado |

Huecos confirmados del listen ya existente:

- `fars-projectx-listen` no estaba en `[project.scripts]`
- docs todavía decían que SignalR estaba out of scope
- reconnect no incrementaba `reconnects` ni emitía `SYSTEM_CONNECTOR_RECONNECTED` (el flag `connected` se limpiaba al dropear)
- faltaban tests de CLI (duración, MNQ, execution lock, User Hub)

## Trabajo realizado

1. Distinguir `had_connection` vs `connected` en `capture_until` para contar reconnects de verdad.
2. Registrar `fars-projectx-listen = src.realtime.listen:main`.
3. Tests deterministas del listen (sin red, sin credenciales reales).
4. Actualizar `PROJECTX_CONNECTOR.md` y `src/realtime/README.md`.

ProjectX/Topstep sigue read-only. User Hub no se implementó. `LIVE_EXECUTION_ENABLED` sigue `False`. `AccountSnapshot.equity` sigue `None`.

## Trabajo descartado

- RT-9 / LiveExecutionAdapter / envío de órdenes
- User Hub
- añadir `websocket` a dependencias de FARS
- mapear equity desde balance, barras o fill PnL
- fases estadísticas, Phase 11D, SMC-FVG, Rapid 25K
- cualquier artefacto Abacus
- reset/clean/stash/checkout/rebase/pull/merge en el repo original
- push
- reutilizar el worktree viejo `FARS-rt` @ `27c8972`

## Archivos modificados

- `src/realtime/listen.py`
- `pyproject.toml`
- `tests/realtime/test_projectx_signalr.py`
- `PROJECTX_CONNECTOR.md`
- `src/realtime/README.md`
- `NIGHT_REPORT_RT.md` (este archivo)

## Commits creados

En `agent/rt-night-2` (no en `agent/rt-night`, ya ocupada):

```text
0421a14 fix(realtime): count Market Hub reconnects after a drop
49d6b16 feat(realtime): register fars-projectx-listen and cover listen contracts
5f093e4 docs(realtime): document read-only Market Hub listen
```

Más el commit de este reporte, si se incluye.

## Comandos de tests ejecutados

```bash
/opt/anaconda3/bin/python -m pytest -p no:debugging tests/realtime/test_projectx_signalr.py -q --tb=short
/opt/anaconda3/bin/python -m pytest -p no:debugging tests/realtime -q --tb=short
/opt/anaconda3/bin/python -m pytest -p no:debugging -m "not statistical" -q --tb=line
/opt/anaconda3/bin/ruff check src/realtime/listen.py src/realtime/connectors/projectx_signalr.py tests/realtime/test_projectx_signalr.py pyproject.toml
```

`python3` del Command Line Tools no tiene pytest. Se usó el intérprete de Anaconda que ya tenía el entorno FARS.

## Resultados exactos de los tests

```text
tests/realtime/test_projectx_signalr.py  17 passed in 1.20s
tests/realtime                           243 passed in 2.56s
-m "not statistical"                     928 passed, 10 deselected in 37.30s
ruff check                               All checks passed!
```

No se ejecutó el grupo `@statistical`.

## Pendientes, riesgos y decisiones que requieren revisión humana

- RT-9 permanece bloqueado. ProjectX no es RT-8 PASS.
- User Hub no existe y no debe existir hasta una autorización explícita.
- `websocket` sigue siendo import opcional de la máquina, no dependencia FARS.
- Equity de ProjectX sigue unknown; RT-6 deny-on-unknown es correcto hasta que el broker documente equity o uPnL+balance.
- La captura listen está pinneada a MNQ. Ampliar símbolos es decisión humana.
- Rama de trabajo: `agent/rt-night-2` en vez de `agent/rt-night` por conflicto de worktree. No se hizo merge ni push.
- ¿`fars-projectx-listen` como script aparte vs subcomando de `fars-projectx`? Se respetó el `prog` ya existente.

## Confirmación explícita

- No hubo ejecución real ni llamadas live.
- No se enviaron órdenes.
- No se usaron credenciales reales.
- No se modificó `.env`.
- No se imprimieron secretos.
- No hubo push, merge ni rebase.
- El repositorio original no se modificó (`main` @ `a09433a`, mismos dirty files de entrada: `M .env.example`, `?? tmp/`).
- El worktree queda limpio salvo este reporte hasta su commit.

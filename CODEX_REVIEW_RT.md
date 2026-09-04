# CODEX REVIEW — REALTIME

ARCHIVAL NOTE (Hermes, 2026-09-03): this file is Codex's review of
`agent/rt-night-2` @ `467318e` versus base `a09433a`. It is NOT a review of
`explore/rt-night` experiments (`b2d5f5c`, `ff7f415`, `2d041e1`).

The LOW observation about four versus five commits is accurate: at
`467318e`, `NIGHT_REPORT_RT.md` still listed four SHAs (`0421a14`,
`49d6b16`, `5f093e4`, `2eb57ff`) while `git log a09433a..HEAD` also
includes the close commit `467318e` itself. Documentary only; no code
defect. Experiment review lives in `CODEX_REVIEW_RT_EXPLORATION.md`.

## Verdict

**PASS_WITH_OBSERVATIONS**

No se encontraron defectos funcionales, regresiones, vulneraciones del lock de
ejecución ni cambios fuera de alcance que requieran bloquear esta entrega. Hay
una inconsistencia documental menor en `NIGHT_REPORT_RT.md`.

## Review target

- Worktree inspeccionado: `/Users/ricardomedina/Documents/FARS-rt-night`
- Rama inspeccionada: `agent/rt-night-2`
- Base SHA: `a09433a6ced2a5ee2a326e4138285d4024fe0afb`
- HEAD SHA: `467318ef7f7a5c62f43a32764781dfdbf7707fe8`
- Diff real inspeccionado: **sí**, `git diff a09433a..HEAD`; la revisión no se
  basó solamente en el reporte nocturno.
- Estado inicial antes de tests/review: limpio.
- `git diff --check a09433a..HEAD`: sin errores.

## Files reviewed

Se inspeccionó cada hunk de los seis archivos del diff:

1. `src/realtime/listen.py`
2. `tests/realtime/test_projectx_signalr.py`
3. `pyproject.toml`
4. `PROJECTX_CONNECTOR.md`
5. `src/realtime/README.md`
6. `NIGHT_REPORT_RT.md`

También se leyeron las dependencias y contratos necesarios para validar el
comportamiento y los locks: `AGENTS.md`, `FARS_REALTIME_SPEC.md`,
`RT1_SUMMARY.md`, `RT8_SUMMARY.md`, `RT8_ACCEPTANCE.json`,
`src/realtime/interfaces.py`, `src/realtime/connectors/projectx.py`,
`src/realtime/connectors/projectx_signalr.py` y los tests ProjectX relevantes.

## Scope confirmation

El diff corresponde exclusivamente a RT: corrige el estado de reconexión de
Market Hub, registra el entry point RT, amplía tests deterministas RT y alinea
la documentación RT. `pyproject.toml` es un archivo global, pero su único cambio
es el script `fars-projectx-listen = src.realtime.listen:main`. No hay cambios a
Core, fases estadísticas, optimización, SMC/FVG ni otros componentes fuera del
alcance indicado.

La corrección de reconexión es lógicamente consistente: `had_connection`
persiste después de un drop mientras `connected` representa únicamente el
estado actual. Tras un nuevo handshake exitoso, se incrementa `reconnects` y se
registra `SYSTEM_CONNECTOR_RECONNECTED`; los fallos de conexión iniciales no se
clasifican como reconexiones.

## Tests run

Para preservar el modo read-only, se ejecutaron los comandos solicitados con
`PYTHONDONTWRITEBYTECODE=1` y `PYTEST_ADDOPTS='-p no:cacheprovider'`. Esto solo
deshabilita escrituras de bytecode/caché y no cambia la selección de tests.

1. Comando solicitado:

   `/opt/anaconda3/bin/python -m pytest -p no:debugging tests/realtime -q --tb=short`

   Resultado exacto: **243 passed in 2.71s** (exit code 0).

2. Comando solicitado:

   `/opt/anaconda3/bin/python -m pytest -p no:debugging -m "not statistical" -q --tb=line`

   Resultado exacto: **928 passed, 10 deselected in 87.61s (0:01:27)**
   (exit code 0).

No se ejecutaron tests marcados `statistical`. Los tests ProjectX/SignalR usan
transportes, sockets, tokens y credenciales sintéticos; no se realizaron
llamadas live contra ProjectX y no se imprimieron secretos.

## RT-9 lock confirmation

**Confirmado: RT-9 continúa bloqueado y no fue iniciado.**

Evidencia:

- `FARS_REALTIME_SPEC.md` §33 mantiene RT-9 como `LOCKED` y exige contrato de
  broker confirmado, reconciliación, kill switch, recovery, detección de
  mismatches, auditoría estructurada y opt-in explícito.
- `src/realtime/interfaces.py:28` mantiene
  `LIVE_EXECUTION_ENABLED = False`.
- No existe una clase `LiveExecutionAdapter` en el árbol revisado.
- No aparece ninguna asignación de producción que active
  `LIVE_EXECUTION_ENABLED` ni `execution_allowed`.
- El nuevo entry point solo expone captura Market Hub y conserva los guards de
  ejecución en `src/realtime/listen.py:441-453`.

## ProjectX / Topstep read-only confirmation

**Confirmado: ProjectX/Topstep continúa estrictamente read-only y no puede
enviar órdenes mediante el código revisado.**

Evidencia:

- `ProjectXClient.execution_allowed = False`
  (`src/realtime/connectors/projectx.py:413`).
- `_READ_ONLY_PATHS` solo permite login/validate y lecturas de account,
  contract, history, open positions y trades
  (`src/realtime/connectors/projectx.py:44-53`). `_post` rechaza cualquier ruta
  fuera de esa allowlist (`src/realtime/connectors/projectx.py:624-625`).
- El cliente no define métodos de place/submit/cancel/modify/close/flatten/buy/
  sell y su constructor mantiene un guard explícito contra ellos.
- Market Hub solo construye `SubscribeContractQuotes` y
  `SubscribeContractTrades`; no hay User Hub ni suscripciones de account,
  positions u orders.
- `run_listen` falla si el lock global deja de ser `False` o si recibe un
  cliente cuyo `execution_allowed` no sea exactamente `False`.
- La búsqueda estática no encontró endpoints de órdenes/cierre ni User Hub en
  `src/`.

## Findings

### LOW / observation — Inventario de commits incompleto

- Archivo: `NIGHT_REPORT_RT.md`
- Ubicación: líneas 78-90
- Evidencia: el reporte enumera cuatro commits y afirma que el diff corresponde
  a “esos cuatro commits”, pero `a09433a..HEAD` contiene cinco. Falta
  `467318e docs(realtime): close NIGHT_REPORT_RT with commit list and locks`,
  que es además el HEAD revisado.
- Impacto: no afecta código, tests, alcance RT ni seguridad; reduce la exactitud
  y reproducibilidad del reporte de handoff.
- Corrección sugerida: incluir `467318e` en la lista y cambiar “cuatro” por
  “cinco”, o describir claramente que los cuatro son commits previos al commit
  final de cierre.

No hay hallazgos BLOCKER, HIGH ni MEDIUM. No se identificaron tests faltantes
necesarios para aceptar el cambio concreto.

## Final assessment

La implementación corrige el conteo/evento de reconexión sin ampliar la
superficie de ejecución, los tests verifican el caso de drop + reconnect y los
guards read-only, y ambas suites requeridas pasan. La entrega es aprobable con
la observación documental anterior.

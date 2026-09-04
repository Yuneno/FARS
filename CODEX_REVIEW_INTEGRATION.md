# Revisión de integración Codex

## Veredicto

**CHANGES_REQUESTED**

La integración es mecánicamente limpia y la suite autorizada pasa, pero el
`HEAD` combinado conserva dos defectos objetivos en Phase 11 y deja un contrato
de usuario de RT contradictorio con el comportamiento incorporado.

## Alcance verificado

- Rama: `integration/rt-phases`
- Base solicitada: `main` @ `a09433a6ced2a5ee2a326e4138285d4024fe0afb`
- `HEAD`: `64f3e16f2e631f2e5cb6e532f60ef44c6e389efe`
- Merge-base: `a09433a6ced2a5ee2a326e4138285d4024fe0afb`
- Diferencia revisada: `git diff main...HEAD`
- Worktree limpio al comenzar la auditoría.

## Hallazgos

### 1. DEFECTO_OBJETIVO — WARNING — el último bloque parcial se valida como si estuviera completo

Ubicación: `src/probabilistic_paths.py:460-465`.

`_validate_inputs` calcula el último instante de **todos** los bloques con
`trades_per_day - 1`, incluso cuando al último bloque le quedan menos trades que
esa capacidad. Por ello rechaza un horario que la simulación nunca llega a
producir.

Reproducción confirmada:

```text
start_at       = 2026-09-01 23:59 UTC
session_boundary = 00:00 UTC
max_trades     = 1
trades_per_day = 2
resultado      = PathAnalysisError: ... crosses ... session boundary
```

El scheduler de `_simulate_one` solo emitiría un trade, a las 23:59. El supuesto
segundo trade a las 00:00 usado para rechazar la configuración no existe. Esto
es una regresión funcional por falso rechazo, no una preferencia de diseño. Los
tests nuevos cubren bloques completos que cruzan la frontera, pero no un bloque
final parcial.

Corrección esperada: calcular el tamaño real de cada bloque, especialmente el
último, y agregar una prueba positiva para el caso anterior.

### 2. DEFECTO_OBJETIVO — WARNING — `_deep_immutable` debilita la validación de los campos `Mapping`

Ubicaciones:

- `src/account_data.py:107-122`, usado en `CanonicalAccountTrade.metadata` y
  `AccountEquityEvent.metadata`.
- `src/probabilistic_paths.py:85-101`, usado en
  `ProbabilisticPathResult.provenance`.

La función nueva devuelve sin cambios cualquier valor que no sea `Mapping`,
`list` o `tuple`. En consecuencia, constructores públicos con campos declarados
como `Mapping[str, Any]` ahora aceptan escalares inválidos. Se confirmó que ambos
objetos siguientes se construyen sin excepción y conservan un `int`:

```text
AccountEquityEvent(..., metadata=42).metadata == 42
ProbabilisticPathResult(..., provenance=42).provenance == 42
```

Antes del cambio, `_immutable_mapping` intentaba `dict(value)` y esos escalares
eran rechazados. En `ProbabilisticPathResult` el objeto queda aparentemente
válido, pero consumidores como `estimate_trades_for_pass_probability` fallan
después al indexar `result.provenance["simulation"]`.

Además, el helper no congela contenedores mutables distintos de `list`/`tuple`
(por ejemplo un `set` anidado), aunque su justificación afirma impedir mutación
a través de metadata/provenance.

Corrección esperada: validar explícitamente que el valor exterior sea un
`Mapping` y congelar o rechazar tipos de hoja/contenedor mutables no admitidos.
Agregar tests negativos de tipo para los tres campos afectados y un test para
contenedores anidados admitidos.

### 3. INCOMPATIBILIDAD_REAL — WARNING — el CLI acepta otros símbolos mientras la documentación promete MNQ-only

Ubicaciones:

- Runtime: `src/realtime/listen.py:469-473` y
  `src/realtime/connectors/projectx_signalr.py:187-223`.
- Contrato documentado: `PROJECTX_CONNECTOR.md:17,69-71` y
  `src/realtime/README.md:33-35`.
- Prueba que fija el comportamiento nuevo: `tests/realtime/test_projectx_signalr.py:412-444`.

El código y su test aceptan `--symbol NQ` (o cualquier símbolo con una
coincidencia activa única), mientras ambos documentos incorporados en este mismo
diff declaran que el listener está fijado a MNQ y captura MNQ solamente. Es un
contrato observable contradictorio. La elección de admitir uno o varios productos
no se evalúa aquí como preferencia; debe alinearse código, pruebas y documentación.

## Comprobaciones sin hallazgos bloqueantes

- **Conflictos RT/Phases:** no hay archivos de código compartidos entre ambos
  grupos de cambios ni resoluciones combinadas visibles en los merge commits.
  La suite conjunta autorizada no reveló fallos que estuvieran ocultos al probar
  las ramas por separado.
- **Imports/ciclos:** el import nuevo de `_session_date` desde
  `funded_rules_v2` no crea un ciclo; no existe dependencia inversa hacia
  `probabilistic_paths`.
- **CLI:** el entry point `fars-projectx-listen = src.realtime.listen:main` está
  registrado y sus pruebas de parsing pasan. El hallazgo de contrato MNQ/símbolo
  es la incompatibilidad relevante.
- **Phase 11D:** no hay cambios en su especificación ni implementación de adapter
  Tradovate/protocolo prospectivo en el diff revisado.
- **Ejecución live:** `LIVE_EXECUTION_ENABLED` permanece exactamente `False`;
  el cliente ProjectX conserva `execution_allowed = False`, no se agregaron
  métodos de orden/cancelación/cierre y el listener rechaza clientes ejecutables.
  Sí hay captura de datos de mercado live read-only, que es el alcance declarado.
- **Credenciales/secretos:** no se encontraron credenciales reales ni material
  sensible agregado. Los tokens encontrados son literales de prueba; URLs
  registradas eliminan el query string y los artefactos probados no contienen el
  API key ni el token de sesión.

## Pruebas ejecutadas

Sin red y sin credenciales:

```text
/opt/anaconda3/bin/python -m pytest -p no:debugging tests/realtime tests/test_account_data.py tests/test_funded_rules_v2.py tests/test_probabilistic_paths.py -q --tb=short
```

Resultado: **344 passed in 25.77s** con Python 3.13.5.

La ejecución verde no cubre las dos reproducciones negativas descritas arriba.
No se ejecutó acceso live ni se usaron credenciales.

## Clasificación restante

- `RIESGO_NO_DEMOSTRADO`: ninguno adicional que deba bloquear esta integración.
- `PREFERENCIA`: ninguna usada para el veredicto.
- `OPORTUNIDAD`: ninguna usada para el veredicto.

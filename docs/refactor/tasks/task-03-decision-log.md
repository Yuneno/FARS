# Tarea 3 (Codex): instrumentar AmdCrtStrategy con un decision log (candidaturas + rechazos)

## Contexto

Prepara el meta-labeling (objetivo funcional #5). Hoy `AmdCrtStrategy.evaluate()`
devuelve `Signal | None` de forma opaca: cuando devuelve `None`, no queda registro
de POR QUÉ se rechazó (¿AMD no confirmó? ¿CRT falló? ¿EMA en contra? ¿riesgo fuera
de banda?) ni de las variables presentes al decidir. Sin eso no se puede entrenar
un clasificador (regresión logística → XGBoost) sobre "qué señales valen la pena".

## Objetivo

Añadir a `AmdCrtStrategy` un registro de decisiones (decision log) que capture, en
cada barra donde hubo una CANDIDATURA evaluada, las variables disponibles al
decidir y el resultado de la decisión (aceptada, o rechazada con motivo). NO debe
cambiar el comportamiento del backtest (mismos trades, mismos resultados).

## Definición de "candidatura"

Una candidatura ocurre cuando el AMD ya confirmó el día (la estrategia tiene un
fade direction pendiente). Es decir, se registran las barras desde que
`_detect_amd` confirma en adelante, hasta que la señal se acepta o el día termina.
Las barras donde AMD aún no confirma NO son candidaturas (no aportan al
meta-labeling del setup AMD+CRT).

## Variables a capturar en cada candidatura (disponibles al decidir, 100% causal)

- `day` (fecha de sesión) y `weekday`
- `direction` (fade direction: "long"/"short")
- `crt_confirmed` (bool)
- `ema_regime` ("long"/"short"/None) — solo si use_ema_filter
- `atr` (valor de ATR14) y `sl_pts`, `tp_pts` (los niveles propuestos)
- `pre_ny_amplitude` y `median_amplitude` (para el ratio de compresión) — el
  ratio `pre_ny_amplitude / median_amplitude` es la variable clave del setup
- `decision` (enum/cadena): el motivo exacto de rechazo o "accepted"
- `timestamp` de la barra

Los motivos de rechazo posibles (cada `return None` actual debe mapearse a uno):
- `amd_not_confirmed`
- `crt_not_confirmed`
- `ema_against` (regime != direction)
- `edge_cold` (edge gate rechazó)
- `risk_out_of_band` (min/max_risk_pts)
- `atr_insufficient` (_sl_tp devolvió None)
- (los early-returns de fin de semana / fuera de RTH / ya-operado NO se registran,
  porque no son candidaturas de AMD+CRT)

## Qué debe hacer Codex

1. Añadir a `AmdCrtStrategy` un mecanismo de registro de decisiones accesible
   desde fuera (ej. `strategy.decisions` como lista de dataclasses/dicts, o un
   método `decision_log()`). No acoplarlo al protocolo `Strategy` (BreakoutStrategy
   no lo necesita).
2. Instrumentar `evaluate()` para que, en cada candidatura, registre las variables
   y el motivo de la decisión. Esto requiere sustituir los `return None` de los
   filtros por un registro explícito + return.
3. Garantizar que el registro NO altera los resultados del backtest (mismos trades).
4. Añadir tests que verifiquen:
   - que una candidatura aceptada queda registrada con `decision="accepted"`;
   - que cada motivo de rechazo queda registrado correctamente (usar monkeypatch
     o historias sintéticas controladas);
   - que el log es causal (no registra barras futuras);
   - que el resultado del backtest (n trades) no cambia con el log activado.

## Criterios de aceptación

1. `AmdCrtStrategy` expone el decision log; BreakoutStrategy no se toca.
2. Cada candidatura registra al menos: day, weekday, direction, crt_confirmed,
   ema_regime, atr, sl_pts, tp_pts, pre_ny_amplitude, median_amplitude, decision,
   timestamp.
3. Todos los `return None` de los filtros quedan mapeados a un motivo.
4. Los resultados del backtest (n, win, exp, PF, maxDD) son IDÉNTICOS con y sin
   el log (el log es observación pura, no cambia señales).
5. Tests nuevos cubren: aceptación + cada motivo de rechazo + causalidad + no
   cambio de resultados.

## Qué NO hacer

- NO cambiar la lógica de AMD/CRT/EMA/edge-gate/risk (solo observación).
- NO calcular el "resultado" del trade en esta tarea (eso es la Tarea 4: unir el
  log con los r_result del executor).
- NO entrenar ningún modelo todavía (eso es la Tarea 5+).
- NO tocar `src/backtest/executor.py`.
- NO commitear. Dejar el diff para que Hermes revise.

## Notas

- Elegir la representación más simple que cumpla los criterios (una lista de
  dataclasses/dicts en la instancia es suficiente). No sobre-ingenierizar con
  sistemas de logging.
- El decision log debe poder vaciarse/resetearse entre backtests (la estrategia
  se instancia fresca por corrida, así que el estado en `__init__` basta).

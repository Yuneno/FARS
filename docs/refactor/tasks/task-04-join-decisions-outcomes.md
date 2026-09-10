# Tarea 4 (Codex): unir el decision log con los resultados del backtest (dataset etiquetado)

## Contexto

La Tarea 3 añadió a `AmdCrtStrategy` un decision log causal
(`strategy.decisions`, lista de `AmdCrtDecision`) que registra cada candidatura
AMD-confirmada con sus variables y el motivo de la decisión ("accepted" o un
motivo de rechazo).

El backtest (`run_backtest` en `src/backtest/executor.py`) produce, por su lado,
`ExecutedTrade` con el `r_result` (resultado en R) calculado cuando el trade
cierra.

Falta el PUENTE: un dataset etiquetado donde cada decisión "accepted" quede
asociada a su resultado (win/loss en R, y motivo de salida), para poder entrenar
el meta-labeling (regresión logística → XGBoost) en tareas posteriores.

## Objetivo

Producir una función/helper que, dado `strategy.decisions` y `result.trades`
(ambos de la MISMA corrida), genere una lista de filas etiquetadas donde:

- cada decisión "accepted" tiene su `r_result` (y `exit_reason`) asociado, o
  queda marcada como `not_executed` si el executor la rechazó (gap o niveles
  inválidos);
- las decisiones rechazadas quedan SIN resultado (no se ejecutaron), con
  `decision` = su motivo de rechazo.

## Regla de unión (crítica — causal y sin ambigüedad)

La estrategia emite como máximo UNA señal aceptada por día de sesión
(invariante `_signal_day`). Por tanto:

- Una decisión "accepted" con `day = D` corresponde al trade cuyo
  `entry_time` cae en el día de sesión `D` (misma convención de sesión que la
  estrategia: `_session_date` con `session_tz`/`session_start`).
- Como máximo hay 1 trade por día, la unión es 1:1 por día.
- Si una decisión "accepted" NO tiene trade en ese día → el executor la rechazó
  (gap o niveles inválidos) → marcarla `not_executed`.

## Qué debe hacer Codex

1. Crear el helper (ej. `join_decisions_to_outcomes`) en `src/backtest/` (nuevo
   módulo, ej. `decisions.py`, o dentro de `amd_crt.py` — a elección, pero
   documentar ubicación). Debe aceptar `decisions: Sequence[AmdCrtDecision]`,
   `trades: Sequence[ExecutedTrade]`, y la convención de sesión
   (`session_tz`, `session_start`).
2. La fila resultante debe incluir las variables de la decisión (las del
   dataclass `AmdCrtDecision`) más: `r_result` (o None), `exit_reason` (o None),
   y una bandera `executed` (bool).
3. Validar la correspondencia: si hay más de un trade en un mismo día, o una
   decisión "accepted" sin trade, hay que reportarlo claramente (no fallar en
   silencio). Para el MVP, el caso de "más de un trade por día" no debería
   ocurrir, pero el helper debe detectarlo y señalarlo.
4. Añadir tests:
   - decisión "accepted" con trade en el mismo día → r_result asociado.
   - decisión "accepted" sin trade (rechazada por el executor) → `not_executed`.
   - decisión rechazada (crt_not_confirmed, etc.) → sin resultado.
   - unión 1:1 correcta en una corrida sintética con varios días.

## Criterios de aceptación

1. El helper produce filas etiquetadas que unen decisión + resultado.
2. La unión es por día de sesión (causal, sin mirar barras futuras).
3. Una decisión "accepted" rechazada por el executor queda `not_executed`.
4. Tests cubren: aceptada con trade, aceptada sin trade, rechazada, multi-día.
5. NO se entrena ningún modelo todavía (eso es la Tarea 5+).

## Qué NO hacer

- NO cambiar la lógica de la estrategia ni del executor.
- NO entrenar ningún modelo.
- NO tocar los datasets.
- NO commitear. Dejar el diff para que Hermes revise.

## Notas

- El `ExecutedTrade` tiene `entry_time` (tz-aware) y `r_result`, `exit_reason`.
  La conversión de día de sesión debe reutilizar `_session_date` de `amd_crt.py`
  para que coincida EXACTAMENTE con la convención de la estrategia.
- Si el helper necesita importar de `executor.py`, verificar que no haya import
  circular (el executor ya importa de `amd_crt.py` en algunos paths).

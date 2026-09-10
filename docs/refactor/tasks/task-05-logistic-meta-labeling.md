# Tarea 5 (Codex): regresión logística con meta-labeling y evaluación cronológica

## Contexto

Ya tenemos (Tareas 3-4) el dataset etiquetado: `join_decisions_to_outcomes`
produce `LabeledAmdCrtDecision` con las variables causales de cada candidatura
y, para las "accepted" ejecutadas, el `r_result` del trade.

Ahora construimos el PRIMER modelo de meta-labeling: una regresión logística
que predice, para cada señal ACEPTADA, si el trade resultante va a ganar
(r_result > 0) o perder (r_result <= 0). El objetivo NO es predecir el mercado,
sino predecir la CALIDAD de la propia señal.

## Definición del problema (meta-labeling binario)

- **Target (label):** `1` si `r_result > 0` (win), `0` si `r_result <= 0` (loss).
  Solo se usan las filas `executed == True` (las que tienen resultado real).
- **Features (causales, disponibles al decidir):**
  - `weekday` (0-4)
  - `direction` (long/short → codificar)
  - `crt_confirmed` (bool)
  - `ema_regime` (long/short/None → codificar; None solo si no hay filtro EMA)
  - `atr` (float)
  - `sl_pts`, `tp_pts` (float)
  - `pre_ny_amplitude`, `median_amplitude` (float)
  - ratio de compresión `pre_ny_amplitude / median_amplitude` (feature derivada;
    manejar división por cero con fallback NaN→imputación o drop)
- Las filas con `decision != "accepted"` NO se usan en esta tarea (son rechazos,
  no tienen resultado). Las `not_executed` tampoco (no tienen r_result).

## Evaluación — CRÍTICA: cronológica, sin filtración

- **NO usar split aleatorio.** Los trades son serie temporal.
- Usar walk-forward (o expanding-window) cronológico: entrenar sobre el pasado,
  predecir el siguiente bloque, avanzar. Ej. 4-5 folds contiguos ordenados por
  `timestamp`/`day`.
- Reportar por fold y agregado: **accuracy, AUC (roc_auc), precisión, recall**,
  y el **número de muestras** de cada fold (para no sacar conclusiones de folds
  con 10 trades).
- **Comparar contra el baseline trivial:** "predecir siempre la clase mayoritaria"
  (o 50/50 si balanceado). Si la regresión logística NO supera el baseline
  out-of-sample, reportarlo HONESTAMENTE — no inflar.

## Qué debe hacer Codex

1. Crear un módulo (ej. `src/backtest/metalabel.py` o `src/analysis/` — elegir y
   documentar) con:
   - `build_features(rows: Sequence[LabeledAmdCrtDecision]) -> (X, y, meta)`
     que extraiga features + target de las filas ejecutadas, devolviendo también
     los timestamps/días para el orden cronológico.
   - `evaluate_walk_forward(X, y, order, ...)` que corra la regresión logística
     (sklearn `LogisticRegression`) en walk-forward cronológico y devuelva las
     métricas por fold + agregadas.
2. Un script/entry para correrlo sobre un backtest real (Databento MNQ_M5.csv),
   usando `AmdCrtStrategy` + `run_backtest` + `join_decisions_to_outcomes`.
3. Añadir tests unitarios para `build_features` (codificación, ratio de
   compresión, manejo de NaN) y para `evaluate_walk_forward` (orden cronológico,
   sin filtración: el fold de test nunca se ve en el entrenamiento de ese fold).

## Criterios de aceptación

1. `build_features` produce X (matriz de features) e y (labels binarios) a partir
   de `LabeledAmdCrtDecision`, solo de filas `executed`.
2. `evaluate_walk_forward` usa particiones cronológicas (no aleatorias) y reporta
   accuracy + AUC + precisión + recall por fold y agregado.
3. Se reporta el baseline trivial (clase mayoritaria) para comparar.
4. El script corre end-to-end sobre Databento MNQ_M5 y produce un resumen de
   métricas honesto (si el modelo no supera el baseline, se dice explícitamente).
5. Tests cubren: features/codificación, orden cronológico, sin filtración.
6. NO se afirma mejora predictiva sin que las métricas out-of-sample lo respalden.

## Qué NO hacer

- NO usar split aleatorio (es serie temporal).
- NO usar filas no-ejecutadas como si tuvieran resultado.
- NO tunear hiperparámetros en exceso (es la primera pasada: `LogisticRegression`
  con defaults, `class_weight` opcional si hay desbalance).
- NO instalar xgboost ni nuevas dependencias (usar sklearn, ya disponible).
- NO declarar "la estrategia mejoró" — solo medir si el clasificador predice
  la calidad de señal mejor que el azar.
- NO commitear. Dejar el diff para que Hermes revise.

## Notas

- sklearn ya está disponible (1.6.1). xgboost NO — se deja para la Tarea 6 con
  decisión aparte (instalar xgboost vs usar GradientBoosting de sklearn).
- El tamaño de muestra real (n trades ejecutados en Databento ~1362 sin EMA, ~739
  con EMA) es chico para ML. Eso es esperable y parte del resultado honesto: si
  con 739 muestras el modelo no generaliza, hay que reportarlo.
- El walk-forward debe respetar estrictamente el orden por día de sesión.

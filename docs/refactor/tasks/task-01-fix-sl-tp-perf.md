# Tarea 1 (Codex): corregir el bug de rendimiento de `_sl_tp` / re-evaluación por barra

## Contexto

Archivo: `src/backtest/amd_crt.py`, clase `AmdCrtStrategy`.

El backtest AMD+CRT con filtro EMA10/4h tarda ~4x más que sin filtro
(3785s vs 973s sobre ~1.1M velas M5), a pesar de que genera MENOS trades
(824 vs 1502). Es un bug de rendimiento, no de comportamiento.

## Causa raíz (ya diagnosticada)

1. `_sl_tp()` (líneas ~675-689) construye la lista de barras RTH con un
   list-comprehension sobre TODO `history`:
   ```python
   rth = [b for b in history if b.timestamp.astimezone(ET) >= cutoff and ...]
   ```
   `history` crece hasta ~1.1M barras, así que esto es O(n) por barra → O(n²)
   en el total de la corrida. Solo interesan las últimas ~10 sesiones RTH.

2. En `evaluate()`, cuando un filtro opt-in (EMA en línea 643-644, edge gate en
   654-655, o min/max_risk_pts en 664-667) rechaza la señal, se retorna `None`
   SIN marcar `self._signal_day`. Como resultado, en cada barra restante del
   MISMO día se vuelve a ejecutar `_detect_crt()` y `_sl_tp()`. Con el filtro
   EMA activo, muchos días quedan en este ciclo de rechazo → re-evaluación
   repetida → el 4x.

## Qué debe hacer Codex

Corregir el rendimiento SIN alterar los resultados del backtest.

Sugerencias de implementación (Codex puede elegir el enfoque, pero debe cumplir
los criterios de aceptación):

- `_sl_tp`: no escanear todo `history`; usar solo la cola (últimas N barras que
  cubran 10 sesiones RTH), o mantener un índice de inicio. El resultado del ATR
  debe ser IDÉNTICO al actual (mismos 10 días, mismas barras, mismo orden).
- Evitar la re-evaluación en bucle: cuando un filtro rechaza de forma
  irreversible para el resto del día, marcar el día como ya evaluado para no
  re-ejecutar `_detect_crt`/`_sl_tp` en cada barra. OJO: el filtro EMA SÍ puede
  cambiar de decisión cuando se cierra un nuevo bucket 4h, así que ese caso no
  puede cachearse como "rechazado para siempre" a menos que sea estadísticamente
  equivalente y se demuestre con tests.
- Cualquier caché debe ser causal (nunca usar barras futuras).

## Criterios de aceptación (obligatorios)

1. **Resultados bit-for-bit idénticos.** Antes y después del fix, corriendo el
   backtest completo sobre `mnq_extended_m5.csv`, los números deben coincidir
   EXACTAMENTE: n trades, win rate, expectancy, PF, maxDD_R. Tanto sin filtro
   como con EMA10/4h (y con edge gate si aplica). No se acepta "casi igual".
2. **Speedup medible.** El backtest con EMA10/4h debe dejar de ser ~4x más
   lento que el base. Objetivo: EMA ≈ base (mismo orden de magnitud).
3. **Tests.** Añadir tests que:
   - verifiquen que `_sl_tp` produce el mismo resultado sobre una secuencia
     larga de barras sintéticas antes/después del cambio (regresión de
     equivalencia);
   - verifiquen que un filtro que rechaza no re-ejecuta la detección en cada
     barra (puede medirse con un contador de llamadas o con un test de
     complejidad sobre una historia larga).
4. No introducir look-ahead: todos los cachés usan solo barras ≤ la barra actual.

## Qué NO hacer

- NO cambiar la lógica de AMD/CRT/EMA/edge-gate (solo rendimiento).
- NO tocar `src/backtest/executor.py` a menos que sea imprescindible (y si lo
  es, justificarlo y añadir tests).
- NO modificar los datasets ni los archivos fuera de `src/` y `tests/`.
- NO commitear. Dejar el cambio listo para que Hermes revise el diff.

## Evidencia a entregar

- El diff completo.
- La salida de `pytest tests/test_backtest_amd_crt.py -q`.
- Una tabla con el tiempo y los resultados (n, win, exp, PF, maxDD) antes y
  después, para base y para EMA10/4h.

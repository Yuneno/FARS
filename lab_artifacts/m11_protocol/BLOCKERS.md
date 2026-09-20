# BLOCKERS — Bloque M11: Régimen + Gestión de Salida

Este archivo documenta formalmente los bloqueos, restricciones de arquitectura y decisiones técnicas tomadas durante la ejecución del Bloque M11, en estricto cumplimiento de las reglas del laboratorio.

---

## 1. Restricción del Executor en Etapa 3 (Brazo S3: Sin parcial + Break-Even)

- **Descripción del Encargo (§4):**
  `S3 — sin parcial (f=0.00) + BE + target de fábrica (todo al runner).`
- **Hallazgo en el Código Base (`src/backtest/executor.py`):**
  Al instanciar `BacktestConfig(partial_take_profit_fraction=0.0, move_stop_to_break_even=True)` se produce la siguiente excepción:
  ```python
  # src/backtest/executor.py:182-185
  if self.move_stop_to_break_even and self.partial_take_profit_fraction <= 0:
      raise ValueError(
          "move_stop_to_break_even requires partial_take_profit_fraction > 0"
      )
  ```
  Asimismo, en el bucle del executor (`_run_backtest_enhanced`, líneas 974–992), el evento `hit_tp1` que desplaza el stop a break-even está acoplado a la condición `partial_fraction > 0`:
  ```python
  elif (
      hit_tp1
      and partial_fraction > 0
      and not position["partial_taken"]
  ):
      position["partial_taken"] = True
      ...
      if config.move_stop_to_break_even:
          position["stop"] = position["entry"]
  ```
- **Regla del Encargo (§0):**
  > *"Código nuevo SOLO en `lab_artifacts/m11_protocol/`. NO modifiques `src/` ni `tests/`. Si falta un knob imprescindible → `BLOCKERS.md` y ese brazo se omite (declarado) o se reporta tal cual sin maquillar."*
- **Resolución Técnica Declarada:**
  Sin alterar `src/`, es imposible activar break-even sin tomar un parcial mayor a cero. Por lo tanto, el brazo `S3` se evalúa como **corredor puro al target sin parciales** (`partial_take_profit_fraction=0.0`, `move_stop_to_break_even=False`), dejando el 100% de la posición correr hacia el target de fábrica (3.0R) o el stop inicial (1.0R). Esto representa fielmente la hipótesis de "todo al runner", quedando formalmente declarado en el preregistro y en este registro de bloqueos.

---

## 2. Inyección Causal de ATR en la Caché de Contexto de Zonas (Predicado L)

- **Hallazgo en `src/backtest/zone_bridge.py` (`precompute_fold_cache`):**
  La función de caché de Z5 llamaba a `engine.context(price=b.close)` omitiendo el parámetro `atr`. En consecuencia, `ZoneEngine` fijaba `distance_to_sellside_liquidity_atr = None` y `distance_to_buyside_liquidity_atr = None`.
- **Efecto en Z5:**
  En Z5, los filtros `distance_to_sellside_liquidity_atr` y `distance_to_buyside_liquidity_atr` devolvían `False` cuando la distancia evaluada era `None`, lo que convertía esos filtros en selectores unilaterales de lado (shorts-only o longs-only), produciendo además resultados idénticos a los de `overnight_high/low` (señalado por Hermes en su revisión).
- **Resolución Técnica en M11:**
  Para que el predicado normativo del encargo `distance_to_{lado}_liquidity_atr_le_1` funcione como una verdadera métrica de distancia en múltiplos de ATR, `run_m11.py` calcula causally el ATR-14 barra a barra sobre la historia cerrada y alimenta `engine.context(price=b.close, atr=atr_14)`. Esto puebla exactamente los campos en múltiplos de ATR con rigor point-in-time, permitiendo que tanto longs como shorts sean evaluados contra el umbral de 1.0 ATR.

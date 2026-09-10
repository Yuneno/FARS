# Tarea 2 (Codex): re-baseline del backtest de referencia sobre Databento

## Contexto

El backtest de referencia se corrió históricamente sobre
`mnq_data_zp/mnq_extended_m5.csv`, que contiene ~54% de datos proxy (el MNQ
Micro E-mini no existió antes de mayo 2019; las filas `is_real_mnq=False` son
un proxy NQ rescalado). Ese dataset NO debe usarse para declarar una línea base
definitiva ni para la comparación final de fidelidad AMD+CRT+EMA.

El candidato canónico es `databento_mnq/databento/MNQ_M5.csv` (Databento directo,
990,452 velas M5, 2010-06-07 → 2026-09-03, sin proxy, sin duplicados). Se
verificó que ambos datasets tienen precios idénticos en los timestamps comunes
(diferencia de close = 0.0) y la misma cobertura horaria (~24h). La diferencia
es solo de CANTIDAD de barras (extended tiene 139,030 más, la mayoría pre-2019).

## Objetivo

Correr el backtest AMD+CRT sobre Databento y producir una tabla de resultados
(base y EMA10/4h) comparable con el baseline del extended, documentando las
diferencias. NO declarar aún cuál es "mejor" — solo medir y reportar.

## Qué debe hacer Codex

1. Crear un script (o usar el loader existente `src.backtest.mnq_csv.load_mnq_csv`)
   que cargue `MNQ_M5.csv` de Databento a velas M5.

2. Correr el backtest con la MISMA configuración del baseline del extended
   (ver `docs/refactor/baseline-and-data-audit.md`):
   - `BacktestConfig`: initial_balance=50_000, risk_per_trade=0.01,
     fixed_quantity=1, max_hold_minutes=60, commission_per_side = friction
     (2 pts round-trip × dollar_per_point / 2), slippage_points=0,
     dollar_per_point=2.0, bar_interval_seconds=300.
   - Estrategia `AmdCrtStrategy()` para base, y `AmdCrtStrategy(use_ema_filter=True)`
     para EMA10/4h.

3. Reportar para cada configuración: n trades, win rate, expectancy, PF, maxDD_R.

4. Comparar contra el baseline del extended y documentar las diferencias en un
   archivo markdown en `docs/refactor/` (ej. `databento-baseline.md`).

## Criterios de aceptación

1. El backtest corre completo sobre Databento (990k velas) sin errores.
2. Se reportan las mismas métricas (n, win, exp, PF, maxDD_R) para base y EMA.
3. Se produce un doc markdown con: la tabla de resultados Databento, la tabla
   del baseline extended (copiada de `baseline-and-data-audit.md`), y una
   columna/nota de diferencia.
4. NO se declara cuál dataset es "mejor" — solo se documentan las diferencias
   objetivas de resultados.
5. NO se ejecuta la comparación final de fidelidad (824 vs 149). Eso es otra
   tarea posterior.

## Qué NO hacer

- NO modificar `src/` ni `tests/` (esto es solo ejecución + documentación).
- NO tocar los datasets (solo LEER el Databento MNQ_M5.csv).
- NO commitear. Dejar el script y el doc para que Hermes revise.

## Notas

- El script puede quedar como un artefacto de investigación en
  `docs/refactor/` o en `scripts/` (a elección de Codex, pero documentar la
  ubicación). NO debe ser un entry point de producción sin revisión.
- Si el backtest tarda, dejarlo correr en background y reportar al terminar.

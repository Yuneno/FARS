# BACKTEST_MVP_REPORT.md

Backtest end-to-end MVP para FARS (MNQ, barras de 1 minuto).

**Worktree:** `/Users/ricardomedina/Documents/FARS-backtest-mvp`
**Rama:** `feature/backtest-mvp` (base `agent/rt-night-2` @ `467318e`)
**Estado:** sin commits (por instrucción); cambios sin commit para revisión de Codex.

---

## 1. Estado inicial encontrado

- Rama base `agent/rt-night-2` @ `467318e`: Core completo (phases 1–11C) + RT-0..8
  + conector REST read-only de ProjectX/TopstepX (`retrieveBars`, `Contract/search`).
- `src/engine.py` (simulación R-multiple), `src/monte_carlo.py`, `src/ingestion.py`
  (Phase 8A CSV), `src/types.py` (`Trade`, `FundedAccountRules`) ya existían.
- El conector `ProjectXClient.retrieve_bars` ya hacía: una sola request (máx 20 000),
  orden cronológico, rechazo de duplicados, validación OHLCV. El CLI `fars-projectx`
  ya exponía `bars` (una sola request, sin paginación).
- **No existía** ninguna estrategia ejecutable SMC-FVG/SMC-OB/SRT ni reglas de
  "Juan Carlos" en el repositorio (solo menciones en specs de mantenerlas
  "identificables"). No existía backtest end-to-end ni modo batch.

## 2. Qué faltaba realmente

1. **Paginación** para rangos mayores a 20 000 barras + manifiesto reproducible.
2. **Persistencia** de barras en `data/raw` + carga.
3. **Una estrategia ejecutable** sin look-ahead (no había).
4. **Ejecutor** bar-a-bar (entrada, TP/SL, comisión, slippage) que conecte con el engine.
5. **Split cronológico** IS/OOS.
6. **Modo batch** con Monte Carlo reutilizando `run_monte_carlo`.
7. **CLI** (`fars-backtest`) con subcomandos `download`/`smoke`/`batch`.

## 3. Archivos modificados / creados

**Creados:**
- `src/backtest/__init__.py`
- `src/backtest/history.py` — descarga paginada + dedup + gaps + manifiesto + persistencia + `synthetic_bars`
- `src/backtest/strategy.py` — `Strategy` protocol + `BreakoutStrategy` (placeholder)
- `src/backtest/executor.py` — backtest bar-a-bar → `Trade` → `run_simulation`
- `src/backtest/pipeline.py` — split cronológico + reporte
- `src/backtest/batch.py` — grid + Monte Carlo + JSONL resumible
- `src/backtest/cli.py` — entry point `fars-backtest`
- `tests/test_backtest_history.py`, `tests/test_backtest_executor.py`

**Modificados:**
- `pyproject.toml` — entry point `fars-backtest` + package `src.backtest`
- `.gitignore` — ignora `data/raw/*.csv|json` y `data/processed/*.csv|json|jsonl`

**Generados (no versionados):** `data/processed/backtest_summary.json`,
`batch_results.jsonl`, `batch_summary.json`, `in_sample_trades.csv`.

## 4. Dataset descargado (REAL)

**Descarga real exitosa.** Contrato activo `CON.F.US.MNQ.U26` (MNQU6), tick 0.25 /
tick_value 0.5 ($2/punto). Rango solicitado 2026-08-31→2026-09-05; **recibido**
2026-08-31T00:00 → 2026-09-04T20:59 UTC. **6,780 barras** de 1 minuto, 0 duplicados,
4 gaps (pausas de sesión overnight). Manifiesto con SHA-256 reproducible en
`data/raw/MNQ_1m_manifest.json`.

Credenciales: se cargan de `/Users/ricardomedina/Documents/FARS/.env` vía
`--env-file` (sin copiar el `.env`, sin imprimir valores, sin tocar Git).

## 5. Estrategia utilizada y supuestos

- **Estrategia:** `BreakoutStrategy` (canal Donchian sobre barras cerradas).
  **PROVISIONAL** — no es una estrategia validada ni reglas de Juan Carlos.
- Parámetros explícitos: `lookback=20`, `stop_atr_mult=1.5`, `target_atr_mult=3.0`,
  `min_atr=0.25`, `max_bars_held=100`.
- **Supuestos provisionales** (solo para correr el MVP, marcados en el reporte):
  - `commission_per_side=0.62` (por contrato por lado) — no cotizado por el proveedor.
  - `slippage_points=0.25` (1 tick MNQ) por fill de mercado.
  - `dollar_per_point=2.0`, `tick_size=0.25` (MNQ micro).
  - política intrabar conservadora: si TP y SL caen en la misma barra (incluida la
    barra de entrada), el SL se asume primero.
- **Sin look-ahead:** señales solo de barras cerradas; entrada en la apertura de la
  barra siguiente; salidas por política intrabar explícita.

## 5b. SMC-FVG / SMC-OB — hallazgos (NO integrado: faltan reglas)

Búsqueda read-only en todos los worktrees encontró documentación **parcial**:

- **Parámetros SMC-FVG** (`data historica/SMC-FVG_parametros.txt`): `f=0.5`,
  `swing_w=5`, `target_rr=3.0`, `wait=48`. M5 (M1 agregado), mercados MNQ/YM/ES/GC.
  Salidas TP/SL/BE, parcial `f` en tp1 (1R) con break-even.
- **Parámetros SMC-OB** (`data historica/SMC-OB_parametros.txt`): `choch_only=1`,
  `f=0.0`, `ob_lookback=60`, `swing=10`, `target_rr=3.0`.
- **Trades pre-calculados**: `SMC-FVG_trades.csv` (137,048) y `SMC-OB_trades.csv`
  (29,663) con columna `R` (R-multiple por trade).
- **Experimento existente** en `FARS copy/research/experiments/strategy_comparison/`
  (lee los CSVs, verifica SHA-256, ordena/particiona, aplica costes y Monte Carlo —
  NO reimplementa la estrategia).

**Lo que FALTA (algoritmo de entrada):** los módulos `strategies/strat_smc_fvg.py` y
`strategies/strat_smc.py` **no existen en ningún worktree** — viven en un codebase
externo. Sin ellos no puedo reconstruir la detección de FVG/OB, swing, CHOCH ni el
significado exacto de `wait`/`swing_w` sin inventar reglas.

**Decisiones concretas que necesito de Ricardo** (para integrar la estrategia real):
1. El código (o especificación) de `strat_smc_fvg.py` / `strat_smc.py`: cómo se
   identifica el FVG y el order block, y la condición exacta de entrada.
2. Semántica de `wait=48`, `swing_w=5`, `swing=10`, `choch_only=1`, `ob_lookback=60`.
3. Mecánica exacta del parcial `f` y del break-even en tp1 (qué fracción cierra,
   cómo se mueve el stop a entrada).
4. Qué `target_rr` usar (los params citan drift 2.0 vs 3.0 entre el bridge frozen
   y los defaults del backtest).
5. Si autoriza traer el módulo externo de estrategia a FARS (y dónde), o si se
   prefiere un adaptador sobre los CSVs de trades pre-calculados.

Donchian permanece como **placeholder técnico** (valida el pipeline, no la estrategia).

## 6. Comandos exactos

Entorno: `/opt/anaconda3/bin/python` (conda base, Python 3.13.5). Sin instalar el
paquete, usar `python -m src.backtest.cli`. Tras `pip install -e .`, el binario es
`fars-backtest`.

1. **Descargar / actualizar datos reales** (requiere `FARS_PROJECTX_USERNAME` y
   `FARS_PROJECTX_API_KEY` en el entorno o `.env`):
   ```
   python -m src.backtest.cli download --start 2026-08-01T00:00:00Z --end 2026-08-15T00:00:00Z --symbol MNQ --out-dir data/raw
   ```
   (o `--contract-id <id>` explícito; si se omite, busca "MNQ" y elige el contrato activo.)

2. **Smoke backtest** (sintético, sin credenciales):
   ```
   python -m src.backtest.cli smoke --synthetic --n-bars 5000 --out-dir data/processed
   ```
   (o con datos reales: `--bars-csv data/raw/MNQ_1m_bars.csv`)

3. **Corrida extensa (batch + Monte Carlo)**:
   ```
   python -m src.backtest.cli batch --synthetic --n-bars 3000 --seeds 1,2,3 --mc-simulations 1000 --out-dir data/processed
   ```

4. **Ver resultados:** `data/processed/backtest_summary.json` (smoke),
   `data/processed/batch_summary.json` + `batch_results.jsonl` (batch),
   `data/processed/in_sample_trades.csv` (trades).

## 7. Resultados del smoke backtest (sintético, seed 0)

- In-sample (3500 barras): 203 trades, win_rate 31.5%, profit_factor 0.36,
  expectancy −22.8 USD/trade, net_pnl −4631.8, max DD 9.3%, terminal `daily_loss`.
- Out-of-sample (1500 barras): 87 trades, win_rate 39.1%, profit_factor 0.53,
  expectancy −14.8 USD/trade, net_pnl −1287.3, max DD 2.6%, terminal `completed`.
- **technical_status: PASS** (pipeline corre y produce resultados válidos; la
  estrategia placeholder pierde dinero, lo cual es honesto y esperado).

Batch (5 escenarios × 2 seeds, MC 200): P_pass = 0.000 en todos (la estrategia
placeholder no pasa las reglas de cuenta fondeada); variaciones de comisión/slippage/
lookback/stop se registran y resumen correctamente.

## 8. Resultados de las pruebas

```
python -m pytest -p no:debugging tests/test_backtest_history.py tests/test_backtest_executor.py -q
```
→ **20 passed**.

Suite determinista completa:
```
python -m pytest -p no:debugging -m "not statistical" -q
```
→ **948 passed, 10 deselected** (sin regresiones).

Cobertura de pruebas obligatorias: paginación multi-chunk, respuestas fuera de orden,
duplicados, ventanas solapadas (dedup), dataset vacío, OHLC inválido, ausencia de
credenciales, idempotencia (roundtrip persistencia), P&L de MNQ, comisión/slippage,
TP+SL en la misma barra (conservador), no look-ahead (entrada en apertura siguiente),
split cronológico, reproducibilidad con seed, estrategia sin señales, persistencia de
resultados.

## 9. Pendientes reales

1. **Credenciales** para descargar datos reales de MNQ (bloqueo externo). El flujo
   está listo; falta `FARS_PROJECTX_USERNAME` + `FARS_PROJECTX_API_KEY`.
2. **Estrategia real**: reemplazar el `BreakoutStrategy` provisional por una estrategia
   con reglas documentadas (SMC-FVG/SMC-OB/SRT) cuando existan.
3. **Walk-forward completo** no implementado (solo split cronológico reproducible).
4. **Comisión/slippage** provisionales: reemplazar por valores cotizados del proveedor.
5. **Performance** del ejecutor (historial por barra): suficiente para smoke/batch
   actuales; optimizar antes de corridas de 100k+ barras.

## 10. Confirmación de restricciones

- **No** se tocó Phase 11D.
- **No** se implementó RT-9.
- **No** se envían/modifican/cancelan órdenes; no se opera la cuenta challenge.
- `LIVE_EXECUTION_ENABLED` sigue en `False` (no modificado).
- ProjectX/TopstepX permanece read-only (solo `_READ_ONLY_PATHS`; sin `place_order`).
- **No** se modificó `src/realtime/interfaces.py`.
- **No** se imprimieron credenciales.
- **No** hubo push, merge, rebase ni commit; **no** se tocó `main`.
- No se fabricaron trades ni se afirmó rentabilidad.

---

*Fin del reporte.*

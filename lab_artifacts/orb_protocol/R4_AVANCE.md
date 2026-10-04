# Reporte de Avance R4 — Cierre de Bloqueantes ORB

## Tarea
Cierre riguroso de los 5 bloqueantes CRITICAL y 3 WARNING dictaminados por Codex en el re-review de ORB (`_codex_orb_rereview.log`), alineando el motor de ejecución, la simulación bootstrap monetaria, la trazabilidad temporal del PnL y la suite de pruebas directas sobre el código de producción.

---

## Cambios hechos

### 1. CRITICAL 1: Stop/target evaluado en la barra de ejecución y rechazo de fills fuera de sesión
- **Archivos:** [`paper_bot.py`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/paper_bot.py)
- **Lógica:**
  - Se extrajo e implementó la función modular [`check_trade_exit(trade, high, low, close, t_now, exit_eod_time)`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/paper_bot.py#L295-L355) siguiendo las convenciones conservadoras de FARS (anclaje al precio efectivo de fill, gaps de apertura, evaluación prioritaria de stop ante barras ambiguas donde tocan stop y target simultáneamente, y salida simétrica a fin de sesión EOD 14:30 para longs y shorts).
  - En [`OrbSignalGenerator.on_bar`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/paper_bot.py#L180-L198), se restringió el rango de emisión de señales a `TRADE_START <= t < TRADE_END` (evitando que barras a las 14:30 emitan señales que se ejecuten fuera de sesión a las 14:35).
  - En [`run_replay`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/paper_bot.py#L398-L445), antes del fill de una orden pendiente se valida `TRADE_START <= t_now <= TRADE_END`. Si caería fuera de sesión, se descarta con motivo `REJECT_SESSION`.
  - Inmediatamente tras ejecutar el fill en la barra $t+1$, se evalúa [`check_trade_exit`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/paper_bot.py#L295-L355) sobre los extremos High/Low de esa misma barra de ejecución. Si se dispara stop o target intra-barra, la posición se liquida inmediatamente registrando su PnL y snapshot de cuenta.

### 2. CRITICAL 2: Regla de cupo mensual calendario en B3
- **Archivos:** [`bootstrap_camino.py`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/bootstrap_camino.py)
- **Lógica:**
  - En [`run_unwrapped_mbb`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/bootstrap_camino.py#L127-L141), se sustituyó el conteo rodante por la regla real de producción: cupo mensual por mes calendario (`YYYY-MM`), reseteando el contador al cambiar de mes y acumulando el conteo por mes de remuestreo, marcando `hit_max_ops = True` si las operaciones en el mes superan 42.

### 3. CRITICAL 3: Simulación de camino monetario real en B3
- **Archivos:** [`bootstrap_camino.py`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/bootstrap_camino.py)
- **Lógica:**
  - En [`run_unwrapped_mbb`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/bootstrap_camino.py#L90-L100), se eliminó la asunción de $1.000 USD fijos por R.
  - Se implementó la lógica de producción [`position_size`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/paper_bot.py#L260-L280): sizing entero dependiente del equity actual (`risk_usd = min(equity * 0.01, 1000.0)`), determinando el número entero de contratos `size = int(risk_usd / 200.0)`. Si el equity decrece por pérdidas consecutivas, el sizing y riesgo monetario se reducen fielmente.
  - Se simulan los límites de drawdown trailing monetario ($8.000) y pérdida diaria acumulada ($2.000) con el equity real trade a trade.

### 4. CRITICAL 4: Exportación de `exit_time` real y eliminación del fallback ciego a 14:30
- **Archivos:** [`run_orb.py`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/run_orb.py), [`separar_senales.py`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/separar_senales.py)
- **Lógica:**
  - En [`run_orb.py:ledger_fars`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/run_orb.py#L106-L125), se exporta la columna `exit_time` directamente del objeto de trade retornado por el backtest (`t.exit_time`), preservando el instante real del stop/target o EOD.
  - En [`separar_senales.py:get_trade_exit_time`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/separar_senales.py#L52-L78), se eliminó la asignación ciega de 14:30 a todos los trades; ahora se parsea el `exit_time` real del trade y solo se recurre a 14:30 si la razón de salida documentada es explícitamente fin de sesión (`eod`).
  - Se incluyó `rejected_trades` en el retorno de [`run_separation`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/separar_senales.py#L274) para introspección y tests.

### 5. CRITICAL 5: Tests directos sobre el código de producción
- **Archivos:** [`test_paper_bot_periods.py`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/test_paper_bot_periods.py), [`test_investigacion.py`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/test_investigacion.py)
- **Lógica:**
  - `test_f_causality_no_lookahead_and_effective_stop`: Ejecuta [`paper_bot.run_replay`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/paper_bot.py#L380) con un DataFrame real vs un DataFrame con barras futuras invertidas, demostrando la invariancia estricta de la señal y stop en $t$ y $t+1$; comprueba que una orden long a 1058 con Low=850 es liquidada en su propia barra de ejecución por stop en 958 (no sobrevive artificialmente); y comprueba el rechazo `REJECT_SESSION` para señales emitidas a las 14:30 que caerían a las 14:35.
  - `test_h_symmetric_eod_exit_for_long_and_short`: Evalúa directamente la función de producción [`check_trade_exit`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/paper_bot.py#L295) para posiciones largas y cortas a las 14:30 y en barras de choque ambiguo (stop prioritario).
  - `test_atribucion_pnl_timestamp_salida`: Ejecuta la función de producción [`separar_senales.run_separation`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/separar_senales.py#L150) con datos reales donde Trade 1 saliendo a las 10:45 liquida su PnL antes de Trade 2 (11:30), provocando el rechazo por pérdida diaria acumulada, mientras que si sale a las 14:30 Trade 2 es aceptado.
  - `test_b3_probabilidades_responden_a_datos`: Test de sensibilidad extrema que FALLE ante hardcode (pérdidas severas continuas forzadas $\to P=1.0$; ganancias puras $\to P=0.0$; $>42$ operaciones mensuales $\to P=1.0$; $\le 42$ operaciones $\to P=0.0$; sizing escalonado decreciente ante menor balance).

### 6. WARNINGS 1, 2 y 3
- **WARNING 1 (Clasificación de Bootstrap):** En [`bootstrap_camino.py:classify_bootstrap_method`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/bootstrap_camino.py#L340-L352), se aseguró que estados `unsupported_or_inconclusive` se clasifiquen como `"unsupported (unsupported_or_inconclusive)"` y NUNCA como `"CBB"`. Se actualizó [`test_investigacion.py:test_reporte_metodo_real_bootstrap`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/test_investigacion.py#L257) para comprobar este invariante.
- **WARNING 2 (Sincronización de artefactos):** En [`paper_run.json`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/paper_run.json), se actualizó la regla de configuración a `"max_trades": null`. En [`INFORME_ORB.md`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/INFORME_ORB.md), se documentó explícitamente en la Sección 6 que el motor de riesgo opera con `max_trades=None` y que el cupo de 42 se gobierna a nivel de estrategia mensual calendario.
- **WARNING 3 (JSON sanitization):** En [`run_orb.py`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/run_orb.py#L80-L95) y [`paper_bot.py`](file:///E:/FARS-LAB/FARS/lab_artifacts/orb_protocol/paper_bot.py#L485), se agregó sanitización `_clean_float` para que valores `t_stat` no finitos (`NaN`, `Inf`) se serialicen como `None`, y se impuso `allow_nan=False` en todas las llamadas a `json.dumps`.

---

## Hallazgos
- **Ninguno bloqueante**: No se encontraron problemas conceptuales ni de integridad nuevos en la arquitectura tras las correcciones.
- **Menor**: En `separar_senales.py`, `run_separation` generaba `rejected_trades` internamente para el conteo de razones y la aserción $S = A + R$, pero no lo exponía en el diccionario de retorno. Se añadió al diccionario retornado para inspección completa de los filtros de riesgo.

---

## No revisado
- No se corrieron los scripts de regeneración pesada (`run_orb.py`, `separar_senales.py`, `bootstrap_camino.py`, `paper_bot.py` en vivo) en cumplimiento estricto de las reglas del sandbox del encargo. Se proporcionan los comandos exactos para Hermes a continuación.
- No se ejecutó ningún comando `git` ni modificaciones a archivos fuera de `lab_artifacts/orb_protocol/`.

---

## Confianza
**Alta**:
- Todos los 5 CRITICAL y 3 WARNING cuentan con fixes concretos y pruebas automatizadas que ejercitan el código de producción.
- La suite completa de 22 pruebas pasó en verde:
  `22 passed in 44.16s`
  (`test_investigacion.py` 8/8, `test_paper_bot.py` 6/6, `test_paper_bot_periods.py` 8/8).

---

## Comandos para Hermes (Regeneración de Artefactos)

Para actualizar en disco los ledgers, metadatos y resúmenes estadísticos con los fixes aplicados, ejecutar en PowerShell:

1. **Regenerar ledger FARS con `exit_time` real:**
```powershell
& "E:/FARS-LAB/.venv-fars/Scripts/python.exe" lab_artifacts/orb_protocol/run_orb.py
```

2. **Regenerar separación S/A/R y exportar ledger de órdenes aceptadas:**
```powershell
& "E:/FARS-LAB/.venv-fars/Scripts/python.exe" lab_artifacts/orb_protocol/separar_senales.py --csv lab_artifacts/orb_protocol/ledger_orb_fars_stop.csv --export-accepted lab_artifacts/orb_protocol/ledger_orb_fars_accepted.csv
```

3. **Regenerar bootstrap dual (bruto y permitido) con cupo mensual calendario y sizing monetario real:**
```powershell
& "E:/FARS-LAB/.venv-fars/Scripts/python.exe" lab_artifacts/orb_protocol/bootstrap_camino.py --csv lab_artifacts/orb_protocol/ledger_orb_fars_stop.csv --accepted-csv lab_artifacts/orb_protocol/ledger_orb_fars_accepted.csv
```

4. **Regenerar replay del paper bot con evaluación intra-barra de stop/target y `max_trades: null`:**
```powershell
& "E:/FARS-LAB/.venv-fars/Scripts/python.exe" lab_artifacts/orb_protocol/paper_bot.py --mode replay
```

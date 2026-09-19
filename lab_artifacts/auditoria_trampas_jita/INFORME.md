# Informe de Auditoría Anti-Trampas: Cotejo de FARS frente a los Defectos de jita-bot

**Fecha:** 2026-09-19  
**Rama de trabajo:** `auditoria-trampas-jita`  
**Objetivo:** Determinar mediante inspección estática rigurosa de código si el framework de backtest y ejecución de FARS arrastra alguno de los vicios metodológicos y artefactos del resolutor identificados en la auditoría de `jita-bot` (`INFORME_REMEDIDA_V8_2026-09-19.md`).  
**Reglas aplicadas:** Cero modificaciones a `src/`, `tests/` y `config.yaml`. Sin ejecuciones de suites ni backtests (evidencia documental y estática exclusiva).

---

## 1. Tabla Resumen Ejecutiva

| # | Pregunta / Aspecto Auditado | Veredicto | Evidencia Principal (archivo:línea) | Implicación en una línea |
|---|---|---|---|---|
| **1.1** | **TP en la propia vela del fill** | **DIFIERE** | `src/backtest/executor.py:441-465` (legacy)<br>`src/backtest/executor.py:908-965` (enhanced) | **FARS arrastra el defecto #1 de jita**: cuenta TP en la misma vela M5 del fill; en límites (`pending_limit_entry`), el extremo previo al fill se computa como win. |
| **1.2** | **Desempate SL / TP en el mismo marco** | **OK** | `src/backtest/executor.py:443-463`<br>`src/backtest/executor.py:910-947`<br>`src/backtest/intrabar.py:71-74` | A diferencia de jita, FARS aplica desempate conservador estricto (gana SL) y nunca excluye el trade como ambiguo. |
| **1.3** | **Marco de resolución e Intrabar M1** | **DIFIERE** | `src/backtest/executor.py:1062-1072`<br>`src/backtest/intrabar.py:42-81` | Resuelve en M5; M1 es un fallback opt-in que **solo se activa si tocan ambos (SL y TP)**, jamás si en la vela del fill solo se tocó el TP. |
| **1.4** | **Fin de datos (`unresolved`)** | **OK** *(declarado)* | `src/backtest/executor.py:594-609, 1008-1040`<br>`src/backtest/pipeline.py:88-109` | Por defecto no cierra forzado a mercado: queda fuera de trades cerrados, pero se declara explícitamente en `unresolved_positions` e `incomplete=True`. |
| **2.1** | **Denominador WR / E[R]: Empates SL/TP** | **OK** | `src/backtest/executor.py:1164-1176`<br>`src/metrics.py:83-135` | Los empates se liquidan como `stop_loss`, computan como pérdida en el numerador y permanecen en el denominador de WR y E[R]. |
| **2.2** | **Denominador WR / E[R]: Cierres por tiempo** | **OK** | `src/backtest/executor.py:474-524, 807-829`<br>`src/backtest/executor.py:1164-1176` | Los time exits generan `ExecutedTrade`, computan en `n = len(trades)` y afectan íntegramente a WR, PF y E[R]. |
| **2.3** | **Denominador WR / E[R]: Trades sin fill** | **OK** *(declarado)* | `src/backtest/executor.py:382-387, 879-882`<br>`src/backtest/smc_fvg.py:202-208` | Las órdenes límite no ejecutadas no entran en `trades` (se auditan como `expired` en `decisions`); no inflan el denominador de trades ejecutados. |
| **2.4** | **Exclusión oculta del denominador** | **OK** | `src/backtest/pipeline.py:83-109`<br>`src/backtest/cli.py:355, 455` | El runner estándar imprime el JSON completo con `raw_n_trades`, `raw_gap_rejections`, `raw_unresolved_positions` e `incomplete`. |
| **3.1** | **Ventanas horarias y bloqueos (Vivo vs BT)** | **DIFIERE** | `src/backtest/smc_fvg.py:55-89`<br>`src/backtest/smc_ob.py:151-193`<br>vs `lab_artifacts/s1_protocol/session_runner.py:533-557` | SMC-FVG y SMC-OB operan 24/7 en backtest sin filtro horario, pero la sesión shadow corta a las 16:00 ET. (EMAS y ORB sí tienen ventana en BT). |
| **3.2** | **Caducidad de límites (Vivo vs BT)** | **DIFIERE** | `src/backtest/executor.py:879-882`<br>vs `src/realtime/paper.py:73-78, 123-132` | El backtest cancela límites tras 36-48 barras M5 (3-4h), pero el camino vivo paper no gestiona colas de límites (fill a mercado simulado inmediato). |
| **3.3** | **Flatten de cierre de sesión** | **DIFIERE** | `src/backtest/executor.py:328-333, 468-472`<br>vs `src/realtime/connectors/projectx.py:38-40` | El backtest **no modela flatten diario** a las 16:00 ET para SMC (posiciones pernoctan días/fines de semana), lo que en cuenta real causaría rescisión. |
| **3.4** | **Tipo de orden (Límite vs Mercado)** | **DIFIERE** | `src/backtest/smc_fvg.py:273`, `src/backtest/smc_ob.py:364`<br>vs `src/realtime/paper.py:73, 108-136` | Backtest modela entrada límite pasiva a precio exacto sin slippage de entrada; el camino shadow solo emite intents a mercado. |
| **4.1** | **Descuento de comisión Y slippage en BT** | **OK** | `src/backtest/executor.py:257-274, 477-501`<br>`src/backtest/executor.py:780-806` | La matemática del motor descuenta rigurosamente comisión (2 lados) y slippage adverso en patas de mercado cuando están configurados. |
| **4.2** | **Coincidencia con escenarios de coste** | **DIFIERE** *(riesgo)* | `src/backtest/executor.py:70-71`<br>`src/backtest/smc_fvg.py:265-266`<br>`lab_artifacts/run_c1_walkforward.py:10-12` | Los runners walkforward aplican los 3 escenarios correctos, pero las funciones fábrica (`smc_fvg_config`, etc.) fijan coste 0 por defecto si no se sobreescriben. |
| **5.1** | **Duplicados: Resolución de trades** | **DIFIERE** | `src/backtest/executor.py:354-620` (legacy)<br>`src/backtest/executor.py:654-1053` (enhanced) | Existen dos bucles de simulación completos e independientes; un fix en la resolución legacy no llega a la resolución enhanced. |
| **5.2** | **Duplicados: Drawdown** | **DIFIERE** | `src/backtest/executor.py:1188`<br>`src/backtest/pipeline.py:223`<br>`src/metrics.py:185`<br>`src/account.py:128-157`<br>`src/account_engine.py:461` | Conviven 5 implementaciones distintas de drawdown (% equity, $ acumulado, R-multiples, cuenta v1 y trailing floor Apex v2). |
| **5.3** | **Duplicados: R-múltiple** | **DIFIERE** | `src/backtest/executor.py:481, 820`<br>vs `src/tsfm_adapter.py:954`<br>vs `src/account_data.py:35` | Conviven dos definiciones incompatibles: Budgeted R (`net_pnl / 500`) vs Stop R (`net_pnl / dollar_risk_at_stop`). |

---

## 2. Detalle por Pregunta

---

### PREGUNTA 1: Resolutor de Trades

> **Pregunta:** Localiza dónde el backtest resuelve fill, SL, TP, empates y fin de datos. ¿En qué marco resuelve (M1/M5/M15)? ¿Cuenta el TP en la MISMA vela del fill, o exige que sea posterior / que la vela cierre más allá? ¿Quién gana cuando SL y TP se tocan en el mismo marco?

#### 1.1 Localización de los resolutores
El backtest cuenta con **dos resolutores de barras** en `src/backtest/executor.py`, despachados dinámicamente por la función `run_backtest()` (`src/backtest/executor.py:1056-1073`):
1. **Resolutor Legacy (`_run_backtest_legacy`):** `src/backtest/executor.py:354-620`.
   - Utilizado cuando `_uses_enhanced_execution(config)` es `False` (`src/backtest/executor.py:1066-1069`).
   - Despacha las estrategias con entrada directa a mercado: AMD+CRT (`src/backtest/amd_crt.py:962`), ORB (`src/backtest/orb.py:220`), CRT-TBS (`src/backtest/crt_tbs.py:418`).
2. **Resolutor Enhanced (`_run_backtest_enhanced`):** `src/backtest/executor.py:654-1053`.
   - Utilizado cuando `_uses_enhanced_execution(config)` es `True` (`src/backtest/executor.py:623-632, 1070-1072`).
   - Despacha estrategias con órdenes límite descansando (`pending_limit_entry`), toma parcial de beneficios (`partial_take_profit_fraction`), movimiento a break-even (`move_stop_to_break_even`) o cooldown: SMC-FVG (`src/backtest/smc_fvg.py:272`), SMC-OB (`src/backtest/smc_ob.py:363`), EMAS (`src/backtest/emas.py:474`), CRT 4H (`src/backtest/crt4h.py:372`).
3. **Sub-resolutor Intrabar M1 (`resolve_intrabar_with_m1`):** `src/backtest/intrabar.py:42-81`.

#### 1.2 Marco temporal de resolución
- **Marco primario:** Resuelve en el marco temporal de las barras suministradas, que en FARS es estándar **M5** (cargadas mediante `load_mnq_csv(..., target_interval_minutes=5)` en `src/backtest/cli.py:391` o desde datasets canónicos M5).
- **Sub-resolución M1:** Si se pasa `m1_bars` a `run_backtest(..., m1_bars=...)` (`src/backtest/executor.py:1062, 1065`), estas se indexan por la marca de inicio de cada vela M5 (`src/backtest/intrabar.py:122-132`). Cuando una vela M5 toca simultáneamente Stop Loss y Take Profit (`hit_target and hit_stop`), se delega a `resolve_intrabar_with_m1` (`src/backtest/executor.py:448, 915` y `src/backtest/intrabar.py:42-81`), inspeccionando las velas M1 constituyentes en estricto orden cronológico.

#### 1.3 ¿Cuenta el TP en la MISMA vela del fill? (Veredicto: DIFIERE — Trampa detectada)
**Sí. FARS arrastra exactamente el defecto estructural #1 de jita-bot.**
- **En `_run_backtest_legacy`:**
  - En la línea `src/backtest/executor.py:388-419`, al recibirse la señal, se abre la posición al `bar.open` de la barra `i`.
  - Inmediatamente en la **misma iteración** `i` (sin pasar a la siguiente barra ni hacer `continue`), el código desciende a las líneas `425-442`:
    ```python
    hit_target = target <= bar.high if direction == "long" else target >= bar.low
    hit_stop = stop >= bar.low if direction == "long" else stop <= bar.high
    ```
  - Si `hit_target` es verdadero y `hit_stop` falso, en la línea `464-465` ejecuta:
    ```python
    elif hit_target:
        reason, exit_price = "take_profit", target
    ```
    Liquidando el trade como Take Profit en la **misma vela en que se originó la entrada**, sin exigir que sea posterior ni que la vela cierre más allá del TP.
- **En `_run_backtest_enhanced` (Crítico para órdenes límite):**
  - En la línea `src/backtest/executor.py:871`, una orden límite pendiente se llena cuando la barra actual negocia el nivel: `filled = bar.low <= pending["entry"] <= bar.high`.
  - Al llenarse, `position = pending` y el flujo **NO hace `continue`**. Cae inmediatamente a la línea `892` y evalúa la misma barra `bar = bars[i]`:
    ```python
    hit_stop = stop >= bar.low if direction == "long" else stop <= bar.high
    hit_target = target <= bar.high if direction == "long" else target >= bar.low
    ...
    elif hit_target:
        reason, fill = "take_profit", target
    ```
    (`src/backtest/executor.py:907-909, 951-965`).
  - **Mecanismo de la trampa:** En una orden límite de compra (long) en un retroceso (como en SMC-OB o SMC-FVG), el precio entra a la zona cayendo. La vela abre arriba, cae hasta el nivel límite (se llena la compra) y cierra. Sin embargo, el `bar.high` de esa vela **ocurrió antes del fill** (en la pierna bajista previa a tocar el límite). Al consultar `target <= bar.high` sobre esa misma vela, el backtest adjudica un Take Profit imposible que ya había ocurrido antes de que la orden existiera en mercado.
  - **Filtro M1 no mitiga esto:** La resolución M1 solo se invoca en el bloque `if hit_target and hit_stop:` (`src/backtest/executor.py:910`). Si en esa vela del fill solo se tocó el target (porque el stop no se alcanzó), el flujo va por el `elif hit_target:` directo (`línea 951`), **sin consultar jamás las velas M1**.

#### 1.4 ¿Quién gana cuando SL y TP se tocan en el mismo marco? (Veredicto: OK)
- Si una vela toca ambos niveles (`hit_target and hit_stop`):
  - **Con datos M1:** Si `resolve_intrabar_with_m1` detecta que M1 tocó TP antes que SL, gana TP (`src/backtest/executor.py:451, 931`). Si tocó SL antes, gana SL (`454, 935`). Si en un mismo minuto M1 se tocan ambos o no hay datos, devuelve `m1_residual_ambiguity_conservative_stop` (`src/backtest/intrabar.py:73, 80`).
  - **Sin datos M1 (o residual):** El desempate es **conservador estricto**: gana el Stop Loss (`reason = "stop_loss"`, `src/backtest/executor.py:455-463, 938-947`).
  - **A diferencia de jita-bot:** El trade **NO es catalogado como "ambiguous" ni extraído del denominador**; se contabiliza íntegramente como pérdida ejecutada.

#### 1.5 Fin de datos
- Definido en `src/backtest/executor.py:535-609` (legacy) y `998-1040` (enhanced).
- Si al terminar la última barra queda una posición abierta, depende de `config.end_of_data_policy`:
  - `"close"`: Fuerza el cierre a la última cotización `last.close` con slippage `end_of_data_slippage_points` y `exit_reason = "end_of_data"` (`src/backtest/executor.py:564-589, 1008-1009`). Entra en la lista de `trades`.
  - `"unresolved"` (**valor por defecto** en `src/backtest/executor.py:93`): La posición **no entra en la lista de trades cerrados** (`trades`). Se registra en el diccionario `open_position` y se incrementa el contador `unresolved_positions = 1` (`src/backtest/executor.py:609, 1040, 1206`).

---

### PREGUNTA 2: Denominadores

> **Pregunta:** ¿Cómo entran en WR, PF y E[R] los casos especiales (sin fill, empate SL/TP, cierre forzado/por tiempo, no resuelto)? ¿Alguna métrica los excluye del denominador sin declararlo? ¿Dónde se define y qué imprime el runner estándar?

#### 2.1 Definición matemática en el código
Las métricas del backtest se calculan en `src/backtest/executor.py:1163-1209` (`_build_result`) y en `src/metrics.py:78-135`:

```python
# src/backtest/executor.py:1163-1176
net_pnl = sum(t.net_pnl for t in trades)
n = len(trades)
wins = [t for t in trades if t.net_pnl > 0]
losses = [t for t in trades if t.net_pnl < 0]
gross_profit = sum(t.net_pnl for t in wins)
gross_loss = abs(sum(t.net_pnl for t in losses))
profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
win_rate = (len(wins) / n) if n else 0.0
expectancy = (net_pnl / n) if n else 0.0
```

Y en `src/metrics.py:83-135`:
```python
# src/metrics.py:91, 134
win_rate = sum(1 for r in r_results if r > 0) / len(r_results)
expectancy_r = float(np.mean(r_results))
```

#### 2.2 Tratamiento de cada caso especial

1. **Sin fill (rechazo por gap u orden límite expirada):**
   - *Gap de entrada:* Si la barra inmediata posterior a la señal no existe (`_entry_gap`), se rechaza la entrada y se suma a `gap_rejections` (`src/backtest/executor.py:382-387, 692-694`). **No genera trade**; no entra en `len(trades)`.
   - *Límite expirado:* En `_run_backtest_enhanced`, si expira el tiempo de espera (`remaining_wait <= 0`), la orden se descarta llamando a `note_order_expired` (`src/backtest/executor.py:879-882`). En SMC-FVG y SMC-OB se audita en la lista `decisions` con estado `"expired"` (`src/backtest/smc_fvg.py:202-208`, `src/backtest/smc_ob.py:289-295`), pero **no genera trade ejecutado**. Queda fuera del denominador de trades cerrados.
2. **Empate SL/TP:**
   - Se resuelve como `stop_loss` (`net_pnl < 0`).
   - Se agrega a `trades`. Está en `n = len(trades)` (denominador de WR y E[R]). Al tener `net_pnl < 0`, entra en `losses`, computa en `gross_loss` (denominador de PF) y no entra en `wins`. **Tratamiento 100% limpio y honesto.**
3. **Cierre forzado / por tiempo (`time_exit`):**
   - Cuando expira el tiempo (`max_hold_minutes` o `max_bars_held`), se liquida la posición con `exit_reason = "time_exit"` (`src/backtest/executor.py:474-524, 807-829`).
   - Entra en `trades` y en `n = len(trades)`. Si tras costes el `net_pnl > 0`, cuenta como win; si `net_pnl < 0`, como loss. Si `net_pnl == 0.0` (empate exacto a breakeven), suma al denominador `n`, no entra en `wins` y por tanto reduce el WR (`len(wins) / n`), tal y como documenta explícitamente `src/metrics.py:129-130`.
4. **No resuelto (`unresolved` al fin de datos):**
   - Bajo la política por defecto `end_of_data_policy = "unresolved"` (`src/backtest/executor.py:93`), la posición viva al llegar a la última barra **no se incluye en `trades`** (`src/backtest/executor.py:594-609, 1008-1040`).
   - Por tanto, **queda fuera del denominador de `raw_win_rate`, `raw_profit_factor` y `raw_expectancy`**.
   - No obstante, a diferencia de jita-bot, **no es una exclusión silenciosa**: el objeto `BacktestResult` almacena el estado en `open_position` y reporta `unresolved_positions = 1` (`src/backtest/executor.py:1206-1207`).
   - En el resumen del pipeline (`src/backtest/pipeline.py:96, 108`), se declara explícitamente:
     ```python
     "raw_unresolved_positions": result.unresolved_positions,
     "incomplete": bool(result.unresolved_positions)
     ```

#### 2.3 Qué imprime el runner estándar
El terminal runner estándar (`src/backtest/cli.py:355, 455`) imprime por `stdout` el JSON serializado por `execute_pipeline()` / `write_report()` (`src/backtest/pipeline.py:178-195`), conteniendo:
- Métricas del segmento in-sample y out-of-sample: `raw_n_trades`, `raw_win_rate`, `raw_profit_factor`, `raw_expectancy`, `raw_max_drawdown_pct`, `raw_total_commission`, `raw_total_slippage_cost`, `raw_gap_rejections`, `raw_unresolved_positions`, `incomplete`.
- Métricas rule-limited (limitadas por las reglas del motor Core): `rule_limited_net_pnl`, `rule_limited_final_equity`, `rule_limited_n_trades`, `rule_limited_terminal_condition`, `rule_limited_max_drawdown_pct`.

---

### PREGUNTA 3: Medición vs Vivo

> **Pregunta:** Localiza las reglas de entrada/salida del camino vivo (sesión real o shadow): ¿hay límites con caducidad? ¿bloqueos de ventanas horarias? ¿no-fill después de cierta hora? ¿flatten de cierre? Tabla: mecanismo → regla en vivo (archivo:línea) → ¿lo modela el backtest? (archivo:línea, o "NO LO MODELA").

#### 3.1 Arquitectura del camino vivo y shadow en FARS
En el estado actual del repositorio, la ejecución con dinero real está bloqueada en cero:
- `src/realtime/interfaces.py:22`: `LIVE_EXECUTION_ENABLED = False`.
- `src/realtime/connectors/projectx.py:33-43`: El cliente oficial de ProjectX/TopstepX prohíbe explícitamente métodos de envío o cierre de órdenes (`_FORBIDDEN_METHODS = ("place_order", "submit_order", "cancel_order", "modify_order", "close_position", "close_positions", "flatten", "buy", "sell")`).
- El camino vivo operativo es el **modo sombra (live shadow)** implementado en `lab_artifacts/s1_protocol/session_runner.py` y `lab_artifacts/s2_protocol/session_runner_ghost.py`.

#### 3.2 Tabla comparativa: Mecanismos en Vivo vs Backtest

| Mecanismo | Regla en Vivo / Shadow (archivo:línea) | ¿Lo modela el Backtest? (archivo:línea o "NO LO MODELA") | Discrepancia metodológica detectada |
|---|---|---|---|
| **Caducidad de órdenes límite** | **NO IMPLEMENTADO EN VIVO** (`src/realtime/paper.py:73, 123-132`). El adaptador paper llena inmediatamente todo intent aprobado; no mantiene colas de órdenes límite vivas. | **SÍ LO MODELA** (`src/backtest/executor.py:879-882`). Decrementa `remaining_wait` en cada barra M5 y expira la orden si no llena en 36 barras (`smc_ob.py:26`) o 48 barras (`smc_fvg.py:28`). | En vivo no existe el concepto de límite descansando; el backtest asume una ventana de 3h a 4h de persistencia pasiva. |
| **Ventana horaria de sesión / corte** | **SÍ EN SHADOW**: `lab_artifacts/s1_protocol/session_runner.py:533-535, 554-557`. Detiene la sesión a las 16:00 ET (20:00 UTC). | **DEPENDE DE LA ESTRATEGIA**:<br>- ORB: SÍ (`src/backtest/orb.py:147`, `end_hour=16`).<br>- CRT-TBS: SÍ (`src/backtest/crt_tbs.py:333`, `session_end="16:00"`).<br>- AMD+CRT: SÍ (`src/backtest/amd_crt.py:80-84`, confirmación 09:30-10:30, CRT RTH 16:00).<br>- EMAS: SÍ (`src/backtest/emas.py:151, 304`, bloquea 18, 21, 22, 23 ET).<br>- SMC-FVG / SMC-OB: **NO LO MODELA** (`src/backtest/smc_fvg.py:55-89`, `src/backtest/smc_ob.py:151-193`). | SMC-FVG y SMC-OB operan 24/7 en backtest; en sesión shadow real solo recibirían barras durante la ventana de conexión diurna. |
| **No-fill / Bloqueo en aperturas / noticias** | **NO LO MODELA EN VIVO**: `src/realtime/risk.py:157-193` evalúa circuit breaker y pérdida diaria, pero no veta aperturas de sesión ni noticias. | **SOLO EN EMAS**: `src/backtest/emas.py:152, 305` (`blackout="09:15-09:45"`). En todas las demás estrategias: **NO LO MODELA**. | No existe en FARS el módulo `evitar_aperturas` que en jita bloqueaba las 09:00-10:00 o las 03:00. |
| **Flatten diario de cierre (EOD)** | **NO EJECUTADO EN VIVO**: `src/realtime/connectors/projectx.py:40` prohíbe `flatten`. `session_runner.py:554-557` solo rompe el bucle de polling a las 16:00 ET. | **NO LO MODELA** (`src/backtest/executor.py:328-333, 468-472`). El backtest no tiene cierre forzado a las 16:00/16:59 ET. | **Discrepancia crítica**: SMC-FVG y SMC-OB tienen `max_bars_held=1_000_000_000` (`smc_fvg.py:269`, `smc_ob.py:360`). Las posiciones cruzan noches y fines de semana abiertas. En un combine real de Apex/Topstep, no estar flat antes del cierre implica suspensión inmediata de la cuenta. |
| **Tipo de ejecución (Límite vs Market)** | **MERCADO EN SHADOW**: `src/realtime/paper.py:73, 108-136` llena el intent como ejecución directa a mercado simulada. | **LÍMITE EN SMC**: `src/backtest/smc_fvg.py:273` y `src/backtest/smc_ob.py:364` configuran `pending_limit_entry = True` (fill pasivo a precio exacto sin slippage de entrada). | Mismo patrón que jita-bot: la medición asume entrada límite perfecta sin coste de cruce de spread, mientras que el camino vivo opera a mercado. |

---

### PREGUNTA 4: Costes

> **Pregunta:** ¿El backtest descuenta comisión Y slippage? ¿Con qué constantes y dónde? ¿Coincide con los escenarios de coste que usamos (canónico / realista / kai)?

#### 4.1 Descuento de comisión y slippage en el código
**Sí, el motor descuenta ambos rigurosamente**, pero con reglas distintas según el tipo de orden:
- **Slippage de entrada (`_entry_price`):** `src/backtest/executor.py:257-260`. Solo se aplica a entradas a mercado (`open_price + slip` en largos, `open_price - slip` en cortos). En órdenes límite (`pending_limit_entry: True`), el slippage de entrada es `0.0` (`src/backtest/executor.py:784-786`).
- **Slippage de salida (`_stop_fill`):** `src/backtest/executor.py:262-274`. Se aplica slippage adverso al Stop Loss (`stop - slip` en largos, `stop + slip` en cortos). Si la vela abre con un gap que salta el stop (`bar.open < stop`), liquida al open peor (`267-270`) sin añadir slippage adicional sobre el precio gapeado (`488-492, 794-799`).
- **Salidas en Take Profit:** No llevan slippage adverso (`exit_price = target`, `src/backtest/executor.py:451, 931`), asumiendo límite pasivo de salida.
- **Salidas por tiempo (`_time_exit_fill`):** `src/backtest/executor.py:276-285`. Si `time_exit_mode == "market"`, aplica `time_exit_slippage_points`. Si `time_exit_mode == "flat"`, liquida exactamente al precio de entrada (`entry`).
- **Comisiones:** Se descuentan round-trip multiplicando por 2 lados y por el número de contratos:
  `commission = config.commission_per_side * 2 * position["qty"]` (`src/backtest/executor.py:479, 780`).
- **PnL Neto:** `net_pnl = gross_pnl - commission` (`src/backtest/executor.py:480, 781`), donde `gross_pnl` ya incorpora el impacto de precios de entrada y salida desfavorables por slippage.

#### 4.2 Constantes por defecto en `BacktestConfig`
En `src/backtest/executor.py:65-96`:
- `dollar_per_point = 2.0` (MNQ)
- `tick_size = 0.25` (MNQ)
- `commission_per_side = 0.62` (\$0.62 por lado = **\$1.24 Round-Trip**)
- `slippage_points = 0.25` (**1 tick** = 0.25 puntos en MNQ)
- `time_exit_slippage_points = 0.0`
- `end_of_data_slippage_points = 0.0`

#### 4.3 Trampa de configuración en las funciones fábrica de estrategias
**Hallazgo crítico de higiene documental:** Aunque `BacktestConfig` tiene valores realistas (\$0.62 y 0.25 pts), las funciones helper de configuración por estrategia **resetean los costes a cero por defecto**:
- `orb_config()` (`src/backtest/orb.py:213-214`): `commission_per_side = 0.0`, `slippage_points = 0.0`.
- `smc_fvg_config()` (`src/backtest/smc_fvg.py:265-266`): `commission_per_side = 0.0`, `slippage_points = 0.0`.
- `smc_ob_config()` (`src/backtest/smc_ob.py:356-357`): `commission_per_side = 0.0`, `slippage_points = 0.0`.
- `emas_config()` (`src/backtest/emas.py:468-469`): `commission_per_side = 0.0`, `slippage_points = 0.0`.
- `crt_tbs_config()` (`src/backtest/crt_tbs.py:411-412`): `commission_per_side = 0.0`, `slippage_points = 0.0`.
- `crt4h_config()` (`src/backtest/crt4h.py:363-364`): `commission_per_side = 0.0`, `slippage_points = 0.0`.
- *Excepción:* `amd_crt_config()` (`src/backtest/amd_crt.py:965-966`) inyecta por defecto la fricción canónica agregada: `commission_per_side = 2.0` (\$4.00 RT) y `slippage_points = 0.0`.

*Implicación:* Si un desarrollador o runner ejecuta `run_backtest(bars, strategy, smc_fvg_config())` sin pasar overrides de coste, la medición se ejecuta **en bruto con CERO comisión y CERO slippage**.

#### 4.4 Cotejo con los 3 escenarios de investigación de FARS

| Escenario | Comisión por lado | Slippage puntos | Coste Round-Trip total | Dónde se define y utiliza |
|---|---|---|---|---|
| **Canónico** | \$2.00 | 0.00 pts | **\$4.00 RT** (fricción fija) | `lab_artifacts/run_c1_walkforward.py:10`<br>`lab_artifacts/run_c2_walkforward.py:35`<br>`src/backtest/markets.py:106` (`friction_points=2.0`)<br>`src/parallel.py:213` |
| **Canónico + 1 tick** | \$2.00 | 0.25 pts (1 tick) | **\$4.00 RT + \$1.00 slippage en stops** | `lab_artifacts/run_c1_walkforward.py:11`<br>`lab_artifacts/reprice_z5_scenarios.py:23` |
| **Realista (por tramo)** | \$0.62 | 0.25 pts (1 tick) | **\$1.24 RT + \$0.50 slippage por pata mercado** | `src/backtest/executor.py:70-71`<br>`lab_artifacts/run_c1_walkforward.py:12`<br>`lab_artifacts/run_c2_walkforward.py:36` |
| **Kai (versión C2)** | \$0.71 | 0.25 pts (1 tick) | **\$1.42 RT + \$0.50 slippage** | `lab_artifacts/run_c2_walkforward.py:37` |
| **Kai (versión Account Engine)** | \$0.50 | 0.25 pts (1 tick) | **\$1.00 RT + \$0.50 slippage** | `src/account_engine.py:48`<br>`docs/refactor/apex-profile.md:49` |

Los escenarios están implementados y probados en los runners de walk-forward, pero existe una discrepancia no resuelta entre el \$1.42 RT de C2 y el \$1.00 RT del motor de cuentas (ver `BLOCKERS.md`).

---

### PREGUNTA 5: Duplicados

> **Pregunta:** ¿Hay más de una implementación de resolución de trades, drawdown o R-multiple? Lista cada una con archivo:línea y di cuál usa el camino de medición actual (un fix en una copia no llega a la otra).

#### 5.1 Duplicados en Resolución de Trades
Se identifican **dos motores completos y paralelos** en `src/backtest/executor.py`:

1. **Motor A: `_run_backtest_legacy` (`src/backtest/executor.py:354-620`)**
   - **Camino de medición que lo usa:** Despachado por `run_backtest()` cuando `_uses_enhanced_execution == False`. Ejecuta AMD+CRT, ORB y CRT-TBS.
   - **Lógica:** Resuelve únicamente entradas al open de la barra siguiente. No admite órdenes descansando ni parciales ni mover stop a break-even.
2. **Motor B: `_run_backtest_enhanced` (`src/backtest/executor.py:654-1053`)**
   - **Camino de medición que lo usa:** Despachado por `run_backtest()` cuando `_uses_enhanced_execution == True`. Ejecuta SMC-FVG, SMC-OB, EMAS y CRT 4H.
   - **Lógica:** Implementa bucle de órdenes pendientes (`pending_limit_entry`), expiración de órdenes por barras, división discreta o continua de contratos en TP1 (`partial_take_profit_fraction`), traslado de stop a break-even y cooldown de barras.
3. **Motor C: `PaperExecutionAdapter` (`src/realtime/paper.py:108-136`)**
   - **Camino de medición que lo usa:** Sesión sombra en tiempo real (`session_runner.py`). No resuelve SL/TP de barras; solo simula el evento de fill.

> **Riesgo:** La existencia de `_run_backtest_legacy` y `_run_backtest_enhanced` constituye un **punto crítico de desincronización**. Por ejemplo, un ajuste para impedir que el TP se compute en la misma vela del fill aplicado en `_run_backtest_legacy` no tendrá ningún efecto sobre SMC-FVG o SMC-OB si no se replica exactamente en `_run_backtest_enhanced`.

#### 5.2 Duplicados en Cálculo de Drawdown
Se identifican **5 implementaciones activas** con fórmulas y unidades dispares:

1. **Drawdown Porcentual de Curva Bruta:** `src/backtest/executor.py:1181-1190`
   - `dd = (peak - equity) / peak` sobre la suma de `net_pnl` de trades cerrados en dólares. Es el campo `max_drawdown_pct` reportado en `BacktestResult`.
2. **Drawdown en Dinero Acumulado:** `src/backtest/pipeline.py:215-224`
   - `max_drawdown_money = max(max_drawdown_money, peak - cumulative)` donde `cumulative` es la suma de `trade.net_pnl`. Expresado en dólares nominales.
3. **Drawdown en Unidades de R:** `src/metrics.py:185-206` (`max_drawdown_r`)
   - `running_peak = np.maximum.accumulate(cumulative_r); drawdowns = running_peak - cumulative_r`. Expresado en múltiplos abstractos de R.
4. **Drawdown de Reglas de Cuenta v1:** `src/account.py:128-157` (`AccountState`)
   - Implementa modo estático: `(initial_balance - equity) / initial_balance` y modo trailing: `(peak_equity - equity) / peak_equity`. Evalúa si se infringe la regla para detener la simulación en `src/engine.py:175-182`.
5. **Drawdown Trailing en Dólares v2 (Apex Trailing Floor):** `src/account_engine.py:461-464` y `src/funded_rules_v2.py:85-86`
   - Calcula el retroceso monetario en dólares contra un suelo dinámico (`state.high_watermark - state.equity`), con soporte de MAE intradía.

#### 5.3 Duplicados en Cálculo de R-Múltiple
Se identifican **dos definiciones matemáticas conceptualmente incompatibles**:

1. **Definición A: "Budgeted R" (R normalizado al riesgo de cuenta):**
   - **Dónde:** `src/backtest/executor.py:481, 820` y `executed_to_core_trades` (`src/backtest/executor.py:1084, 1125`).
   - **Fórmula:** `r_result = net_pnl / (risk_per_trade * initial_balance)` (típicamente `net_pnl / $500.00`).
   - **Propósito:** Que la suma de R en el motor Core (`src/engine.py`) reproduzca exactamente el PnL monetario en dólares de la cuenta.
2. **Definición B: "Stop R" o "Effective R" (R geométrico de la orden):**
   - **Dónde:** `src/tsfm_adapter.py:948-955`, `src/account_data.py:35, 947-965`, y guardado como `effective_r` en `src/backtest/executor.py:522, 827` y `metadata["r_vs_stop"]` en `src/backtest/executor.py:1123`.
   - **Fórmula:** `r_result = net_pnl / (|entry_price - stop_price| * dollar_per_point * quantity)`.
   - **Discrepancia:** Si una operación tiene un stop ajustado que arriesga \$200 en vez del presupuesto de \$500, una ganancia de \$400 representa:
     - Según la Definición B (`tsfm_adapter`): **+2.0 R**.
     - Según la Definición A (`executor`): **+0.8 R**.

---

## 3. Conclusiones y Recomendaciones de la Auditoría

1. **FARS arrastra el defecto principal de jita-bot:** El cómputo de Take Profit en la propia vela del fill (`src/backtest/executor.py:441-465, 908-965`) sin exigir velas posteriores ni cierre confirmado está presente en el código. Esto afecta directamente a las estrategias de reversión a zonas con órdenes límite (SMC-OB, SMC-FVG y CRT 4H).
2. **FARS es limpio en desempates y denominadores:** A diferencia de jita-bot, FARS **no oculta trades ambiguos** fuera del denominador del Win Rate. Todo empate se resuelve a favor del Stop Loss (`stop_loss`), se suma al denominador y resta en la esperanza matemática.
3. **Desconexión entre Backtest y Reglas de Prop Trading:** Las estrategias SMC no tienen flatten de cierre de sesión en backtest (`max_bars_held=1e9`), permitiendo que posiciones crucen noches y fines de semana, lo que en el vivo real de Apex o TopstepX generaría descalificación inmediata.
4. **Duplicidad de código a consolidar:** La separación entre `_run_backtest_legacy` y `_run_backtest_enhanced`, así como las dos definiciones de R-múltiple, representan una deuda técnica donde cualquier corrección metodológica corre el riesgo de no propagarse a toda la suite.

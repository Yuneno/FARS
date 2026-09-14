# Registro Canónico de Versiones y Estrategias — FARS Engine

Fecha de actualización: 2026-09-14  
Rama: `bloque-b-reconciliacion`  
Base commit: `fe295d5` (Bloque B cerrado) / `0f2fb5b` (merge Bloque A1+A2)

---

## 1. Nota Formal de Corrección de Alcance (§5 del Plan Original)

> [!IMPORTANT]
> **CORRECCIÓN DE ALCANCE VERIFICADA EN CÓDIGO E HISTORIAL GIT:**  
> El plan original (§5) contemplaba la premisa de que existían *"dos implementaciones rivales con el mismo nombre"* para SMC-FVG y EMAS (un supuesto motor divergente entre ramas vs `main`).
> 
> **Esta premisa es estrictamente falsa.**  
> - La rama `feature/port-smcfvg-emas` (commit `50d0efc`, *"port SMC-FVG and EMAS strategies from kai-backtesting"*) y la rama `main` contienen **exactamente la misma implementación y lógica algorítmica**.
> - La única diferencia respecto a los commits posteriores (`a3c41eb` y `836b92b`) consiste en la adición opcional del parámetro `discrete_partial_contracts` (`src/backtest/smc_fvg.py` +2 líneas; `src/backtest/emas.py` +6 líneas) para modelar salidas parciales discretas (`floor(qty/2)`) con contratos enteros de futuros.
> - **Conclusión:** No existen motores rivales, bifurcaciones incompatibles ni versiones alternativas de código para SMC-FVG o EMAS.

---

## 2. Definición del Baseline de Parciales para Bloque C

> [!NOTE]
> **POLÍTICA DE PARCIALES (CONTINUO VS DISCRETO):**  
> - **Evaluación de Paridad (Bloque B):** Los análisis de paridad histórica con Kai/Mac emplearon parciales continuos (`discrete_partial_contracts=False`), dado que el motor original de arrays de Kai modela los parciales como fracciones exactas continuas de posición sin restricciones de lotes enteros.
> - **Baseline Canónico de Ejecución (Bloque C):** Para el Bloque C (ejecución y simulación realista de micro-futuros MNQ), el baseline canónico oficial es **`discrete_partial_contracts=True`**. Bajo esta modalidad:
>   - Salida TP1 ejecuta `floor(quantity / 2)`.
>   - Para posiciones unitarias (`quantity == 1`), no se efectúa parcial; se conserva íntegra la posición hasta TP final o stop, moviendo el stop loss a Break-Even (1R) en el disparador.
>   - Ambas configuraciones están implementadas y testeadas exhaustivamente en el motor.

---

## 3. Registro Canónico de Estrategias

*Base de cálculo de frecuencia: 7.33 años × 252 días/año = **1,847 días de trading** en el dataset canónico MNQ M5 (`timestamp >= 2019-05-06`).*

| estrategia | código fuente | commit | parámetros | frecuencia (operación explícita) | política de concurrencia | instrumento | datos | costes |
|---|---|---|---|---|---|---|---|---|
| **SMC-FVG** | [`src/backtest/smc_fvg.py`](file:///E:/FARS-LAB/FARS/src/backtest/smc_fvg.py) | `a3c41eb` *(port de `50d0efc` + `discrete_partial_contracts`)* | `tp_rr=1.5` (`smc_fvg.py:25`), `f=0.5` (`:23`), `swing_w=5` (`:24`), `wait=48` (`:26`), `min_risk_pts=8.0` (`:27`), `cooldown=6` (`:28`), `pivot_confirmation="point_in_time"` (`:41`), `move_stop_to_be=True`. Parciales: `discrete_partial_contracts=False` (paridad) / `True` (baseline canónico Bloque C). | Alta: 5,986 trades (`gross_vs_net_reconciliation.json:67-81`)<br>Operación: **5,986 / 1,847 ≈ 3.24 trades/día** (~817/año) | 1 posición a la vez (`allow_multiple_positions=False`), cooldown 6 barras M5 tras cierre (`smc_fvg.py:28`) | MNQ ($2/pto) | Databento MNQ M5 canónico (`timestamp >= 2019-05-06`, 518,237 velas M5) | Bruto ($0) / Fricción canónica ($4.00 RT/contrato = 2.0 pts, 0 slippage) |
| **EMAS** | [`src/backtest/emas.py`](file:///E:/FARS-LAB/FARS/src/backtest/emas.py) | `836b92b` *(port de `50d0efc` + `discrete_partial_contracts`)* | EMA 10/20/55/200 (`emas.py:3`, `:153-156`: `p_entry=10, p_second=20, p_pullback=55, p_bias=200`), `target_rr=3.0` (`:139`), `f=0.5` (`:138`), `confirm_closes=3` (`:140`), `atr_mult=2.0` (`:141`), `stop_min=5.0` (`:142`), `stop_max=50.0` (`:143`), `max_trades_day=3` (`:150`), `session_block="18,21,22,23"` (`:151`), `blackout="09:15-09:45"` (`:152`). Parciales: `discrete_partial_contracts=False` (paridad) / `True` (Bloque C). | Alta: 5,098 trades (`gross_vs_net_reconciliation.json:97-111`)<br>Operación: **5,098 / 1,847 ≈ 2.76 trades/día** (~696/año) | 1 posición a la vez (`allow_multiple_positions=False`), máx 3 trades/día (`emas.py:150`), filtros de sesión | MNQ ($2/pto) | Databento MNQ M5 canónico (`timestamp >= 2019-05-06`, 518,237 velas M5) | Bruto ($0) / Fricción canónica ($4.00 RT/contrato = 2.0 pts, 0 slippage) — **Control Negativo** |
| **CRT-TBS** | [`src/backtest/crt_tbs.py`](file:///E:/FARS-LAB/FARS/src/backtest/crt_tbs.py) | `a1ef293` | **Champion real** (overrides de [`run_flat_vs_market.py:60-65,71`](file:///E:/FARS-LAB/FARS/lab_artifacts/run_flat_vs_market.py#L60-L71) sobre defaults de [`crt_tbs.py:47-52`](file:///E:/FARS-LAB/FARS/src/backtest/crt_tbs.py#L47-L52)): `require_4h_bias=True` (`crt_tbs.py:47`), `require_half_zone=False` (override champion en `run_flat_vs_market.py:62`; default `True` en `crt_tbs.py:48`), `target_mode="fixed_rr"` (override champion en `run_flat_vs_market.py:63`; default `"crt"` en `crt_tbs.py:49`), `fixed_rr=2.0` (`run_flat_vs_market.py:64`, `crt_tbs.py:50`), `time_exit_mode="market"` (baseline corregido en `run_flat_vs_market.py:71`; `"flat"` en `crt_tbs.py:51` queda como variante de referencia), `cooldown_hours=4.0` (`crt_tbs.py:52`), `m5_confirm_bars=48` (`:41`), `min_risk_points=2.0` (`:44`), `max_risk_points=180.0` (`:43`), sesión 09:30–16:00 NY (`:45-46`). | Selectiva: 93 trades (`four_strategies_benchmark.json:39`, `flat_vs_market_comparison.json:6`)<br>Operación: **93 / 1,847 ≈ 0.05 trades/día** (~13/año) | 1 posición a la vez (`allow_multiple_positions=False`), cooldown 4 horas tras trade (`crt_tbs.py:52`) | MNQ ($2/pto) | Databento MNQ M5 canónico (`timestamp >= 2019-05-06`) con contexto causal H1/H4 (`crt_tbs.py:10-15`) | Fricción canónica ($4.00 RT/contrato = 2.0 pts, 0 slippage) |
| **ORB** | [`src/backtest/orb.py`](file:///E:/FARS-LAB/FARS/src/backtest/orb.py) | `e232344` | Experimental: rango 09:30–10:00 NY (`orb.py:38`), stop opuesto (`stop_mode="opposite"`, `:39`), `rr=2.0` (`:40`), `use_bias=False` (`:41`), `fade=False` (`:42`), fin entradas 16:00 NY (`end_hour=16`, `:43`), `max_hold_bars=192` M5 (16h, `:44`), `time_exit_mode="market"` (baseline corregido en [`run_flat_vs_market.py:71`](file:///E:/FARS-LAB/FARS/lab_artifacts/run_flat_vs_market.py#L71); default `"flat"` en `orb.py:45` queda como variante de referencia). | Diaria: 2,544 trades (`four_strategies_benchmark.json:735`, `flat_vs_market_comparison.json:37`)<br>Operación: **2,544 / 1,847 ≈ 1.38 trades/día** (~347/año) | 1 posición a la vez (`allow_multiple_positions=False`), rearme en midpoint (`orb.py:11`) | MNQ ($2/pto) | Databento MNQ M5 canónico (`timestamp >= 2019-05-06`) | Fricción canónica ($4.00 RT/contrato = 2.0 pts, 0 slippage) |
| **CRT 4H (Kai)** | **AUSENTE LOCALMENTE** *(sin repo `reference/kai-backtesting`)* | N/A *(código fuente inaccesible)* | Parámetros originales no inspeccionables localmente; rango 4H reportado | n=372 trades reportados en `canonical-dataset.md:56`<br>Operación: **372 / 1,847 ≈ 0.20 trades/día** (~51/año) | Desconocida | MNQ | Dataset cortado post-2019-05-06 | Reportado en bruto sin comisiones ni slippage (WR 35.5%, PF 2.689, netR +391.9 R / exp +1.054 R) — **BLOQUEADO (`reportado-sin-revisión`)** |

---

## 4. Declaración de Bloqueo Formal: CRT 4H de Kai (Tarea B4)

> [!CAUTION]
> **BLOQUEO FORMAL — ESTRATEGIA CRT 4H DE KAI:**
> 1. **Ausencia de código fuente:** El repositorio original `reference/kai-backtesting` **no está disponible en el entorno local**. No existe implementación ejecutable ni código fuente rastreable localmente.
> 2. **Imposibilidad de verificación causal:** Dado lo anterior, el trade atípico individual del **`2020-05-13 (+286.92 R)`** —que representa una fracción masiva de la supuesta ganancia de CRT 4H— **no se puede auditar, verificar causalmente ni reproducir de forma determinista**.
> 3. **Clasificación de métricas:** Los valores reportados en `docs/refactor/canonical-dataset.md:56` para CRT 4H (`n=372`, `WR 35.5%`, `PF 2.689`, `+391.9 R` / `exp +1.054 R` / `~+1,054 R bruto` en reportes previos) quedan catalogados formalmente con el estado:  
>    **`reportado-sin-revisión`**.
> 4. **Prohibición de sustitución:** En estricto apego al mandato de auditoría, **no se permite sustituir dicho trade ni imputar sintéticamente operaciones alternativas**.

---

## 5. Regla de Trazabilidad Canónica

> [!IMPORTANT]
> **REGLA DE TRAZABILIDAD CANÓNICA DE FARS:**  
> *"Todo número debe citar `archivo:línea` o artefacto; ninguno se estima de memoria."*

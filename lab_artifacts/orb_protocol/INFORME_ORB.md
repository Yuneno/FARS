# INFORME ORB — bot MNQ con estrategia externa + FARS como capa de riesgo

**Fecha:** 2026-09-28 · **Orquestador:** Hermes · **Alcance:** investigación + paper trading. Sin credenciales, sin órdenes con dinero real, sin commit/merge/push, sin tocar especificaciones ni FARS Core.

---

## 1. Comparación de repositorios (3 auditados, todos MIT)

| Criterio | `giovannibrusco/nq-intraday-breakout` | `s-k-28/nq-es-trader-5k-payout` | `prashanthaitha24/nq-atb-bot-archived` |
|---|---|---|---|
| Reglas concretas en código | ✅ muy explícitas | ✅ 12 modelos | ✅ ATR breakout + D3 |
| Licencia clara | ✅ MIT | ✅ MIT | ✅ MIT |
| Backtest reproducible | ✅ 75 tests + CI + `bias_attribution.py` | ✅ engine_v2 + Monte Carlo 25k | ⚠ datos solo 7 meses |
| Órdenes/stops/límites de riesgo | ❌ solo backtest | ✅ broker_paper/topstep/ib + límites reales | ✅ Tradovate + config |
| Paper / ruta Tradovate | ❌ | ✅ `broker_paper.py` | ✅ Tradovate |

**Decisión (aprobada por Ricardo): híbrido.** Estrategia de `nq-intraday-breakout` (el más limpio y auditable: 10 años de 1-min, tests con CI, su README *es* una auditoría de convenciones de simulación, deps solo pandas+numpy) + la capa de ejecución/riesgo de **FARS** (`src/realtime/`, ya construida). El diseño de límites de `nq-es-trader` se toma como referencia de controles.

`nq-atb-bot-archived` queda descartado como estrategia: su autor lo retiró tras su propio backtest honesto (61,1% WR, **35 meses bajo el agua**).

## 2. Reproducción del backtest — VERIFICADA contra sus números publicados

Sus datos **no están publicados** (export de MultiCharts, feed no registrado), así que la reproducción es sobre **nuestros** MNQ 1-min (Databento 2010-06 → 2026-09) en su ventana de muestra (2015-03 → 2025-04). Motor del autor **sin modificar**; la adaptación vive aparte.

| Escenario (su ventana de muestra) | trades | Sharpe | Max DD | t-stat | Neto |
|---|---:|---:|---:|---:|---:|
| **Nosotros, corregido (stop fill)** | 1.788 | **0,99** | **−16,78%** | 3,18 | $2.765.105 |
| Él publica (corrected, stop fill) | 1.725 | 1,02 | −16,82% | 3,24 | $2.800.810 |
| Nosotros, legacy (close fill) | 1.798 | 1,06 | −12,21% | 4,06 | $4.325.999 |
| Él publica (legacy, close fill) | 1.728 | 0,95 | −15,06% | 3,63 | $3.287.392 |

**Conclusión:** el motor reproduce sus resultados con un proveedor de datos distinto (Databento MNQ vs MultiCharts NQ): Sharpe 0,99 vs 1,02, DD −16,78% vs −16,82%, t 3,18 vs 3,24, +3,6% de trades. Diferencia residual atribuible al feed. **El backtest es reproducible.**

## 3. Auditoría causal / costes / ejecución

**Sin look-ahead encontrado.** Verificado en `backtest.py`:
- La ventana (08:30–10:00 CT) se construye con `window_end` **exclusivo** y `window_ready` solo se activa al pasar las 10:00 → la barra que define el rango nunca puede ser la que rompe.
- Entrada `stop`: `max(window_high, row.open)` → si abre por encima del nivel se paga el open (conservador), no el nivel.
- **Stop gana en barras ambiguas** (chequeo de SL antes de TP) → supuesto conservador ante ambigüedad intrabarra.
- **Gap-aware fills**: si la vela abre a través del stop, llena al open.
- Sin salidas en la barra de entrada ("intra-bar sequencing from OHLC alone is unknowable" — documentado por el autor como limitación, no oculto).
- Cada convención discutible es un flag (`SimFlags`): el motor no esconde supuestos.

**Bugs / problemas hallados (3):**
1. **(adaptación mía)** los timestamps salían *naive* → FARS bloqueaba `temporal_analysis` y `daily_rule_simulation`. Corregido: tz-aware `America/Chicago` (`fix_ledger_tz.py`).
2. **(mío, de medición)** usar la equity del motor con `daily_mark_to_market=False` infla el Sharpe (1,84). La construcción correcta es `legacy_equity_curve` del propio repo → 1,06. Verificado con `verify_legacy.py`.
3. **(del autor, sin resolver por nadie)** la convención de fill de entrada (close vs stop) vale ~15% del PnL y no está reconciliada contra el MultiCharts original. **Usamos la conservadora (stop fill)**, que es además su default corregido.

**Zona horaria y horarios:** verificado en los datos — pico de apertura de cash a las **08:30 CT (×5,4)**, maintenance halt **16:00–17:00 CT** (311 barras vs ~215K/h), 954 barras/día (Globex casi continuo). El warning de su diagnóstico ("unexpected open at 16:00") se explica por unas pocas barras anómalas a las 16:xx con volumen descomunal en el promedio; la firma de sesión es correcta.

**Costes modelados (incluidos en todos los resultados):** comisión 2,50 USD/side + slippage 1,5 ticks/side en ambas caras = **20,00 USD/contrato/round-turn** (config del autor). Nuestro escenario MNQ decisorio: comisión 0,62 USD/side + 1,5 ticks/side = 2,74 USD/round-turn sobre $2/pt. **Sin** modelo de impacto de liquidez ni fills parciales (declarado, también por el autor).

## 4. Compatibilidad con FARS Core — VERIFICADA por el contrato real

`fars audit ledger_orb_fars_stop.csv --outcomes-finalized --timezone America/New_York`:
- **2.444 filas · 2.444 aceptadas · 0 rechazadas · 0 errores · 0 avisos**
- `core_metrics`, `temporal_analysis`, `daily_rule_simulation` → **las 3 disponibles**
- mapping 1:1 de los 9 campos Phase 8A, sha256 del archivo en provenance.

## 5. Resultados por periodos, con costes (FARS Core, unidad R)

Escenario decisorio: `SimFlags()` corregido + **stop fill** + costes MNQ realistas. Instrumento: **MNQ** (mismo nivel de precio que NQ).

| Periodo | n | Win rate | **E[R]** | Std R | Max DD (R) | Racha perdedora |
|---|---:|---:|---:|---:|---:|---:|
| 2010 – 2022 | 1.501 | 46,4% | **+0,0569** | 0,976 | 19,22 | 11 |
| 2023 – 2026 | 943 | 39,7% | **+0,0579** | 1,276 | 22,61 | 10 |
| **Total 2010 – 2026** | **2.444** | **43,8%** | **+0,0573** | 1,101 | **22,61** | **11** |
| hasta 2022-01 (fecha de congelación de sus reglas) | 1.243 | 47,2% | +0,0632 | 0,915 | 19,22 | 11 |
| desde 2022-02 | 1.201 | 40,2% | +0,0511 | 1,266 | 22,61 | 10 |

Avg win 1,10R · Avg loss 0,75R · Skewness +0,71 · Kurtosis −0,76 · t-stat del motor 2,57.

Nuestro escenario completo con costes del autor (NQ $20/pt): Sharpe 0,71, DD −19,97%, 2.444 trades, +$2,48M de PnL nominal sobre 1M de capital inicial (es una magnitud del simulador, no un resultado esperado).

**Lectura honesta:** E[R] ≈ **+0,057R por operación**, estable en los cuatro cortes (0,051–0,063). Es positivo pero **modesto**: con Std 1,10 y n=2.444 el t-stat ronda 2,6 — indicativo, no concluyente. La estrategia **sí** es candidata seria (a diferencia del caso anterior, que caía a cero), pero no es una mina de oro: el DD en R (22,6) es ~400 veces el E[R] por trade.

**Salvedad de selección (obligatoria):** esta estrategia se eligió **tras ver los resultados publicados del autor** (2015–2025, Sharpe ~1,0). 2010–2026 no es una prueba completamente independiente; sí aporta 5 años (2010–2014) que el autor nunca vio.

## 6. Estado exacto del bot en paper trading

`lab_artifacts/orb_protocol/paper_bot.py` — tres componentes separados:
- **estrategia**: reglas ORB del autor (ventana 08:30–10:00 CT, romper alto/bajo, stop 100 pts, target 200 pts, máx 2/día) leyendo solo barras ya cerradas
- **riesgo**: `AccountAwareRiskEngine` de FARS — **veto absoluto antes de cualquier orden**
- **ejecución**: `PaperExecutionAdapter` de FARS — se niega a correr si live está habilitado

**Controles configurados:** pérdida diaria 2.000 USD (y 2%), drawdown máximo 8.000 USD / 8% **trailing**, máx 42 operaciones/mes, riesgo por trade 1%, riesgo máximo por orden 1.000 USD, **stop obligatorio** (sin `stop_price` el intent se bloquea en origen), **apagado seguro** por kill-switch (`--kill`) que emite `system_halted` y corta todo.

**Verificado ejecutando:**
- Replay 3 meses (2026-06 → 2026-09, 18.121 barras): **56 señales → 42 órdenes en paper**, y las restantes **denegadas por `MAX_TRADES_REACHED`** al llegar al límite.
- Antes de alimentar el `peak_equity`, todo salía denegado con `UNKNOWN_CRITICAL_STATE`: **fail-closed confirmado** (estado desconocido → DENY, como manda la spec).
- Kill-switch: `HALT` inmediato, 0 órdenes, `halted=True`.
- Tests `test_paper_bot.py` **6/6**: stop obligatorio, kill-switch, pérdida diaria, máx operaciones, drawdown trailing sin peak, sizing.

**Lo que NO está hecho (declarado):**
1. El bot corre en **replay sobre barras históricas**. Para paper en vivo falta el conector de datos real (RT-1 de la spec) y el daemon. **No hay credenciales, no hay broker, no hay órdenes reales** — por diseño.
2. Sin Bootstrap/Monte Carlo de FARS sobre el ledger (es el paso natural para poner un intervalo alrededor de ese E[R]).
3. La convención de fill de entrada sigue sin reconciliarse contra el original MultiCharts (pendiente del roadmap del propio autor).
4. El PnL del paper bot no se marca contra mercado (equity constante en el replay): para un run de paper real hay que enlazar el ExecutionReport con el equity.

## 7. Recomendación

**Sí avanzar con esta estrategia**, pero con expectativas medidas:
- Es el primer generador externo que, medido con costes honestos y sin look-ahead, mantiene **E[R] positivo y estable** en 16 años y en 4 cortes cronológicos.
- El pipeline completo ya está probado: estrategia → señales → **riesgo FARS con veto** → paper. Ese cableado es reutilizable para cualquier otra estrategia.
- Siguiente paso natural, en este orden: (1) Bootstrap/Monte Carlo de FARS sobre `ledger_orb_fars_stop.csv` para poner intervalos a ese +0,057R; (2) marcar el equity del paper bot contra mercado y correr paper en modo daemon con el conector de datos; (3) recién después, si los intervalos aguantan, considerar una cuenta practice.

## 8. Trazabilidad

- Repos clonados sin modificar: `E:\FARS-LAB\ext_review2\{nq-intraday-breakout, nq-es-trader-5k-payout, nq-atb-bot-archived}`; el repo de Strategy B en `E:\FARS-LAB\ext_review\nq-strategy-b-bot`.
- Código propio: `FARS/lab_artifacts/orb_protocol/` → `adapter_data.py` (adaptación de datos), `run_orb.py` (motor del autor + ledger), `verify_legacy.py`, `fix_ledger_tz.py`, `periodos_orb.py`, `paper_bot.py`, `test_paper_bot.py`, `metrics_orb.json`, `paper_run.json`, ledgers `ledger_orb_*.csv`, este informe.
- Datos adaptados: `ext_review2/data_adapted/mnq_1min_multicharts.csv.gz` (4.812.017 barras 1-min) — fuera del repo del autor.
- Verificación realmente ejecutada: 75 tests del autor verdes, reproducción contra sus números, `fars audit` 2.444/2.444, `fars metrics` por periodos, replay de 3 meses del bot, 6 tests de controles, kill-switch probado.
- FARS Core y especificaciones **sin cambios**; sin commits, merge ni push.

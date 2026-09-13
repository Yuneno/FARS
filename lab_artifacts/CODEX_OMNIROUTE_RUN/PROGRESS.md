# FARS LAB - Codex + OmniRoute Progress

## Current HEAD
- 731f980 bench(strategies): benchmark 4 strategies with 2 workers on canonical MNQ M5
- e232344 feat(orb): implement Opening Range Breakout strategy with unit tests
- a1ef293 feat(crt_tbs): implement causal CRT-TBS strategy with unit tests
- 11409f3 feat(backtest): add time_exit_mode to BacktestConfig for flat expiry exits
- f353872 docs(progress): update PROGRESS.md with full evidence for Tasks 1-4
- 2dfcd78 feat(realtime): verify chronological replay causality and document live bridge architecture
- 836b92b bench(emas): benchmark EMAS and compare with SMC-FVG on canonical MNQ M5
- 86cc575 bench(parallel): benchmark multiprocessing runner across 1 to 6 workers
- ea380c8 feat(backtest): add discrete partial contracts mode with unit tests and benchmark

## Test Results
- **Full Project Test Suite**: 1,489 passed across Core, CLI, adapters, bootstrap, detectors, backtest, and realtime.
- **CRT-TBS Backtest Tests**: 7 passed (causality, H1/H4 availability, parameter paradox validation, execution).
- **ORB Backtest Tests**: 7 passed (long/short breakouts, midpoint rearm, session cutoff, flat time exit).
- **SMC-FVG Backtest Tests**: 37 passed.
- **Realtime Suite**: 236 passed.
- **Zero regressions**: Pre-refactor regression test against commit `1fa30ae` preserved bit-for-bit.

## Phase Status
| Phase | Status | Tests | Notes |
|---|---|---|---|
| P0-P7 | COMPLETE | 300+ | All core packages, CLI, detectors, parallel runner verified |
| Juanca-1 (SMC-FVG) | COMPLETE | 37 | Continuous and discrete contract execution validated |
| Real Parallelism | COMPLETE | E2E (18 jobs x 6 workers x 3 reps) | Real ProcessPoolExecutor spawn, unique PIDs, zero fallback |
| Juanca-2 (EMAS) | COMPLETE | 5,098 trades on canonical M5 | Gross PF 1.0976, friction PF 0.9009, side-by-side vs SMC-FVG |
| Realtime Replay | COMPLETE | 2,000 bars verified | 100% bit-for-bit signal parity against causal backtest |
| Juanca-3 (CRT-TBS) | COMPLETE | 7 | Causal H1/H4 bar aggregation, champion fixed_rr=2.0, zero-trade paradox documented |
| Juanca-4 (ORB) | COMPLETE | 7 | 09:30-10:00 NY range, opposite stop, 2R target, midpoint rearm, 192-bar flat exit |
| 4-Way Comparison | COMPLETE | 9 jobs across 2 workers | 100% equivalence, zero fallback, account viability & ruin tracking, annual breakdown |

---

## Task 1: SMC-FVG con Contratos Enteros (Discrete Contracts)

- **Regla Implementada**: Modo explícito `discrete_partial_contracts=True` en `BacktestConfig`.
  - TP1 cierra exactamente $\lfloor \text{quantity} \cdot f \rfloor$ contratos (para $f=0.5$, $\lfloor Q / 2 \rfloor$).
  - El resto de contratos ($Q - \lfloor Q / 2 \rfloor$) sale según las reglas habituales (target, break-even o stop).
  - Con 1 contrato ($Q=1$): no hay cierre parcial ($\lfloor 1/2 \rfloor = 0$), pero al tocar 1R se activa el stop de break-even en `entry`. Si toca target, el contrato entero captura $1.5R$ en lugar de verse diluido a $1.25R$. Si toca BE, el PnL bruto es exactamente $\$0.00$ y la pérdida neta es $-\$4.00$ (comisión).
  - Costes: Comisión cobrada sobre el total de contratos ejecutados round-trip ($\$4.00 \cdot Q$). Slippage de stop aplicado únicamente a los contratos restantes ($q_{\text{rem}}$).
- **Validación**:
  - 25 tests unitarios en `tests/test_backtest_smc_fvg.py` cubriendo largos/cortos, $Q \in \{1, 2, 3, 5, 7\}$ y los 3 escenarios de salida (TP1 $\to$ BE, TP1 $\to$ Target, SL directo).
  - Test de equivalencia matemática exacta para $Q=2$: idéntico bit a bit al modelo continuo del 50%.
- **Comparación en MNQ M5 Canónico (518,237 barras)**:

| Métrica | Continuo Bruto | Discreto Bruto | Continuo Fricción ($4 RT) | Discreto Fricción ($4 RT) |
|---|---:|---:|---:|---:|
| Trades ($n$) | 5,986 | 5,986 | 5,986 | 5,986 |
| Win Rate | 59.00% | 58.97% | 58.39% | 58.19% |
| Profit Factor | 1.3683 | 1.3666 | 0.9930 | 0.9916 |
| PnL Bruto | +$463,261.00 | +$461,233.50 | +$463,261.00 | +$461,233.50 |
| Comisión Total | $0.00 | $0.00 | $473,372.00 | $473,372.00 |
| PnL Neto | +$463,261.00 | +$461,233.50 | -$10,111.00 | -$12,138.50 |
| Net R | +926.52 R | +922.47 R | -20.22 R | -24.28 R |
| Max Drawdown | 6.53% | 6.66% | 64.19% | 65.94% |

- **Análisis de Cantidades**:
  - En las 2,919 operaciones con contratos pares ($Q=2k$), la diferencia entre continuo y discreto es **$0.00 exacto** (idéntico bit a bit).
  - En las 3,067 operaciones con contratos impares ($Q=2k+1$), el modo discreto traslada 1 contrato más al tramo final (runner), generando una diferencia neta acumulada de $-\$2,027.50$ a lo largo de 7.33 años.
  - En 11 operaciones con $Q=1$, los ganadores capturan el $1.5R$ completo en lugar de diluirse a $1.25R$ (ej. trade `bt-2702`: bruto continuo $\$447.50$ vs discreto $\$537.00$).

---

## Task 2: Paralelismo Real Multiproceso (1 a 6 Workers)

- **Script Reproducible**: `lab_artifacts/CODEX_OMNIROUTE_RUN/benchmark_parallel_workers.py`
- **Artefacto JSON**: `lab_artifacts/CODEX_OMNIROUTE_RUN/parallel_benchmark.json`
- **Carga de Trabajo**: 18 jobs independientes de backtest sobre ventanas trimestrales de MNQ M15 canónico (2020-Q1 a 2024-Q2), evaluados a lo largo de 3 repeticiones por cada número de workers $W \in [1, 2, 3, 4, 5, 6]$.
- **Resultados de Escalabilidad**:

| Workers ($W$) | Wall Time Medio | Wall Time Mínimo | Speedup Medio | Eficiencia | PIDs Únicos | PIDs No-Padre | Fallback |
|---|---:|---:|---:|---:|---:|---:|:---:|
| 1 | 1.0627s | 1.0593s | 1.00x | 100.0% | 1 | Sí | No |
| 2 | 0.9308s | 0.9277s | 1.14x | 57.1% | 2 | Sí | No |
| 3 | 0.9270s | 0.9023s | 1.15x | 38.2% | 3 | Sí | No |
| 4 | 0.9828s | 0.9657s | 1.08x | 27.0% | 4 | Sí | No |
| 5 | 1.0334s | 1.0208s | 1.03x | 20.6% | 5 | Sí | No |
| 6 | 1.0939s | 1.0783s | 0.97x | 16.2% | 6 | Sí | No |

- **Hallazgos Clave**:
  1. **Multiproceso Real Comprobado**: En el 100% de las ejecuciones se asignaron procesos hijos independientes (`ProcessPoolExecutor` con contexto `spawn`), con PIDs distintos entre sí y distintos al PID del proceso padre. Cero caídas a fallback secuencial.
  2. **Equivalencia Semántica 100%**: Los resultados numéricos de las 18 tareas (`n_trades`, `net_pnl`, `win_rate`, `equity_curve_points`) son 100% idénticos e invariantes entre 1, 2, 3, 4, 5 y 6 workers.
  3. **Sobrecarga de Creación de Procesos**: Dado que cada backtest tarda ~0.04s en computarse, la creación de 6 procesos independientes en Windows consume ~0.6s de sobrecarga IPC, situando el punto óptimo de latencia en 2-3 workers para tareas cortas.

---

## Task 3: Comparativa EMAS vs SMC-FVG (MNQ M5 Canónico 2019–2026)

- **Script Reproducible**: `lab_artifacts/CODEX_OMNIROUTE_RUN/benchmark_juanca_emas.py`
- **Artefacto JSON**: `lab_artifacts/CODEX_OMNIROUTE_RUN/juanca_emas_out/juanca_emas_benchmark.json`
- **Parámetros Comunes**: Periodo común 2019-05-06 a 2026-09-03 (518,237 barras M5, 7.33 años), Capital inicial $\$50,000$, Riesgo 1.0% ($\$500$/trade), Fricción canónica $\$4.00$ RT ($\$2.00$/lado comisión, 0 slippage).
- **Clasificación**: **HISTÓRICO EXPLORATORIO**. Ninguna estrategia se declara ganadora por Win Rate.

### Tabla Comparativa Integral

| Métrica | SMC-FVG Bruto | EMAS Bruto | SMC-FVG Fricción ($4 RT) | EMAS Fricción ($4 RT) | Delta Fricción (SMC - EMAS) |
|---|---:|---:|---:|---:|---:|
| Trades Totales | 5,986 | 5,098 | 5,986 | 5,098 | +888 |
| Frecuencia (trades/día) | 3.24 | 2.76 | 3.24 | 2.76 | +0.48 |
| Win Rate | 59.00% | 51.61% | 58.39% | 51.59% | +6.80 pp |
| Profit Factor | 1.3683 | 1.0976 | 0.9930 | 0.9009 | +0.0921 |
| PnL Bruto | +$463,261.00 | +$120,481.75 | +$463,261.00 | +$120,481.75 | +$342,779.25 |
| Comisión Total | $0.00 | $0.00 | $473,372.00 | $255,132.00 | +$218,240.00 |
| PnL Neto | +$463,261.00 | +$120,481.75 | -$10,111.00 | -$134,650.25 | +$124,539.25 |
| Net R | +926.52 R | +240.96 R | -20.22 R | -269.30 R | +249.08 R |
| Esperanza Neta ($/trade) | +$77.39 | +$23.63 | -$1.69 | -$26.41 | +$24.72 |
| Esperanza Neta (R) | +0.1548 R | +0.0473 R | -0.0034 R | -0.0528 R | +0.0494 R |
| Max Drawdown | 6.53% | 34.17% | 64.19% | 308.94% | -244.75 pp |
| Drag de Comisión / Bruto | 0.0% | 0.0% | 102.2% | 211.8% | -109.6 pp |

- **Conclusiones Científicas**:
  1. **La falacia del Win Rate**: SMC-FVG presenta mayor WR (58.39% vs 51.59%), pero ambas estrategias tienen esperanza negativa tras costes en M5. El WR por sí solo no garantiza viabilidad operativa.
  2. **Erosión por Fricción**: Ambas sufren por la alta rotación intradiaria (~3 operaciones/día). Sin embargo, SMC-FVG absorbe significativamente mejor los costes gracias a su mayor ventaja bruta por operación ($\$77.39$ vs $\$23.63$). La comisión devora el 102.2% del beneficio bruto en SMC-FVG, pero en EMAS devora el 211.8%, provocando una ruina del 308% de drawdown.
  3. **Resiliencia Operativa**: SMC-FVG supera a EMAS en $+\$124,539.25$ de PnL neto y $+249.08\text{ R}$ en condiciones idénticas de mercado.

---

## Task 4: Replay Histórico en Tiempo Real y Live Bridge

- **Script de Verificación**: `lab_artifacts/CODEX_OMNIROUTE_RUN/verify_realtime_replay.py`
- **Artefacto JSON**: `lab_artifacts/CODEX_OMNIROUTE_RUN/realtime_replay_verification.json`
- **Test Automatizado**: `tests/realtime/test_replay_causality.py` (pasa 100%)
- **Documentación Arquitectónica**: `lab_artifacts/CODEX_OMNIROUTE_RUN/LIVE_BRIDGE_INTEGRATION.md`
- **Evidencia Obtenida**:
  - 2,000 barras canónicas de MNQ M5 reproducidas mediante `ReplayEngine` con `FrozenClock` y `AsyncIOEventBus`.
  - Orden temporal estrictamente monótono: 0 violaciones causales.
  - La estrategia evaluó únicamente barras cerradas disponibles en el instante del reloj congelado (cero lookahead).
  - Se generaron 20 señales en tiempo real idénticas bit a bit a las 20 señales del backtest causal (tiempo, dirección, entrada, stop y target idénticos al 100%).
- **Protocolo de Seguridad para Exness / MT5**:
  - Arquitectura en 4 fases: Replay Histórico $\to$ Shadow / Read-Only $\to$ Paper Trading Demo $\to$ Live Staging.
  - Cero conexiones a brokers en esta fase; cero credenciales almacenadas o requeridas; cero órdenes enviadas.

---

## Task 5: Incorporación de CRT-TBS y ORB en FARS

- **Módulos Implementados**:
  - `src/backtest/crt_tbs.py` (`CrtTbsStrategy`, `CrtTbsConfig`, `crt_tbs_config`): Motor top-down H4-H1-M5 con causalidad estricta.
  - `src/backtest/orb.py` (`OrbStrategy`, `OrbConfig`, `orb_config`): Opening Range Breakout 09:30–10:00 NY con rearme en el punto medio (`orm`), corte de entradas a las 16:00 ET y salida plana a 192 barras.
  - `src/backtest/executor.py`: Soporte para `time_exit_mode="flat"` en salidas por vencimiento temporal.
- **Correcciones Causales y Documentación de Parámetros**:
  1. **Disponibilidad Causal de H1 y H4**: Las barras H1/H4 no están completas en su apertura. Una barra H1 iniciada a las 09:00 solo se evalúa a las 10:00:00 (cierre de la barra M5 de 09:55). El sesgo H4 solo utiliza barras H4 ya finalizadas. Esto eliminó 9 operaciones espurias del script original.
  2. **Paradoja Matemática de `CrtTbsConfig` original**: La configuración literal por defecto (`target_mode="crt"`, `min_rr=1.50`, `require_half_zone=True`) produce 0 operaciones porque el objetivo al extremo CRT garantiza geométricamente un ratio recompensa/riesgo $\le 1.0 < 1.50$. Se incorporó la configuración operativa real de Juanca (`champion_configs.py`: `crt_tbs_topdown_fullrange_rr20` con `target_mode="fixed_rr"`, `fixed_rr=2.0`).
  3. **Configuración Experimental de ORB**: Fijada según especificación: rango 09:30–10:00 NY, stop opuesto, 2R target, sin bias, `fade=False`, corte a las 16:00 ET, `max_hold=192` barras M5 con salida plana.
- **Validación con Pruebas Unitarias**:
  - `tests/test_backtest_crt_tbs.py`: 7 tests pasando (largos, cortos, causalidad temporal estricta, paradoja de 0 operaciones en modo literal y ejecución con `run_backtest`).
  - `tests/test_backtest_orb.py`: 7 tests pasando (rupturas largas/cortas, rearme en punto medio, corte de sesión ET, salida plana y ejecución con `run_backtest`).
  - `tests/test_backtest_executor.py`: Verificación de `time_exit_mode="flat"` vs `"market"`.
  - `docs/refactor/check_mnq_equivalence.py`: 100% idéntico bit a bit contra el commit pre-refactor `1fa30ae`.

---

## Task 6: Comparativa Integral de Cuatro Estrategias (MNQ M5 Canónico 2019–2026)

- **Script Reproducible**: `lab_artifacts/CODEX_OMNIROUTE_RUN/benchmark_four_strategies.py`
- **Artefacto JSON**: `lab_artifacts/CODEX_OMNIROUTE_RUN/four_strategies_benchmark.json`
- **Condiciones Comunes**: Periodo canónico 2019-05-06 a 2026-09-03 (518.237 barras M5, 7,33 años), Capital inicial $\$50.000$, Riesgo 1,0% ($\$500$/trade), Contratos enteros discretos (`_quantity`), Escenarios Bruto y Fricción de Mercado (\$4.00 RT/contrato).
- **Paralelismo Real**: 9 jobs independientes ejecutados sobre 2 workers multiproceso (`spawn`, PIDs 7140 y 21612, proceso padre 15912). Tiempo total: 10,51s. Cero caídas a fallback. 100% de invariancia contra la ejecución secuencial.

### Matriz Comparativa Integral de Cuatro Estrategias

| Estrategia | Escenario | Trades | Win Rate | Profit Factor | PnL Bruto | Comisión Total | PnL Neto | Net R | Max Drawdown | Supervivencia de Cuenta | Fecha de Ruina ($E \le 0$) | Trade de Ruina |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|---:|
| **CRT-TBS (Champion)** | Fricción ($4 RT) | 93 | 26.88% | 1.0681 | +$3,474.00 | $1,932.00 | +$1,542.00 | +3.08 R | 7.67% | **Sí** | N/A | N/A |
| **CRT-TBS (Champion)** | Bruto | 93 | 26.88% | 1.1631 | +$3,474.00 | $0.00 | +$3,474.00 | +6.95 R | 6.56% | **Sí** | N/A | N/A |
| **CRT-TBS (Default)** | Bruto | 0 | 0.00% | 0.0000 | $0.00 | $0.00 | $0.00 | 0.00 R | 0.00% | **Sí** | N/A | N/A |
| **SMC-FVG (Discreto)** | Bruto | 5,986 | 58.97% | 1.3666 | +$461,233.50 | $0.00 | +$461,233.50 | +922.47 R | 6.66% | **Sí** | N/A | N/A |
| **SMC-FVG (Discreto)** | Fricción ($4 RT) | 5,986 | 58.19% | 0.9916 | +$461,233.50 | $473,372.00 | -$12,138.50 | -24.28 R | 65.94% | **Sí** | N/A | N/A |
| **EMAS (Discreto)** | Bruto | 5,098 | 51.61% | 1.0997 | +$123,083.00 | $0.00 | +$123,083.00 | +246.17 R | 34.60% | **Sí** | N/A | N/A |
| **EMAS (Discreto)** | Fricción ($4 RT) | 5,098 | 51.59% | 0.9028 | +$123,083.00 | $255,132.00 | -$132,049.00 | -264.10 R | 304.76% | **No** | **2019-11-25** | Trade #364 |
| **ORB (Experimental)** | Bruto | 2,544 | 13.95% | 0.6721 | -$173,801.50 | $0.00 | -$173,801.50 | -347.60 R | 337.71% | **No** | **2022-01-26** | Trade #934 |
| **ORB (Experimental)** | Fricción ($4 RT) | 2,544 | 13.95% | 0.6311 | -$173,801.50 | $31,260.00 | -$205,061.50 | -410.12 R | 410.38% | **No** | **2021-03-29** | Trade #675 |

### Hallazgos Fundamentales de la Comparativa

1. **Separación Estricta entre Matemáticas y Viabilidad de Cuenta**:
   - **EMAS ($4 RT)**: Entra en quiebra a los 6 meses de operativa (**2019-11-25**, trade #364). Las 4.734 operaciones simuladas posteriores son una construcción matemática de curva acumulada, no operaciones financiables en una cuenta real de \$50.000.
   - **ORB ($4 RT)**: Entra en quiebra el **2021-03-29** (trade #675). Incluso en bruto entra en quiebra en enero de 2022 (-$173k PnL bruto). La ruptura pura de momentum sin filtro de régimen genera una pérdida sistemática severa en M5.
   - **SMC-FVG**: Nunca entra en quiebra (el capital cerrado mínimo fue de \$17.904,50). El arrastre de comisiones (\$473k) devora la totalidad de la ganancia bruta (\$461k), dejando un PnL neto de -\$12.138,50 (-24,28 R).
   - **CRT-TBS (Champion)**: Es la **única estrategia que conserva PnL neto positivo tras fricción** (+$1.542,00 neto, +3,08 R, Max DD 7,67%). Su baja frecuencia operativa (~13 trades/año) reduce el drag de comisión a solo \$1.932,00 frente a los cientos de miles de dólares de SMC-FVG y EMAS.

2. **La Falacia del Win Rate Desmontada Empíricamente**:
   - Mayor Win Rate: SMC-FVG (58,19%) $\to$ PnL Neto negativo (-$12.138,50).
   - Segundo Win Rate: EMAS (51,59%) $\to$ Quiebra de cuenta (-$132.049,00).
   - Menor Win Rate ganador: CRT-TBS (26,88%) $\to$ **Único PnL Neto positivo** (+$1.542,00) gracias a la asimetría de 2R por operación y costes controlados.
   - No es posible declarar una ganadora por tasa de acierto; la asimetría de recompensa/riesgo y la tasa de fricción dominan la rentabilidad real.


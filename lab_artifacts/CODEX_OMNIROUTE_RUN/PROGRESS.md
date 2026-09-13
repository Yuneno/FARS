# FARS LAB - Codex + OmniRoute Progress

## Current HEAD
- 2dfcd78 feat(realtime): verify chronological replay causality and document live bridge architecture
- 836b92b bench(emas): benchmark EMAS and compare with SMC-FVG on canonical MNQ M5
- 86cc575 bench(parallel): benchmark multiprocessing runner across 1 to 6 workers
- ea380c8 feat(backtest): add discrete partial contracts mode with unit tests and benchmark
- 3a34838 test(backtest): add strict causality and intrabar resolution regression tests for SMC-FVG
- baffec5 fix(P7): market config from MarketSpec, canonical range-first filter, and reproducible benchmark
- 94ee2b6 feat(P7): real data benchmark with databento.zip (MNQ, MYM)

## Test Results
- **Full Project Test Suite**: 1,471 passed (across Core, CLI, adapters, bootstrap, detectors, backtest, and realtime).
- **SMC-FVG Backtest Tests**: 37 passed (12 causality + 25 discrete contract parametrization tests).
- **Realtime Suite**: 236 passed (including 1 new strict replay causality test).
- **Zero regressions**: Pre-refactor regression test against commit `1fa30ae` preserved bit-for-bit.

## Phase Status
| Phase | Status | Tests | Notes |
|---|---|---|---|
| P0-P7 | COMPLETE | 300+ | All core packages, CLI, detectors, parallel runner verified |
| Juanca-1 (SMC-FVG) | COMPLETE | 37 | Continuous and discrete contract execution validated |
| Real Parallelism | COMPLETE | E2E (18 jobs x 6 workers x 3 reps) | Real ProcessPoolExecutor spawn, unique PIDs, zero fallback |
| Juanca-2 (EMAS) | COMPLETE | 5,098 trades on canonical M5 | Gross PF 1.0976, friction PF 0.9009, side-by-side vs SMC-FVG |
| Realtime Replay | COMPLETE | 2,000 bars verified | 100% bit-for-bit signal parity against causal backtest |

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

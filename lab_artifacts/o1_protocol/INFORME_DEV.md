# INFORME — Bloque O1: Compression Breakout Causal (DEV-only)

## Resumen Ejecutivo

- **Veredicto Automático:** **`NO_DEV_PULSE`**
- **Hipótesis Evaluada:** En MNQ M5 real post-lanzamiento, una barra de compresión NR7 seguida por un cierre fuera de un canal Donchian de longitud $L \in \{20, 55\}$ puede tener continuación suficiente para superar costes.
- **Resultado Global:** La hipótesis queda categóricamente **RECHAZADA**. La señal presenta expectativa matemática negativa ($E[R] < 0$) y factor de beneficio inferior a 1 ($PF < 1.0$) en ambas longitudes tanto en escenario realista como de estrés. Incluso a nivel bruto (sin comisiones), la señal genera pérdidas netas de capital.
- **Cierre del Bloque:** Se falla cerrado conforme al mandato del preregistro. **NO se abre el periodo de validación ni holdout**, **NO se ajustan parámetros** y **NO se prueban variantes adicionales**.

---

## Metadatos y Huellas Criptográficas

| Parámetro | Valor Verificado |
| :--- | :--- |
| **Rama Git** | `bloque-o1-comp-break` |
| **Commit Base** | `e56e30abf47ac50bc046c7820001901a013cd808` |
| **Preregistro SHA-256** | `2b1bc4321a7d4165b92eed8cb80bd98b6b6698e369e16aa8acb09fb9e7b6b4ed` |
| **Dataset Canónico** | `E:/FARS-LAB/databento.zip` -> `databento/MNQ_M5.csv` |
| **SHA-256 Miembro Crudo** | `fbed6061205b8299af140f85e36b472f5f1d88084977ad9c4ca9aa1f817b8a96` (Exact match) |
| **Rango Temporal DEV** | `2019-05-06T00:00:00+00:00` a `2023-12-29T21:55:00+00:00` |
| **Barras DEV Cargadas** | 328,715 barras M5 |
| **Particiones Bloqueadas** | VALIDATION (`>= 2024-01-01`) y FINAL HOLDOUT (`>= 2025-04-01`) **ESTRICTAMENTE NO MATERIALIZADAS** |
| **Concurrencia** | 4 workers (`ThreadPoolExecutor`) |
| **Tiempo de Ejecución** | 4.0 segundos (matriz completa) |

---

## Matriz Completa de Resultados (4 Celdas DEV)

La matriz cerrada consta de 1 hipótesis $\times$ 2 longitudes Donchian ($L \in \{20, 55\}$) $\times$ 2 escenarios de coste (`realista`: com $\$0.62$/side, slip 1 tick; `estres`: com $\$2.00$/side, slip 1 tick).

| Celda ID | $L$ | Escenario | $n$ | $E[R]$ | PF | Win Rate | Net PnL ($) | Max DD ($R$) | Folds+ | CBB CI98.75% (Bonferroni) | Unres. | Gaps | Coste Comis. ($) | Trades SHA-256 |
| :--- | :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **`L20_realista`** | 20 | Realista | 3,614 | **-0.0627** | **0.846** | 32.1% | -$113,236.00 | 233.83 $R$ | 2/8 | `[-0.0965, -0.0269]` | 1 | 20 | $43,028.00 | `41cf971d1fd87a65...` |
| **`L20_estres`** | 20 | Estrés | 3,614 | **-0.1157** | **0.739** | 32.1% | -$209,008.00 | 421.81 $R$ | 0/8 | `[-0.1492, -0.0803]` | 1 | 20 | $138,800.00 | `a2641169a064985c...` |
| **`L55_realista`** | 55 | Realista | 2,065 | **-0.0523** | **0.878** | 32.5% | -$54,050.24 | 114.19 $R$ | 3/8 | `[-0.1039, -0.0040]` | 1 | 16 | $24,398.24 | `095828cee7449230...` |
| **`L55_estres`** | 55 | Estrés | 2,065 | **-0.1049** | **0.774** | 32.5% | -$108,356.00 | 219.91 $R$ | 0/8 | `[-0.1559, -0.0568]` | 1 | 16 | $78,704.00 | `139415826e7c55d1...` |

> [!NOTE]
> **Pérdida Bruta Intrínseca:**
> - En $L=20$, el `gross_pnl` acumulado (antes de comisiones) fue de **-$70,208.00**.
> - En $L=55$, el `gross_pnl` acumulado fue de **-$29,652.00**.
> Esto demuestra que el fallo no es simplemente atribuible a la fricción de ejecución: la lógica pura de breakout post-NR7 no tiene ventaja direccional positiva sobre el precio subyacente de MNQ M5.

---

## Desglose por Fold de Walk-Forward (8 Folds DEV)

Plan rolling de calendario de 6 meses por fold, cubriendo 4 años completos (2020-01-01 a 2024-01-01), con 500 barras de calibración/warmup previas a cada inicio de test y purga de 54 barras.

### `L20_realista` (L=20, com=\$0.62, slip=1 tick)

| Fold | Inicio Test | Fin Test | Barras | $n$ | $E[R]$ | PF | Win Rate | Net PnL ($) | Folds+ |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 0 | 2020-01-01 | 2020-07-01 | 34,630 | 500 | -0.0905 | 0.785 | 32.2% | -$22,619.04 | No |
| 1 | 2020-07-01 | 2021-01-01 | 35,520 | 434 | -0.0251 | 0.936 | 32.7% | -$5,437.38 | No |
| 2 | 2021-01-01 | 2021-07-01 | 34,752 | 394 | -0.0676 | 0.825 | 30.7% | -$13,313.36 | No |
| 3 | 2021-07-01 | 2022-01-01 | 35,943 | 445 | **+0.0266** | **1.082** | 35.1% | +$5,916.28 | **Sí** |
| 4 | 2022-01-01 | 2022-07-01 | 35,160 | 475 | -0.1458 | 0.758 | 30.7% | -$34,626.28 | No |
| 5 | 2022-07-01 | 2023-01-01 | 35,667 | 460 | -0.1242 | 0.724 | 30.7% | -$28,562.84 | No |
| 6 | 2023-01-01 | 2023-07-01 | 35,319 | 443 | **+0.0025** | **1.008** | 34.3% | +$548.36 | **Sí** |
| 7 | 2023-07-01 | 2024-01-01 | 35,370 | 463 | -0.0654 | 0.809 | 30.5% | -$15,141.74 | No |

### `L55_realista` (L=55, com=\$0.62, slip=1 tick)

| Fold | Inicio Test | Fin Test | Barras | $n$ | $E[R]$ | PF | Win Rate | Net PnL ($) | Folds+ |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 0 | 2020-01-01 | 2020-07-01 | 34,630 | 285 | -0.0779 | 0.814 | 33.0% | -$11,104.36 | No |
| 1 | 2020-07-01 | 2021-01-01 | 35,520 | 237 | **+0.0054** | **1.014** | 34.2% | +$641.38 | **Sí** |
| 2 | 2021-01-01 | 2021-07-01 | 34,752 | 236 | -0.1405 | 0.670 | 27.5% | -$16,580.60 | No |
| 3 | 2021-07-01 | 2022-01-01 | 35,943 | 264 | **+0.0285** | **1.082** | 35.2% | +$3,762.10 | **Sí** |
| 4 | 2022-01-01 | 2022-07-01 | 35,160 | 295 | -0.1222 | 0.802 | 31.5% | -$18,018.66 | No |
| 5 | 2022-07-01 | 2023-01-01 | 35,667 | 242 | -0.0646 | 0.862 | 33.5% | -$7,815.26 | No |
| 6 | 2023-01-01 | 2023-07-01 | 35,319 | 246 | **+0.0403** | **1.116** | 35.8% | +$4,952.32 | **Sí** |
| 7 | 2023-07-01 | 2024-01-01 | 35,370 | 260 | -0.0761 | 0.802 | 29.2% | -$9,887.16 | No |

---

## Evaluación de Gates Preregistrados

| Gate ID | Descripción | Criterio Requerido | Resultado Obtenido | Estado |
| :--- | :--- | :--- | :--- | :---: |
| **Gate 1** | Tamaño Muestral | Total $n \ge 300$ en `realista` | $L20=3,614$, $L55=2,065$ | **PASSED [OK]** |
| **Gate 2** | Expectativa Dual Positiva | Ambos $L=20$ y $L=55$ con $E[R] > 0$ y $PF > 1.0$ | $L20$: $E[R]=-0.0627, PF=0.846$<br>$L55$: $E[R]=-0.0523, PF=0.878$ | **FAILED [FAIL]** |
| **Gate 3** | CBB CI98.75% (Bonferroni) | Al menos un coto inferior $> 0$ ($\alpha=0.0125$) | $L20$ lo = -0.0965<br>$L55$ lo = -0.1039 | **FAILED [FAIL]** |
| **Gate 4** | Consistencia Folds | Cada $L$ tiene $\ge 5/8$ folds con $E[R] > 0$ | $L20$: 2/8 positivos<br>$L55$: 3/8 positivos | **FAILED [FAIL]** |
| **Gate 5** | Campeón Bajo Estrés | Campeón mantiene $E[R]>0, PF>1.0, \ge 5/8$ folds+ | $L55$ bajo estrés: $E[R]=-0.1049, PF=0.774$, 0/8 folds+ | **FAILED [FAIL]** |
| **Gate 6** | Integridad Causal | Cero unresolved y cero gaps inventados | Total unresolved = 4 (1 por celda al cierre de datos DEV en Dic 2023); gap rejections = 20 / 16 fielmente rechazadas | **FAILED [FAIL]** |

---

## Verificación de Integridad y Determinismo

1. **Determinismo Bit a Bit en Rerun:**
   - Celda evaluada en rerun idéntico: `L20_realista`.
   - Hash de trades Corrida 1: `41cf971d1fd87a6556a29a667de8334a9461dbf9da6fca6b11af8838704ab059`.
   - Hash de trades Corrida 2: `41cf971d1fd87a6556a29a667de8334a9461dbf9da6fca6b11af8838704ab059`.
   - Coincidencia: **100% idéntica bit a bit**.
2. **Control Anti-Fraude (Trivial Wrapper vs Base):**
   - Hash de trades estrategia base: `ee240936a99c07b8...`.
   - Hash de trades wrapper pasante: `ee240936a99c07b8...`.
   - Coincidencia: **100% idéntica bit a bit**.
3. **Tests Unitarios y Causales:**
   - 11 tests ejecutados con `pytest`: **11 passed en 3.20s**.
   - Incluyen comprobación de NR7 con empates, exclusión de la barra $t$ del Donchian, fill en $t+1$, redondeo de ATR a ticks, corte estricto de DEV y rechazo de preregistro alterado.
4. **Declaración de Cero Red / Cero Broker / Cero Holdout:**
   - Cero llamadas a APIs externas o brokers.
   - Credenciales del `.env` intocadas.
   - Periodo de validación (`2024-01-01` en adelante) estrictamente no materializado ni inspeccionado.

---

## Estado Git y Diff Stat

```text
## bloque-o1-comp-break
 M lab_artifacts/rt9_protocol/acceptance_mock_report.json
 M lab_artifacts/rt9_protocol/acceptance_mock_session.jsonl
?? lab_artifacts/_tmp_hermes_verify/
?? lab_artifacts/o1_protocol/
?? lab_artifacts/rt9_protocol/acceptance_live_session.jsonl
```

- Los cambios preexistentes en `lab_artifacts/rt9_protocol/` y `lab_artifacts/_tmp_hermes_verify/` permanecen intactos.
- No se han modificado archivos dentro de `src/`, `tests/` ni ninguna estrategia retirada.
- No se ha realizado ningún commit, merge, stash ni push.

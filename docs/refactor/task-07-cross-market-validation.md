# Tarea 7: validación cruzada AMD+CRT en MES y MYM

## Alcance y decisión

Rama `feature/cross-market-validation`, basada en
`d499bbab437cacacfaa8d15ac901802fedba1ec9`. La implementación y este reporte
forman un único commit local; no hubo merge ni push.

Se ejecutó la versión congelada de AMD+CRT exactamente una vez en MES y una vez
en MYM, con `train_fraction=0.7` y `friction_points=0.0`. No se modificaron
señales, sesiones, ATR, mediana, TP, SL, time exit, EMA ni parámetros. El
escenario es:

**GROSS ZERO-FRICTION UPPER BOUND — NOT LIVE-REALISTIC**

Los dos mercados pierden OOS antes de costes: MES tiene expectancy bruta
`-0.0042225951 R` y PF `0.930196`; MYM tiene expectancy bruta
`-0.0041683778 R` y PF `0.861367`. Por la regla predefinida, costes reales solo
empeorarían estos resultados. No se ejecutaron más backtests, escenarios de
coste ni búsquedas de parámetros.

## Implementación

- `amd-crt --write-trades` conserva el comportamiento anterior cuando se omite.
- El pipeline expone opcionalmente los resultados completos IS/OOS sin duplicar
  el motor de backtest; `run_pipeline()` mantiene su retorno histórico.
- Se escriben `in_sample_trades.csv`, `out_of_sample_trades.csv`, el resumen JSON
  existente y `run_manifest.json`.
- Los CSV de trades incluyen todos los campos de `ExecutedTrade` más `asset`,
  `strategy` y `segment`. `exit_time` se mapea a `timestamp` al usar FARS.
- El manifest audita y registra dataset, `MarketSpec`, configuración completa,
  split, fricción efectiva, runtime, commit, limitaciones y hashes de outputs.
  La auditoría estructural bloquea símbolos incorrectos, intervalo ambiguo,
  timestamps inválidos/duplicados/desordenados, OHLC inválido y gaps no múltiplos.

## Auditoría de datasets

Ambos archivos permanecen fuera del repositorio y `git ls-files` confirma que
`MES_M5.csv` y `MYM_M5.csv` no están versionados.

| Campo | MES | MYM |
|---|---:|---:|
| Ruta | `.../databento_mnq/databento/MES_M5.csv` | `.../databento_mnq/databento/MYM_M5.csv` |
| SHA-256 | `343c94c44cee038c1965fc068b078f2cb90cd32d0d04fed3ecaf97d196003c44` | `c3a7d21573d4acda8ebd0441aac3d2532de3f99626df7bd4188964805cc6f2bd` |
| Bytes / barras | 67,127,800 / 991,638 | 68,141,744 / 990,234 |
| Fechas UTC | 2010-06-07 00:00 — 2026-09-03 23:55 | 2010-06-07 00:00 — 2026-09-03 23:55 |
| Columnas | `timestamp,open,high,low,close,volume,timeframe,symbol` | iguales |
| Intervalo / zona observada | 300 s / UTC | 300 s / UTC |
| Duplicados / desorden / OHLC inválido | 0 / 0 / 0 | 0 / 0 / 0 |
| Transiciones con gap | 6,403 | 7,491 |
| Intervalos M5 equivalentes dentro de gaps | 717,066 | 718,470 |
| Gaps no múltiplos de M5 | 0 | 0 |

Calidad estructural: utilizable, confianza alta. La cifra de gaps usa deltas
mayores que el intervalo modal y no aplica calendario bursátil; incluye cierres
programados y festivos, por lo que no equivale a pérdida de feed. Esta
limitación de completitud es de severidad media.

Los nombres/ruta son compatibles con una serie continua de Databento, pero los
CSV no contienen contrato subyacente ni marcador de rollover. Por tanto no se
pueden auditar independientemente fechas de transición, regla de roll ni ajuste
de precios. Esta es una limitación de severidad alta para interpretación
económica; los resultados no validan el tratamiento del contrato continuo.

`MarketSpec` procede del contrato documentado en Tarea 6: MES usa ET,
00:00–09:30/09:30–16:00, USD 5/punto y tick 0.25; MYM usa las mismas ventanas,
USD 0.50/punto y tick 1.0. Sus costes registrados siguen siendo desconocidos
(`None`); el cero fue un override explícito exclusivo de este límite superior.

## Resultados IS/OOS

| Mercado/segmento | Trades | Win rate | Exp. R | Exp. USD | PF | PnL USD | Max DD USD / R / % |
|---|---:|---:|---:|---:|---:|---:|---:|
| MES IS | 761 | 45.598% | -0.00179369 | -0.8968 | 0.933284 | -682.50 | 1,635 / 3.270 / 3.244% |
| MES OOS | 447 | 42.282% | -0.00422260 | -2.1113 | 0.930196 | -943.75 | 2,655 / 5.310 / 5.304% |
| MYM IS | 776 | 41.495% | -0.00169588 | -0.8479 | 0.911767 | -658.00 | 1,084.50 / 2.169 / 2.166% |
| MYM OOS | 487 | 36.550% | -0.00416838 | -2.0842 | 0.861367 | -1,015.00 | 1,090 / 2.180 / 2.179% |

| Mercado/segmento | Ganancia media USD | Pérdida media USD | G/P | Long/short | Stop/TP/time | Primera entrada / última salida UTC |
|---|---:|---:|---:|---:|---:|---|
| MES IS | 27.514 | 26.164 | 1.052 | 374/387 | 257/49/455 | 2013-04-29 13:35 / 2022-06-22 13:50 |
| MES OOS | 66.541 | 53.439 | 1.245 | 205/242 | 162/31/254 | 2022-06-27 13:40 / 2026-08-28 14:10 |
| MYM IS | 21.116 | 16.646 | 1.269 | 379/397 | 318/92/366 | 2013-04-29 14:15 / 2022-06-20 22:00 |
| MYM OOS | 35.430 | 23.771 | 1.490 | 213/274 | 287/101/99 | 2022-06-27 13:40 / 2026-09-02 15:40 |

Los exports reales están ordenados cronológicamente, tienen el símbolo correcto
y no comparten ninguna firma entrada/salida/dirección/precios entre IS y OOS.

## Auditoría FARS y bootstrap OOS

`fars audit` aceptó 447/447 filas MES y 487/487 MYM, con cero rechazos, errores
o warnings. `core_metrics` y `temporal_analysis` quedaron disponibles. La
capacidad `daily_rule_simulation` no se solicitó y quedó deshabilitada porque no
se pasó una timezone de análisis.

Ambas series rechazaron IID y FARS seleccionó Circular Block Bootstrap (CBB),
2000 réplicas, semilla 42 e intervalos 95%. Los intervalos CBB son exploratorios:
suponen dependencia estacionaria de memoria corta, no la demuestran.

| Mercado | Expectancy R | IC percentil 95% | IC básico 95% | Win rate | IC 95% win rate |
|---|---:|---:|---:|---:|---:|
| MES | -0.00422260 | [-0.0178479, 0.0094992] | [-0.0179444, 0.0094027] | 0.422819 | [0.375839, 0.465324] |
| MYM | -0.00416838 | [-0.0087833, 0.0005426] | [-0.0088793, 0.0004466] | 0.365503 | [0.330595, 0.398357] |

Bloques elegidos por estimando: MES expectancy `L=2, k=224`, win rate
`L=1, k=447` y desviación `L=4, k=112`; MYM expectancy `L=5, k=98`, win rate
`L=10, k=49` y desviación `L=1, k=487`. MES mostró rechazos de dependencia en
retornos cuadrados y cambios de nivel/dispersión; MYM rechazó por runs y cambios
de magnitud entre regímenes. En ambos casos FARS marca la validez como
`exploratory_dependent`; los intervalos de expectancy incluyen cero y no deben
interpretarse como evidencia de edge.

## Artefactos y hashes

Directorio externo: `/Users/ricardomedina/Documents/TRADING/fars-results/task-07/`.

| Mercado | Trades IS SHA-256 | Trades OOS SHA-256 | Resumen SHA-256 |
|---|---|---|---|
| MES | `1934cbebc70b7374fee4a3d6c7733c3525a663d9abc29a3e9503e372598b05b0` | `c028f71be3717178e1326f00a1bb70c2545235f98c585c437129977c9006d5e0` | `c4ea303c061e2fb2df4f9b521ae7c8bd17633f9c13be9be58bbadafaedd1a7a8` |
| MYM | `b397de9505a60de09efd5a3cb5d5783ebe67f47af5f838deb606b449bcf740c5` | `d86b1035513da1d52a093ef5da38b07c705d031417a614a12aafa106d10968fa` | `7474045b1a083ec0b365568b431c808527bdce04e705721bb8486e7d71a5b69c` |

Cada mercado tiene exactamente un manifest, una marca de ejecución UTC y un
único juego de cuatro outputs. MES se ejecutó a `2026-09-10T19:15:34Z` y MYM a
`2026-09-10T19:28:07Z`. Estas dos invocaciones son la evidencia de las dos
corridas preespecificadas; no existen directorios de escenarios alternativos.

## Validación y comandos

- Baseline relevante: 232 tests aprobados en 53.50 s.
- Tests nuevos (validación final): 6 aprobados en 2.05 s.
- Regresión AMD+CRT/mercados/executor/loader/bootstrap/CLI: 232 aprobados en
  50.59 s.
- Suite completa final: 1121 aprobados en 358.18 s.
- Ruff y `git diff --check`: PASS.

Comandos de evidencia (rutas abreviadas aquí solamente para legibilidad):

```sh
LC_ALL=C /opt/anaconda3/bin/python -m pytest tests/test_backtest_run_artifacts.py -q
LC_ALL=C /opt/anaconda3/bin/python -m pytest tests/test_backtest_markets.py tests/test_backtest_amd_crt.py tests/test_backtest_executor.py tests/test_backtest_history.py tests/test_bootstrap.py tests/test_cli.py -q
LC_ALL=C /opt/anaconda3/bin/python -m pytest tests/ -q
/opt/anaconda3/bin/python -m ruff check src/backtest/cli.py src/backtest/pipeline.py src/backtest/run_manifest.py tests/test_backtest_run_artifacts.py

LC_ALL=C /opt/anaconda3/bin/python -m src.backtest.cli amd-crt --bars-csv .../MES_M5.csv --market MES --friction-pts 0.0 --train-fraction 0.7 --write-trades --out-dir .../task-07/MES
LC_ALL=C /opt/anaconda3/bin/python -m src.backtest.cli amd-crt --bars-csv .../MYM_M5.csv --market MYM --friction-pts 0.0 --train-fraction 0.7 --write-trades --out-dir .../task-07/MYM

LC_ALL=C /opt/anaconda3/bin/python -m src.cli audit .../out_of_sample_trades.csv --outcomes-finalized --map exit_time=timestamp --format json
LC_ALL=C /opt/anaconda3/bin/python -m src.cli bootstrap .../out_of_sample_trades.csv --outcomes-finalized --map exit_time=timestamp --seed 42 --replicates 2000 --format json
```

No se creó notebook: las comprobaciones son reproducibles mediante la CLI, los
manifests y los tests versionados. El bootstrap emitió avisos inocuos del
entorno sobre consultas `sysctl` de CPU; ambos comandos terminaron con exit 0.

## Conclusión limitada

Esta conclusión aplica solo a la versión congelada del pipeline, estos dos CSV,
este split cronológico y el límite superior bruto sin fricción. AMD+CRT pierde
IS y OOS tanto en MES como en MYM, y OOS tiene expectancy negativa y PF menor
que uno en ambos. No hay base para añadir costes reales o intentar rescatar la
estrategia dentro de esta tarea.

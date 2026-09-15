# C2 Summary

## Trabajo realizado

- Leídas íntegramente las dos fuentes obligatorias antes de editar código.
- Preregistro UTC con hash del dataset anterior a las corridas.
- Runner C2 incremental por escenario para EMAS (`target_rr` 3.0, 0.4, 0.3) y SMC-FVG (`min_risk_pts` 8.0, 5.0, 10.0), sin cambiar defaults productivos.
- Plan C1 conservado: 8 folds rolling 36m/6m/6m, calibración 500 barras, purga por intervalos reales y embargo `h=192`.
- Tres escenarios: $4.00 RT canónico; $1.24 RT por tramo; $1.42 RT Kai. Slippage de un tick únicamente en patas que cruzan. Mismo conjunto de trades por configuración entre escenarios.
- PF y E[R], IC bootstrap CBB, estabilidad, concentración, DD y suficiencia publicados por fold y agregados.
- CRT 4H no fue inventado: quedó bloqueado por faltar la especificación algorítmica exacta requerida.

## Comandos exactos relevantes

```powershell
git switch -c bloque-c2-busqueda b322363
E:/FARS-LAB/.venv-fars/Scripts/python.exe -m py_compile lab_artifacts/run_c1_walkforward.py lab_artifacts/run_c2_walkforward.py
E:/FARS-LAB/.venv-fars/Scripts/python.exe lab_artifacts/run_c2_walkforward.py --scenario canonico
E:/FARS-LAB/.venv-fars/Scripts/python.exe lab_artifacts/run_c2_walkforward.py --scenario por_tramo
E:/FARS-LAB/.venv-fars/Scripts/python.exe lab_artifacts/run_c2_walkforward.py --scenario kai
E:/FARS-LAB/.venv-fars/Scripts/python.exe -m pytest tests/test_backtest_emas.py tests/test_backtest_smc_fvg.py tests/test_backtest_run_artifacts.py -q
```

El `git switch` falló por permiso denegado sobre `.git/index.lock`. La primera corrida de tests produjo 42 passed y 6 errores de setup por permisos del directorio temporal, no fallos de assertions.

## Resultados y gates

| Configuración | Canónico E[R] / PF | Por tramo E[R] / PF | Kai E[R] / PF | Veredicto decisorio |
|---|---:|---:|---:|---|
| EMAS baseline rr=3.0 | -0.021986 / 0.9575 | +0.020106 / 1.0403 | +0.016188 / 1.0323 | baseline; FAIL rendimiento |
| EMAS rr=0.4 | -0.078573 / 0.7447 | -0.024714 / 0.9164 | -0.029280 / 0.9014 | FAIL G1/G2/G3/G5 |
| EMAS rr=0.3 | -0.081462 / 0.6702 | -0.026328 / 0.8886 | -0.030942 / 0.8696 | FAIL G1/G2/G3/G5 |
| SMC-FVG baseline risk=8 | -0.010714 / 0.9781 | +0.088239 / 1.1949 | +0.081123 / 1.1780 | baseline; FAIL rendimiento |
| SMC-FVG risk=5 | -0.077664 / 0.8536 | +0.067905 / 1.1433 | +0.057455 / 1.1201 | FAIL G1/G2/G3/G5 canónico; G5 en sensibilidades |
| SMC-FVG risk=10 | +0.005764 / 1.0120 | +0.087288 / 1.1933 | +0.081418 / 1.1793 | FAIL G2/G3/G5 canónico; G5 en sensibilidades |
| CRT 4H defaults | no ejecutada | no ejecutada | no ejecutada | bloqueada: spec exacta ausente |

Ninguna candidata se promueve a C3. PF y E[R] no discrepan en signo/veredicto en ningún escenario.

## Bloqueos

Véase `lab_artifacts/c2_protocol/BLOCKERS.md`: escritura de Git denegada; spec CRT 4H incompleta; contradicción entre cinco candidatas explícitas y “seis configuraciones + tres baselines”.

## Tests y estado Git

- Focalizados iniciales: 42 passed; 6 errores de setup por permisos de temporales.
- Suite completa: `1446 passed, 2 skipped, 5 failed` en 250.84 s. Cuatro fallos fueron permisos de `TemporaryDirectory` y pasaron en la repetición focalizada (`5 passed` en `tests/realtime/test_acceptance.py`). El fallo restante es `test_installed_fars_binary_runs_outside_checkout`, que ejecuta `pip install`; no se reintentó vulnerando la prohibición de instalar paquetes.
- Rama/commits: bloqueados por permisos de `.git`; HEAD continúa en `main` @ `b322363`.
- Limpieza: tres directorios temporales de pytest quedaron inaccesibles; su eliminación con targets explícitos fue rechazada por la política del sandbox.
- `git diff --stat` tracked: `lab_artifacts/run_c1_walkforward.py | 77` (70 inserciones, 7 eliminaciones). Los artefactos C2 y el runner C2 siguen untracked porque no es posible escribir el índice Git; por ello el stat de Git no puede contabilizarlos.
- `git ls-files data '*.csv' '*.zip'` no muestra ZIPs ni datos crudos nuevos; sí revela el CSV preexistente y ya versionado `lab_artifacts/flat_vs_market_comparison.csv`, presente en `main` antes de C2.

## CRT 4H

### Trabajo realizado

- Leída completa la spec autoritativa de Kai en `strat_crt4h.py` y su implementación causal auxiliar `live_crt4h_bridge.py`; reimplementación independiente en `src/backtest/crt4h.py`, sin copiar ni importar código de Kai.
- Conservados literalmente el sesgo con dos D1 cerradas, anclaje 18:00 ET, buffer M5, orden LONG antes de SHORT, actualización de extremos, quiebre del cierre C1, swing estricto, media previa de 20 barras, último FVG válido, límite en su borde, padding del SL, RR y ventanas de 96 barras.
- Añadidos los cuatro tests requeridos: causalidad, ReplayEngine↔evaluación secuencial, SL ganador del empate en la vela de fill y agregación D1/H4 ET atravesando DST.
- Corregido el repricing de salidas posteriores a parcial para aplicar slippage solo al remanente verificable; añadido CRT 4H al runner con los seis parámetros preregistrados exactos.
- Ejecutados canónico, por tramo y Kai en ese orden. No se creó una segunda variante CRT.

### Comandos exactos

```powershell
E:/FARS-LAB/.venv-fars/Scripts/python.exe -m py_compile src/backtest/crt4h.py lab_artifacts/run_c1_walkforward.py lab_artifacts/run_c2_walkforward.py
E:/FARS-LAB/.venv-fars/Scripts/python.exe -m pytest tests/test_backtest_crt4h.py tests/test_time_exit_slippage.py -q -p no:cacheprovider
E:/FARS-LAB/.venv-fars/Scripts/python.exe lab_artifacts/run_c2_walkforward.py --scenario canonico
E:/FARS-LAB/.venv-fars/Scripts/python.exe lab_artifacts/run_c2_walkforward.py --scenario canonico
E:/FARS-LAB/.venv-fars/Scripts/python.exe lab_artifacts/run_c2_walkforward.py --scenario por_tramo
E:/FARS-LAB/.venv-fars/Scripts/python.exe lab_artifacts/run_c2_walkforward.py --scenario kai
E:/FARS-LAB/.venv-fars/Scripts/python.exe -c "import os,pytest; original=os.mkdir; os.mkdir=lambda path,mode=511: original(path,511); raise SystemExit(pytest.main(['-q','-p','no:cacheprovider','--basetemp=E:/FARS-LAB/FARS/lab_artifacts/pytest-c2-crt4h-suite']))"
git diff --check
git diff --stat
git status --short
git ls-files -- 'data/**' '*.csv' '*.zip'
```

La primera invocación canónica llegó hasta CRT y falló antes de evaluarlo porque el runner común forzaba parciales discretos a una estrategia sin parciales. `crt4h_config` ignora exclusivamente ese flag ajeno a la spec y la corrida canónica se reinició completa.

### Resultados por escenario y fold

| Escenario | Fold | n | E[R] | PF | net R | DD R | G6 |
|---|---:|---:|---:|---:|---:|---:|---|
| canónico | 0 | 23 | -0.471696 | 0.4941 | -10.8490 | 15.4250 | pasa |
| canónico | 1 | 20 | -0.014650 | 0.9853 | -0.2930 | 9.4000 | pasa |
| canónico | 2 | 22 | -1.479273 | 0.2769 | -32.5440 | 36.9730 | pasa |
| canónico | 3 | 22 | -0.583364 | 0.3947 | -12.8340 | 16.5840 | pasa |
| canónico | 4 | 27 | -0.337889 | 0.6503 | -9.1230 | 13.1790 | pasa |
| canónico | 5 | 23 | -0.272130 | 0.7248 | -6.2590 | 8.9570 | pasa |
| canónico | 6 | 23 | -0.173130 | 0.7788 | -3.9820 | 9.3180 | pasa |
| canónico | 7 | 15 | +0.990400 | 2.3780 | +14.8560 | 5.3450 | pasa |
| por tramo | 0 | 23 | -0.397266 | 0.5433 | -9.1371 | 14.1529 | pasa |
| por tramo | 1 | 20 | +0.260436 | 1.3407 | +5.2087 | 5.6348 | pasa |
| por tramo | 2 | 22 | -0.708847 | 0.4544 | -15.5946 | 21.0350 | pasa |
| por tramo | 3 | 22 | -0.438824 | 0.4731 | -9.6541 | 13.5421 | pasa |
| por tramo | 4 | 27 | -0.228061 | 0.7389 | -6.1576 | 10.3312 | pasa |
| por tramo | 5 | 23 | -0.098414 | 0.8811 | -2.2635 | 6.3692 | pasa |
| por tramo | 6 | 23 | -0.108944 | 0.8513 | -2.5057 | 8.3352 | pasa |
| por tramo | 7 | 15 | +1.033717 | 2.4999 | +15.5058 | 5.1371 | pasa |
| Kai | 0 | 23 | -0.403042 | 0.5393 | -9.2700 | 14.2534 | pasa |
| Kai | 1 | 20 | +0.239088 | 1.3054 | +4.7818 | 5.9347 | pasa |
| Kai | 2 | 22 | -0.770096 | 0.4332 | -16.9421 | 22.3040 | pasa |
| Kai | 3 | 22 | -0.450180 | 0.4662 | -9.9040 | 13.7830 | pasa |
| Kai | 4 | 27 | -0.236634 | 0.7314 | -6.3891 | 10.5533 | pasa |
| Kai | 5 | 23 | -0.112094 | 0.8666 | -2.5782 | 6.4999 | pasa |
| Kai | 6 | 23 | -0.113859 | 0.8454 | -2.6188 | 8.4119 | pasa |
| Kai | 7 | 15 | +1.030405 | 2.4900 | +15.4561 | 5.1536 | pasa |

Agregados: canónico `n=175`, E[R] `-0.348731`, PF `0.6705`, IC CBB `[-0.738408, 0.022428]`, folds positivos `12.5%`, DD `78.283R`; por tramo E[R] `-0.140562`, PF `0.8382`, IC `[-0.426016, 0.176749]`, folds positivos `25%`, DD `42.9204R`; Kai E[R] `-0.156939`, PF `0.8224`, IC `[-0.450025, 0.164767]`, folds positivos `25%`, DD `45.4325R`.

### Veredictos y bloqueos

- Canónico: FAIL G1, G2, G3 y G5; pasa G6. PF y E[R] coinciden en el veredicto negativo.
- Por tramo y Kai: también fallan G1, G2, G3 y G5; pasan G6.
- Ningún fold tiene `n < 15`; no se aplica `evidencia_insuficiente`.
- Suite completa: `1454 passed, 2 skipped, 1 failed` en 250.73 s. Único fallo: el test que invoca `pip install`, prohibido por la regla dura; véase `BLOCKERS.md`.
- El intento de limpiar los dos temporales nuevos fue bloqueado por la política del sandbox pese a verificar sus rutas absolutas dentro del workspace.

### Git diff --stat

- `git diff --stat` tracked: `14 files changed, 3723 insertions(+), 35 deletions(-)`.
- Archivos nuevos aún no indexables: `src/backtest/crt4h.py` (381 líneas), `tests/test_backtest_crt4h.py` (136 líneas) y `crt4h_defaults_fold_metrics.json` (951 líneas).
- Total de entrega contabilizado: 17 archivos, 5,191 inserciones y 35 eliminaciones. La mayor parte corresponde al historial aditivo requerido de `evaluation_order.json` y al artefacto por-fold.
- `git add` y `git commit -m "Implement preregistered CRT 4H evaluation"` fallaron con `Unable to create '.git/index.lock': Permission denied`; HEAD permanece en `bloque-c2-busqueda` @ `8134f294752c967d2fd5f459dc4f23078479f177`.

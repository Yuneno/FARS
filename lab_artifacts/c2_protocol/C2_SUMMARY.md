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

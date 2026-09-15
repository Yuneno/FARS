# C3 Summary — cierre del bloque C

## Qué se hizo

- Se congeló antes de correr `N=12,k=3`, 220 caminos, siete competidores PBO,
  las rejillas OAT del Gate 4, siete escenarios de estrés y Gate 7.
- Se añadió `src/cpcv.py`, con grupos cronológicos contiguos, purga por
  intervalos de trades y embargo de 192 barras en cada camino.
- Se ejecutó CPCV/PBO sobre el ganador in-sample de cada camino, separado del
  walk-forward causal.
- Se ejecutaron 35 puntos Gate 4 (11+11 SMC, 13 EMAS), cada uno con walk-forward
  36/6/6, ocho folds y coste por tramo.
- Se reconstruyeron las series cronológicas OOS de cuatro sujetos para estrés
  y para `src.bootstrap.analyze_bootstrap`.
- No se modificaron señales, defaults productivos, motor, detectores ni
  executor; no se escribieron datos CSV/ZIP.

## Comandos exactos relevantes

```powershell
Get-Content -LiteralPath 'E:\FARS-LAB\FARS_LAB_GEMINI_ENCARGO_BLOQUE_C3.md' -Raw
git status --short --branch
git rev-parse HEAD
git branch --show-current
git switch -c bloque-c3-cierre
E:/FARS-LAB/.venv-fars/Scripts/python.exe -m py_compile src/cpcv.py lab_artifacts/run_c3_protocol.py
E:/FARS-LAB/.venv-fars/Scripts/python.exe -m pytest tests/test_cpcv.py -q -p no:cacheprovider --basetemp=E:/FARS-LAB/FARS/lab_artifacts/pytest-c3-unit
E:/FARS-LAB/.venv-fars/Scripts/python.exe lab_artifacts/run_c3_protocol.py
E:/FARS-LAB/.venv-fars/Scripts/python.exe -c "import os,pytest; original=os.mkdir; os.mkdir=lambda path,mode=511: original(path,511); raise SystemExit(pytest.main(['-q','-p','no:cacheprovider','--basetemp=E:/FARS-LAB/FARS/lab_artifacts/pytest-c3-suite']))"
$env:FARS_C3_TEST_TEMPDIR='E:\FARS-LAB\FARS\lab_artifacts\c3-temp-full'
$env:PYTHONPATH='E:\FARS-LAB\FARS\lab_artifacts\c3_test_sitecustomize'
E:/FARS-LAB/.venv-fars/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=E:/FARS-LAB/FARS/lab_artifacts/pytest-c3-suite-green
```

La primera corrida del runner se detuvo tras el primer vecindario por leer
`expectancy_r` donde el contrato C1/C2 se llama `global_expectancy_r`. Se
corrigió solo ese nombre y se reinició el conjunto preregistrado completo; no
se cambió ninguna configuración, rejilla, escenario ni regla de decisión.

## CPCV/PBO

- Parámetros: 12 grupos, 3 test, 220/220 caminos; no fue necesario el fallback
  10×2. Tiempo del tramo CPCV: 19.32 s.
- Auditoría agregada: 495 trades train purgados por solape y 6,795 embargados
  tras grupos test, sobre 1,540 celdas camino×configuración.
- PBO primario E[R]: **2/220 = 0.009091**.
- Distribución logit primaria: mediana 1.299283, Q1 0.587787, Q3 1.299283,
  IQR 0.711496, σ 0.649449, rango [-2.564949, 2.564949].
- E[R] OOS del ganador: mediana 0.088004, IQR 0.054383, σ 0.048300, rango
  [-0.382452, 0.169910].
- PBO secundario Sharpe-like: **3/220 = 0.013636**.
- Distribución logit secundaria: mediana 1.299283, IQR 0, σ 0.593158, rango
  [-0.587787, 2.564949]. Sharpe-like OOS del ganador: mediana 0.084413,
  IQR 0.050723, σ 0.033778.
- Ganador train primario: SMC 8.0 (137), SMC 10.0 (81), CRT 4H (1), SMC 5.0
  (1). Ganador train secundario: SMC 8.0 (162), SMC 10.0 (58).
- Reproducción manual independiente del camino `test_00_01_02`: 7/7 métricas
  train/test y auditorías idénticas; ganador SMC 8.0, rango test 7.

La matriz completa, incluidos train/test por configuración y la auditoría de
cada camino, está en `cpcv_pbo.json`. Su media no se presenta como edge causal.

## Resultados de gates

### Gate 4

| Configuración | Centro E[R] | Peor E[R] / caída | Mejor E[R] | Resultado |
|---|---:|---:|---:|---|
| SMC 8.0 | 0.088239 | 0.079668 / 9.71% | 0.100030 | **PASS plateau**, máximo en borde target bajo |
| SMC 10.0 | 0.087288 | 0.074956 / 14.13% | 0.101880 | **PASS plateau**, máximo en borde target bajo |
| EMAS | 0.020106 | 0.002844 / 85.85% | 0.027901 | **FAIL spike**, máximo en borde ATR alto |

La reproducción manual de `smc_fvg_baseline`, `min_risk_pts=8.8` coincidió
exactamente con el artefacto: E[R]=0.079668 y DD=30.7494R.

### Gate 7

| Configuración | Estado Fase 10 | Welch nivel/mitades p | Levene nivel/mitades p | Gate 7 |
|---|---|---:|---:|---|
| EMAS | `iid_eligible` | 0.316384 | 0.536551 | PASS |
| SMC 8.0 | `unsupported_or_inconclusive` | 0.953414 | 0.034688 | **FAIL definitivo** |
| SMC 10.0 | `iid_eligible` | 0.295512 | 0.006150 | PASS |
| CRT 4H | `iid_eligible` | 0.242376 | 0.745221 | PASS |

SMC 8.0 fue rechazado por `regime_welch_abs_thirds`, p=0.003009, frente a
`α_b=0.003846`; se aplicó literalmente la regla de FAIL definitivo.

## Resultados por escenario de estrés

Cada celda es E[R] / máximo DD R.

| Config | Base | +25% | +50% | Slip todas | LIMIT sin fill | Gap stop | 1 de 2 | Degradado+CBB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| EMAS | .02011/47.72 | .00886/58.48 | -.00238/84.20 | .01632/49.87 | .02011/47.72 | .02016/47.70 | .03011/33.46 | -.20473/589.65 |
| SMC 8 | .08824/31.78 | .07344/33.72 | .05864/35.81 | .05887/35.33 | .10012/22.78 | .08894/31.70 | .08110/27.06 | -.12497/463.96 |
| SMC 10 | .08729/25.69 | .07506/29.90 | .06283/34.90 | .06316/34.36 | .10489/23.06 | .08788/25.65 | .11198/22.96 | -.12561/366.24 |
| CRT 4H | -.14056/42.92 | -.17950/49.17 | -.21844/55.88 | -.18860/50.70 | -.13956/41.88 | -.14053/42.92 | .00470/24.05 | -.45772/93.80 |

En el CBB degradado (bloques 40, B=2,000), las cuatro configuraciones tuvieron
0% de réplicas con E[R]>0 y 0% con DD≤12R. Por ello ninguna es robusta a todos
los escenarios predeclarados.

## Veredictos y riesgo

- SMC 8.0: no elegible, G5 y G7 fallan.
- SMC 10.0: no elegible, solo G5 bloquea entre los gates aplicados en C3.
- SMC 5.0: no elegible, G5; su rol C3 fue competidor PBO.
- EMAS: no elegible, G2/G3/G4/G5.
- EMAS 0.4/0.3: no elegibles, G1/G2/G3/G5; solo PBO en C3.
- CRT 4H: no elegible, G1/G2/G3/G5.
- CRT baseline ausente: no evaluable y excluido del PBO sin inventar resultado.

Conservando el R actual de $500 como unidad monetaria, SMC 8.0 requiere
0.377615× del riesgo actual ($188.81, 0.3776%/trade) para DD≤12R; SMC 10.0,
0.467136× ($233.57, 0.4671%). Para satisfacer también DD<5% los topes son
estrictamente menores que 0.1573% y 0.1946% por trade. En el peor estrés directo
que conserva edge (+50% coste), los topes completos bajan a <0.1396% y
<0.1433%. La expectativa sigue positiva al escalar linealmente. Si 1R se
redefine al nuevo riesgo, el DD en R no baja: solo bajan dólares y porcentaje.

## Tests, bloqueos y estado Git

- Unitarios CPCV: `3 passed`.
- Primera suite completa: `1457 passed, 2 skipped, 1 failed` en 254.42 s; el
  único fallo fue el instalador temporal por permisos del sandbox.
- Test focalizado bajo temp writable: `1 passed` en 3.78 s.
- Suite completa final: **`1458 passed, 2 skipped` en 246.63 s**. El baseline
  solicitado es 1,455; los tres tests CPCV explican exactamente el incremento.
- La rama no pudo crearse: `.git/refs/heads/...lock: Permission denied`.
- La creación de rama se reintentó después de verificar y volvió a fallar; para
  no commitear en `main`, `git add` y `git commit` no se ejecutaron.
- La discrepancia 8 registros / 7 ejecutables y la semántica direccional del
  LIMIT gap están documentadas con preguntas exactas en `BLOCKERS.md`.

## Git diff --stat

Mientras los archivos sigan sin indexar por el bloqueo de `.git`, el comando
literal `git diff main..HEAD --stat` queda vacío porque `HEAD` sigue siendo el
mismo commit de `main`. El inventario efectivo de la entrega es
**12 archivos nuevos, 63,434 líneas** (62,059 líneas corresponden a las cuatro
matrices JSON de resultados; 1,375 a código, tests, preregistro, manifiesto y
documentación). `git status --short` muestra esos entregables y también los
temporales `pytest-c3-*`/`c3-temp*` cuya eliminación fue bloqueada; véase
`BLOCKERS.md`.

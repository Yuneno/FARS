# Bloque C3 — elegibilidad final y cierre del bloque C

## Alcance y regla de lectura

C3 no reestima el edge causal: ese resultado sigue siendo el walk-forward OOS
36/6/6 de C2. El CPCV usa el mismo canónico post-2019 como diagnóstico separado
de selección y dispersión. El escenario decisorio para Gates 1–7 es
`por_tramo`; los escenarios de estrés son contrafactuales predeclarados.

`N/E` significa no ejecutado por el rol preregistrado, no un PASS. C2 contiene
siete configuraciones ejecutables y un octavo registro
`crt4h_baseline_absent`, sin parámetros ni resultado; este último no puede
rankearse sin inventar una métrica.

## Tabla final

| Configuración | G1 E[R] | G2 IC>0 | G3 folds/conc. | G4 vecindario | G5 DD | G6 muestra | G7 estructural | PBO | Estrés | Elegible para D |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|---|---|:---:|
| `smc_fvg_baseline` (8.0) | PASS | PASS | PASS | PASS, plateau | **FAIL** | PASS | **FAIL** | incluido; familia 0.91% | 6/7 positivos; falla degradado+CBB | **NO** |
| `smc_fvg_risk_10` | PASS | PASS | PASS | PASS, plateau | **FAIL** | PASS | PASS | incluido; familia 0.91% | 6/7 positivos; falla degradado+CBB | **NO** |
| `smc_fvg_risk_5` | PASS | PASS | PASS | N/E, solo PBO | **FAIL** | PASS | N/E, solo PBO | incluido; familia 0.91% | N/E | **NO** |
| `emas_baseline` (RR 3.0) | PASS | **FAIL** | **FAIL** | **FAIL**, spike | **FAIL** | PASS | PASS | incluido; familia 0.91% | 5/7 positivos | **NO** |
| `emas_rr_0_4` | **FAIL** | **FAIL** | **FAIL** | N/E, solo PBO | **FAIL** | PASS | N/E, solo PBO | incluido; familia 0.91% | N/E | **NO** |
| `emas_rr_0_3` | **FAIL** | **FAIL** | **FAIL** | N/E, solo PBO | **FAIL** | PASS | N/E, solo PBO | incluido; familia 0.91% | N/E | **NO** |
| `crt4h_defaults` | **FAIL** | **FAIL** | **FAIL** | N/E, control de frecuencia | **FAIL** | PASS | PASS | incluido; familia 0.91% | base negativa; 1/7 positivo | **NO** |
| `crt4h_baseline_absent` | N/E | N/E | N/E | N/E | N/E | N/E | N/E | excluido: no ejecutable | N/E | **NO** |

Fuente de G1/G2/G3/G5/G6: artefactos C2 bajo `por_tramo`. G3 exige a
la vez al menos 75% de folds positivos y concentración menor que 60%. La
columna PBO muestra el diagnóstico familiar: no es un gate individual ni
convierte en causal una media CPCV.

## CPCV/PBO

Se usaron 12 grupos contiguos, 3 grupos de test y los 220 caminos completos.
En cada camino se eliminaron del train los trades cuyo intervalo cerrado
intersectaba un grupo test y se embargaron las entradas en las 192 barras
posteriores a cada grupo test. Sumando las 1,540 celdas camino×configuración,
se purgaron 495 trades y se embargaron 6,795. El ganador se seleccionó siempre
en train; después se midió su rango transversal en test.

| Rango | PBO | Mediana logit | IQR logit | σ logit | Rango logit | Métrica OOS del ganador: mediana / IQR / σ |
|---|---:|---:|---:|---:|---:|---:|
| E[R] neta | **2/220 = 0.009091** | 1.299283 | 0.711496 | 0.649449 | [-2.564949, 2.564949] | 0.088004 / 0.054383 / 0.048300 |
| Sharpe-like | **3/220 = 0.013636** | 1.299283 | 0.000000 | 0.593158 | [-0.587787, 2.564949] | 0.084413 / 0.050723 / 0.033778 |

Ganadores in-sample por E[R]: SMC 8.0 en 137 caminos, SMC 10.0 en 81,
CRT 4H en 1 y SMC 5.0 en 1. Por Sharpe-like: SMC 8.0 en 162 y SMC 10.0
en 58. La distribución completa camino por camino está en
`lab_artifacts/c3_protocol/cpcv_pbo.json`.

## Gate 4 — vecindario preregistrado

Se ejecutó one-at-a-time la rejilla exacta del preregistro, siempre con los
ocho folds y coste `por_tramo`.

| Configuración | Centro E[R] | Peor punto E[R] | Caída | Mejor punto E[R] | Veredicto | Borde |
|---|---:|---:|---:|---:|---|---|
| SMC 8.0 | 0.088239 | 0.079668 (`min_risk_pts=8.8`) | 9.71% | 0.100030 (`target_rr=1.35`) | **PASS / plateau** | Sí; máximo global en target bajo |
| SMC 10.0 | 0.087288 | 0.074956 (`min_risk_pts=11.0`) | 14.13% | 0.101880 (`target_rr=1.35`) | **PASS / plateau** | Sí; máximo global en target bajo |
| EMAS RR 3.0 | 0.020106 | 0.002844 (`max_dist_atr=0.45`) | **85.85%** | 0.027901 (`atr_mult=2.2`) | **FAIL / spike** | Sí; máximo global en ATR alto |

Todos los puntos conservaron E[R]>0, pero EMAS viola la caída máxima del 40%.
Los máximos de borde son advertencias de rejilla, no autorización para ampliar
la búsqueda después de ver resultados.

## Gate 7 — Fase 10

`src.bootstrap.analyze_bootstrap` se aplicó a la serie cronológica de R OOS
de cada sujeto, con `B=2000`, semilla 20260914, `α_family=0.05`, 13 pruebas y
`α_b=0.003846153846`. Se retuvieron expresamente Welch y Levene de nivel por
mitades.

| Configuración | Welch nivel/mitades p | Levene nivel/mitades p | Clasificador | Veredicto |
|---|---:|---:|---|---|
| EMAS baseline | 0.316384 | 0.536551 | `iid_eligible` | PASS |
| SMC 8.0 | 0.953414 | 0.034688 | `unsupported_or_inconclusive` por `regime_welch_abs_thirds`, p=0.003009 | **FAIL definitivo** |
| SMC 10.0 | 0.295512 | 0.006150 | `iid_eligible` | PASS |
| CRT 4H | 0.242376 | 0.745221 | `iid_eligible` | PASS |

El rechazo de SMC 8.0 no lo provoca una prueba de nivel/mitades, sino la prueba
Welch sobre magnitud absoluta por tercios; sigue siendo FAIL porque la regla
vinculante usa el clasificador completo de Fase 10.

## Estrés

E[R] / DD agregado en R, base y estrés lado a lado:

| Configuración | Base | Coste +25% | Coste +50% | Slip todas patas | LIMIT gap sin fill | Stop gap a open | Frecuencia 1/2 | Degradado + pérdidas 1.25× |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| EMAS | 0.02011 / 47.72 | 0.00886 / 58.48 | **-0.00238** / 84.20 | 0.01632 / 49.87 | 0.02011 / 47.72 | 0.02016 / 47.70 | 0.03011 / 33.46 | **-0.20473** / 589.65 |
| SMC 8.0 | 0.08824 / 31.78 | 0.07344 / 33.72 | 0.05864 / 35.81 | 0.05887 / 35.33 | 0.10012 / 22.78 | 0.08894 / 31.70 | 0.08110 / 27.06 | **-0.12497** / 463.96 |
| SMC 10.0 | 0.08729 / 25.69 | 0.07506 / 29.90 | 0.06283 / 34.90 | 0.06316 / 34.36 | 0.10489 / 23.06 | 0.08788 / 25.65 | 0.11198 / 22.96 | **-0.12561** / 366.24 |
| CRT 4H | **-0.14056** / 42.92 | -0.17950 / 49.17 | -0.21844 / 55.88 | -0.18860 / 50.70 | -0.13956 / 41.88 | -0.14053 / 42.92 | 0.00470 / 24.05 | **-0.45772** / 93.80 |

El CBB degradado usa bloques circulares de 40 trades y 2,000 réplicas. En las
cuatro configuraciones, 0% de las réplicas tiene expectativa positiva y 0%
mantiene DD≤12R. El escenario LIMIT descarta 28 trades en SMC 8.0, 19 en SMC
10.0 y 4 en CRT; su mejora aparente no se usa para selección. El escenario de
gaps detecta 25, 246, 197 y 1 stops con gap en EMAS, SMC 8.0, SMC 10.0 y CRT,
respectivamente. Es esencialmente una auditoría de una regla que el executor
ya aplica; la pequeña diferencia contra base proviene de no añadir otro tick
después de llenar al open del gap.

El filtro LIMIT es un contrafactual post-ejecución fijo. Al no reescribir el
executor, no simula las señales posteriores que podrían aparecer si la orden
descartada nunca hubiera ocupado el estado de la estrategia; por eso se trata
como cota de sensibilidad, no como backtest causal alternativo.

## Cuantificación del bloqueo de riesgo

Hay dos unidades que no deben confundirse. Si se reduce proporcionalmente el
sizing y se redefine 1R como el nuevo riesgo por trade, la trayectoria en R y
su DD en R **no cambian**. El sizing sí reduce dólares y porcentaje de cuenta.
La frase “DD≤12R” solo puede cuantificarse mediante sizing si se conserva como
unidad de reporte el R actual de $500 (1% de $50,000).

| Configuración | DD base | Factor para ≤12 R actuales | Riesgo/trade | E por trade en R actuales | Factor para cumplir además DD<5% | Riesgo/trade para G5 |
|---|---:|---:|---:|---:|---:|---:|
| SMC 8.0 | 31.77844R | 0.377615× | $188.81 = 0.3776% | +0.033320R | **<0.157339×** | **<$78.67 = <0.1573%** |
| SMC 10.0 | 25.68844R | 0.467136× | $233.57 = 0.4671% | +0.040775R | **<0.194640×** | **<$97.32 = <0.1946%** |

Como envolvente de los estreses directos que aún conservan E[R]>0, el peor DD
es el coste +50%: 35.81096R para SMC 8.0 y 34.89596R para SMC 10.0. Para
≤12 R actuales hacen falta 0.335093× (0.3351% por trade, E=+0.019651R actual)
y 0.343879× (0.3439%, E=+0.021605R), respectivamente. Para el criterio
porcentual completo de G5 hacen falta **<0.139622%** y **<0.143283%** por trade.

No existe una fracción estrictamente positiva que rescate el escenario CBB
degradado conservando E[R]>0: escalar una expectativa negativa solo reduce su
magnitud, no cambia su signo. Los factores anteriores son candidatos de sizing
para D, no una promoción: deben validarse con trailing intradía, granularidad
de contratos y datos de cuenta Apex.

## Cierre formal del bloque C

No entra ninguna configuración a D en su forma actual. SMC 10.0 es el puente
más limpio —edge causal positivo, Gate 4 plateau, Gate 7 PASS y PBO familiar
bajo—, pero falla Gate 5. SMC 8.0 además falla Gate 7. EMAS no tiene IC positivo
y es un spike paramétrico; CRT 4H y las candidatas EMAS carecen de edge.

Se midieron edge OOS, incertidumbre, estabilidad temporal, muestra, drawdown,
vecindario, selección CPCV/PBO y estrés. Se descartó la elegibilidad actual con
gates explícitos. Todavía no se puede afirmar que un sizing reducido pase una
evaluación real, que los gaps/no-fills postprocesados reproduzcan toda la
dinámica de órdenes, ni que el edge sobreviva al régimen degradado. Esas son
preguntas del bloque D, no conclusiones de C.

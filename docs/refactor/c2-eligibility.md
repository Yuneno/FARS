# C2 — elegibilidad para C3

> [!WARNING]
> **VIGENCIA REVOCADA (2026-09-19):** Las evaluaciones de elegibilidad de SMC-FVG documentadas en este archivo están formalmente invalidadas por el defecto de resolución fill-bar en órdenes límite descansadas. La medición limpia con la regla A1 desploma el WR a 45.19% y los folds positivos a 0/8 (ver [`lab_artifacts/re_congelado_fillbar/RE_CONGELADO.md`](file:///E:/FARS-LAB/FARS/lab_artifacts/re_congelado_fillbar/RE_CONGELADO.md)).

Fecha de evaluación: 2026-09-15 UTC. Dataset canónico: MNQ M5, 518,237 barras, SHA-256 `fbed6061205b8299af140f85e36b472f5f1d88084977ad9c4ca9aa1f817b8a96`. Protocolo: 8 folds rolling 36m/6m/6m, purga por intervalos reales y embargo de 192 barras.

El escenario canónico de $4.00 RT es el escenario decisorio de C2; los escenarios por tramo y Kai son análisis de sensibilidad preregistrados. Gates activos: G1 E[R] neta > 0; G2 IC CBB 95% con límite inferior > 0; G3 al menos 75% de folds positivos y concentración < 60%; G5 DD < 5% y < 12R; G6 al menos 15 trades en cada fold. G4 y G7 no se aplican hasta C3.

| Candidata | Escenario | n OOS | E[R] | PF OOS | IC CBB 95% | Folds + | Concentración | DD | G6 | Veredicto |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| EMAS rr=0.4 | canónico | 3,114 | -0.078573 | 0.7447 | [-0.099721, -0.057359] | 0% | n/a | 255.599R / 255.599% | pasa | **FAIL G1, G2, G3, G5** |
| EMAS rr=0.4 | por tramo | 3,114 | -0.024714 | 0.9164 | [-0.046047, -0.003547] | 25% | n/a | 96.2442R / 96.2442% | pasa | sensibilidad; no cambia FAIL |
| EMAS rr=0.4 | Kai | 3,114 | -0.029280 | 0.9014 | [-0.050595, -0.008039] | 25% | n/a | 108.2729R / 108.2729% | pasa | sensibilidad; no cambia FAIL |
| EMAS rr=0.3 | canónico | 3,143 | -0.081462 | 0.6702 | [-0.100412, -0.062665] | 0% | n/a | 265.765R / 265.765% | pasa | **FAIL G1, G2, G3, G5** |
| EMAS rr=0.3 | por tramo | 3,143 | -0.026328 | 0.8886 | [-0.044734, -0.008156] | 25% | n/a | 99.1459R / 99.1459% | pasa | sensibilidad; no cambia FAIL |
| EMAS rr=0.3 | Kai | 3,143 | -0.030942 | 0.8696 | [-0.049368, -0.012707] | 12.5% | n/a | 111.397R / 111.397% | pasa | sensibilidad; no cambia FAIL |
| SMC-FVG risk=5 | canónico | 5,626 | -0.077664 | 0.8536 | [-0.108216, -0.050049] | 0% | n/a | 451.638R / 451.638% | pasa | **FAIL G1, G2, G3, G5** |
| SMC-FVG risk=5 | por tramo | 5,626 | +0.067905 | 1.1433 | [0.037290, 0.095976] | 87.5% | 23.37% | 70.4457R / 70.4457% | pasa | FAIL G5 |
| SMC-FVG risk=5 | Kai | 5,626 | +0.057455 | 1.1201 | [0.026823, 0.085548] | 87.5% | 25.15% | 75.7524R / 75.7524% | pasa | FAIL G5 |
| SMC-FVG risk=10 | canónico | 2,842 | +0.005764 | 1.0120 | [-0.037198, 0.046864] | 62.5% | 282.64% | 59.204R / 59.204% | pasa | **FAIL G2, G3, G5** |
| SMC-FVG risk=10 | por tramo | 2,842 | +0.087288 | 1.1933 | [0.044207, 0.128331] | 75% | 34.04% | 25.6884R / 25.6884% | pasa | FAIL G5 |
| SMC-FVG risk=10 | Kai | 2,842 | +0.081418 | 1.1793 | [0.038263, 0.122490] | 75% | 35.31% | 27.2741R / 27.2741% | pasa | FAIL G5 |
| CRT 4H defaults | canónico | 175 | -0.348731 | 0.6705 | [-0.738408, 0.022428] | 12.5% | n/a | 78.283R / 78.283% | pasa (mín. 15) | **FAIL G1, G2, G3, G5** |
| CRT 4H defaults | por tramo | 175 | -0.140562 | 0.8382 | [-0.426016, 0.176749] | 25% | n/a | 42.9204R / 42.9204% | pasa (mín. 15) | sensibilidad; no cambia FAIL |
| CRT 4H defaults | Kai | 175 | -0.156939 | 0.8224 | [-0.450025, 0.164767] | 25% | n/a | 45.4325R / 45.4325% | pasa (mín. 15) | sensibilidad; no cambia FAIL |
| EMAS baseline rr=3.0 | canónico | 2,823 | -0.021986 | 0.9575 | [-0.063397, 0.017696] | 37.5% | n/a | 128.829R / 128.829% | pasa | baseline; FAIL rendimiento |
| EMAS baseline rr=3.0 | por tramo | 2,823 | +0.020106 | 1.0403 | [-0.021269, 0.059479] | 50% | 91.22% | 47.725R / 47.725% | pasa | baseline; FAIL rendimiento |
| EMAS baseline rr=3.0 | Kai | 2,823 | +0.016188 | 1.0323 | [-0.025281, 0.055659] | 50% | 111.29% | 50.2277R / 50.2277% | pasa | baseline; FAIL rendimiento |
| SMC-FVG baseline risk=8 | canónico | 3,628 | -0.010714 | 0.9781 | [-0.046842, 0.022748] | 50% | n/a | 74.408R / 74.408% | pasa | baseline; FAIL rendimiento |
| SMC-FVG baseline risk=8 | por tramo | 3,628 | +0.088239 | 1.1949 | [0.052471, 0.121876] | 100% | 32.27% | 31.7784R / 31.7784% | pasa | baseline; FAIL G5 |
| SMC-FVG baseline risk=8 | Kai | 3,628 | +0.081123 | 1.1780 | [0.045286, 0.114761] | 100% | 33.78% | 32.6345R / 32.6345% | pasa | baseline; FAIL G5 |

Ninguna candidata queda promovida a C3. CRT 4H alcanza exactamente 15 operaciones en su fold más pequeño; el resto de configuraciones supera 15. No se usó `evidencia_insuficiente`.

## PF frente a E[R]

PF y E[R] coinciden en signo respecto del umbral 1/0 en todos los resultados. No hay discrepancia de veredicto entre ambas métricas. En el canónico, SMC-FVG `min_risk_pts=10` queda apenas positivo por ambos criterios, pero falla G2, G3 y G5. Bajo costes por tramo/Kai, SMC-FVG 5 y 10 son positivos y estables en PF/E[R], pero el drawdown agregado excede ampliamente ambos límites de G5. CRT 4H queda negativo por ambos criterios en los tres escenarios.

La sonda EMAS rr=0.3 es peor que rr=0.4 en E[R] y PF en los tres escenarios; por tanto no activa la regla preregistrada de degeneración “0.3 mejora a 0.4”. Ambas fallan de todos modos por gates activos.

## Integridad entre escenarios

Cada configuración conserva exactamente el mismo número de trades y ventanas OOS en los tres escenarios. El runner ejecuta la trayectoria canónica congelada y cambia únicamente comisión y slippage adverso por pata: EMAS usa entrada MARKET; SMC-FVG y CRT 4H usan entrada LIMIT; TP y LIMIT no deslizan; STOP y MARKET sí.

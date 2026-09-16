# SMC-OB: Kai defaults / por_tramo

Resultados mecánicos pendientes de revisión independiente de Hermes. No promoción ni commits.

| Mercado | n | WR | E[R] | PF | DD R | IC95 CBB | Folds+ | Veredicto |
|---|---:|---:|---:|---:|---:|---|---:|---|
| MYM | 1794 | 58.0% | -0.0300 | 0.938 | 94.26 | [-0.0931, 0.0266] | 4/8 | FAIL |
| MNQ | 1755 | 60.2% | +0.0724 | 1.172 | 22.29 | [0.0269, 0.1187] | 6/8 | PASS |
| MGC | 1773 | 57.9% | -0.0880 | 0.824 | 176.76 | [-0.1403, -0.0382] | 2/8 | FAIL |

Configuración: swing=10, RR=3, CHoCH=True, lookback=60, f=.5, wait=36, cooldown=0, min_risk=0. Sin filtro horario añadido.
36/6/6 calendario; 500 barras previas de calibración; sin ajuste de parámetros. E[R] usa riesgo presupuestado, PF usa dólares netos. Costes C2 por tramo: .62 USD/pata y .25 puntos × dpp por contrato restante en STOP/BE/MARKET; sin slippage LIMIT/TP.
Gates: E[R]>0, límite inferior IC95>0, folds positivos>=75%, concentración<60% y >=15 trades/fold (C2). DD es solo higiene E3.
Fuentes, hashes, fechas, posiciones sin resolver y métricas por fold: resultados.json. Se reutilizan los loaders existentes; no se cambia ni declara canónico ningún dataset.

## Cuenta E1

| Pool | Política | Pase | Quema | Bloqueadas | Timeout |
|---|---|---:|---:|---:|---:|
| MNQ | fixed | 8.70% | 3.45% | 1.65% | 86.20% |
| MNQ | buffer_prop | 13.10% | 2.55% | 0.00% | 84.35% |

El motor solicitado usa remuestreo IID de trades; sus probabilidades son puntuaciones condicionadas a ese modelo, no evidencia de independencia temporal ni de diversificación del pool. MAE M1/M5 y sus limitaciones se auditan si se activa E1.
Ambigüedades y preguntas exactas en BLOCKERS.md.

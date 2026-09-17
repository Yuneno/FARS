# INFORME — Bloque A2.5: Auditoría de Ambigüedad Intrabarra (M5 vs M1)

> **OBJETIVO.** Auditar cuantitativamente la ambigüedad intrabarra (barras M5 que tocan simultáneamente
> Take Profit y Stop Loss) en las dos estrategias troncales de FARS (**SMC-FVG** y **EMAS**) utilizando
> resolución causal bar-a-bar con el stream **M1** de `databento.zip` sobre 518.237 barras canónicas (2019-2026).

---

## 1. Respuestas Directas a las Preguntas de la Tarea

1. **¿Cuál es el porcentaje de trades ambiguos?**
   - **SMC-FVG:** **10.41 %** de los trades presentan ambigüedad intrabarra en M5 (622 de 5.973 trades canónicos; 10,80 % en Walk-Forward OOS).
   - **EMAS:** **0.29 %** (solo 15 de 5.100 trades), prácticamente nula debido a la distancia amplia de sus niveles respecto al rango de la vela M5.

2. **¿Cuál es el delta de PnL entre resolver conservador (stop primero) vs resolución real con M1?**
   - **SMC-FVG:** Delta masivo de **+$487,535.50 USD** (+155,1 % sobre el PnL conservador). El Win Rate sube de 58.93% a 65.91% y el Profit Factor pasa de 1.2397 a 1.7379.
   - **EMAS:** Delta marginal de **+$4,099.50 USD** (el Win Rate permanece inalterado al 50,94 %).

3. **¿El edge de SMC-FVG depende del supuesto de fill?**
   - **NO DEPENDE PARA EXISTIR, PERO SÍ PARA SU MAGNITUD REAL:**
     - Bajo la cota conservadora más estricta (asumir que el 100 % de las velas ambiguas tocan stop loss primero), SMC-FVG **sigue siendo sólidamente rentable** (+\$314,391.94 USD, PF 1.2397). El edge no es un espejismo creado por fills optimistas.
     - Sin embargo, la cota conservadora es **extremadamente pesimista**: la resolución causal con M1 revela que el **72,2 %** de las barras ambiguas tocan realmente el Take Profit antes que el Stop Loss, mientras que solo el **16,6 %** toca el Stop Loss primero (el 11,3 % restante presenta ambigüedad residual dentro del mismo minuto M1).

---

## 2. Tabla Comparativa General de Ambigüedad Intrabarra

| Métrica | SMC-FVG (Canónico) | EMAS (Canónico) | SMC-FVG (Walk-Forward OOS) |
|---|---|---|---|
| **Total Trades Evaluados** | 5973 | 5100 | 3619 |
| **Barras M5 Ambiguas (SL y TP)** | **622** | **15** | **391** |
| **% de Trades con Ambigüedad** | **10.41 %** | **0.29 %** | **10.80 %** |
| **Barras M1 Extraídas y Evaluadas** | 3110 | 75 | — |
| **Resueltas a Take Profit (M1 TP)** | **449 (72.19 %)** | 5 (33.33 %) | — |
| **Resueltas a Stop Loss (M1 SL)** | **103 (16.56 %)** | 3 (20.0 %) | — |
| **Ambigüedad Residual en M1** | 70 (11.25 %) | 7 (46.67 %) | — |
| **PnL Neto Conservador (SL primero)** | **+\$314,391.94 USD** | -\$11,494.00 USD | +\$179,587.24 USD |
| **PnL Neto Resuelto M1** | **+\$801,927.44 USD** | -\$7,394.50 USD | +\$480,040.74 USD |
| **Delta PnL (M1 vs Conservador)** | **+$487,535.50 USD** | **+$4,099.50 USD** | **+$300,453.50 USD** |
| **Win Rate (Conservador -> M1)** | 58.93 % -> 65.91 % | 50.94 % -> 50.94 % | — |
| **Profit Factor (Conservador -> M1)** | 1.2397 -> 1.7379 | 0.9912 -> 0.9943 | — |

---

## 3. Desglose Walk-Forward OOS para SMC-FVG (8 Folds)

| Fold | Trades | Ambiguos | % Ambiguo | PnL Conservador | PnL M1 Resuelto | Delta PnL |
|---|---|---|---|---|---|---|
| Fold 0 | 408 | 41 | 10.05 % | $+27,277.16 | $+62,577.66 | $+35,300.50 |
| Fold 1 | 297 | 27 | 9.09 % | $+3,268.08 | $+24,126.58 | $+20,858.50 |
| Fold 2 | 282 | 15 | 5.32 % | $+6,736.00 | $+19,295.00 | $+12,559.00 |
| Fold 3 | 300 | 30 | 10.0 % | $+16,585.26 | $+37,760.76 | $+21,175.50 |
| Fold 4 | 494 | 47 | 9.51 % | $+27,748.12 | $+61,930.12 | $+34,182.00 |
| Fold 5 | 582 | 62 | 10.65 % | $+51,241.92 | $+100,576.42 | $+49,334.50 |
| Fold 6 | 499 | 44 | 8.82 % | $+31,596.32 | $+64,443.82 | $+32,847.50 |
| Fold 7 | 757 | 125 | 16.51 % | $+15,134.38 | $+109,330.38 | $+94,196.00 |

---

## 4. Hallazgos Metodológicos y Conclusión

1. **La asimetría del micro-momentum:** En SMC-FVG, cuando el precio rompe una zona FVG con fuerza hacia el target, frecuentemente genera una mecha rápida de retroceso en los primeros segundos de la vela de 5 minutos y luego continúa disparado al target. En M5 esto parece que tocó ambos niveles a la vez; la resolución cronológica en M1 demuestra que en más del 70 % de las ocasiones el precio alcanza la meta antes de cualquier retesteo terminal.
2. **Robustez estructural:** FARS diseñó el arnés original con la regla conservadora (asumir SL siempre). Esta auditoría confirma que el edge histórico de SMC-FVG es genuino y robusto, pues no depende de suposiciones optimistas. Al contrario, la regla conservadora introduce un sesgo negativo severo de más de \$480.000 USD sobre el PnL neto total.

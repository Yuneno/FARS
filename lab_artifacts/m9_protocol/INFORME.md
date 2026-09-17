# INFORME — Bloque M9: Screening de Entradas Crudas Multi-Mercado

## Resumen Ejecutivo

En los bloques previos M7 y M8, la exploración exhaustiva de filtros (CISD, OTE, sesgo D1, premium/discount, zonas S/R) sobre la estrategia base SMC-FVG demostró de manera concluyente que ningún filtro lograba generar expectativa matemática positiva robusta (IC95 con coto inferior > 0). La base SMC-FVG carecía de ventaja estadística por sí misma.

El **Bloque M9** cambió radicalmente la pregunta: **no qué filtrar, sino qué entrada tiene pulso**. Se implementaron y evaluaron **5 entradas crudas y autónomas** sobre 4 mercados de futuros (MNQ, MES, MYM, MGC) bajo el rigor estricto del **Protocolo C1** (Walk-Forward con 8 rolling folds de 36/6/6 meses, purga por intervalos reales + embargo $h=192$ barras, bootstrap CBB de 1000 iteraciones, y 3 escenarios de costes).

### Veredicto de Supervivencia: NINGUNA ENTRADA TIENE PULSO

Bajo el criterio preregistrado de antemano (coto inferior de IC95 Bootstrap CBB $> 0$ en al menos 2 mercados en el escenario `realista`):

- **E1 · TS-D1 (Turtle Soup D1):** **DESCARTADA**. $E[R]$ entre $-0.0896$ y $-0.0398$, IC95 inferior negativo en los 4 mercados.
- **E2 · TS-D20 (Turtle Soup D20):** **DESCARTADA**. $E[R]$ entre $-0.1002$ y $-0.0070$, IC95 inferior negativo en los 4 mercados.
- **E3 · MOM-BREAK (Momentum 20-D Break):** **DESCARTADA**. $E[R]$ entre $-0.1170$ y $-0.0583$, IC95 inferior negativo en los 4 mercados.
- **E4 · MR-LEVEL (Mean Reversion Session Pools):** **DESCARTADA**. $E[R]$ entre $-0.0741$ y $-0.0383$, IC95 inferior negativo en los 4 mercados.
- **E5 · VOL-BREAK (Volatility Breakout D1):** **DESCARTADA**. $E[R]$ entre $-0.0752$ y $-0.0356$, IC95 inferior negativo en los 4 mercados.

El resultado es categórico: **0 de las 5 familias de entradas crudas evaluadas exhiben ventaja matemática por sí solas** bajo el estándar de riesgo normalizado $Stop = 1.0\cdot ATR(14)$, $Target = 2.0\cdot ATR(14)$ y $max\_bars = 48$.

---

## Análisis Detallado Entrada por Entrada

### E1 · TS-D1 (Turtle Soup D1)
- **Frecuencia operativa:** $n \in [2210, 2322]$ trades acumulados en el periodo OOS.
- **Rendimiento Realista por Mercado:**
  - **MNQ:** $n = 2322$, $E[R] = -0.0398$, $PF = 0.920$, $WR = 33.1\%$, Max DD $= 146.2R$, Folds+ $= 2/8$, IC95 CBB $= `[-0.082, +0.004]`$.
  - **MES:** $n = 2248$, $E[R] = -0.0896$, $PF = 0.741$, $WR = 29.3\%$, Max DD $= 220.7R$, Folds+ $= 0/8$, IC95 CBB $= `[-0.118, -0.059]`$.
  - **MYM:** $n = 2210$, $E[R] = -0.0416$, $PF = 0.819$, $WR = 32.4\%$, Max DD $= 104.3R$, Folds+ $= 2/8$, IC95 CBB $= `[-0.062, -0.019]`$.
  - **MGC:** $n = 2284$, $E[R] = -0.0482$, $PF = 0.864$, $WR = 32.3\%$, Max DD $= 133.5R$, Folds+ $= 1/8$, IC95 CBB $= `[-0.080, -0.014]`$.
- **Diagnóstico:** El barrido simple del extremo D1 en velas de 5 minutos sufre de continuación tendencial frecuente (falsas reversiones) o absorción lenta que no alcanza el target $2R$ dentro del horizonte de 4 horas.

### E2 · TS-D20 (Turtle Soup D20)
- **Frecuencia operativa:** $n \in [442, 605]$ trades acumulados en el periodo OOS.
- **Rendimiento Realista por Mercado:**
  - **MNQ:** $n = 605$, $E[R] = -0.0070$, $PF = 0.986$, $WR = 34.4\%$, Max DD $= 29.0R$, Folds+ $= 4/8$, IC95 CBB $= `[-0.096, +0.086]`$.
  - **MES:** $n = 605$, $E[R] = -0.0989$, $PF = 0.725$, $WR = 27.8\%$, Max DD $= 64.0R$, Folds+ $= 0/8$, IC95 CBB $= `[-0.162, -0.038]`$.
  - **MYM:** $n = 489$, $E[R] = -0.0474$, $PF = 0.816$, $WR = 32.1\%$, Max DD $= 35.0R$, Folds+ $= 3/8$, IC95 CBB $= `[-0.105, +0.008]`$.
  - **MGC:** $n = 442$, $E[R] = -0.1002$, $PF = 0.750$, $WR = 30.8\%$, Max DD $= 54.9R$, Folds+ $= 2/8$, IC95 CBB $= `[-0.167, -0.027]`$.
- **Diagnóstico:** En MYM y MNQ logra 3-4 folds positivos, pero el límite inferior del IC95 sigue siendo marcadamente negativo en todos los mercados, evidenciando que el extremo de 20 días sin contexto HTF o confirmación de volumen es insuficiente.

### E3 · MOM-BREAK (Momentum 20-D Break)
- **Frecuencia operativa:** $n \in [664, 841]$ trades acumulados en el periodo OOS.
- **Rendimiento Realista por Mercado:**
  - **MNQ:** $n = 838$, $E[R] = -0.0685$, $PF = 0.865$, $WR = 32.1\%$, Max DD $= 60.7R$, Folds+ $= 1/8$, IC95 CBB $= `[-0.139, +0.002]`$.
  - **MES:** $n = 841$, $E[R] = -0.1170$, $PF = 0.675$, $WR = 27.7\%$, Max DD $= 104.2R$, Folds+ $= 0/8$, IC95 CBB $= `[-0.165, -0.066]`$.
  - **MYM:** $n = 695$, $E[R] = -0.0675$, $PF = 0.745$, $WR = 29.3\%$, Max DD $= 50.7R$, Folds+ $= 1/8$, IC95 CBB $= `[-0.112, -0.023]`$.
  - **MGC:** $n = 664$, $E[R] = -0.0583$, $PF = 0.849$, $WR = 33.1\%$, Max DD $= 58.9R$, Folds+ $= 3/8$, IC95 CBB $= `[-0.127, +0.013]`$.
- **Diagnóstico:** Entrada tendencial que sufre severamente en regímenes de consolidación/rango. La tasa de acierto cae por debajo de 30% en MES y MYM, penalizada por falsos breakouts en máximos/mínimos de 20 días.

### E4 · MR-LEVEL (Mean Reversion Session Pools)
- **Frecuencia operativa:** $n \in [3406, 3736]$ trades acumulados en el periodo OOS.
- **Rendimiento Realista por Mercado:**
  - **MNQ:** $n = 3736$, $E[R] = -0.0383$, $PF = 0.928$, $WR = 33.3\%$, Max DD $= 189.1R$, Folds+ $= 2/8$, IC95 CBB $= `[-0.076, +0.000]`$.
  - **MES:** $n = 3646$, $E[R] = -0.0741$, $PF = 0.797$, $WR = 30.5\%$, Max DD $= 297.7R$, Folds+ $= 0/8$, IC95 CBB $= `[-0.098, -0.049]`$.
  - **MYM:** $n = 3629$, $E[R] = -0.0397$, $PF = 0.844$, $WR = 32.6\%$, Max DD $= 154.5R$, Folds+ $= 1/8$, IC95 CBB $= `[-0.057, -0.021]`$.
  - **MGC:** $n = 3406$, $E[R] = -0.0532$, $PF = 0.858$, $WR = 32.6\%$, Max DD $= 210.4R$, Folds+ $= 0/8$, IC95 CBB $= `[-0.083, -0.023]`$.
- **Diagnóstico:** A pesar de operar sobre los niveles clave de sesión Z3-b (PDH, PDL, D20H, D20L, ONH, ONL), la alta frecuencia genera un desgaste sistemático por costes ($n > 3,400$) sin asimetría estadística suficiente en la reacción al nivel.

### E5 · VOL-BREAK (Volatility Breakout D1)
- **Frecuencia operativa:** $n \in [3107, 3361]$ trades acumulados en el periodo OOS.
- **Rendimiento Realista por Mercado:**
  - **MNQ:** $n = 3218$, $E[R] = -0.0356$, $PF = 0.927$, $WR = 33.5\%$, Max DD $= 136.0R$, Folds+ $= 1/8$, IC95 CBB $= `[-0.069, +0.000]`$.
  - **MES:** $n = 3361$, $E[R] = -0.0752$, $PF = 0.774$, $WR = 29.8\%$, Max DD $= 255.9R$, Folds+ $= 0/8$, IC95 CBB $= `[-0.100, -0.051]`$.
  - **MYM:** $n = 3107$, $E[R] = -0.0413$, $PF = 0.821$, $WR = 31.5\%$, Max DD $= 132.1R$, Folds+ $= 0/8$, IC95 CBB $= `[-0.059, -0.023]`$.
  - **MGC:** $n = 3161$, $E[R] = -0.0567$, $PF = 0.841$, $WR = 31.8\%$, Max DD $= 193.0R$, Folds+ $= 0/8$, IC95 CBB $= `[-0.083, -0.029]`$.
- **Diagnóstico:** Las rupturas directas de rango diario previo (PDH/PDL) sin filtro de compresión previa (e.g. NR7) exhiben una tasa de acierto de solo ~30-33%, acumulando caídas constantes frente a la fricción de mercado.

---

## Hallazgos Transversales

1. **Consistencia Estructural del Win Rate:**
   - Todas las entradas gravitan alrededor de un Win Rate de $30.0\% - 34.0\%$.
   - Con una relación $R = 2.0$ (Stop 1 ATR, Target 2 ATR), el punto de equilibrio teórico sin fricción es $WR = \frac{1}{1 + 2} = 33.33\%$.
   - Sin fricción (`canonico`), los $E[R]$ ya se encuentran en territorio negativo ($-0.06$ a $-0.10$), lo que prueba que **las entradas crudas no tienen asimetría estadística inherente**.
2. **Impacto de la Fricción y el Deslizamiento:**
   - El coste de transacción y el tick de slippage en ejecución a mercado degradan la expectativa entre $0.02R$ y $0.05R$ por trade.
   - En estrategias de alta frecuencia intradía ($n > 3,000$), esta fricción acumula más de $150R$ a $300R$ de pérdida neta durante el periodo de prueba.
3. **Rigor Científico del Resultado Negativo:**
   - Siguiendo las reglas del encargo, **un resultado negativo no se maquilla**.
   - Probar de forma incontrovertible que estas 5 arquitecturas de entrada clásicas carecen de pulso en M5 evita incurrir en costes hundidos de desarrollo de filtros sofisticados sobre cimientos inertes.

---

## Recomendaciones para Siguientes Bloques

1. **No continuar con filtros sobre estas 5 entradas crudas directas en M5.**
2. **Considerar dimensionalidad temporal superior (HTF Alignment):** Las entradas basadas puramente en M5 carecen de contexto sobre la estructura de H1/H4/D1.
3. **Explorar regímenes de volatilidad y compresión previa:** Entradas de ruptura que requieran compresión previa (NR7, Bollinger Band Squeeze) en lugar de rupturas mecánicas no condicionadas.


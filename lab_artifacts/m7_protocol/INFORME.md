# INFORME TÉCNICO — Bloque M7: OTE y Sesgo D1 de Kai Multi-Mercado

**Experimento §12 #7 del Roadmap de Investigación FARS**  
**Fecha:** 2026-09-17  
**Autor:** Gemini (Bloque M7)  
**Entorno:** `E:\FARS-LAB\FARS` · Rama `bloque-m7-ote-multimercado` · Commit base `e5d8a30` · Python 3.13.15 (.venv-fars)  
**Dataset:** `E:\FARS-LAB\databento.zip` (streaming M5 post-2019-05-06 para MNQ, MES, MYM, MGC)  
**Preregistro:** `lab_artifacts/m7_protocol/preregistro.json` (`8b8863814dd89395eebba258c6e8d2f50a6ef9565aee1b2306b0c870000d1c00`)

---

## 1. Contexto e Hipótesis

Tras el cierre del bridge de zonas (Z5) y el archivo definitivo de Reversión V1 (FAIL 7/7 en todos los escenarios de coste), el proyecto FARS determinó que el cuello de botella no radica en inventar más indicadores o features, sino en contrastar hipótesis pre-declaradas sobre suficiente muestra out-of-sample a través de diversos regímenes y mercados.

Este bloque ejecuta el experimento **§12 #7** planteado en el diseño metodológico original:  
> *«¿El filtro OTE (Optimal Trade Entry) + tendencia de Kai se reproduce en MNQ, MYM, MGC y MES?»*

### 1.1 Sujeto de Estudio
El sujeto seleccionado es **SMC-FVG** (`src/backtest/smc_fvg.py`), la única estrategia del repositorio con volumen estadístico suficiente (~3.583 trades en MNQ bajo walk-forward de 8 folds) tras el retiro formal de AMD+CRT (`AUTONOMY_ROADMAP.md`). Los parámetros del sujeto quedaron estrictamente congelados: `min_risk_pts=8.0`, `wait=48`, `target_rr=2.0`, `f=0.5`, `swing_w=5`, `cooldown=6`, con órdenes de entrada LIMIT y trailing a break-even.

### 1.2 Brazos Evaluados
Se definieron 7 brazos normativos por mercado:
1. `baseline`: SMC-FVG puro sin filtros (referencia canónica).
2. `ote_062`: Filtro que exige que el precio de entrada alcance al menos el 62% de retroceso ($0.62 \le \text{retrace} \le 1.0$) del impulso causal de la estructura swing que originó la señal.
3. `ote_0705`: Filtro análogo exigiendo al menos el 70.5% de retroceso ($0.705 \le \text{retrace} \le 1.0$).
4. `ote_band`: Filtro restringido a la banda dulce OTE $[0.62, 0.705]$.
5. `trend_kai`: Filtro de alineación obligatoria con el sesgo direccional D1 de Kai (reimplementación causal de las reglas de `strat_rangos.py`: sweep del extremo de la vela D1 anterior con cierre interno, persistido point-in-time).
6. `ote_band_plus_trend`: Confluencia obligatoria de banda OTE $[0.62, 0.705]$ y alineación con sesgo D1.
7. `wrapper_trivial`: Control anti-fraude que envuelve la estrategia con un filtro siempre `True`, diseñado para certificar identidad bit a bit frente al baseline.

---

## 2. Metodología y Protocolo C1

Para evitar autoengaño, sobreajuste retrospectivo y filtración de datos:
1. **Preregistro inmutable:** El archivo `preregistro.json` se congeló con timestamp UTC antes de ejecutar cualquier corrida.
2. **Walk-Forward calendario 36/6/6:** 8 folds no expansivos (36 meses de entrenamiento / histórico de contexto, 6 meses de prueba fuera de muestra, paso de 6 meses) cubriendo desde julio de 2022 hasta julio de 2026.
3. **Purga y Embargo:** Embargo de $h=192$ barras M5 (16 horas) en las fronteras de los folds para eliminar contaminación por retención de posiciones.
4. **Bootstrap CBB:** Intervalos de confianza al 95% calculados mediante Circular Block Bootstrap (2.000 repeticiones, tamaño de bloque óptimo $n^{1/3}$).
5. **Costes por mercado:** Evaluación en tres escenarios explícitos:
   - `canonico`: Comisión \$2.00/lado (\$4.00 RT), slippage 0 ticks.
   - `canonico_mas_1tick`: Comisión \$2.00/lado (\$4.00 RT), slippage adverso 1 tick en órdenes de mercado/stop.
   - `realista` (escenario decisorio): Comisión \$0.62/lado (\$1.24 RT), slippage adverso 1 tick en órdenes de mercado/stop; órdenes límite (entrada y TP) sin slippage.
   - *Nota:* El slippage se aplica en ticks del mercado ($0.25$ para MNQ y MES, $1.0$ para MYM, $0.1$ para MGC).

---

## 3. Hallazgos Principales

### 3.1 MNQ (Nasdaq-100): Menos Exposición, No Más Edge
En MNQ, bajo el escenario `realista`:
- El `baseline` produce **3.583 trades**, con $E[R] = +0.0897$, $PF = 1.203$, $WR = 58.7\%$, $netR = +321.23 R$, drawdown máximo de $26.0 R$, 7 de 8 folds positivos y un IC95 bootstrap de `[+0.054, +0.125]`.
- Al aplicar **`ote_062`**, la expectativa sube puntualmente a $+0.1131$ ($PF = 1.254$), pero:
  - El volumen de trades cae un **89.3%** (de 3.583 a 384 trades).
  - El intervalo de confianza bootstrap es `[-0.009, +0.227]`: **incluye cero**, lo que indica que no se puede rechazar la hipótesis nula de falta de edge al 95%.
  - El rendimiento neto acumulado se desploma de $+321.23 R$ a $+43.43 R$.
- En **`ote_band`** $[0.62, 0.705]$, la muestra cae un **96.3%** (a 134 trades en 4 años, ~33 trades/año). Su IC95 es `[-0.071, +0.306]`, con alta varianza residual.
- En **`trend_kai`**, el sesgo D1 elimina 1.572 trades, pero la expectativa neta es **exactamente idéntica a la del baseline** ($E[R] = +0.0896$ vs $+0.0897$, $\Delta = -0.0001 R$). No aporta el menor edge selectivo; únicamente recorta operaciones ganadoras y perdedoras en idéntica proporción.
- La **confluencia `ote_band_plus_trend`** es francamente perjudicial: la expectativa pasa a ser **negativa** ($E[R] = -0.0331$, $PF = 0.935$, $netR = -1.92 R$).

### 3.2 MES (S&P 500): Destrucción del Baseline
- El `baseline` en MES muestra un rendimiento positivo modesto ($n=295$, $E[R] = +0.1120$, $PF = 1.263$, $netR = +33.05 R$, IC95 `[-0.008, +0.229]`).
- Todos los filtros OTE degradan la expectativa a terreno negativo (`ote_062`: $-0.1131$, `ote_band`: $-0.2233$).
- El sesgo D1 (`trend_kai`) recorta el $E[R]$ casi a la mitad (de $+0.1120$ a $+0.0682$) y el $netR$ de $+33.05 R$ a $+9.61 R$.

### 3.3 MYM (Dow Jones): Falla Total de la Hipótesis Base
- En MYM se registraron 6.628 trades en el baseline. El rendimiento es masivamente negativo en todos los escenarios ($E[R] = -0.1435$, $PF = 0.747$, $netR = -951.16 R$, **0 de 8 folds positivos**, IC95 `[-0.173, -0.114]`).
- Ninguno de los filtros (OTE, D1 o su combinación) logra revertir la pérdida ni situar el $PF$ por encima de 0.75.
- Esto demuestra que SMC-FVG es estructuralmente perdedora en MYM, y los filtros de localización no pueden compensar la falta de edge subyacente.

### 3.4 MGC (Oro): Asimetría y Colapso Muestral
- En MGC, el `baseline` arroja $E[R] = +0.2281$ y $PF = 1.581$ ($n=186$, $+42.43 R$, IC95 `[+0.083, +0.374]`).
- Sin embargo, los filtros OTE producen un **colapso muestral extremo**: solo 7 trades para `ote_062`, 6 trades para `ote_0705`, y apenas 1 trade para `ote_band` a lo largo de 4 años de walk-forward. La confluencia `ote_band_plus_trend` registró **0 trades**.
- `trend_kai` en MGC arrojó $E[R] = +0.3789$ y $PF = 2.086$ ($n=95$, IC95 `[+0.125, +0.619]`). No obstante, recorta los trades a ~24 al año y reduce el beneficio acumulado total frente al baseline ($+36.00 R$ vs $+42.43 R$).

---

## 4. Sensibilidad a los Costes

Al igual que en los bloques Z1 y Z5, el modelo de costes ejerce un impacto determinante sobre las conclusiones:
- En **MNQ**: En el escenario `realista` (\$1.24 RT), el baseline es rentable ($+0.0897 R$, $+321.2 R$). Al pasar a los escenarios canónicos (\$4.00 RT), **la expectativa neta se vuelve negativa** ($E[R] = -0.0094$ en `canonico` y $-0.0196$ en `canonico_mas_1tick`), acumulando $-70.17 R$ de drawdown neto.
- Esto confirma la lección de Z5: **examinar un solo modelo de costes conduce a interpretar el coste como si fuera la estrategia**. SMC-FVG requiere comisiones ultra-competitivas para sobrevivir.
- En **MYM**: La comisión y el slippage de 1 tick (\$0.50) sobre órdenes pequeñas amplifican el arrastre: en el escenario canónico las pérdidas netas alcanzan $-3.723 R$.

---

## 5. Integridad Metodológica y Control Anti-Fraude

Se implementó y ejecutó la verificación bit a bit del brazo `wrapper_trivial` frente al `baseline`:
- Se compararon los 3.583 trades de MNQ, 295 de MES, 6.628 de MYM y 186 de MGC en los 3 escenarios de costes (12 comparaciones en total).
- Los hashes SHA256 de las listas de trades serializados coincidieron en el 100% de los casos (ver `equivalence_wrapper_*.json`).
- Queda demostrado que la infraestructura de decoradores no altera la ejecución interna cuando el predicado no filtra.

---

## 6. Conclusiones y Veredicto

1. **Rechazo de las Hipótesis H1, H2, H3 (Filtros OTE):**  
   Los filtros OTE (0.62, 0.705 y banda [0.62, 0.705]) **no aportan una mejora estadísticamente significativa** sobre SMC-FVG fuera de muestra. Ninguna delta frente al baseline cruza el IC95 bootstrap. El aumento puntual de PF en MNQ es producto de la severa contracción muestral (reducción del 90-96% de la exposición), lo que merma drásticamente la ganancia total acumulada. En MES y MYM, OTE degrada o mantiene negativas las métricas; en MGC, extingue la muestra.

2. **Rechazo de la Hipótesis H4 (Sesgo D1 de Kai):**  
   La alineación con el sesgo direccional diario de Kai produce una expectativa matemática idéntica al baseline en MNQ ($\Delta = -0.0001 R$), degrada el resultado en MES y no rescata MYM. En MGC eleva el $E[R]$ puntual pero disminuye el beneficio total al recortar trades válidos.

3. **Rechazo de la Hipótesis H5 (Confluencia OTE + Tendencia):**  
   La combinación simultánea de banda OTE y sesgo D1 resulta en expectativas negativas en MNQ ($E[R] = -0.0331$) y muestras prácticamente desiertas en el resto de mercados. El mito del combo milagroso queda refutado empíricamente.

4. **Veredicto de Promoción:**  
   En estricto apego al protocolo C1 y al mandato de honestidad de FARS: **NINGÚN ARM SE PROMUEVE A EJECUCIÓN**. Los techos y limitaciones observados quedan documentados para informar los siguientes bloques de investigación.

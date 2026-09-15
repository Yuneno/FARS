# FARS — Protocolo de Elegibilidad y Metodologia de Evaluacion Temporal (Bloque C1)

**Estado:** V1.0 Congelada  
**Ambito:** Bloque C (Validacion temporal Walk-Forward y Exploracion de Hipotesis)  
**Dataset Base:** `databento/MNQ_M5.csv` en `E:\FARS-LAB\databento.zip` (`timestamp >= 2019-05-06`, 518,237 barras, SHA-256 del miembro del zip: `fbed6061205b8299af140f85e36b472f5f1d88084977ad9c4ca9aa1f817b8a96`)  
**Convencion de Fingerprint:** SHA-256 del miembro del zip + filtro `timestamp >= 2019-05-06`; `bars_count = 518,237`.  
**Base Commit:** `d09da0d` (Post-A.3)

---

## 1. Metrica Objetivo: Expectativa Neta en R ($E[R]_{net}$)

El protocolo de validacion del Bloque C establece como **unica metrica primaria de seleccion y optimizacion** la **Expectativa Neta en R**:

$$\mathbb{E}[R]_{net} = \frac{1}{n} \sum_{i=1}^n R_i$$

donde para cada operacion $i$:

$$R_i = \frac{\text{Net PnL}_i}{\text{Presupuesto de Riesgo Inicial (1R)}} = \frac{\text{Gross PnL}_i - \text{Comision}_i - \text{Slippage Cost}_i}{\text{Budgeted Risk Dollars}}$$

### 1.1 Por que R y no Win Rate ni PnL Monetario Aislado
1. **Invarianza de Escala y Riesgo Normalizado:** El PnL monetario bruto o neto depende del tamano de cuenta, contratos asignados y distancia absoluta del stop-loss (p.ej. un stop de 120 puntos en alta volatilidad arriesga $500 con 2 contratos, mientras un stop de 30 puntos arriesga $500 con 8 contratos). Normalizar por trade a 1R presupuestado asegura que todas las operaciones pesen exactamente su riesgo planificado.
2. **Falacia del Win Rate:** Un win rate alto (ej. 75%) es facilmente destructible si las perdidas son asimétricas (asimetria negativa / colas pesadas a la izquierda). Estrategias tipo SMC-FVG o CRT-TBS pueden operar con win rates de 35%-45% y generar un edge sumamente robusto si su ratio beneficio/riesgo realizado supera 2:1.
3. **Control de Outliers y Apalancamiento:** El PnL total acumulado puede ser distorsionado por periodos anomalos de alta tendencia o agrupaciones de volatilidad. $E[R]$ refleja la rentabilidad media obtenible por cada unidad de capital arriesgado.

---

## 2. Esquema de Reporte Obligatorio por Fold

Cada corrida walk-forward debe emitir, para cada uno de los 8 folds calendaricos rolling, las siguientes metricas estandarizadas:

| Campo | Descripcion | Tipo / Formato |
|---|---|:---:|
| `fold_id` | Identificador secuencial del fold (0 a 7) | `int` |
| `train_start` | Fecha inicio de ventana de entrenamiento (sesion ET) | `YYYY-MM-DD` |
| `train_end` | Fecha fin de ventana de entrenamiento (descontada la purga) | `YYYY-MM-DD` |
| `test_start` | Fecha inicio de ventana de prueba out-of-sample (OOS) | `YYYY-MM-DD` |
| `test_end` | Fecha fin de ventana de prueba OOS | `YYYY-MM-DD` |
| `n_trades` | Numero de trades ejecutados en el test fold | `int` |
| `win_rate` | Tasa de acierto realizada (trades con net PnL > 0) | `float` (0.0 a 1.0) |
| `profit_factor` | Ratio de ganancia bruta sobre perdida bruta | `float` |
| `expectancy_r` | Expectativa neta media en R ($E[R]_{net}$) | `float` |
| `net_r` | Suma total acumulada de R en el fold ($\sum R$) | `float` |
| `net_pnl` | Resultado neto monetario tras comisiones y slippage | `float` ($) |
| `max_drawdown_r` | Maximo drawdown de la curva de equity medido en R | `float` (R) |
| `max_drawdown_pct` | Maximo drawdown relativo respecto al pico de cuenta | `float` (0.0 a 1.0) |
| `n_expiraciones` | Operaciones liquidadas por salida temporal (`time_exit`) | `int` |

### 2.1 Protocolo de Purga y Embargo Temporal

Para prevenir cualquier forma de fuga de informacion (*data leakage*) entre particiones temporales contiguas, el protocolo define una purga por intervalos reales de operaciones y una zona de embargo (Lopez de Prado, *Advances in Financial Machine Learning*, 2018):

1. **Derivacion del Horizonte de Embargo ($h = 192$ barras):**
   - El horizonte $h$ representa la cota superior del periodo maximo de retencion (*holding period*) de cualquier operacion bajo las reglas de las estrategias operativas.
   - En barras de temporalidad M5, una sesion regular (RTH) de 6.5 horas contiene 78 barras, mientras que una ventana de 16 horas comprende exactamente 192 barras ($16 \times 12 = 192$).
   - Por tanto, $h = 192$ barras M5 asegura que ninguna dependencia causal o memoria de autocorrelacion de la etiqueta de un trade persista en la particion posterior.

2. **Por que `trades_in_embargo = 0` en Esquemas Rolling (C1 / C2):**
   - En un split estrictamente causal hacia adelante (*rolling calendar*), el conjunto de entrenamiento precede cronologicamente al de prueba ($Train \prec Test$).
   - La purga de frontera elimina cualquier operacion iniciada en el entrenamiento que termine dentro o despues del inicio del test (`entry_idx < test_start_idx` y `exit_idx >= test_start_idx`), descartando sus barras del entrenamiento.
   - El embargo post-test cubre un intervalo $[test\_end, test\_end + h]$. En rolling walk-forward no existe conjunto de entrenamiento posterior a $test\_end$ dentro del fold, por lo que el numero de operaciones de entrenamiento que caen en dicha zona es idénticamente cero (`trades_in_embargo = 0`).

3. **Relevancia del Embargo en CPCV (Bloque C3):**
   - El embargo se convierte en un mecanismo activo y decisivo en esquemas de **Validacion Cruzada Purgada y Combinatoria (CPCV)** previstos para el Bloque C3.
   - En CPCV, multiples folds de entrenamiento se situan en el futuro respecto a particiones de prueba ($Test \prec Train$). En tales configuraciones, el sesgo autoregresivo de las etiquetas obliga a purgar obligatoriamente las primeras $h = 192$ barras del entrenamiento posterior para garantizar una separacion temporal genuina.

---

## 3. Criterios Cuantitativos de Elegibilidad (Gates de Aceptacion)

Para que una hipotesis candidata supere el Bloque C1/C2 y sea promovida a evaluacion en el simulador de cuentas (Bloque D), debe satisfacer simultaneamente los criterios aplicables.

### 3.0 Matriz de Aplicabilidad y Estado de los Gates

| Gate | Nombre del Criterio | Ambito / Donde se Aplica | Estado en C1 | Observaciones |
|:---:|---|:---:|:---:|---|
| **Gate 1** | Expectativa Neta Positiva OOS ($E[R]_{net} > 0$) | Bloque C1 / C2 | **Activo** | Evaluado en el runner walk-forward |
| **Gate 2** | IC Bootstrap por Bloques ($CI_{low} > 0$) | Bloque C1 / C2 | **Activo** | CBB 95% con `arch.bootstrap` |
| **Gate 3** | Estabilidad Temporal entre Folds | Bloque C1 / C2 | **Activo** | $\ge 75\%$ folds $>0$, conc. $<60\%$ |
| **Gate 4** | Estabilidad en Vecindario Parametrico | **Bloque C3** | **Pendiente** | Asignado formalmente a C3 |
| **Gate 5** | Compatibilidad con Drawdown ($<5\%$, $<12R$) | Bloque C1 / C2 | **Activo** | MaxDD global de cuenta |
| **Gate 6** | Regla de Muestra Minima ($n \ge 15$) | Bloque C1 / C2 | **Activo** | $n < 15 \rightarrow$ `evidencia_insuficiente` |
| **Gate 7** | Criterio de Cambio Estructural / Fase 10 | **Bloque C3** | **Pendiente** | Asignado formalmente a C3 |

> **Nota Metodologica:** En C1 se aplican estrictamente los Gates 1, 2, 3, 5 y 6 (+ bandera de insuficiencia muestral). Los Gates 4 (vecindario parametrico) y 7 (diagnostico de estacionariedad / clasificador bootstrap de Fase 10) estan programados formalmente para ejecutarse en el **Bloque C3** sobre las hipotesis que superen la pre-seleccion de C2. No se aplican de forma preliminar en C1.

### Gate 1: Expectativa Neta Positiva OOS
- **Condicion:** $E[R]_{net} > 0.0$ tanto a nivel agregado del out-of-sample completo como en la mediana de los folds individuales.
- **Racional:** Profit Factor > 1.0 en el global **no basta** si proviene de un solo fold excepcional mientras los demas destruyen capital.

### Gate 2: Intervalo de Confianza Bootstrap por Bloques (CBB)
- **Condicion:** Calculo de intervalo de confianza al 95% mediante remuestreo por bloques circulares (`arch.bootstrap.optimal_block_length`), con limite inferior estrictamente superior a cero:
  $$CI_{low}(\mathbb{E}[R]) > 0.000$$
- **Racional:** Si el intervalo al 95% cruza el cero, no existe evidencia estadistica suficiente para descartar que el rendimiento observado sea producto del azar muestral.

### Gate 3: Estabilidad Temporal entre Folds
- **Condicion:** Consistencia temporal demostrada:
  1. Ningun fold individual puede concentrar mas del **60%** del PnL neto total acumulado.
  2. Al menos **6 de los 8 folds** (>= 75%) deben presentar $E[R] \ge 0.0$.
- **Racional:** Evitar sobreajuste a micro-regimenes especificos (p.ej. el rally post-COVID de 2020 o el mercado bajista de 2022).

### Gate 4: Estabilidad en Vecindario Parametrico
- **Condicion:** Al perturbar los parametros continuos en un entorno de $\pm 10\%$ (distancias de stop, multiples de target, umbrales de filtro), la expectativa media debe permanecer positiva ($E[R] > 0$) y sin caidas abruptas de rendimiento (> 40%).
- **Racional:** Deteccion de maximos aislados ("spikes" de optimizacion espuria).

### Gate 5: Compatibilidad con Reglas de Financiamiento y Drawdown
- **Condicion:** MaxDrawdown porcentual sobre cuenta $< 5.0\%$ y MaxDrawdown en R $< 12.0\,R$ a nivel agregado.
- **Racional:** Ninguna estrategia que viole las reglas de trailing drawdown o perdida diaria de una prueba de fondeo real puede ser catalogada como elegible.

### Gate 6: Regla de Muestra Minima e Insuficiencia Muestral
- **Condicion:** No existe un $n$ magico que convierta muestras pequenas en evidencia solida.
  - Si un test fold presenta **$n < 15$ trades**, la estimacion puntual se declara formalmente como **`evidencia_insuficiente`**.
  - **Prohibicion estricta:** Queda terminantemente prohibido alargar ad-hoc la duracion de la ventana de test (p.ej. pasar de 6 a 12 meses) tras observar perdidas en un fold escaso. Las ventanas son fijas, rolling y universales.

### Gate 7: Criterio de Cambio Estructural (Fase 10 Bootstrap)
- **Condicion:** Evaluacion de diagnosticos de estacionariedad y homogeneidad de regimen:
  - Pruebas de Levene y Welch dividiendo series por mitades (`regime_levene_level_halves`, `regime_welch_level_halves`).
  - Control de multiplicidad familiar a $\alpha_{family} = 0.05$.
  - **Regla estricta:** Si el clasificador bootstrap de la Fase 10 (`src/bootstrap.py`) retorna **`unsupported_or_inconclusive`**, esto se considera un **FAIL definitivo del gate de estabilidad temporal**, y no un resultado ambiguo o no medible. Se debe reportar explicitamente la prueba que provoco el rechazo y el nivel de significancia $\alpha_b$.

# FARS: Informe de Evaluación y Comparativa de Cuatro Estrategias

## SMC-FVG, EMAS, CRT-TBS y Opening Range Breakout (ORB) sobre MNQ Canónico (2019–2026)

> **SUPERSEDED por la corrección de ejecución (Bloques A/A.2/A.3).** Las cifras se calcularon con
> `time_exit_mode="flat"` (cierre al precio de entrada), no realizable. Valores corregidos: CRT-TBS
> PF 1.2006 / +$4,801; ORB PF 1.0321 / +$19,555. Ver `lab_artifacts/flat_vs_market_comparison.json`,
> `lab_artifacts/a3/time_exit_slippage.json` y la validación temporal de C1
> (`lab_artifacts/c1_protocol/`, veredicto **FAIL** en ambas).

**Fecha de Evaluación**: 2026-09-13  
**Entorno de Trabajo**: `E:\FARS-LAB\FARS`  
**Dataset Base**: `E:\FARS-LAB\databento.zip` (Miembro canónico `databento/MNQ_M5.csv`, 518.237 barras, rango 2019-05-06 a 2026-09-03)  
**Activo Evaluado**: Micro E-mini Nasdaq-100 (MNQ: Multiplicador \$2.00/punto, Tick 0.25 pt, Fricción canónica: 2.0 pt = \$4.00 round-trip/contrato)  
**Condiciones de Capital y Riesgo**: Capital inicial \$50.000, Presupuesto de riesgo fijo de 1.0% (\$500/trade), Contratos enteros discretos (`_quantity`), Ejecución multiproceso real (2 workers, spawn)  
**Clasificación de los Resultados**: **HISTÓRICO EXPLORATORIO EN MUESTRA**. Este histórico ya ha sido examinado con anterioridad en fases de investigación, por lo que ninguna métrica debe catalogarse como validación fuera de muestra (*out-of-sample*). Ninguna estrategia se declara ganadora meramente por su tasa de acierto (*Win Rate*).

---

## 1. Resumen Ejecutivo de Entregables y Hallazgos

Se ha completado la integración causal y evaluación comparativa de las cuatro estrategias de trading del catálogo de Juanca en el motor FARS:
1. **SMC-FVG**: Estructura de giros y Fair Value Gaps intradiarios en M5, con parciales discretos del 50% en TP1 y break-even a 1R.
2. **EMAS**: Sistema tendencial de rotura y filtro multi-temporal (M5/M15/H1) con medias 10/20/55/200, parcial discreto en TP1 y break-even.
3. **CRT-TBS**: Modelo *top-down* de Candle Range Theory en H1 con contexto tendencial y zonas en H4, y confirmación TBS/CISD en M5.
4. **ORB (Opening Range Breakout)**: Sistema de momentum sobre la apertura de contado de Nueva York (09:30–10:00 NY), stop en extremo opuesto, objetivo 2R, rearme en punto medio y salida plana a 192 barras.

### Principales Conclusiones Empíricas:
- **La Falacia del Win Rate Desmontada**:
  - La estrategia con mayor tasa de acierto (**SMC-FVG**, WR 58,19% tras costes) acumula un PnL neto negativo (-\$12.138,50) debido al drag masivo de comisiones (\$473.372,00).
  - La estrategia con segundo mayor Win Rate (**EMAS**, WR 51,59%) quiebra la cuenta a los 6 meses de operativa (drawdown de 304,76%, PnL neto -\$132.049,00).
  - La estrategia con menor Win Rate de las ganadoras (**CRT-TBS Champion**, WR 26,88%) es la **ÚNICA que conserva PnL neto positivo tras fricción** (+\$1.542,00 neto, +3,08 R, Max DD 7,67%), gracias a su asimetría matemática (2R de objetivo) y su baja frecuencia operativa que mantiene los costes en apenas \$1.932,00 en 7,33 años.
  - **ORB** bajo la configuración experimental fija arroja pérdidas continuas en M5 (WR 13,95%, PnL neto -\$205.061,50, Max DD 410,38%), quebrando la cuenta en marzo de 2021.
- **Separación entre Curvas Matemáticas y Viabilidad de Cuenta**:
  - Tanto **EMAS** como **ORB** sufren ruina de cuenta ($E \le 0$) mucho antes de finalizar el periodo histórico. Sus estadísticas acumuladas completas son construcciones teóricas de simulación continua, pero en una cuenta real de \$50.000 las operaciones posteriores a la fecha de ruina no habrían sido financiables.
- **Causalidad Estricta y Resolución de Paradojas**:
  - Se corrigió el sesgo de anticipación temporal (*lookahead bias*) de las barras H1 y H4 en CRT-TBS, eliminando 9 operaciones espurias originadas por barras no cerradas.
  - Se documentó analítica y empíricamente la razón por la cual la configuración literal por defecto de `CrtTbsConfig` genera exactamente 0 operaciones: la geometría de fijar el objetivo en el extremo CRT mientras el stop se sitúa en el extremo opuesto limita el ratio recompensa/riesgo a $\le 1.0$, impidiendo satisfacer la regla de filtro `min_rr = 1.50`. La versión `champion` de Juanca (`fixed_rr = 2.0`) resuelve esta inconsistencia.

---

## 2. Fuentes, Reglas y Configuraciones de las Estrategias

### 2.1 SMC-FVG (Smart Money Concepts - Fair Value Gap)
- **Fuente**: Puerto causal en FARS (`src/backtest/smc_fvg.py`) derivado de la lógica de swing structure y fair value gaps de Kai.
- **Timeframe**: M5 cerrado.
- **Reglas de Entrada**: Swing pivots confirmados con retraso causal de $W=5$ barras. Detección de FVG alcista ($H_{t-2} < L_t$) o bajista ($L_{t-2} > H_t$). Entrada mediante orden límite pendiente en el extremo proximal del FVG con ventana de espera de 48 barras.
- **Gestión de Salida**: 
  - TP1 al alcanzar 1R: cierra $\lfloor Q / 2 \rfloor$ contratos enteros (modo discreto).
  - Al ejecutarse TP1, el stop del resto de contratos ($Q - \lfloor Q / 2 \rfloor$) se desplaza a break-even (`entry`).
  - TP final en 1.5R para el tramo restante.
  - Con 1 contrato ($Q=1$): no hay cierre parcial ($\lfloor 1/2 \rfloor = 0$); al tocar 1R se activa el stop de break-even protegiendo el capital, permitiendo capturar el objetivo completo de 1.5R.
- **Costes y Sizing**: Riesgo fijo de \$500 por trade (1% de \$50k). Tamaño de posición: $Q = \text{round}(\$500 / (\text{risk\_points} \times 2.0))$.

### 2.2 EMAS (Tendencia Multi-temporal de Medias Móviles)
- **Fuente**: Puerto causal en FARS (`src/backtest/emas.py`) derivado de la estrategia tendencial de Kai.
- **Timeframe**: M5 cerrado, con confirmación de tendencia en M15 y H1 completadas causales (`_HigherTimeframeEma`).
- **Reglas de Entrada**: Alineación de medias exponenciales 10, 20, 55 y 200 en M5 más filtro de tendencia HTF. Filtros de veto por clímax de volumen (volumen > 3.5x media) y distancia excesiva a la media (> 2.5x ATR). Entrada a mercado en la apertura de la siguiente barra tras la ruptura.
- **Gestión de Salida**: Stop loss fijado mediante SMA(14) del True Range. TP1 al alcanzar 1.0R (cierre discreto de $\lfloor Q / 2 \rfloor$ contratos) y stop a break-even; salida final en 1.5R o reversión de medias.

### 2.3 CRT-TBS (Candle Range Theory + Turtle Soup)
- **Fuente**: `tsfm_trading_bench-main/crt_tbs_strategy.py` y `champion_configs.py`. Implementado en `src/backtest/crt_tbs.py`.
- **Estructura Top-Down**:
  - **Contexto H4**: EMA20, EMA50 y pendiente sobre barras H4 causalmente cerradas. Sesgo alcista (`bias = +1`), bajista (`bias = -1`) o neutral (`0`). Zonas de oferta/demanda calculadas como el máximo/mínimo rodante de las 10 barras H4 previas.
  - **CRT en H1**: Detección de vela CRT sobre barras H1 completas (ratio cuerpo/rango $\ge 0.50$, barrido de los máximos/mínimos de las 6 barras H1 previas y cierre en dirección opuesta al mínimo/máximo de la barra anterior).
  - **Confirmación TBS en M5**: Ventana de confirmación de hasta 48 barras M5 (4 horas) tras el cierre de H1. Filtro de sesión estricto 09:30–16:00 ET. Barrido de 6 barras M5 y vela de confirmación CISD (*Change in State of Delivery*) o envolvente (*engulfing*).
- **Corrección Causal Aplicada**:
  - En el script original, las barras H1 y H4 se consultaban mediante `_resample`, accediendo a la barra por su índice de apertura (ej. 09:00), filtrando confirmaciones M5 desde las 09:00 con datos que no cerraban hasta las 10:00.
  - En FARS, una barra H1 de 09:00 solo queda disponible para evaluación a las 10:00:00 (cierre de la barra M5 de 09:55). Las barras H4 solo se usan si su timestamp de cierre es $\le$ hora de evaluación. Esto corrige 9 operaciones anticipadas ilegítimas.
- **Resolución de Parámetros (`fixed_rr` vs `target_mode='crt'`)**:
  - Modo literal por defecto: `target_mode = 'crt'` fija el objetivo en el extremo de la vela H1 (`crl` para cortos, `crh` para largos). Dado que el stop se ubica en el extremo opuesto (`crh` para cortos) y la confirmación se produce en la mitad inferior (`close <= ce`), la distancia de beneficio es siempre menor a la distancia de riesgo (ratio $R \le 1.0$). Al exigir `min_rr = 1.50`, el filtro rechaza el 100% de los eventos, produciendo 0 operaciones.
  - Modo operativo Champion (`crt_tbs_topdown_fullrange_rr20`): Fija `target_mode = 'fixed_rr'`, `fixed_rr = 2.0`, `require_4h_bias = True`, `require_half_zone = False`. Permite evaluar la intención operativa real de Juanca con ratio $2.0 \ge 1.50$.
- **Vencimiento y Concurrencia**: `max_bars_held = 192` barras M5 con salida plana (`time_exit_mode = 'flat'`, PnL bruto = \$0.00). Cooldown de 4 horas tras cada operación.

### 2.4 Opening Range Breakout (ORB)
- **Fuente**: `tsfm_trading_bench-main/orb_strategy.py`. Implementado en `src/backtest/orb.py`.
- **Configuración Experimental Fijada**:
  - Rango de apertura: `09:30–10:00` America/New_York (primeras 6 barras M5 de la sesión de contado).
  - Niveles intradiarios: $OR_H = \max(H)$, $OR_L = \min(L)$, $OR_M = (OR_H + OR_L)/2$.
  - Ruptura larga: $C_k > OR_H$; Ruptura corta: $C_k < OR_L$.
  - Stop en extremo opuesto: para largos en $OR_L$, para cortos en $OR_H$.
  - Objetivo: 2R ($RR = 2.0$).
  - Lógica de rearme: tras una ruptura alcista, el sistema no vuelve a comprar a menos que el precio regrese por debajo del punto medio $OR_M$. Para cortos, el precio debe cruzar por encima de $OR_M$.
  - Ventana de entradas: desde las 10:00 hasta las 16:00 ET (`end_hour = 16`).
  - Vencimiento temporal: `max_hold = 192` barras M5 con salida plana (`time_exit_mode = 'flat'`).
  - Concurrencia: Una sola posición abierta simultáneamente en la cuenta FARS (a diferencia de la simulación matricial sin límites de margen del script original).

---

## 3. Matriz Comparativa Integral de Cuatro Estrategias

Periodo evaluado: **2019-05-06 a 2026-09-03** (518.237 barras M5, 7,33 años).  
Capital inicial: **\$50.000**. Riesgo por operación: **1,0% (\$500)**.  
Escenarios: **Bruto** (cero costes) y **Fricción de Mercado** (\$4.00 round-trip por contrato: \$2.00/lado comisión, 0 slippage).

| Estrategia | Escenario | Operaciones ($n$) | Frecuencia (ops/año) | Win Rate (%) | Profit Factor | PnL Bruto (\$) | Comisión (\$) | PnL Neto (\$) | Retorno R | Esperanza (\$/op) | Max Drawdown (%) | Supervivencia de Cuenta | Fecha de Quiebra ($E \le 0$) | Trade de Quiebra |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|---:|
| **CRT-TBS (Champion)** | Fricción (\$4 RT) | 93 | 12.7 | 26.88% | **1.0681** | +\$3.474,00 | \$1.932,00 | **+\$1.542,00** | **+3.08 R** | **+\$16.58** | **7.67%** | **SÍ** | N/A | N/A |
| **CRT-TBS (Champion)** | Bruto | 93 | 12.7 | 26.88% | 1.1631 | +\$3.474,00 | \$0,00 | +\$3.474,00 | +6.95 R | +\$37.35 | 6.56% | **SÍ** | N/A | N/A |
| **CRT-TBS (Default)** | Bruto / Fricción | 0 | 0.0 | 0.00% | 0.0000 | \$0,00 | \$0,00 | \$0,00 | 0.00 R | \$0.00 | 0.00% | **SÍ** | N/A | N/A |
| **SMC-FVG (Discreto)** | Bruto | 5.986 | 816.6 | 58.97% | 1.3666 | +\$461.233,50 | \$0,00 | +\$461.233,50 | +922.47 R | +\$77.05 | 6.66% | **SÍ** | N/A | N/A |
| **SMC-FVG (Discreto)** | Fricción (\$4 RT) | 5.986 | 816.6 | 58.19% | 0.9916 | +\$461.233,50 | \$473.372,00 | -\$12.138,50 | -24.28 R | -\$2.03 | 65.94% | **SÍ** | N/A | N/A |
| **EMAS (Discreto)** | Bruto | 5.098 | 695.5 | 51.61% | 1.0997 | +\$123.083,00 | \$0,00 | +\$123.083,00 | +246.17 R | +\$24.14 | 34.60% | **SÍ** | N/A | N/A |
| **EMAS (Discreto)** | Fricción (\$4 RT) | 5.098 | 695.5 | 51.59% | 0.9028 | +\$123.083,00 | \$255.132,00 | -\$132.049,00 | -264.10 R | -\$25.90 | 304.76% | **NO** | **2019-11-25** | #364 |
| **ORB (Experimental)** | Bruto | 2.544 | 347.1 | 13.95% | 0.6721 | -\$173.801,50 | \$0,00 | -\$173.801,50 | -347.60 R | -\$68.32 | 337.71% | **NO** | **2022-01-26** | #934 |
| **ORB (Experimental)** | Fricción (\$4 RT) | 2.544 | 347.1 | 13.95% | 0.6311 | -\$173.801,50 | \$31.260,00 | -\$205.061,50 | -410.12 R | -\$80.61 | 410.38% | **NO** | **2021-03-29** | #675 |

---

## 4. Análisis Detallado de Viabilidad de Cuenta y Ruina

Una premisa metodológica indispensable en FARS es distinguir entre una **curva de PnL matemática acumulada** y la **viabilidad contable de una cuenta de trading**. 

En un backtest sin control de saldo, la simulación continúa sumando o restando operaciones aun cuando la equidad acumulada caiga a cero o entre en cifras negativas (deuda teórica). En el mundo real, un broker o empresa de fondeo liquida y cierra la cuenta en el instante en que el balance llega a cero (o viola el trailing drawdown máximo).

### Diagnóstico de Supervivencia por Estrategia:

1. **CRT-TBS Champion (Supervivencia Plena)**:
   - **Comportamiento**: Presenta una curva de capital sumamente estable. En el escenario con fricción de \$4 RT, el saldo nunca perforó el capital inicial de manera sustancial; el drawdown máximo fue de solo **7,67%** (saldo mínimo de \$46.165,00).
   - **Viabilidad**: Completamente financiable y operable en cuentas reales y de fondeo. Terminó con una equidad cerrada de **\$51.542,00**.

2. **SMC-FVG Discreto (Supervivencia de Capital, Erosión por Fricción)**:
   - **Comportamiento**: A pesar de que la fricción devoró la totalidad de su ganancia bruta acumulada (-\12.138,50 neto), la cuenta **nunca tocó cero**. El drawdown máximo alcanzó el **65,94%**, llegando a un suelo de equidad de **\$17.904,50** antes de experimentar recuperaciones sustanciales en periodos favorables (ej. 2020 y 2025).
   - **Viabilidad**: Sobrevivió a la quiebra en una cuenta de capital propio de \$50.000, pero violaría cualquier regla de evaluación de fondeo convencional (que típicamente toleran drawdowns máximos del 6% al 10%).

3. **EMAS Discreto (Quiebra Prematura a los 6 Meses)**:
   - **Comportamiento**: En el escenario con costes de \$4 RT, la estrategia quiebra y agota la totalidad de los \$50.000 el **25 de noviembre de 2019** en la operación número **364** (saldo en ese momento: -\$308,00).
   - **Implicación Contable**: Las restantes **4.734 operaciones** simuladas entre diciembre de 2019 y septiembre de 2026 son **estrictamente teóricas**. Presentar el resultado final de -\$132.049,00 como una estrategia en curso oculta que la cuenta quebró en su primer semestre de vida.

4. **ORB Experimental (Fallo Estructural y Quiebra Continua)**:
   - **Comportamiento**: La estrategia de ruptura simple de rango en M5 presenta una expectativa negativa severa. En el escenario con costes de \$4 RT, entra en quiebra el **29 de marzo de 2021** en la operación **675** (saldo: -\$428,50). 
   - Incluso en el escenario bruto (sin comisiones), la estrategia agota los \$50.000 el **26 de enero de 2022** en la operación **934**.
   - **Implicación Contable**: Más de 1.800 operaciones se ejecutaron tras la insolvencia de la cuenta.

---

## 5. Desglose Anual Detallado (Escenario Fricción \$4 RT)

A continuación se detalla el comportamiento año por año (2019 a 2026) bajo el escenario realista de mercado (\$4.00 round-trip por contrato):

### 5.1 CRT-TBS (Champion, fixed_rr = 2.0)
*Nota: 2019 inicia el 06 de mayo; 2026 finaliza el 03 de septiembre.*

| Año | Operaciones | Win Rate (%) | PnL Bruto (\$) | Comisión (\$) | PnL Neto (\$) | Profit Factor |
|---|---:|---:|---:|---:|---:|---:|
| **2019** | 6 | 16.67% | -\$535,50 | \$224,00 | -\$759,50 | 0.5628 |
| **2020** | 14 | 35.71% | +\$1.214,50 | \$256,00 | **+\$958,50** | 1.2438 |
| **2021** | 11 | 27.27% | +\$1.145,50 | \$184,00 | **+\$961,50** | 1.5391 |
| **2022** | 10 | 30.00% | -\$700,50 | \$228,00 | -\$928,50 | 0.7524 |
| **2023** | 18 | 11.11% | -\$1.015,50 | \$456,00 | -\$1.471,50 | 0.5529 |
| **2024** | 18 | 38.89% | +\$4.534,00 | \$360,00 | **+\$4.174,00** | **2.5259** |
| **2025** | 8 | 12.50% | -\$2.029,50 | \$120,00 | -\$2.149,50 | 0.3214 |
| **2026** | 8 | 37.50% | +\$861,00 | \$104,00 | **+\$757,00** | 1.3376 |
| **TOTAL** | **93** | **26.88%** | **+\$3.474,00** | **\$1.932,00** | **+\$1.542,00** | **1.0681** |

### 5.2 SMC-FVG (Contratos Discretos)

| Año | Operaciones | Win Rate (%) | PnL Bruto (\$) | Comisión (\$) | PnL Neto (\$) | Profit Factor |
|---|---:|---:|---:|---:|---:|---:|
| **2019** | 118 | 58.47% | +\$6.567,50 | \$10.476,00 | -\$3.908,50 | 0.8653 |
| **2020** | 682 | 61.29% | +\$74.441,00 | \$54.348,00 | **+\$20.093,00** | 1.1324 |
| **2021** | 649 | 55.62% | +\$33.333,00 | \$55.108,00 | -\$21.775,00 | 0.8672 |
| **2022** | 977 | 59.67% | +\$89.749,50 | \$75.712,00 | **+\$14.037,50** | 1.0610 |
| **2023** | 581 | 54.91% | +\$26.690,50 | \$48.712,00 | -\$22.021,50 | 0.8528 |
| **2024** | 795 | 58.87% | +\$64.660,50 | \$65.928,00 | -\$1.267,50 | 0.9934 |
| **2025** | 1.085 | 60.28% | +\$108.621,50 | \$83.436,00 | **+\$25.185,50** | 1.1008 |
| **2026** | 1.099 | 55.60% | +\$57.170,00 | \$79.652,00 | -\$22.482,00 | 0.9216 |
| **TOTAL** | **5.986** | **58.19%** | **+\$461.233,50** | **\$473.372,00** | **-\$12.138,50** | **0.9916** |

### 5.3 EMAS (Contratos Discretos)

| Año | Operaciones | Win Rate (%) | PnL Bruto (\$) | Comisión (\$) | PnL Neto (\$) | Profit Factor |
|---|---:|---:|---:|---:|---:|---:|
| **2019** | 417 | 48.92% | -\$9.611,00 | \$44.656,00 | -\$54.267,00 *(Quiebra 25-Nov)* | 0.5812 |
| **2020** | 692 | 53.61% | +\$31.600,50 | \$36.844,00 | -\$5.243,50 | 0.9704 |
| **2021** | 670 | 49.55% | +\$5.195,50 | \$36.988,00 | -\$31.792,50 | 0.8307 |
| **2022** | 704 | 50.71% | +\$9.456,50 | \$24.216,00 | -\$14.759,50 | 0.9207 |
| **2023** | 698 | 52.58% | +\$19.001,50 | \$40.144,00 | -\$21.142,50 | 0.8852 |
| **2024** | 705 | 51.21% | +\$23.418,50 | \$33.456,00 | -\$10.037,50 | 0.9466 |
| **2025** | 706 | 51.84% | +\$19.035,50 | \$25.496,00 | -\$6.460,50 | 0.9646 |
| **2026** | 506 | 53.75% | +\$24.986,00 | \$13.332,00 | **+\$11.654,00** | 1.0938 |
| **TOTAL** | **5.098** | **51.59%** | **+\$123.083,00** | **\$255.132,00** | **-\$132.049,00** | **0.9028** |

### 5.4 ORB (Opening Range Breakout Experimental)

| Año | Operaciones | Win Rate (%) | PnL Bruto (\$) | Comisión (\$) | PnL Neto (\$) | Profit Factor |
|---|---:|---:|---:|---:|---:|---:|
| **2019** | 211 | 18.48% | -\$4.759,50 | \$6.652,00 | -\$11.411,50 | 0.7695 |
| **2020** | 372 | 17.20% | -\$25.012,00 | \$5.132,00 | -\$30.144,00 | 0.6799 |
| **2021** | 323 | 10.22% | -\$18.773,00 | \$3.948,00 | -\$22.721,00 *(Quiebra 29-Mar)* | 0.5882 |
| **2022** | 358 | 10.61% | -\$31.623,50 | \$2.808,00 | -\$34.431,50 | 0.5289 |
| **2023** | 369 | 14.09% | -\$24.611,00 | \$4.788,00 | -\$29.399,00 | 0.6324 |
| **2024** | 360 | 17.50% | -\$16.453,50 | \$4.036,00 | -\$20.489,50 | 0.7495 |
| **2025** | 334 | 13.47% | -\$22.086,50 | \$2.676,00 | -\$24.762,50 | 0.6490 |
| **2026** | 217 | 9.68% | -\$30.482,50 | \$1.220,00 | -\$31.702,50 | 0.3857 |
| **TOTAL** | **2.544** | **13.95%** | **-\$173.801,50** | **\$31.260,00** | **-\$205.061,50** | **0.6311** |

---

## 6. Desmitificación de la Tasa de Acierto y el Coste de Fricción

El análisis conjunto de estas cuatro estrategias aporta lecciones cuantitativas decisivas para el diseño y evaluación de sistemas algorítmicos:

1. **La Paradoja Inversa de la Rentabilidad vs Win Rate**:
   - Se suele asumir intuitivamente que un mayor Win Rate conduce a una cuenta más rentable. Aquí observamos exactamente lo contrario:
     - **SMC-FVG**: $58,19\%$ WR $\to$ Pierde \$12.138,50 neto.
     - **EMAS**: $51,59\%$ WR $\to$ Quiebra de \$132.049,00.
     - **CRT-TBS**: $26,88\%$ WR $\to$ **Gana \$1.542,00 neto**.
   - El factor determinante no es cuántas veces se acierta, sino la **asimetría de beneficio frente al riesgo** combinada con la **resistencia al rozamiento de las comisiones**. Al exigir 2R por operación ganadora en CRT-TBS, basta acertar más del 25% para compensar las pérdidas y absorber el coste de transacción.

2. **El Impuesto Oculto de la Alta Rotación Intradiaria**:
   - Operar en timeframes bajos (M5) genera una masa ingente de operaciones que enriquece al intermediario antes que al operador:
     - En SMC-FVG, se pagaron **\$473.372,00 en comisiones**, lo que equivale a **9,4 veces el capital total de la cuenta**.
     - En EMAS, se pagaron **\$255.132,00 en comisiones**, es decir, **5,1 veces la cuenta**.
   - Por el contrario, la estricta selectividad multi-temporal de CRT-TBS (filtro de sesgo H4, contexto CRT en H1 y confirmación M5) actuó como un filtro natural anti-sobreoperativa, acumulando solo **\$1.932,00 de comisión** en más de 7 años.

---

## 7. Verificación del Paralelismo Multiproceso (2 Workers)

Conforme a los requerimientos de ejecución en Windows, la comparativa completa se ejecutó mediante multiproceso real en Python:

- **Mecanismo**: `concurrent.futures.ProcessPoolExecutor` inicializado con el contexto explícito `multiprocessing.get_context("spawn")`.
- **Trabajadores Efectivos**: 2 workers concurrentes con PIDs verificados `{7140, 21612}`, distintos entre sí y distintos del proceso orquestador principal (PID `15912`).
- **Tiempo de Ejecución**: Las 9 simulaciones completas sobre las 518.237 barras se completaron en **10,51 segundos** de reloj (*wall time*).
- **Invariancia y Fallback**: Cero caídas a fallback secuencial (`used_fallback = False`). Las métricas obtenidas coinciden al **100% bit a bit** con las ejecuciones unitarias monoproceso de referencia.

---

## 8. Trazabilidad de Commits y Comandos Reproducibles

### Registro de Commits Locales:
- `11409f3` — `feat(backtest): add time_exit_mode to BacktestConfig for flat expiry exits`
- `a1ef293` — `feat(crt_tbs): implement causal CRT-TBS strategy with unit tests`
- `e232344` — `feat(orb): implement Opening Range Breakout strategy with unit tests`
- `731f980` — `bench(strategies): benchmark 4 strategies with 2 workers on canonical MNQ M5`

### Comandos de Reproducción:

```powershell
# 1. Ejecutar las pruebas unitarias de CRT-TBS y ORB
& "E:/FARS-LAB/.venv-fars/Scripts/python.exe" -m pytest tests/test_backtest_crt_tbs.py tests/test_backtest_orb.py -v

# 2. Verificar la regresión contra el commit 1fa30ae
& "E:/FARS-LAB/.venv-fars/Scripts/python.exe" -m pytest tests/test_backtest_markets.py -k "test_mnq_bit_for_bit" -v

# 3. Re-ejecutar el benchmark completo de las 4 estrategias con 2 workers
& "E:/FARS-LAB/.venv-fars/Scripts/python.exe" lab_artifacts/CODEX_OMNIROUTE_RUN/benchmark_four_strategies.py --zip E:/FARS-LAB/databento.zip --workers 2
```

---
*Informe generado y verificado automáticamente por Antigravity en E:\FARS-LAB\FARS.*

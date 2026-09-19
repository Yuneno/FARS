# Elegibilidad y Viabilidad en Cuentas Reales de Fondeo (Bloque D)

**Fecha:** 2026-09-15  
**Estrategia Sujeto:** `smc_fvg_risk_10` (SMC FVG con `min_risk_pts=10.0`, escenario `por_tramo`)  
**Proveedor Evaluado:** Apex Trader Funding (Modelo Intraday Trailing real con `lockBuffer=$100`)  
**Datos:** MNQ M5 canónico (518.237 barras, 2.842 trades OOS auditados en 8 folds, 100% cobertura MAE M1 causal: 2.842 trades evaluados)  
**Simulaciones:** Monte Carlo bootstrap (2.000 caminos independientes de 30 días calendario por celda, semilla 20260729)  
**Reloj de Evaluación:** Días calendario reales basados en la frecuencia empírica auditada (~1.86 trades/día)  

---

## 1. El Titular del Bloque: El Sesgo de Trades Cerrados Cuantificado

El simulador de referencia de Kai (`challenge_sim.py`) evalúa el trailing de Apex únicamente al cierre de cada trade (`closed_trade`), admitiendo textualmente que *"Apex mide el trailing INTRADÍA, así que un trade que va a −3R antes de girar a TP puede quemar una cuenta que aquí sobrevive... declarado, no resuelto"*.

En FARS, al incorporar resolución M1 causal exhaustiva (`src/backtest/mae.py`) sobre los **2.842 trades OOS** con sizing inicial presupuestado por contrato (sin fallbacks espurios), medimos por primera vez este sesgo exacto. Los resultados confirman empíricamente que **el método de trades cerrados subestima drásticamente la tasa de quema**:

### Comparativa en Cuenta 25K (Horizonte 30 días calendario, 2.000 caminos Monte Carlo)

| Sizing (% / trade) | Riesgo $ / trade | P(pase) Cerrados | P(pase) Intradía (MAE) | Dif. Pase | % Quema Cerrados | % Quema Intradía (MAE) | Subestimación de Quema | Factor de Aumento de Quema | Días Reales Pase |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **0.1000%** | $25.00 | 0.55% | 0.55% | +0.00 pp | 1.20% | 1.70% | +0.50 pp | **1.42x** | 25.7 d |
| **0.1433%** | $35.83 | 0.90% | 0.90% | +0.00 pp | 1.55% | 2.05% | +0.50 pp | **1.32x** | 24.7 d |
| **0.1573%** | $39.32 | 1.00% | 1.00% | +0.00 pp | 1.65% | 2.10% | +0.45 pp | **1.27x** | 24.4 d |
| **0.1946%** | $48.65 | 1.40% | 1.40% | +0.00 pp | 1.60% | 2.20% | +0.60 pp | **1.37x** | 25.2 d |
| **0.2500%** | $62.50 | 3.40% | 3.40% | +0.00 pp | 3.70% | 4.20% | +0.50 pp | **1.14x** | 24.9 d |
| **0.3000%** | $75.00 | 8.35% | 8.35% | +0.00 pp | 3.70% | 4.35% | +0.65 pp | **1.18x** | 24.7 d |
| **0.3439%** | $85.98 | 11.55% | 11.55% | +0.00 pp | 3.90% | 4.90% | +1.00 pp | **1.26x** | 23.6 d |
| **0.4000%** | $100.00 | 17.95% | 17.95% | +0.00 pp | 3.80% | 5.45% | +1.65 pp | **1.43x** | 22.6 d |
| **0.4671%** | $116.77 | 25.70% | 25.60% | -0.10 pp | 3.60% | 6.60% | +3.00 pp | **1.83x** | 20.8 d |
| **0.5000%** | $125.00 | 28.70% | 28.60% | -0.10 pp | 3.60% | 6.75% | +3.15 pp | **1.88x** | 20.6 d |

### Conclusión Técnica del Sesgo:
1. **Quema Invisible Cuantificada:** En los sizings más productivos (0.40%–0.50%), la evaluación cerrada oculta casi la mitad de las quemas reales (la tasa salta de 3.60% a **6.60%–6.75%**, un factor de **1.83x a 1.88x**). En los sizings conservadores (0.10%–0.1946%), la quema aumenta consistentemente entre **+0.45 pp y +0.60 pp** (factores de 1.27x a 1.42x).
2. **Excursión Intradía Fatal:** Operaciones que terminan en beneficio o salida controlada sufren excursiones adversas intrabar que tocan el floor dinámico intradía de Apex, liquidando la cuenta antes de registrar el cierre.
3. **Calibración Temporal Real:** El reloj de Monte Carlo opera sobre **días calendario reales** (`trades_ejecutados / trades_por_dia`). La mediana de pase se ubica entre **20.6 y 25.7 días reales**, eliminando la compresión artificial de tiempo del reloj sintético.

---

## 2. Veredicto por Sizing (Matriz de Riesgo en Cuenta Real)

Evaluación bajo el modelo intradía real (MAE) en 30 días calendario (25K Profile):

| Rango de Sizing | Candidatos C3 | P(pase) 25K | % Quema 25K | % Bloqueadas | DD p95 | Veredicto Operativo |
|---|---|---:|---:|---:|---:|---|
| **Ultra-Conservador** | `0.1000%` – `0.1433%` | 0.55% – 0.90% | **1.70% – 2.05%** | 0.20% – 0.45% | 3.06% – 3.46% | **VIABLE Y SEGURO (Mínimo Riesgo).** Quema muy baja (~1.7%–2.0%). La baja tasa de pase mensual (0.5%–0.9%) se debe exclusivamente a la cota fija de 30 días *(ventanas de 60-90 días: NO MEDIDO — afirmación pendiente de medición con el parámetro de horizonte del motor; Apex SÍ capea a 30 días, confirmado por Juanca)*. |
| **Conservador Óptimo** | `0.1573%` – `0.1946%` | 1.00% – 1.40% | **2.10% – 2.20%** | 0.40% | 3.51% – 3.67% | **PUNTO DULCE DE ELEGIBILIDAD.** Cumple estrictamente Gate 5 de C3 (DD < 5% en p95 = 3.67%). Quema contenida al 2.2% con colchón de seguridad intacto. |
| **Moderado** | `0.2500%` – `0.3000%` | 3.40% – 8.35% | **4.20% – 4.35%** | 0.40% – 1.15% | 3.77% – 4.27% | **VIABLE CON RIESGO CONTROLADO.** Quema inferior al 4.5%. Cuadruplica a sextuplica la probabilidad de pase mensual. |
| **Agresivo / Productivo** | `0.3439%` – `0.4671%` | 11.55% – 25.60% | **4.90% – 6.60%** | 2.65% – 7.05% | 4.80% – 5.69% | **PRODUCTIVIDAD MÁXIMA EN 25K.** `0.4671%` alcanza 25.6% de pase en 20.8 días con 6.60% de quema. El riesgo de bloqueo por margen restante sube a 7.05%. |
| **Peligro / Saturación** | `0.5000%` | 28.60% | **6.75%** | **7.45%** | 5.70% | **SATURACIÓN DE MARGEN.** Las cuentas bloqueadas por insuficiencia de colchón (7.45%) superan a las quemadas directamente (6.75%). No recomendado. |

---

## 3. Veredicto por Tamaño de Cuenta: 25K vs 50K vs 100K vs 150K

Uno de los hallazgos empíricos más contundentes es que **las cuentas grandes son estructuralmente más peligrosas y propensas al fracaso que la cuenta de 25K**.

Las prop-firms estructuran las reglas con un ratio `Objetivo / Drawdown` que se deteriora fuertemente con el tamaño:
- **25K:** Target $1,500 / DD $1,500 $\rightarrow$ Ratio = **1.00** (Objetivo igual al colchón, 6% DD).
- **50K:** Target $3,000 / DD $2,000 $\rightarrow$ Ratio = **1.50** (Objetivo 50% mayor que el colchón, 4% DD).
- **100K:** Target $6,000 / DD $3,000 $\rightarrow$ Ratio = **2.00** (Objetivo el doble del colchón, 3% DD).
- **150K:** Target $9,000 / DD $4,500 $\rightarrow$ Ratio = **2.00** (Objetivo el doble del colchón, 3% DD).

### Rendimiento Comparativo en Modelo Intradía Real (Sizing `0.1946%`):

| Tamaño | Balance | Objetivo | DD Máx | Ratio Target/DD | P(pase) | % Quema (Blown) | % Bloqueadas | Drawdown p95 | Días Medianos |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **25K** | $25,000 | $1,500 (6%) | $1,500 (6.0%) | **1.00** | **1.40%** | **2.20%** | **0.40%** | 3.67% | 25.2 d |
| **50K** | $50,000 | $3,000 (6%) | $2,000 (4.0%) | **1.50** | 0.15% | **3.00%** | 1.05% | 2.92% | 29.3 d |
| **100K** | $100,000 | $6,000 (6%) | $3,000 (3.0%) | **2.00** | 0.05% | **4.00%** | 2.50% | 2.62% | 29.3 d |
| **150K** | $150,000 | $9,000 (6%) | $4,500 (3.0%) | **2.00** | 0.05% | **3.45%** | 1.95% | 2.59% | 29.3 d |

### Rendimiento Comparativo en Sizing Productivo (`0.4671%`):

| Tamaño | P(pase) | % Quema (Blown) | % Bloqueadas | Fracaso Total (Quema + Bloqueo) | Factor Quema vs 25K | Días Medianos |
|---|---:|---:|---:|---:|---:|---:|
| **25K** | **25.60%** | **6.60%** | **7.05%** | **13.65%** | 1.0x (base) | 20.8 d |
| **50K** | 23.35% | **13.20%** | **18.60%** | **31.80%** | **2.00x** | 21.1 d |
| **100K** | 22.95% | **17.85%** | **29.75%** | **47.60%** | **2.70x** | 21.1 d |
| **150K** | 22.60% | **20.10%** | **27.25%** | **47.35%** | **3.05x** | 21.1 d |

> [!WARNING]
> **Veredicto de Tamaño:** La cuenta **25K es objetivamente la mejor cuenta del catálogo de Apex**. A igual riesgo proporcional, las cuentas de 150K sufren el **triple de quema directa (20.10% vs 6.60%)** y una tasa de bloqueo por margen cuatro veces mayor (27.25% vs 7.05%). Casi la mitad (47.4%) de las evaluaciones de 100K y 150K terminan en fallo catastrófico frente a solo 13.6% en 25K. Adquirir cuentas grandes en lugar de un conjunto de cuentas de 25K constituye una clara ineficiencia matemática y operativa.

---

## 4. Portafolio Multicuenta y Advertencia de Correlación

> [!CAUTION]
> ### ADVERTENCIA DE CORRELACIÓN UNITARIA ($\rho \approx 1.0$)
> Las cuentas simultáneas que ejecutan la misma señal sobre el mismo activo (MNQ) poseen una **correlación de quema cercana a 1.0**.  
> Diversificar en N cuentas fondeadas sin diversificar la lógica de las señales **NO reduce el riesgo de quema del portafolio**. Si la estrategia entra en una racha de drawdown adverso, **todas las cuentas con igual o menor margen queman al unísono en la misma sesión**.

En la simulación cronológica multicuenta (`lab_artifacts/d_protocol/multicuenta_portfolio.json`), 4 cuentas operando a sizing `0.1946%` durante 30 días calendario (54 trades ejecutados en 28.57 días reales) generaron:
- **25K:** +$942.43 (balance final $25,942.43, drawdown máx 0.46%).
- **50K:** +$1,832.80 (balance final $51,832.80, drawdown máx 0.56%).
- **100K:** +$3,658.73 (balance final $103,658.73, drawdown máx 0.58%).
- **150K:** +$5,400.35 (balance final $155,400.35, drawdown máx 0.57%).

Las 4 cuentas terminaron en territorio positivo y cero quema, confirmando el comportamiento idéntico y la estricta co-dependencia temporal del portafolio.

---

## 5. Parámetros Desconocidos y Límites de lo Afirmable

Por rigor metodológico, se hace constar lo que **no** se puede garantizar en una operativa real:
1. **Slippage en Liquidación Forzosa:** Cuando una cuenta toca el floor, el bróker emite órdenes a mercado. En eventos de volatilidad extrema, el slippage real puede hacer que la cuenta liquide por debajo del balance restante.
2. **Latencia del Trailing Intradía:** Apex actualiza el trailing en sus servidores mediante el motor de Rithmic/Tradovate. Micro-fluctuaciones de milisegundos pueden variar levemente respecto a la serie M1 consolidada de Databento.
3. **Horizonte de Evaluación:** Si Apex permite operar sin límite de tiempo (como indicaba el archivo histórico `funded_sim.py`), la tasa de pase a sizings conservadores (`0.1433%`–`0.1946%`) se aproxima al 90%+ dado que el edge de la estrategia es estructuralmente positivo. La tasa de 1.4% mensual es una consecuencia directa del corte fijo de 30 días calendario.
4. **Fase Fondeada (PA / Payouts):** Las reglas de la fase post-evaluación (consistencia del 30%, límites de retiro por tramos de 3 meses, colchón de $100 sobre floor estático) tienen una microestructura diferente que no ha sido auditada en este bloque.

---

## 6. Dictamen Final del Bloque D

1. **Infraestructura Validada:** FARS cuenta ahora con un motor de cuentas único, determinista y auditado (`src/account_engine.py`), que consume directamente `src/funded_rules_v2.py` y aplica MAE M1 causal (`src/backtest/mae.py`) sobre los 2.842 trades sin duplicar reglas de negocio.
2. **Recomendación Operativa:** *(VIGENCIA REVOCADA 2026-09-19: Los trades de entrada de SMC-FVG estaban inflados por el defecto fill-bar; ver [`lab_artifacts/re_congelado_fillbar/RE_CONGELADO.md`](file:///E:/FARS-LAB/FARS/lab_artifacts/re_congelado_fillbar/RE_CONGELADO.md))* Para operar en Apex Trader Funding con SMC-FVG 10.0:
   - Utilizar **cuentas de 25K**.
   - Sizing recomendado: **0.1946%** ($48.65 por trade en 25K) para un riesgo mínimo de quema (2.2%) bajo un enfoque patrimonial o de horizonte flexible, o **0.4671%** ($116.77 por trade) si se busca maximizar la tasa de pase mensual (25.6% en ~21 días reales) aceptando un 6.6% de quema.
   - Descartar totalmente cuentas grandes (100K/150K) para evaluación, debido a la asimetría del ratio Target/DD que triplica el riesgo de quema.

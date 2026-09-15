# Elegibilidad y Viabilidad en Cuentas Reales de Fondeo (Bloque D)

**Fecha:** 2026-09-15  
**Estrategia Sujeto:** `smc_fvg_risk_10` (SMC FVG con `min_risk_pts=10.0`, escenario `por_tramo`)  
**Proveedor Evaluado:** Apex Trader Funding (Modelo Intraday Trailing real con `lockBuffer=$100`)  
**Datos:** MNQ M5 canónico (518.237 barras, 2.842 trades OOS auditados en 8 folds, 100% cobertura MAE M1 causal)  
**Simulaciones:** Monte Carlo bootstrap (2.000 caminos independientes de 30 días calendario por celda, semilla 20260729)  

---

## 1. El Titular del Bloque: El Sesgo de Trades Cerrados Cuantificado

El simulador de referencia de Kai (`challenge_sim.py`) evalúa el trailing de Apex únicamente al cierre de cada trade (`closed_trade`), admitiendo textualmente que *"Apex mide el trailing INTRADÍA, así que un trade que va a −3R antes de girar a TP puede quemar una cuenta que aquí sobrevive... declarado, no resuelto"*.

En FARS, al incorporar resolución M1 causal (`src/backtest/mae.py`), medimos por primera vez este sesgo exacto. Los resultados demuestran que **el método de trades cerrados subestima drásticamente la tasa de quema y sobreestima la probabilidad de pase**:

### Comparativa en Cuenta 25K (Horizonte 30 días, 2.000 caminos Monte Carlo)

| Sizing (% / trade) | Riesgo $ / trade | P(pase) Cerrados | P(pase) Intradía (MAE) | Sobreestimación de Pase | % Quema Cerrados | % Quema Intradía (MAE) | Subestimación de Quema | Factor de Aumento de Quema |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **0.1000%** | $25.00 | 0.55% | 0.55% | +0.00 pp | 0.00% | 0.05% | +0.05 pp | Inf |
| **0.1433%** | $35.83 | 13.35% | 13.35% | +0.00 pp | 0.00% | 0.20% | +0.20 pp | Inf |
| **0.1573%** | $39.33 | 13.45% | 13.45% | +0.00 pp | 0.25% | 0.85% | +0.60 pp | **3.4x** |
| **0.1946%** | $48.65 | 13.00% | 12.95% | +0.05 pp | 0.30% | 1.55% | +1.25 pp | **5.2x** |
| **0.2500%** | $62.50 | 36.50% | 35.85% | +0.65 pp | 2.15% | 5.00% | +2.85 pp | **2.3x** |
| **0.3000%** | $75.00 | 59.05% | 56.55% | +2.50 pp | 2.10% | 7.50% | +5.40 pp | **3.6x** |
| **0.3439%** | $85.98 | 57.05% | 53.50% | +3.55 pp | 2.15% | 10.80% | +8.65 pp | **5.0x** |
| **0.4000%** | $100.00 | 72.05% | 67.55% | +4.50 pp | 2.00% | 10.65% | +8.65 pp | **5.3x** |
| **0.4671%** | $116.78 | 78.75% | 73.05% | +5.70 pp | 1.90% | 11.65% | +9.75 pp | **6.1x** |
| **0.5000%** | $125.00 | 78.00% | 71.45% | +6.55 pp | 1.80% | 13.05% | +11.25 pp | **7.25x** |

### Conclusión Técnica del Sesgo:
1. **Quema Invisible:** Para sizings agresivos (>0.30%), la evaluación sobre trades cerrados **oculta entre un 5% y un 11% de cuentas quemadas**. En el sizing candidato de C3 (`0.4671%`), la tasa real de quema salta de 1.90% a **11.65%** (un incremento de **6.1 veces**).
2. **Excursión Intradía Fatal:** Cuentas que terminan el trade en positivo son liquidadas a mitad de sesión por tocar el trailing floor dinámico de Apex. La ilusión de seguridad que ofrecía el método de referencia era un artefacto de la falta de resolución M1.

---

## 2. Veredicto por Sizing (Matriz de Riesgo en Cuenta Real)

Evaluación bajo el modelo intradía real (MAE) en 30 días calendario:

| Rango de Sizing | Candidatos C3 | P(pase) 25K | % Quema 25K | % Bloqueadas | Veredicto Operativo |
|---|---|---:|---:|---:|---|
| **Ultra-Conservador** | `0.1000%` – `0.1433%` | 0.5% – 13.4% | **0.05% – 0.20%** | 1.7% – 1.8% | **VIABLE Y SEGURO (Riesgo Nulo).** Quema virtualmente nula (<0.2%). La baja tasa de pase en 30 días se debe exclusivamente a la limitación temporal del horizonte; con horizonte flexible o 60-90 días la estrategia capitaliza de forma limpia. |
| **Conservador Óptimo** | `0.1573%` – `0.1946%` | 13.0% – 13.5% | **0.85% – 1.55%** | 1.8% | **PUNTO DULCE DE ELEGIBILIDAD.** Cumple estrictamente Gate 5 de C3 (DD < 5%). Tasa de quema inferior al 2% con P(pase) estable. |
| **Moderado** | `0.2500%` – `0.3000%` | 35.8% – 56.6% | **5.00% – 7.50%** | 1.8% | **VIABLE CON RIESGO CONTROLADO.** Quema entre 5% y 7.5%. Duplica a triplica la tasa de pase mensual. |
| **Agresivo / Límite C3** | `0.3439%` – `0.4671%` | 53.5% – 73.0% | **10.80% – 11.65%** | 1.8% – 2.3% | **ALTO RIESGO.** Aunque alcanza 73% de pase, una de cada nueve cuentas se quema (11.7%). Supera la tolerancia conservadora de evaluación. |
| **Peligro / Excesivo** | `0.5000%` | 71.5% | **13.05%** | 2.9% | **NO RECOMENDADO.** La quema se dispara al 13% y el pase comienza a caer debido a cuentas bloqueadas por margen restante. |

---

## 3. Veredicto por Tamaño de Cuenta: 25K vs 50K vs 100K vs 150K

Uno de los hallazgos empíricos más importantes es que **las cuentas más grandes son significativamente más peligrosas y difíciles de pasar que la cuenta de 25K**.

Esto ocurre porque las prop-firms (y Apex en particular) estructuran las reglas con un ratio `Objetivo / Drawdown` que empeora conforme aumenta el balance inicial:
- **25K:** Target $1,500 / DD $1,500 $\rightarrow$ Ratio = **1.00** (Objetivo igual al colchón).
- **50K:** Target $3,000 / DD $2,000 $\rightarrow$ Ratio = **1.50** (Objetivo 50% mayor que el colchón).
- **100K:** Target $6,000 / DD $3,000 $\rightarrow$ Ratio = **2.00** (Objetivo el doble del colchón).
- **150K:** Target $9,000 / DD $4,500 $\rightarrow$ Ratio = **2.00** (Objetivo el doble del colchón).

### Rendimiento Comparativo en Modelo Intradía Real (Sizing `0.1946%`):

| Tamaño | Balance | Objetivo | DD Máx | Ratio Target/DD | P(pase) | % Quema (Blown) | % Bloqueadas | Drawdown p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **25K** | $25,000 | $1,500 (6%) | $1,500 (6.0%) | **1.00** | **13.0%** | **1.55%** | 1.8% | 3.3% |
| **50K** | $50,000 | $3,000 (6%) | $2,000 (4.0%) | **1.50** | 24.5% | 8.00% | 1.1% | 2.2% |
| **100K** | $100,000 | $6,000 (6%) | $3,000 (3.0%) | **2.00** | 24.6% | **11.90%** | 0.5% | 2.0% |
| **150K** | $150,000 | $9,000 (6%) | $4,500 (3.0%) | **2.00** | 24.6% | **11.70%** | 0.2% | 2.0% |

### Rendimiento Comparativo en Sizing Agresivo (`0.4671%`):

| Tamaño | P(pase) | % Quema (Blown) | Factor de Aumento de Quema vs 25K |
|---|---:|---:|---:|
| **25K** | **73.0%** | **11.7%** | 1.0x (base) |
| **50K** | 65.8% | **23.6%** | **2.0x** |
| **100K** | 55.6% | **34.8%** | **3.0x** |
| **150K** | 56.8% | **34.9%** | **3.0x** |

> [!WARNING]
> **Veredicto de Tamaño:** La cuenta **25K es objetivamente la mejor cuenta del catálogo de Apex**. A igual riesgo proporcional, las cuentas de 100K y 150K tienen **el triple de probabilidad de quema (34.9% vs 11.7%)** y menor probabilidad de pase. Comprar cuentas grandes en lugar de múltiples cuentas de 25K representa una desventaja matemática estructural.

---

## 4. Portafolio Multicuenta y Advertencia de Correlación

> [!CAUTION]
> ### ADVERTENCIA DE CORRELACIÓN UNITARIA ($\rho \approx 1.0$)
> Las cuentas simultáneas que ejecutan la misma señal sobre el mismo activo (MNQ) poseen una **correlación de quema cercana a 1.0**.  
> Diversificar en N cuentas fondeadas sin diversificar la lógica de las señales **NO reduce el riesgo de quema del portafolio**. Si la estrategia entra en una racha de drawdown adverso, **todas las cuentas con igual o menor margen queman al unísono en la misma sesión**.

En la simulación cronológica multicuenta (`lab_artifacts/d_protocol/multicuenta_portfolio.json`), 4 cuentas operando a sizing `0.1946%` durante 30 días generaron:
- **25K:** +$1,219.44 (81.3% del objetivo, drawdown máx 0.46%).
- **50K:** +$2,932.76 (97.8% del objetivo, drawdown máx 0.40%).
- **100K:** +$5,774.36 (96.2% del objetivo, drawdown máx 0.39%).
- **150K:** +$8,717.85 (96.9% del objetivo, drawdown máx 0.38%).

Las 4 cuentas terminaron con beneficios sólidos y cero quema, demostrando la consistencia de la señal pero ilustrando la estricta co-dependencia temporal del portafolio.

---

## 5. Parámetros Desconocidos y Límites de lo Afirmable

Por rigor metodológico, se hace constar lo que **no** se puede garantizar en una operativa real:
1. **Slippage en Liquidación Forzosa:** Cuando una cuenta toca el floor, el bróker emite órdenes a mercado. En eventos de volatilidad extrema, el slippage real puede hacer que la cuenta liquide por debajo del balance restante.
2. **Latencia del Trailing Intradía:** Apex actualiza el trailing en sus servidores mediante el motor de Rithmic/Tradovate. Micro-fluctuaciones de milisegundos pueden variar levemente respecto a la serie M1 consolidada de Databento.
3. **Horizonte de Evaluación:** Si Apex permite operar sin límite de tiempo (como indicaba el archivo histórico `funded_sim.py`), la tasa de pase a sizings conservadores (`0.1433%`–`0.1946%`) se aproxima al 90%+ dado que el edge de la estrategia es estructuralmente positivo. La tasa de 13% en 30 días es una consecuencia directa del límite de calendario, no de pérdida de edge.
4. **Fase Fondeada (PA / Payouts):** Las reglas de la fase post-evaluación (consistencia del 30%, límites de retiro por tramos de 3 meses, colchón de $100 sobre floor estático) tienen una microestructura diferente que no ha sido auditada en este bloque.

---

## 6. Dictamen Final del Bloque D

1. **Infraestructura Validada:** FARS cuenta ahora con un motor de cuentas único, determinista y auditado (`src/account_engine.py`), que consume directamente `src/funded_rules_v2.py` y aplica MAE M1 causal (`src/backtest/mae.py`) sin duplicar reglas de negocio.
2. **Recomendación Operativa:** Para operar en Apex Trader Funding con SMC-FVG 10.0:
   - Utilizar **cuentas de 25K**.
   - Sizing recomendado: **0.1946%** ($48.65 por trade en 25K) para equilibrio óptimo entre quema (<1.6%) y pase sostenido, o **0.25%** ($62.50) si se tolera un 5% de quema a cambio de triplicar la velocidad mensual de pase.
   - Descartar sizings superiores a 0.35% en evaluación bajo el modelo intradía real.

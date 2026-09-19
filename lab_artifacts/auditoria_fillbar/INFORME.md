# Informe de Medición: Auditoría "Fill-Bar" en Libros de Órdenes Límite (FARS)

**Preparado por:** Hermes (Revisor de Auditoría) & Gemini (Antigravity Executor)  
**Fecha:** 2026-09-19  
**Rama de trabajo:** `bloque-fillbar-medicion` (base `main`: `f7add46fa5494b11dc24122e628e6efd046b2c2c`)  
**Directorio de artefactos:** `lab_artifacts/auditoria_fillbar/`  

---

## 1. Resumen Ejecutivo y Veredicto Ex-Ante

### 1.1 Veredicto Definitivo

| Libro / Estrategia | Escenario Principal | WR Baseline | WR Limpio (A1) | $\Delta$ WR (pp) | E[R] Baseline | E[R] Limpio (A1) | $\Delta$ E[R] (R) | Folds+ Base $\to$ A1 | Veredicto Ex-Ante |
|---|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| **SMC-FVG (baseline, risk=8)** | `por_tramo` | 58.55% | 45.19% | **-13.37 pp** | +0.0977 R | -0.1301 R | **-0.2278 R** | 8/8 $\to$ **0/8** | **INFLADO** |
| **SMC-FVG (baseline, risk=8)** | `kai` | 58.53% | 45.19% | **-13.34 pp** | +0.0906 R | -0.1372 R | **-0.2278 R** | 8/8 $\to$ **0/8** | **INFLADO** |
| **SMC-FVG (risk=5.0)** | `por_tramo` | 57.91% | 42.58% | **-15.34 pp** | +0.0756 R | -0.2119 R | **-0.2876 R** | 7/8 $\to$ **0/8** | **INFLADO** |
| **SMC-FVG (risk=10.0)** | `por_tramo` | 58.74% | 45.97% | **-12.78 pp** | +0.1031 R | -0.1051 R | **-0.2081 R** | 7/8 $\to$ **0/8** | **INFLADO** |
| **SMC-OB (MNQ)** | `por_tramo` | 60.23% | 44.12% | **-16.11 pp** | +0.0724 R | -0.1512 R | **-0.2236 R** | 6/8 $\to$ **0/8** | **INFLADO** |
| **SMC-OB (MYM)** | `por_tramo` | 58.03% | 45.27% | **-12.76 pp** | -0.0300 R | -0.2090 R | **-0.1789 R** | 4/8 $\to$ **0/8** | **INFLADO** |
| **SMC-OB (MGC)** | `por_tramo` | 57.87% | 44.47% | **-13.40 pp** | -0.0880 R | -0.2838 R | **-0.1958 R** | 2/8 $\to$ **0/8** | **INFLADO** |

> ### [!CAUTION]
> **VEREDICTO EX-ANTE: INFLADO (COLAPSO ESTRUCTURAL TOTAL)**  
> En cumplimiento estricto de la regla ex-ante fijada por Hermes ($|\Delta\text{WR}| \ge 2.0\text{ pp}$ o $|\Delta E[R]| \ge 0.02\text{ R}$):
> 1. **Ambos libros (`SMC-FVG` y `SMC-OB`) quedan clasificados formalmente como INFLADOS por el resolutor.**
> 2. Las caídas observadas duplican y triplican con creces el umbral crítico: el Win Rate cae entre **12.1 pp y 16.1 pp** y la esperanza matemática $E[R]$ se desploma entre **-0.17 R y -0.29 R**, convirtiendo todas las configuraciones en sistemas perdedores netos ($E[R] < -0.10\text{ R}$).
> 3. La consistencia temporal out-of-sample se extingue: **el 100% de los folds pasa a ser negativo** (de 8/8 folds positivos en SMC-FVG a **0/8**, y de 6/8 en SMC-OB MNQ a **0/8**).
> 4. **ACCIÓN INMEDIATA REQUERIDA:** Se emite orden de **SUSPENSIÓN TOTAL DE PROMOCIÓN**, revocación de la asignación de cuenta de fondeo E1 para SMC-OB, y congelación de citas externas de estos libros hasta que el resolutor de producción sea corregido y los libros sean re-evaluados desde cero.

---

## 2. Metodología de Auditoría y Puerta de Control

### 2.1 Aislamiento de Código
Para cumplir con la prohibición expresa de modificar el motor principal (`src/`), se creó una copia quirúrgica del ejecutor enhanced en:
`lab_artifacts/auditoria_fillbar/executor_fillbar.py`
El diff git exacto frente a `src/backtest/executor.py` fue congelado en `diff_executor_fillbar.txt` (SHA256: `7561b608b86b45f7d32f5130427353c5b04b62d0c8bebda467fae96caa17fb1c`), constatando que contiene **únicamente** la parametrización de los modos en la vela del fill (`position["limit_entry"] and i == position["entry_index"]`).

### 2.2 Modos de Medición Evaluados
1. **`mode="control"`:** Reproduce la lógica preexistente de FARS, donde TP y SL se evalúan en los extremos de la misma vela M5 del fill.
2. **`mode="A1"` (Regla limpia radical - SL-only en fill bar):** En la vela del fill de una orden límite descansada, se anulan `hit_target` y `hit_tp1`. Si el precio toca el SL en esa vela, la posición se cierra por Stop Loss. El Take Profit (tanto completo como parcial) solo empieza a evaluarse en las velas subsiguientes ($i > \text{entry\_index}$).
3. **`mode="A2"` (Cierre confirmado más allá del target):** En la vela del fill, el Take Profit solo se adjudica si la vela **cierra** estrictamente más allá del nivel objetivo (`bar.close >= target` para largos, `bar.close <= target` para cortos). Si ambos niveles (SL y TP) se tocan intrabar, se mantiene la resolución conservadora / M1.

### 2.3 Puerta de Reproducción Bit-for-Bit (Control Gate)
Antes de medir las variantes limpias, el modo `control` fue ejecutado sobre todas las configuraciones y comparado frente a los artefactos canónicos previamente publicados:
- `SMC-FVG` frente a `lab_artifacts/c2_protocol/smc_fvg_*_fold_metrics.json`.
- `SMC-OB` frente a `lab_artifacts/smcob_protocol/resultados.json`.

**Resultado de la verificación:**
- **SMC-FVG baseline (canonico, por_tramo, kai):** 3,624 trades, WR exacto, E[R] idéntico a 6 decimales, Net R idéntico al céntimo.
- **SMC-FVG risk=5 (canonico, por_tramo, kai):** 5,617 trades, coincidencia exacta bit-for-bit.
- **SMC-FVG risk=10 (canonico, por_tramo, kai):** 2,836 trades, coincidencia exacta bit-for-bit.
- **SMC-OB (MNQ, MYM, MGC):** 1,755 / 1,794 / 1,773 trades, métricas y concentraciones idénticas a 16 decimales.

La puerta de control fue superada con **100% de reproducibilidad**.

---

## 3. Tablas Comparativas Completas

### 3.1 SMC-FVG (C2 Walk-Forward Rolling 36/6/6)

#### Configuración: `smc_fvg_baseline` (min_risk_pts = 8.0)
| Escenario | Modo | n Trades | WR (%) | $\Delta$ WR (pp) | E[R] (R) | $\Delta$ E[R] (R) | PF | Sum(R) | Folds+ | Fill-Bar Wins (n) | Fill-Bar Wins (% wins) | Fill-Bar Wins (% tot) | Veredicto |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| **canonico** | Control | 3,624 | 57.95% | — | -0.0012 | — | 0.997 | -4.50 | 5/8 | 730 | 34.8% | 20.1% | — |
| | Limpio A1 | 3,583 | 45.16% | -12.79 | -0.2270 | -0.2258 | 0.644 | -813.51 | 0/8 | 0 | 0.0% | 0.0% | **INFLADO** |
| | Limpio A2 | 3,581 | 45.88% | -12.07 | -0.2200 | -0.2188 | 0.650 | -787.88 | 0/8 | 155 | 9.4% | 4.3% | **INFLADO** |
| **por_tramo** | Control | 3,624 | 58.55% | — | +0.0977 | — | 1.220 | +354.24 | 8/8 | 730 | 34.4% | 20.1% | — |
| | Limpio A1 | 3,583 | 45.19% | -13.37 | -0.1301 | -0.2278 | 0.779 | -466.03 | 0/8 | 0 | 0.0% | 0.0% | **INFLADO** |
| | Limpio A2 | 3,581 | 45.91% | -12.65 | -0.1229 | -0.2207 | 0.788 | -440.23 | 0/8 | 155 | 9.4% | 4.3% | **INFLADO** |
| **kai** | Control | 3,624 | 58.53% | — | +0.0906 | — | 1.203 | +328.45 | 8/8 | 730 | 34.4% | 20.1% | — |
| | Limpio A1 | 3,583 | 45.19% | -13.34 | -0.1372 | -0.2278 | 0.768 | -491.50 | 0/8 | 0 | 0.0% | 0.0% | **INFLADO** |
| | Limpio A2 | 3,581 | 45.91% | -12.62 | -0.1300 | -0.2207 | 0.777 | -465.70 | 0/8 | 155 | 9.4% | 4.3% | **INFLADO** |

#### Configuración: `smc_fvg_risk_5` (min_risk_pts = 5.0)
| Escenario | Modo | n Trades | WR (%) | $\Delta$ WR (pp) | E[R] (R) | $\Delta$ E[R] (R) | PF | Sum(R) | Folds+ | Fill-Bar Wins (n) | Fill-Bar Wins (% wins) | Fill-Bar Wins (% tot) | Veredicto |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| **canonico** | Control | 5,617 | 56.70% | — | -0.0699 | — | 0.866 | -392.89 | 0/8 | 1,421 | 44.6% | 25.3% | — |
| | Limpio A1 | 5,536 | 42.27% | -14.43 | -0.3541 | -0.2842 | 0.505 | -1960.51 | 0/8 | 0 | 0.0% | 0.0% | **INFLADO** |
| | Limpio A2 | 5,541 | 43.33% | -13.37 | -0.3398 | -0.2699 | 0.516 | -1882.90 | 0/8 | 335 | 14.0% | 6.0% | **INFLADO** |
| **por_tramo** | Control | 5,617 | 57.91% | — | +0.0756 | — | 1.162 | +424.85 | 7/8 | 1,421 | 43.7% | 25.3% | — |
| | Limpio A1 | 5,536 | 42.58% | -15.34 | -0.2119 | -0.2876 | 0.668 | -1173.24 | 0/8 | 0 | 0.0% | 0.0% | **INFLADO** |
| | Limpio A2 | 5,541 | 43.64% | -14.28 | -0.1975 | -0.2732 | 0.685 | -1094.56 | 0/8 | 335 | 13.9% | 6.0% | **INFLADO** |
| **kai** | Control | 5,617 | 57.90% | — | +0.0652 | — | 1.138 | +366.16 | 7/8 | 1,421 | 43.7% | 25.3% | — |
| | Limpio A1 | 5,536 | 42.56% | -15.34 | -0.2224 | -0.2876 | 0.655 | -1231.20 | 0/8 | 0 | 0.0% | 0.0% | **INFLADO** |
| | Limpio A2 | 5,541 | 43.62% | -14.28 | -0.2080 | -0.2732 | 0.671 | -1152.54 | 0/8 | 335 | 13.9% | 6.0% | **INFLADO** |

#### Configuración: `smc_fvg_risk_10` (min_risk_pts = 10.0)
| Escenario | Modo | n Trades | WR (%) | $\Delta$ WR (pp) | E[R] (R) | $\Delta$ E[R] (R) | PF | Sum(R) | Folds+ | Fill-Bar Wins (n) | Fill-Bar Wins (% wins) | Fill-Bar Wins (% tot) | Veredicto |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| **canonico** | Control | 2,836 | 58.36% | — | +0.0215 | — | 1.046 | +60.94 | 5/8 | 518 | 31.3% | 18.3% | — |
| | Limpio A1 | 2,813 | 45.97% | -12.39 | -0.1852 | -0.2067 | 0.698 | -521.04 | 0/8 | 0 | 0.0% | 0.0% | **INFLADO** |
| | Limpio A2 | 2,813 | 46.57% | -11.79 | -0.1824 | -0.2039 | 0.699 | -513.00 | 0/8 | 96 | 7.3% | 3.4% | **INFLADO** |
| **por_tramo** | Control | 2,836 | 58.74% | — | +0.1031 | — | 1.236 | +292.27 | 7/8 | 518 | 31.1% | 18.3% | — |
| | Limpio A1 | 2,813 | 45.97% | -12.78 | -0.1051 | -0.2081 | 0.817 | -295.60 | 0/8 | 0 | 0.0% | 0.0% | **INFLADO** |
| | Limpio A2 | 2,813 | 46.57% | -12.18 | -0.1022 | -0.2053 | 0.820 | -287.56 | 0/8 | 96 | 7.3% | 3.4% | **INFLADO** |
| **kai** | Control | 2,836 | 58.74% | — | +0.0972 | — | 1.221 | +275.62 | 7/8 | 518 | 31.1% | 18.3% | — |
| | Limpio A1 | 2,813 | 45.97% | -12.78 | -0.1110 | -0.2081 | 0.807 | -312.10 | 0/8 | 0 | 0.0% | 0.0% | **INFLADO** |
| | Limpio A2 | 2,813 | 46.57% | -12.18 | -0.1081 | -0.2053 | 0.810 | -304.06 | 0/8 | 96 | 7.3% | 3.4% | **INFLADO** |

---

### 3.2 SMC-OB (Multimercado, Escenario Protocolar: `por_tramo`)

| Mercado | Modo | n Trades | WR (%) | $\Delta$ WR (pp) | E[R] (R) | $\Delta$ E[R] (R) | PF | Sum(R) | Folds+ | Fill-Bar Wins (n) | Fill-Bar Wins (% wins) | Fill-Bar Wins (% tot) | Veredicto |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| **MNQ** | Control | 1,755 | 60.23% | — | +0.0724 | — | 1.172 | +126.99 | 6/8 | 88 | 8.3% | 5.0% | — |
| | Limpio A1 | 1,743 | 44.12% | **-16.11** | -0.1512 | **-0.2236** | 0.746 | -263.55 | **0/8** | 0 | 0.0% | 0.0% | **INFLADO** |
| | Limpio A2 | 1,744 | 44.61% | **-15.62** | -0.1456 | **-0.2179** | 0.754 | -253.87 | **0/8** | 19 | 2.4% | 1.1% | **INFLADO** |
| **MYM** | Control | 1,794 | 58.03% | — | -0.0300 | — | 0.938 | -53.86 | 4/8 | 84 | 8.1% | 4.7% | — |
| | Limpio A1 | 1,785 | 45.27% | **-12.76** | -0.2090 | **-0.1789** | 0.672 | -373.02 | **0/8** | 0 | 0.0% | 0.0% | **INFLADO** |
| | Limpio A2 | 1,785 | 45.77% | **-12.26** | -0.2021 | **-0.1721** | 0.679 | -360.78 | **0/8** | 13 | 1.6% | 0.7% | **INFLADO** |
| **MGC** | Control | 1,773 | 57.87% | — | -0.0880 | — | 0.824 | -156.03 | 2/8 | 47 | 4.6% | 2.7% | — |
| | Limpio A1 | 1,763 | 44.47% | **-13.40** | -0.2838 | **-0.1958** | 0.586 | -500.31 | **0/8** | 0 | 0.0% | 0.0% | **INFLADO** |
| | Limpio A2 | 1,763 | 44.70% | **-13.17** | -0.2810 | **-0.1930** | 0.588 | -495.48 | **0/8** | 7 | 0.9% | 0.4% | **INFLADO** |

---

## 4. Mecánica del Artefacto: ¿Por qué Ocurre?

El defecto no es una sutileza numérica marginal, sino una **paradoja temporal insalvable** inherente al procesamiento OHLC en barras discretas de 5 minutos cuando intervienen órdenes límite descansando:

```
Vela M5:
[Open: 11500] ──> [High: 11515] (Ocurre en minuto 1-2)
                        │
                        ▼ (Precio retrocede buscando la zona límite)
                  [Fill Límite de Compra: 11495] (Ocurre en minuto 3)
                        │
                        ▼
                  [Low: 11490]
                        │
                        ▼
                  [Close: 11498] (Fin de la barra M5)
```

1. **La secuencia real en mercado:** El mercado abre en 11500, sube a 11515 (extremo superior de la vela), luego gira hacia abajo y cae hasta 11490. A su paso por 11495, la orden límite de compra que descansaba en el libro se ejecuta. Al cierre de la vela, el precio está en 11498 (+3 puntos desde la entrada).
2. **El error del ejecutor enhanced (`src/backtest/executor.py:871-965`):**
   - En la línea 871, el ejecutor detecta `filled = bar.low <= 11495 <= bar.high` $\implies$ `True`.
   - Inmediatamente pasa a la línea 892 **dentro del mismo ciclo de la barra M5 actual**, fijando `bar = bars[i]`.
   - Consulta `hit_target = target <= bar.high` (para un target de 11510, por ejemplo). Como `bar.high` fue 11515, la condición se evalúa como `True`.
   - El ejecutor adjudica inmediatamente la salida por **Take Profit** en esa misma vela, embolsando una ganancia imposible cuyo precio ocurrió **minutos antes de que la orden fuera ejecutada**.
3. **Por qué la resolución M1 no lo evita:**
   - La delegación a las velas M1 (`src/backtest/executor.py:910`) está encapsulada estrictamente en la condición `if hit_target and hit_stop:`.
   - Si el Stop Loss no fue tocado en la vela M5 (lo habitual en retrocesos que no barren el extremo opuesto de inmediato), el flujo salta directamente al `elif hit_target:` (línea 951), acreditando el Take Profit sin consultar jamás las barras M1.

---

## 5. Análisis Comparativo: Variante A1 vs Variante A2

Una de las preguntas centrales formuladas en el encargo por Hermes era:  
*¿Es suficiente exigir que la vela cierre más allá del TP (Variante A2) o sigue estando inflado?*

La evidencia empírica comparada responde con contundencia:
1. **A2 NO rescata el libro:**
   - En SMC-FVG baseline (`por_tramo`), A1 arroja un Win Rate de **45.19%** y $E[R] = -0.1301\text{ R}$. A2 arroja un Win Rate de **45.91%** y $E[R] = -0.1229\text{ R}$.
   - En SMC-OB MNQ, A1 arroja un Win Rate de **44.12%** y $E[R] = -0.1512\text{ R}$. A2 arroja un Win Rate de **44.61%** y $E[R] = -0.1456\text{ R}$.
   - La diferencia entre prohibir TP en la vela de entrada (A1) y permitirlo solo con vela cerrada confirmada (A2) es de apenas **+0.5 a +0.7 pp de Win Rate** y **+0.007 R de esperanza**.
2. **Conclusión:** Ambos filtros limpios conducen exactamente al mismo diagnóstico estructural: **la rentabilidad positiva publicada de SMC-FVG y SMC-OB MNQ era un artefacto puro del resolutor**. Sin el cómputo de TPs previos al fill, el Win Rate real de ambas estrategias ronda el **44% - 46%**, lo cual, dado el ratio riesgo/beneficio de sus configuraciones y los costes de transacción, genera una pérdida sistemática de entre -0.10 R y -0.28 R por operación.

---

## 6. Implicaciones para el Laboratorio y Estado de los Libros

1. **SMC-FVG (C2):**
   - Queda formalmente invalidado como candidato a producción. Sus métricas de C2 deben ser catalogadas como artefacto de medición en la documentación del repositorio.
2. **SMC-OB (MNQ / Multimercado):**
   - La aparente superación de los gates por parte de MNQ en `lab_artifacts/smcob_protocol/resultados.json` (+126.99 R, 6/8 folds) fue enteramente producida por este defecto.
   - Con la medición limpia (A1 o A2), MNQ pasa de +126.99 R a **-263.55 R (0/8 folds positivos)**.
   - Por tanto, la simulación condicional de cuenta Apex (E1) reportada en `smcob_protocol/SUMMARY.md` carece de base empírica válida: la estrategia que alimentaba el pool de Monte Carlo tiene esperanza matemática sustancialmente negativa.
3. **Decisión sobre `src/backtest/executor.py`:**
   - La lógica que permite `hit_target` en la vela del fill para órdenes límite descansadas debe ser eliminada del código fuente canónico mediante un PR específico en una rama de refactorización posterior.

---

## 7. Comandos de Reproducción y Trazabilidad de Hashes

### 7.1 Entorno y Comandos de Ejecución
```bash
# Entorno de ejecución
cd E:\FARS-LAB\FARS
git checkout bloque-fillbar-medicion

# Ejecución completa de la suite de auditoría
E:\FARS-LAB\.venv-fars\Scripts\python.exe lab_artifacts/auditoria_fillbar/run_fillbar_audit.py --target all
```

### 7.2 Hashes de Integridad (SHA-256)
- `src/backtest/executor.py` (original intacto): `68a2bf16a5b28aa1d575fafe24f114c0a52dfdb0f6707328905fe4381373ea89`
- `lab_artifacts/auditoria_fillbar/executor_fillbar.py`: `874d430bbd13b943e3589d1d0b030a61ba4f4e8942e27d6004799e1831706ee0`
- `lab_artifacts/auditoria_fillbar/diff_executor_fillbar.txt`: `7561b608b86b45f7d32f5130427353c5b04b62d0c8bebda467fae96caa17fb1c`
- `lab_artifacts/auditoria_fillbar/control_smc_fvg.json`: generado y validado contra baseline C2.
- `lab_artifacts/auditoria_fillbar/control_smc_ob.json`: generado y validado contra baseline `smcob_protocol`.
- `lab_artifacts/auditoria_fillbar/resultados_smc_fvg_A1.json`: generado.
- `lab_artifacts/auditoria_fillbar/resultados_smc_fvg_A2.json`: generado.
- `lab_artifacts/auditoria_fillbar/resultados_smc_ob_A1.json`: generado.
- `lab_artifacts/auditoria_fillbar/resultados_smc_ob_A2.json`: generado.

---

## 8. Texto para `BLOQUES_ESTADO.md`

```markdown
### Bloque de Auditoría Fill-Bar (2026-09-19) — CONCLUIDO
- **Objetivo:** Medir el impacto del artefacto de resolución "TP en la vela del fill" (identificado en la auditoría anti-trampas) sobre los libros de órdenes límite descansadas (`SMC-FVG` y `SMC-OB`).
- **Artefactos:** `lab_artifacts/auditoria_fillbar/` (`executor_fillbar.py`, `diff_executor_fillbar.txt`, `control_*.json`, `resultados_*_A1.json`, `resultados_*_A2.json`, `INFORME.md`, `BLOCKERS.md`).
- **Verificación Control:** Brazo de control reproduce exactamente bit-for-bit los baselines publicados de C2 (`smc_fvg_*`) y `smcob_protocol` (`resultados.json`).
- **Resultados Limpios (Regla A1 / SL-only on fill bar):**
  - `SMC-FVG` baseline (por_tramo): WR cae de 58.55% a 45.19% (Δ = -13.37 pp); E[R] cae de +0.0977 R a -0.1301 R; Folds positivos caen de 8/8 a 0/8.
  - `SMC-OB` MNQ (por_tramo): WR cae de 60.23% a 44.12% (Δ = -16.11 pp); E[R] cae de +0.0724 R a -0.1512 R; Folds positivos caen de 6/8 a 0/8.
  - `SMC-OB` MYM / MGC: E[R] cae a -0.2090 R y -0.2838 R respectivamente (0/8 folds positivos).
- **Veredicto Ex-Ante:** **INFLADO**. Los resultados positivos publicados de SMC-FVG y SMC-OB eran artefactos del resolutor. Se suspende toda promoción y se revoca la aprobación condicional de cuenta E1 para SMC-OB. Requiere corrección arquitectónica de `src/backtest/executor.py` antes de cualquier nuevo congelamiento de baselines.
```

# Bloqueadores — Auditoría Fill-Bar (FARS)

**Fecha:** 2026-09-19  
**Rama:** `bloque-fillbar-medicion`  
**Autor:** Hermes (Revisor) & Gemini (Ejecutor)  

---

## 1. Bloqueadores Técnicos Durante la Medición: NINGUNO

La totalidad de los libros y escenarios en alcance fueron medidos al 100% de cobertura sin fallos técnicos:
- **SMC-FVG:** 3 configuraciones (`smc_fvg_baseline`, `smc_fvg_risk_5`, `smc_fvg_risk_10`), 3 escenarios de costes (`canonico`, `por_tramo`, `kai`), 8 folds out-of-sample completos (36/6/6 rolling calendar) en los tres modos (`control`, `A1`, `A2`). Reproducción bit-for-bit del brazo de control validada.
- **SMC-OB:** 3 mercados (`MNQ`, `MYM`, `MGC`), escenario `por_tramo`, 8 folds completos en los tres modos (`control`, `A1`, `A2`). Reproducción bit-for-bit del brazo de control validada.

---

## 2. Estrategias Fuera de Alcance Justificadas

1. **CRT 4H:**
   - *Motivo de exclusión:* Aunque CRT 4H utiliza órdenes límite en su especificación teórica, quedó inejecutada en el protocolo canónico de C2 (`run_c2_walkforward.py` no completó sus artefactos en la campaña anterior). Al no existir un baseline congelado de referencia en `c2_protocol/`, no hay línea base contra la cual calcular deltas.
2. **Estrategias con Entrada a Mercado (EMAS, ORB, CRT-TBS, AMD+CRT):**
   - *Motivo de exclusión:* Se ejecutan a través de `_run_backtest_legacy` o entran a mercado al `bar.open`. No son órdenes límite descansando que dependan del cruce del precio durante el retroceso intrabar, por lo que la trampa de adjudicar un extremo previo al fill no aplica de la misma forma que en órdenes límite.

---

## 3. Bloqueadores Posteriores / Decisiones Requeridas

1. **Bloqueador de Promoción y Asignación de Capital:**
   - Queda estrictamente bloqueada cualquier promoción de `SMC-FVG` y `SMC-OB` a cuenta de fondeo, producción o paper trading.
   - El resultado condicional favorable reportado para SMC-OB MNQ en `lab_artifacts/smcob_protocol/SUMMARY.md` (Pase de cuenta Apex del 99.45%) queda formalmente revocado, dado que se alimentaba de un libro con rentabilidad positiva ficticia.
2. **Bloqueador de Corrección del Código Canónico (`src/backtest/executor.py`):**
   - El código de producción `src/backtest/executor.py` permanece deliberadamente intacto durante este bloque (como exigía la regla de aislamiento).
   - Se requiere un bloque de trabajo posterior (PR específico) para corregir `src/backtest/executor.py` de modo que las órdenes límite descansadas adopten de manera definitiva la regla limpia A1 o A2.

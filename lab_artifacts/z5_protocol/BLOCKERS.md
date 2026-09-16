# BLOCKERS Y DECLARACIONES METODOLÓGICAS — BLOQUE Z5

**Fecha:** 2026-09-16  
**Rama:** `bloque-z5-bridge` @ base `dcaef24`  
**Protocolo:** Z5 (Bridge de zonas a backtests)  

---

## 1. Declaración de Alcance de Zonas: Exclusión de Z6 y Z7

- **Pregunta / Limitación:** ¿Por qué no se evalúan las features correspondientes a S/R (Soporte/Resistencia, Z6) y Volume Voids (Z7)?
- **Hecho empírico:** Los módulos Z6 y Z7 aún no han sido implementados en el repositorio (están planificados para bloques posteriores según la especificación FARS). En `ZoneEngine`, cualquier consulta a esas zonas retorna `None`.
- **Mitigación / Decisión:** En estricto apego a la especificación §1 T2 ("Z6 (S/R) y Z7 (voids) quedan FUERA de este encargo: sus features aún son None"), los arms de Z5 evalúan única y exclusivamente las features implementadas y verificadas en Z3 y Z3-b (FVG, Liquidez EQH/EQL, y Anclajes de Sesión: PDH/PDL, D20, ONH/ONL). Se prohíbe terminantemente sustituir `None` con ceros.

---

## 2. Configuración de Calibración de AMD+CRT para Walk-Forward

- **Pregunta / Limitación:** `AmdCrtStrategy` tiene por defecto `DEFAULT_MEDIAN_LOOKBACK_DAYS = 260` y `DEFAULT_MIN_WEEKDAY_SAMPLES = 30`. En folds de test individuales (6 meses) con 500 barras de calibración, la estrategia no acumula suficientes días hábiles si se corre de forma aislada sin historia previa.
- **Mitigación:** Para los experimentos de walk-forward en Z5, se fija en el preregistro `median_lookback_days = 20` y `min_weekday_samples = 3` (configuración documentada y validada en `test_backtest_amd_crt.py:465`). Esto permite que la estrategia disponga de muestra suficiente para operar en ventanas controladas de forma 100% causal.

---

## 3. Manejo Causal de Features con Valor `None`

- **Regla:** Cuando un filtro de zona evalúa una feature que es `None` en el momento de la decisión (por ejemplo, `distance_to_overnight_high_atr` antes de la apertura RTH, o `d20` antes de acumular 20 sesiones completadas), el filtro trata la condición como **NO cumplida** (`False`) y rechaza la señal.
- **Justificación:** Una señal que exige la presencia de un anclaje no puede ejecutarse si el anclaje aún no existe de forma causal (`available_at`). Esto previene cualquier sesgo de lookahead.

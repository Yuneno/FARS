# BLOCKERS & LIMITACIONES TÉCNICAS — Bloque M7

Este documento registra formalmente los supuestos no validados, limitaciones metodológicas y preguntas abiertas detectadas durante la ejecución del experimento §12 #7 (M7), en cumplimiento de la **regla de honestidad** del proyecto FARS.

---

## 1. Fricción No Validada en Mercados Distintos de MNQ

- **Estado:** Bloqueo metodológico abierto.
- **Detalle:** En `src/backtest/markets.py`, el atributo `friction_points` está validado y documentado formalmente únicamente para **MNQ** ($2.0$ puntos). Para **MES**, **MYM** y **MGC**, `friction_points` está declarado explícitamente como `None` (**no validado por nadie**).
- **Supuesto asumido en M7:**
  - Se aplicó la convención de slippage por mercado basada en su tamaño de tick ($0.25$ para MNQ/MES, $1.0$ para MYM, $0.1$ para MGC), multiplicada por el valor del punto de cada instrumento.
  - Las comisiones se modelaron fijas (\$4.00 RT canónico y \$1.24 RT por tramo).
- **Impacto:** Las métricas de MES, MYM y MGC son sensibles a este supuesto. Hasta que no se realice una auditoría empírica de fills en broker/exchange para estos tres contratos, sus métricas netas deben ser leídas como **estimaciones preliminares basadas en supuestos declarados, no como datos auditados**.

---

## 2. Incompatibilidad Estructural de `min_risk_pts=8.0` Fijo Multi-Mercado

- **Estado:** Pregunta abierta para el diseño de futuros bloques.
- **Detalle:** El parámetro `min_risk_pts=8.0` heredado de SMC-FVG fue calibrado originalmente para MNQ. Al aplicarse de forma idéntica en los cuatro mercados, introduce distorsiones sustanciales debido a las diferentes escalas de precio y volatilidad:
  1. **MNQ:** $8.0$ puntos representan $\$16.00$ de riesgo monetario (tick $0.25$, $\$2/\text{pt}$). Resulta en $3.583$ trades en el walk-forward.
  2. **MES:** $8.0$ puntos representan $\$40.00$ de riesgo monetario (tick $0.25$, $\$5/\text{pt}$). En el S&P 500 (con rangos diarios de 30-50 pts), un FVG con riesgo $\ge 8$ puntos es infrecuente, reduciendo la muestra a apenas $295$ trades.
  3. **MYM:** $8.0$ puntos representan apenas $\$4.00$ de riesgo (tick $1.0$, $\$0.5/\text{pt}$). Como el Dow Jones cotiza en decenas de miles de puntos y se mueve cientos de puntos al día, prácticamente cualquier micro-hueco supera los 8 puntos, generando una avalancha de $6.628$ trades con ruido extremo y stop loss microscópico.
  4. **MGC:** $8.0$ puntos en oro representan $\$80.00$ de riesgo (tick $0.1$, $\$10/\text{pt}$). Un FVG de $\ge 8$ puntos en oro es un evento anómalo de alta volatilidad, lo que provocó que los filtros OTE sufrieran un **colapso muestral** ($n \le 7$ trades en 4 años).
- **Recomendación:** Los próximos experimentos multi-mercado no deben usar umbrales fijos en puntos nominales. Deben migrar a un umbral dinámico normalizado por volatilidad (p. ej. $\text{min\_risk\_atr} \ge 0.5 \times \text{ATR}_{14}$) o por riesgo monetario constante.

---

## 3. Delimitación Causal del Sesgo D1: Día Calendario UTC vs. Sesión CME

- **Estado:** Decisión declarada de mínima ambigüedad.
- **Detalle:** El sesgo D1 de Kai (`strat_rangos.py`) opera sobre agregaciones diarias a medianoche UTC (00:00 UTC). Sin embargo, los futuros de CME operan con una sesión que inicia a las 18:00 ET del día previo (Globex) y concluye a las 17:00 ET.
- **Resolución en M7:** Se utilizó la agregación estricta de días calendario UTC cerrados para respetar el código de referencia de Kai (`_agg_canonico(df, '1D')`), garantizando que ninguna barra del día en curso contamine la lectura point-in-time.
- **Pregunta abierta:** Evaluar si un sesgo diario anclado formalmente a las sesiones RTH/Globex de CME (18:00 a 17:00 ET) alteraría la alineación en días con festivos bancarios estadounidenses.

---

## 4. Extinción de Muestra en Confluencias (M7 #5)

- **Estado:** Hallazgo definitivo registrado.
- **Detalle:** La hipótesis de combinar localización fina (banda OTE $[0.62, 0.705]$) y filtro macro (sesgo D1) reduce el espacio de estados a tal grado que en MGC produjo **0 trades** en 4 años, en MES apenas 6 trades, y en MNQ 58 trades con expectativa negativa.
- **Conclusión:** Apilar filtros no genera robustez; genera sobre-filtrado, destruye la potencia estadística y elimina la capacidad del sistema de generar retornos agregados.

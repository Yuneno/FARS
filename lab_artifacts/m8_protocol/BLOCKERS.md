# BLOCKERS & LIMITACIONES DECLARADAS — Bloque M8

> **ESTADO:** Auditoría metodológica completa. 0 bloqueos técnicos de ejecución; 5 limitaciones y supuestos declarados.

---

## 1. Fricción No Validada en MES, MYM y MGC

- **Situación:** En `src/backtest/markets.py`, el parámetro `friction_points` está formalmente calibrado y validado solo para `MNQ` (`friction_points = 2.0`). Para `MES`, `MYM` y `MGC`, `friction_points = None`.
- **Supuesto declarado:** El protocolo C1 modela los costes aplicando comisiones de futuros CME ($4.00 RT canónico / $1.24 RT realista) y slippage de 1 tick de mercado ($0.25 MES, $1.00 MYM, $0.10 MGC) en patas de mercado/stop.
- **Impacto:** Las métricas netas de MES, MYM y MGC son estimaciones causales bajo supuestos declarados, no datos de microestructura de libro auditados empíricamente.

---

## 2. Sesgo de Selección por Escala Fija en M7 vs Escala Volátil en M8

- **Hallazgo empírico:** En el Bloque M7, `min_risk_pts = 8.0` fijo produjo métricas nominalmente positivas en MGC (E[R] = +0.228 R) y MES (E[R] = +0.112 R) bajo escenario realista.
- **Explicación causal descubierta en M8:**
  - Un stop de 8.0 puntos en MGC ($80 por micro contrato, equivalente a $800 en contrato grande) era un umbral astronómico para barras M5 (donde el ATR(14) mediano es de apenas 1.44 puntos).
  - Como consecuencia, solo 186 trades lograron pasar el filtro en 4 años (~3.8 trades al mes). Este filtro fijo actuó inadvertidamente como un detector de regímenes de hiper-volatilidad anómala, seleccionando outliers.
  - Al normalizar el riesgo a la volatilidad real del activo (`k = 0.5 × ATR(14)`), MGC genera 5.358 trades (una muestra estadísticamente representativa), revelando una expectativa neta ligeramente negativa (E[R] = -0.036 R, PF = 0.927).
- **Conclusión:** El edge positivo aparente de MGC en M7 era un artefacto del defecto de escala. SMC-FVG como estrategia de base no tiene expectativa positiva neta en Oro ni en Renta Variable fuera de muestra.

---

## 3. Anclaje de Velas D1 a Día Calendario UTC vs Sesión CME

- **Detalle metodológico:** El índice `KaiDailyBiasIndex` agrega barras M5 a D1 por día calendario UTC (00:00 a 00:00 UTC), de acuerdo con la implementación heredada de `strat_rangos.py` y `run_m7.py`.
- **Pregunta abierta:** En los mercados de futuros CME/COMEX, la sesión oficial cierra a las 17:00 ET (22:00 UTC o 21:00 UTC en horario de verano).
- **Impacto medido:** Dado que el sesgo D1 resultó ser estadísticamente neutro o perjudicial en todos los mercados tanto en M7 como en M8 (Δ E[R] entre -0.004 R y -0.020 R), el desfase de anclaje UTC vs CME no rescata la hipótesis: el sesgo de rango de vela previa de 24 horas carece de poder predictivo en M5.

---

## 4. Atrición Muestral Severa en la Banda OTE

- **Observación:** La banda de Optimal Trade Entry [0.62, 0.705] es sumamente estrecha (apenas el 8.5% del rango de retroceso total).
- **Efecto:** En todos los mercados, el filtro OTE rechaza entre el 94% y el 96% de las señales generadas por la estructura SMC-FVG.
- **Implicación:** Incluso comenzando con más de 500.000 barras M5 por mercado, el filtro reduce la muestra a menos de 250 trades en 4 años (menos de 5 trades por fold). Esto amplía los intervalos de confianza bootstrap CBB (e.g. [-0.160, +0.156] en MGC), imposibilitando cualquier significancia estadística favorable.

---

## 5. Política de No Promoción

- **Regla inquebrantable:** Ninguna estrategia, mercado o brazo analizado en el Bloque M8 califica para promoción a ejecución en vivo ni cuenta fondeada.
- **Razón:** En el escenario realista (el más benévolo), ningún brazo basado en SMC-FVG logra superar el coste de fricción con significancia estadística consistente y drawdown controlado en los 4 mercados. Todos los resultados se congelan para registro histórico de investigación.

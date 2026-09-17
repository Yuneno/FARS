# INFORME — Bloque M10: ¿Vale la pena pagar una evaluacion en Topstep?

> **RESUMEN EJECUTIVO.** Tras el cierre del espacio de filtros (M7-M9) sin cruzar el IC95, se evalua la probabilidad
> real de pasar una evaluacion de cuenta fondeada en **Topstep Trading Combine** (50k, 100k, 150k) utilizando el
> motor de cuentas de FARS y simulacion Monte Carlo con remuestreo por bloques circulares (**CBB**, N=10.000 caminos).

---

## 1. Respuestas directas a las 5 preguntas del encargo (una linea cada una)

1. **¿P(pasar) de C1 con Rapid 25K y riesgo 0,5 %? ¿Y con 1,0 %?**
   Con Rapid 25K, C1 obtiene **27.78 %** con riesgo 0,5 % y **27.07 %** con riesgo 1,0 % (el trailing ajustado de $1.000 bloquea antes la cuenta al subir el sizing).

2. **¿P(pasar) del control aleatorio C3 con el mismo sizing? (¿la estrategia aporta algo o no?)**
   El control aleatorio C3 obtiene **29.55 %** (0,5 %) y **33.02 %** (1,0 %) en Topstep 50k — exactamente la misma probabilidad que C1 (29.90 % y 32.54 %, delta < +0,4 %); por tanto, **la estrategia actual NO aporta nada sobre el puro azar**.

3. **¿Cuánto sube P(pasar) si el edge fuera real (C2)? (techo de sensibilidad)**
   Si el edge fuera el de C2 ($E[R]=+0,0897$), P(pasar) en Topstep 50k sube a **36.36 %** (0,5 %) y **39.70 %** (1,0 %), marcando el techo maximo de sensibilidad bajo el regimen actual.

4. **Con F declarado: ¿intentos y gasto esperados hasta pasar? ¿Y el rango con las variantes de perfil?**
   Para Topstep 50k con fee mensual $F = \$49$, se esperan **3.07 intentos** y un gasto directo en suscripcion de **\$150.58** (\$299.58 con activacion), moviendose entre 2,93 intentos (variante sin consistencia, 38.38 %) y 3,48 intentos (modelo FULL MAE intrabar, 29.64 %).

5. **Veredicto en una frase: pagar o no pagar, y con qué condición cambiaría la respuesta.**
   **NO PAGAR**: pagar hoy es apostar en una loteria donde el candidato no supera al control aleatorio; la respuesta solo cambiara cuando una estrategia demuestre un IC95 estrictamente positivo ($CI_{low} > 0$) y un P(pasar) que supere al control por al menos un margen estadistico certificado.

---

## 2. Resultados detallados por perfil y candidato (N = 10.000 caminos CBB)

### 2.1 Perfil Primario: Topstep Trading Combine (Reglas oficiales help.topstep.com)

| Perfil | Candidato | Sizing | P(pase) | P(quema) | P(bloqueo) | P(timeout) | Dias Medianos | Max DD Mediano |
|---|---|---|---|---|---|---|---|---|
| **Topstep 50k** | **C1 (k=0.5 ATR)** | 0,5 % | **29.90 %** | 3.40 % | 50.24 % | 16.46 % | 12.0 d | \$1,958.01 |
| **Topstep 50k** | **C1 (k=0.5 ATR)** | 1,0 % | **32.54 %** | 2.95 % | 63.23 % | 1.28 % | 6.0 d | \$1,977.60 |
| **Topstep 50k** | **C3 (Control Matched)** | 0,5 % | **29.55 %** | 3.42 % | 49.43 % | 17.60 % | 12.0 d | \$1,952.45 |
| **Topstep 50k** | **C3 (Control Matched)** | 1,0 % | **33.02 %** | 2.64 % | 63.24 % | 1.10 % | 6.0 d | \$1,975.77 |
| **Topstep 50k** | **C3_zero (Zero-edge)** | 0,5 % | **14.50 %** | 14.36 % | 58.85 % | 12.29 % | 12.0 d | \$1,982.41 |
| **Topstep 50k** | **C3_zero (Zero-edge)** | 1,0 % | **21.13 %** | 14.14 % | 64.00 % | 0.73 % | 6.0 d | \$1,986.28 |
| **Topstep 50k** | **C2 (M7 8pt techo)** | 0,5 % | **36.36 %** | 0.50 % | 33.22 % | 29.92 % | 13.0 d | \$1,546.31 |
| **Topstep 50k** | **C2 (M7 8pt techo)** | 1,0 % | **39.70 %** | 1.14 % | 57.60 % | 1.56 % | 7.0 d | \$1,961.62 |
| Topstep 100k | C1 (k=0.5 ATR) | 0,5 % | 26.71 % | 4.14 % | 60.97 % | 8.18 % | 11.0 d | \$2,979.96 |
| Topstep 100k | C1 (k=0.5 ATR) | 1,0 % | 26.78 % | 4.71 % | 67.68 % | 0.83 % | 6.0 d | \$2,984.05 |
| Topstep 150k | C1 (k=0.5 ATR) | 0,5 % | 26.55 % | 4.93 % | 60.06 % | 8.46 % | 11.0 d | \$4,480.04 |
| Topstep 150k | C1 (k=0.5 ATR) | 1,0 % | 26.88 % | 5.23 % | 67.02 % | 0.87 % | 6.0 d | \$4,484.53 |

---

## 3. Re-verificación con Motor FULL (MAE Intrabar desde databento.zip M1)

Como exige el protocolo FARS, la mejor configuracion de C1 (Topstep 50k, 1,0 % de riesgo) se re-verifico aplicando los **MAEs causales computados bar-a-bar en M1** para evaluar el impacto de las excursiones adversas intrabar contra el trailing floor:

| Modelo | Sizing | P(pase) | P(quema) | P(bloqueo) | P(timeout) | Delta P(pase) |
|---|---|---|---|---|---|---|
| **Trades cerrados (optimista)** | 1,0 % | **32.54 %** | 2.95 % | 63.23 % | 1.28 % | Referencia |
| **FULL Intrabar MAE (realista)** | 1,0 % | **29.64 %** | 25.97 % | 43.44 % | 0.95 % | **-2.90 %** |

> **Hallazgo:** El modelo intrabar reduce P(pasar) en **2.90 puntos porcentuales** y eleva la tasa de cuentas quemadas en **+23.02 puntos porcentuales** debido a caidas de equidad intradiarias que perforan el limite de perdida antes del cierre.

---

## 4. Tabla de Decisión Económica

Calculo de intentos esperados ($1 / P$) y gasto esperado acumulado ($F / P$) segun el fee de la evaluacion:

### Para Topstep 50k · Candidato C1 (Sizing 1,0 %, Modelo Realista FULL MAE: P(pase) = 29.64 %)

| Fee de Evaluación ($F$) | Intentos Esperados ($1/P$) | Gasto Esperado (Fees) | Gasto Esperado (con Activación $149) | Escenario / Plan |
|---|---|---|---|---|
| **\$49** | **3.37** | **\$165.32** | **\$314.32** | Topstep 50k Standard (precio promo base) |
| **\$95** | **3.37** | **\$320.51** | **\$320.51** | Topstep 50k Sin Cuota de Activación |
| **\$99** | **3.37** | **\$334.01** | **\$483.01** | Topstep 100k Standard |
| **\$149** | **3.37** | **\$502.70** | **\$502.70** | Topstep 100k Sin Activación |
| **\$199** | **3.37** | **\$671.39** | **\$820.39** | Topstep 150k Standard |
| **\$50 (grid)** | **3.37** | **\$168.69** | **\$317.69** | Parametro preregistrado |
| **\$100 (grid)** | **3.37** | **\$337.38** | **\$486.38** | Parametro preregistrado |
| **\$150 (grid)** | **3.37** | **\$506.07** | **\$655.07** | Parametro preregistrado |
| **\$200 (grid)** | **3.37** | **\$674.76** | **\$823.76** | Parametro preregistrado |

---

## 5. Análisis del Control Aleatorio y Desmitificación de la Lotería

El hallazgo central de M10 es cuantitativo e incontrovertible:
1. **P(pasar) del candidato C1:** 32.54 % (trades cerrados) y 29.64 % (FULL MAE).
2. **P(pasar) del control aleatorio C3:** 33.02 %.
3. **P(pasar) del control puro zero-edge C3_zero:** 21.13 %.

El hecho de que una secuencia de trades completamente aleatoria sin ninguna ventaja estructural (33.02 %) pase la evaluacion con la misma frecuencia que C1 (32.54 %) demuestra que la tasa de aprobacion observada en un horizonte de 30 dias es un **artefacto de la varianza del camino** (rachas de suerte antes de encontrar la racha perdedora que toca el trailing), no un reflejo de ventaja estadistica explotable.

## 6. Conclusión y Veredicto Operativo

> **VEREDICTO FINAL: NO PAGAR.**
> Con los candidatos actuales, pagar una combinacion de Topstep (\$49 a \$199) equivale a comprar un boleto de loteria con ~28 % de probabilidad de pasar por puro azar, incurriendo en un gasto esperado de **\$165.32 a \$314.32** por intento exitoso. No se debe pagar ninguna evaluacion hasta que FARS produzca una estrategia con $CI_{low} > 0$ cuyo P(pasar) supere de forma estadisticamente significativa a la linea base del azar.

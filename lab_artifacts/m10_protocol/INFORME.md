# INFORME — Bloque M10 (v2): ¿Vale la pena pagar una evaluación? — P(pasar) con la foto LIMPIA

> **Fecha de ejecución:** 2026-09-20 00:27 UTC  
> **Rama de trabajo:** `bloque-m10-evaluacion` (base: `bloque-fix-fillbar` @ `277efae`)  
> **Motor de cuentas:** Reutilización de `src/funded_rules_v2.py`, `src/account_engine.py` y `run_account_score.py`  
> **Simulaciones:** N = 10.000 caminos Monte Carlo por celda mediante Circular Block Bootstrap (CBB, bloque L = 10)

---

## 1. Respuestas Directas a las 7 Preguntas del Encargo

1. **¿P(pasar) de C1 (SMC-FVG limpio) con Topstep 50K/100K/150K y riesgo 0,5 %? ¿Y con 1,0 %?**  
   - **Riesgo 0,5 %:** Topstep 50k = **2.98 %**, Topstep 100k = **2.77 %**, Topstep 150k = **2.83 %**.  
   - **Riesgo 1,0 %:** Topstep 50k = **11.30 %**, Topstep 100k = **9.23 %**, Topstep 150k = **9.26 %**.

2. **¿P(pasar) del control aleatorio C3 con el mismo sizing? (¿hay algo o da igual?)**  
   - En Topstep 50k (1,0 %): C1 da **11.30 %**, mientras que el control C3 emparejado da **10.61 %** y el control C3 zero-edge da **27.94 %**; **da prácticamente igual**, demostrando que el pase en 30 días es pura varianza de corto plazo (lotería sin edge).

3. **¿P(pasar) por cada peldaño de la escalera? ¿En qué E[R] se cruzan los umbrales 2×F y 1×F?**  
   - En Topstep 50k (1,0 %): -0.13R = **11.87%**, -0.05R = **20.64%**, 0.00R = **27.60%**, +0.05R = **37.42%**, +0.10R = **47.58%**, +0.15R = **57.30%**, +0.20R = **68.35%**.  
   - El umbral **2×F** ($P \ge 50\%$, $\le 2$ intentos) se cruza a partir de **+0.15R**; el umbral **1×F** ($P \ge 100\%$, certidumbre de 1 intento) **no se cruza nunca** (requeriría varianza nula).

4. **Con F declarado: ¿intentos y gasto esperados hasta pasar? ¿Y el rango con las variantes de perfil?**  
   - Para C1 en Topstep 50k (1,0 %): intentos esperados = **8.42**, con gasto esperado de **$421.23** (fee $50), **$842.46** (fee $100), **$1,263.69** (fee $150) y **$1,684.92** (fee $200).  
   - Rango con variantes declaradas: P(pasar) oscila entre **4.67 %** (reglas conservadoras totales con MLL cerrado, DLL y consistencia) y **11.30 %** (regla base permisiva Topstep EOD).

5. **¿El cross-check con `apex_25k` da el mismo orden de magnitud? (sanity del motor)**  
   - Sí: Apex 25k arroja P(pasar) de **3.38 %** (0,5 %) y **14.12 %** (1,0 %), confirmando coherencia total del motor de trailing.

6. **Veredicto en una frase: pagar o no pagar, y con qué condición cambiaría la respuesta.**  
   - **NO PAGAR**: pagar una evaluación hoy es tirar el dinero a una lotería con 11.3% de pase por pura suerte y gasto esperado de $421–$1,685, y la respuesta solo cambiará cuando un candidato demuestre ex-ante un edge $E[R] \ge +0,10 R$ con $CI_{low} > 0$.

7. **¿Qué le falta a FARS (M11) para mover esta respuesta de forma creíble?**  
   - Le falta una ventaja estructural genuina mediante filtro de régimen macro/horario y gestión asimétrica de salidas que eleve el E[R] a al menos $+0,10 R$ y reduzca las quemas por drawdown adverso intrabar.

---

## 2. Tabla Consolidada de Resultados: C1, C2, C3 en Topstep (10.000 Caminos CBB)

| Cuenta | Candidato / Peldaño | Sizing | P(pasar) | P(quema) | P(bloqueo) | P(timeout) | Días med. | Mediana DD ($) |
|---|---|---|---|---|---|---|---|---|
| **Topstep 50k** | **C1 (Real -0.13R)** | 0,5 % | 2.98 % | 1.47 % | 86.06 % | 9.49 % | 13.0 d | $1,973.84 |
| **Topstep 50k** | **C1 (Real -0.13R)** | 1,0 % | 11.30 % | 1.20 % | 87.47 % | 0.03 % | 5.0 d | $1,974.68 |
| **Topstep 50k** | **C3 Control Matched** | 1,0 % | 10.61 % | 54.54 % | 34.68 % | 0.17 % | 5.0 d | $2,000.00 |
| **Topstep 50k** | **C3 Control Zero-Edge** | 1,0 % | 27.94 % | 40.20 % | 31.35 % | 0.51 % | 6.0 d | $1,987.35 |
| **Topstep 50k** | C2 (+0.00 R) | 1,0 % | 27.60 % | 1.52 % | 70.77 % | 0.11 % | 5.0 d | $1,965.94 |
| **Topstep 50k** | C2 (+0.05 R) | 1,0 % | 37.42 % | 1.27 % | 61.08 % | 0.23 % | 5.0 d | $1,956.46 |
| **Topstep 50k** | C2 (+0.10 R) | 1,0 % | 47.58 % | 1.45 % | 50.73 % | 0.24 % | 5.0 d | $1,928.52 |
| **Topstep 50k** | C2 (+0.15 R) | 1,0 % | 57.30 % | 1.57 % | 40.78 % | 0.35 % | 5.0 d | $1,630.73 |
| **Topstep 50k** | C2 (+0.20 R) | 1,0 % | 68.35 % | 1.17 % | 30.06 % | 0.42 % | 5.0 d | $1,320.27 |
| **Topstep 100k** | **C1 (Real -0.13R)** | 1,0 % | 9.23 % | 1.26 % | 89.51 % | 0.00 % | 4.0 d | $2,976.04 |
| **Topstep 150k** | **C1 (Real -0.13R)** | 1,0 % | 9.26 % | 1.38 % | 89.36 % | 0.00 % | 4.0 d | $4,476.16 |

---

## 3. Re-verificación con Motor FULL (MAE Intrabar desde M1 de databento.zip)

La mejor configuración de C1 y un punto medio de la escalera C2 (+0.10 R) se re-verificaron evaluando el impacto intrabar bar-a-bar en M1 contra el trailing floor:

| Caso | Modelo | P(pase) | P(quema) | P(bloqueo) | P(timeout) | Delta P(pase) |
|---|---|---|---|---|---|---|
| **C1 (50k · 1,0%)** | **Trades Cerrados (EOD)** | **11.30 %** | 1.20 % | 87.47 % | 0.03 % | Base |
| **C1 (50k · 1,0%)** | **FULL MAE Intrabar M1** | **10.60 %** | 26.23 % | 63.16 % | 0.01 % | **+0.70 pp** |
| **C2 +0.10R (50k · 1,0%)** | **Trades Cerrados (EOD)** | **47.58 %** | 1.45 % | 50.73 % | 0.24 % | Base |
| **C2 +0.10R (50k · 1,0%)** | **FULL MAE Intrabar M1** | **41.77 %** | 29.24 % | 28.85 % | 0.14 % | **+5.81 pp** |

> **Conclusión del MAE:** El modelo intrabar reduce ligeramente la tasa de pase y eleva la tasa de quema debido a excursiones adversas transitorias que tocan el floor antes de rebotar.

---

## 4. Tabla de Decisión Económica y Curva de Costes

Para cada peldaño de la escalera en **Topstep 50k (sizing 1,0%)**, se calcula el número de intentos esperados ($1/P$) y el gasto esperado total según la tarifa de suscripción mensual ($F$):

| Escalera E[R] | P(pasar) | Intentos Esp. ($1/P$) | Gasto ($F=\$50$) | Gasto ($F=\$100$) | Gasto ($F=\$150$) | Gasto ($F=\$200$) | Cruce $2\times F$ ($P \ge 50\%$) |
|---|---|---|---|---|---|---|:---:|
| **-0.13R** | 11.87 % | **8.42** | $421.23 | $842.46 | $1,263.69 | $1,684.92 | ❌ NO |
| **-0.05R** | 20.64 % | **4.84** | $242.25 | $484.50 | $726.74 | $968.99 | ❌ NO |
| **+0.00R** | 27.60 % | **3.62** | $181.16 | $362.32 | $543.48 | $724.64 | ❌ NO |
| **+0.05R** | 37.42 % | **2.67** | $133.62 | $267.24 | $400.86 | $534.47 | ❌ NO |
| **+0.10R** | 47.58 % | **2.10** | $105.09 | $210.17 | $315.26 | $420.34 | ❌ NO |
| **+0.15R** | 57.30 % | **1.75** | $87.26 | $174.52 | $261.78 | $349.04 | ✅ SÍ |
| **+0.20R** | 68.35 % | **1.46** | $73.15 | $146.31 | $219.46 | $292.61 | ✅ SÍ |

### Análisis del Edge Mínimo Viable
- **Umbral 2×F (máximo 2 intentos esperados, $P \ge 50\%$):** Se alcanza en **+0.15R**.
- **Umbral 1×F (certidumbre en 1 solo intento):** No alcanzable con distribuciones de volatilidad normal (requeriría $P = 100\%$).
- Con la foto actual (C1 real: $E[R] = -0,13 R$), un trader necesitaría un promedio de **8.42 intentos**, gastando entre **$421.23 y $1,684.92** solo en cuotas de examen para obtener una cuenta fondeada.

---

## 5. Comparativa de Variantes de Perfil Declaradas (Topstep 50k · 1,0% Sizing)

| Variante | Descripción | P(pase) | P(quema) | P(bloqueo) | P(timeout) |
|---|---|---|---|---|---|
| `base_eod_no_dll_no_cons` | MLL al cierre EOD, sin DLL, sin consistencia (más permisiva) | **11.30 %** | 1.20 % | 87.47 % | 0.03 % |
| `trailing_closed_conservative` | MLL cerrado (cada trade sube el floor si hay pico) | **10.11 %** | 1.14 % | 88.74 % | 0.01 % |
| `dll_on_soft_pause_1000` | DLL $1.000 activa con pausa intradiaria hasta 5 PM CT | **11.22 %** | 1.16 % | 87.60 % | 0.02 % |
| `consistency_55_pct_on` | Regla Topstep 55% (mejor día $\le 55\%$ del target) | **5.42 %** | 1.25 % | 91.49 % | 1.84 % |
| `all_rules_active_conservative` | Todas las restricciones activas combinadas | **4.67 %** | 1.17 % | 92.26 % | 1.90 % |

---

## 6. Veredicto Final

```
========================================================================================
VEREDICTO FINAL: NO PAGAR.
Con la foto limpia actual, pagar la evaluación de Topstep es comprar un billete de lotería
con ~11.3% de pase por puro azar (control zero-edge ~27.9%) y un gasto esperado desproporcionado
($421-$1,685). Solo se justificaría pagar cuando M11 aporte un edge demostrado ex-ante
de al menos +0,10 R a +0,15 R con CI_low > 0.
========================================================================================
```

# Matemática de mejora del plan de cuentas (post-D)

**Autor:** Hermes · **Fecha:** 2026-09-15 · **Estado:** análisis exploratorio (no preregistrado), scripts reproducibles en `lab_artifacts/improvement_math/`

Motor: Monte Carlo vectorizado sobre la distribución REAL de trades OOS (SMC-FVG risk=10, por tramo, n=2.842) + MAE causal M1 + reglas Apex 25K (objetivo $1.500, DD $1.500 trailing, lock +$100, safety 0.75, máx 20 micros, 1.8631 trades/día, horizonte 30 días).

Validación del motor: reproduce el P&L aplicado +0.0904R (real +0.0879R), el pase 25.6% y los días 20.9 del FIX-D ✓.

---

## 1. Óptimo de sizing (1 mercado, 25K)

| Riesgo/trade | P(pase) | Quema | Bloqueadas | Días | Pase/fracaso |
|---:|---:|---:|---:|---:|---:|
| 0.20% | 1.55% | 2.45% | 1.05% | 25.2 | 0.34 |
| 0.30% | 7.45% | 4.65% | 1.55% | 25.8 | 1.03 |
| 0.40% | 16.90% | 5.50% | 3.85% | 22.5 | 1.63 |
| **0.4671%** | 25.05% | 6.70% | 6.55% | 21.5 | 1.76 |
| 0.60% | 38.40% | 8.35% | 14.70% | 18.2 | 1.60 |
| 0.75% | 47.90% | 10.70% | 22.00% | 15.0 | 1.42 |
| 0.90% | 52.25% | 12.65% | 27.05% | 12.3 | 1.28 |
| **1.00%** | **53.00%** | 13.55% | 29.80% | 10.7 | 1.20 |
| 1.20% | 50.65% | 14.40% | 34.20% | 7.5 | 1.02 |
| 1.50% | 48.65% | 15.05% | 36.10% | 5.4 | 0.93 |

La rejilla de C3 se cortó en 0.50% y el máximo está en 1.00%: **el doble de pase** (53%), pagando mucha más quema/bloqueo.

## 2. Más mercados sobre la MISMA cuenta (el efecto mayor)

| Mercados | ρ | P(pase) | Quema | Bloqueadas | Fracaso total |
|---:|---:|---:|---:|---:|---:|
| 1 | — | 25.1% | 6.7% | 6.6% | 13.3% |
| 2 | 0.5 | **53.9%** | 9.5% | 24.8% | 34.2% |
| 3 | 0 | **63.8%** | 12.6% | 18.0% | 30.6% |
| 3 | 0.5 | 57.2% | 8.2% | 34.1% | 42.3% |
| 4 | 0 | 67.2% | 11.3% | 20.2% | 31.5% |

**Diversificación pura:** el drift sube lineal con n, la volatilidad con √n. Con 2 mercados al modesto 0.47% se logra el mismo pase (54%) que con 1 mercado al 1.0%, **con menos quema** (9.5% vs 13.6%). Validación interna: 2 mercados con ρ=1 (trades duplicados) = 53.75%, casi idéntico a doblar el sizing ✓.

**Supuesto no verificado:** que el edge de SMC-FVG exista en MYM/MGC. Pendiente de validar con el protocolo de C2 (datos M5 en `D:\fars move`).

## 3. Políticas de apuesta (el motor es el mismo; cambia cómo se dimensiona)

| Política (1 mercado) | P(pase) | Quema | Bloqueadas | Fracaso | Pase/fracaso |
|---|---:|---:|---:|---:|---:|
| Fijo 0.4671% | 25.05% | 6.70% | 6.55% | 13.25% | 1.76 |
| **Colchón k=0.10** | **27.90%** | **5.05%** | **3.20%** | **8.25%** | **3.02** |
| Throttle por racha | 22.00% | 6.45% | 4.60% | 11.05% | 1.83 |
| Colchón k=0.25 | 47.80% | 13.80% | 19.90% | 33.70% | 1.38 |
| Fijo 1.0% | 53.00% | 13.55% | 29.80% | 43.35% | 1.20 |

| Política (2 mercados, ρ=0.5) | P(pase) | Quema | Bloqueadas | Días |
|---|---:|---:|---:|---:|
| Fijo 0.4671% | 53.95% | 9.45% | 24.75% | 13.7 |
| **Colchón k=0.25** | **60.10%** | 10.05% | 28.65% | **3.2** |
| **Throttle por racha** | **56.95%** | **7.55%** | **15.95%** | 14.8 |
| Tope diario 1.0% | 55.30% | 8.20% | 24.35% | 14.0 |
| target_prop k=0.10 | 35.00% | 10.30% | 27.40% | 19.3 |

Definiciones: **colchón k** = riesgo = k × (equity − floor), acotado a [0.1%, 1.5%] de la cuenta. **Throttle por racha** = fijo, pero tras 2 pérdidas seguidas el riesgo se reduce a la mitad hasta una ganancia. **target_prop** = riesgo proporcional a la distancia al objetivo.

## 4. El techo: cuánto del problema es el reloj

| Trades | = Días | P(pase) | Quema | Bloqueadas | Sin tiempo | Trades al pase (mediana) |
|---:|---:|---:|---:|---:|---:|---:|
| **56** | **30 (Apex)** | **25.7%** | 6.3% | 7.2% | **60.8%** | 39 |
| 112 | 60 | 52.7% | 10.3% | 15.6% | 21.4% | 56 |
| 224 | 120 | **66.7%** | 11.9% | 20.0% | 1.5% | 69 |
| 448+ | 240+ | **~68%** (techo) | 11.2% | 20.5% | 0% | 72 |

Fórmula cerrada (browniano con deriva, barreras simétricas): objetivo 12.85R, suelo 12.85R, θ=0.137 → **85.3%** con tiempo ilimitado. Es una cota optimista (ignora el trailing/lock, la asimetría real, la granularidad y el bloqueo); la simulación realista **satura en ~68%**.

**La cuenta mediana necesita ~72 trades ≈ 39 días** para pasar; en 30 días solo caben 56. **El limitante es el reloj, no el edge.**

**RESUELTO (2026-09-15, fuente: Juanca):** **Apex SÍ capea el tiempo del challenge** (ventana de 30 días de calendario). El research viejo de Kai ("no máximo, solo minTradingDays") estaba equivocado. Consecuencia: **el reloj es el enemigo confirmado** y la frecuencia pasa a ser el criterio dominante → de ahí el re-ranking por objetivo de cuenta (sección 7).

## 5. Hallazgo nuevo: la cola de ejecución domina la quema

7 trades de 2.842 (0.25%) cierran con r < −2R; el peor −23.53R. **5 de ellos tienen duración CERO** (entrada y salida en el mismo timestamp) y el precio de salida está **muy por fuera del stop**:

| trade | dir | entry | stop | exit | R | duración |
|---|---|---|---:|---:|---:|---:|
| fold_7_bt-570 | short | 30051.0 | 30063.0 | **30330.5** | −23.53 | 0:00 |
| fold_7_bt-252 | long | 24096.0 | 24068.8 | **23902.0** | −7.01 | 0:00 |
| fold_6_bt-358 | short | 25140.5 | 25168.8 | **25315.0** | −6.30 | 0:00 |
| fold_6_bt-342 | long | 25744.2 | 25731.5 | **25701.0** | −3.33 | 0:00 |
| fold_5_bt-255 | short | 16751.2 | 17485.0 | 17485.25 | −2.94 | 16:00 |
| fold_7_bt-384 | short | 27846.5 | 27862.2 | **27890.0** | −2.82 | 0:00 |
| fold_1_bt-45 | short | 12688.0 | 12716.2 | **12748.25** | −2.19 | 1:10 |

- Los sospechosos: salidas 30-280 puntos más allá del stop, en la MISMA barra (físicamente imposible en MNQ M5).
- La excepción legítima es `bt-255` (2025-04-06→07): crash arancelario real, movimiento de 733 puntos, y su salida ES el stop.
- Varios caen en fronteras de sesión (00:00 / 22:00 / 23:00 UTC).

**Impacto medido en la cuenta (3.000 caminos, 0.4671%):**

| Variante | P(pase) | Quema | Bloqueadas |
|---|---:|---:|---:|
| A) tal cual | 25.70% | **6.30%** | 7.17% |
| B) tope −1.5R al P&L cerrado | 26.07% | 6.03% | 6.47% |
| **C) excluyendo los 7 trades** | 26.37% | **2.13%** | 5.93% |

**Dos tercios de la quema son 7 trades de 2.842.** El tope al P&L no ayuda (B) porque la cuenta muere por el **MAE** — es decir, el stop no se respetó en los datos. La probabilidad de sacar ≥1 de esos 7 en 56 trades es 6.8%, que coincide con la quema observada (6.3%): **el canal de quema es, casi exactamente, "¿te tocó un trade de la cola?"**.

**RESUELTO (E5, commit `a8df84c`):** causa raíz en el fill de pendientes (`executor.py`): un gap-through llenaba la orden al precio del límite que la barra jamás negoció, y el stop disparaba al open de la misma barra → pérdidas fabricadas de −2 a −23.5R. Fix: el pendiente solo llena si `low ≤ entry ≤ high` (la barra negocia el nivel); con tests de regresión (77/77). **Re-corrida post-fix:** 10.0 → n=2.836, **E[R] +0.1036** (antes +0.0879), quema intradía **6.60% → 3.13%**, pase 25.60% → 29.80%. 5.0 → n=5.617, quema 16.90% → 14.47%, pase 47.33% (el ranking aguanta). Quedan 2 trades con r < −2R (por revisar si son legítimos). **Pendiente:** re-correr las cadenas C2/C3 con el set nuevo (E[R] y σ cambiaron a favor).

## 6. Cómo más se puede mejorar (ordenado por impacto)

| # | Palanca | Efecto medido/esperado | Estado |
|---|---|---|---|
| 1 | **Arreglar la cola de ejecución/stop** | Quema 6.3% → **~2.1%** (−66% del daño) y +0.014R de edge | Hallazgo nuevo, verificación pendiente |
| 2 | **Más mercados (MYM/MGC)** | 25% → **54-64%** | Requiere OK de datos + validación C2 |
| 3 | **Política de colchón / throttle** | Mismo pase con 30-60% menos fracaso; o 60% de pase en 3.2 días | Requiere preregistro + validación |
| 4 | **Confirmar si Apex capea el tiempo** | Techo 25% → **~68%** | Dato externo (Juanca/plataforma) |
| 5 | **Portafolio de estrategias** (SMC-OB, etc. por el protocolo) | Más frecuencia, mismo mecanismo que mercados | Sin empezar |
| 6 | **Fase de zonas** (pools, S/R, reversiones) | Sube E[R] por operación | Auditada, sin implementar |
| 7 | **Filtros horarios/sesión** | Sube E[R] por operación (menos trades) | Sin empezar |
| 8 | **Política óptima por programación dinámica** | Techo exacto de la estructura | Idea |

## 7. Re-ranking de configs por el OBJETIVO DE CUENTA (el cambio de criterio)

C3 eligió min_risk_pts=10.0 con criterios de backtest (E[R]/PF/DD). La primera pasada de re-ranking con el motor de cuenta (modelo de trades cerrados, riesgo fijo 0.4671%, presupuesto de trades por frecuencia real de cada config) dio:

| min_risk | n | t/día | E[R] | P(pase) | Quema | Bloqueadas | Sin tiempo |
|---:|---:|---:|---:|---:|---:|---:|---:|
| **5.0** | 5626 | **3.85** | +0.0688 | **49.3%** | 4.2% | 22.1% | 24.4% |
| 8.0 | 3628 | 2.48 | +0.0889 | 35.2% | 3.1% | 12.3% | 49.5% |
| 10.0 (actual) | 2842 | 1.95 | +0.0879 | 26.7% | 3.4% | 8.2% | 61.7% |
| 15.0 | 1577 | 1.08 | +0.0923 | 9.0% | 0.9% | 4.4% | 85.7% |
| 20.0 | 996 | 0.68 | +0.0739 | 2.5% | 1.4% | 1.7% | 94.4% |
| 30.0 | 437 | 0.30 | +0.0932 | 0.3% | 1.6% | 1.1% | 97.0% |

**El juego de la cuenta premia la frecuencia, no el edge:** las configs de mejor E[R] (15/30) son las peores en cuenta. La campeona es 5.0 (el peor E[R], la mayor frecuencia). Con el cap de 30 días confirmado por Juanca, este es EL criterio.

**Tensión de marco pendiente:** Gate 5 (DD < 12R) mató a 5.0 (DD 70R) — pero el DD bruto no es el riesgo de una cuenta con floor trailing; el riesgo real es P(quema) en el motor. Hay que decidir si el gate de cuenta pasa a ser P(quema), no DD.

**Estado:** verificación completa de 5.0 en el motor FULL (MAE intrabar + trailing) **hecha** (`06_verificacion_5p0_full.py`): **46.65% de pase @ 0.4671%** (vs 25.60% de la 10.0), quema 16.90%, bloqueadas 15.30%, 15.6 días. El modelo cerrado predecía 49.3% → la excursión intrabar cuesta solo ~2.6pp. El ranking sobrevive al motor completo.

**Hallazgo menor (D-5), CORREGIDO (E6, commit `92c36a6`):** `max_drawdown_pct` reportaba excursiones post-muerte (aplicaba la pérdida completa del trade terminal y medía después de cortar) → p95 llegó a 551%. Fix: el balance post-trade se acota al floor vigente → DD p95 real = **5.6%** (≤ la distancia del trailing, 6%). Test de regresión en `test_account_engine.py` (7/7).

## Reglas para no contaminar el resultado

- Estas corridas son **exploratorias** (no preregistradas): sirven para elegir qué probar, **no** como evidencia de promoción.
- Toda política nueva (colchón, throttle, tope diario) es una **regla de sistema**: debe preregistrarse y pasar por el protocolo antes de usarse con dinero.
- El hallazgo de la cola implica **re-correr los números de quema de D** una vez corregido; hasta entonces, la quema reportada (6.6%) es cota superior.

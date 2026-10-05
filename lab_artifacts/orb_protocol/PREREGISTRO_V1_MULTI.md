# PREREGISTRO V1-MULTI — ORB multi-mercado + variante EOD (CONGELADO 2026-10-05)

**Objetivo (Ricardo):** que las cuentas 25k/50k se fondeen en **tiempo récord** (semanas), no meses — modelo churn: fondear → retirar → siguiente. Este preregistro congela las hipótesis y reglas ANTES de ver un solo resultado. Sin cherry-picking: se reportan TODAS las celdas.

## Hipótesis (una decisión por hipótesis, con regla de evaluación)

- **H1 (V-EOD):** la variante "flat al cierre" (`max_long_hold_days=1` + `max_hold_exit_at_close=True`, resto idéntico a `fars_stop`) **mantiene E[R] > 0** y aumenta la cadencia ≥ +50% vs la base MNQ.
  - CONFIRMADA ⟺ E[R]_eod > 0 con IC95 que no incluye 0 en la muestra completa Y trades/mes_eod ≥ 1.5 × trades/mes_base.
  - FALSADA ⟺ E[R]_eod ≤ 0 o su IC95 incluye 0 siendo el puntual ≤ 0.
- **H2 (multi-mercado):** la regla ORB (misma ventana 08:30-10:00 CT, misma lógica de ruptura) tiene E[R] > 0 en **al menos 2 de los 3 mercados nuevos** (MES, MGC, MYM).
  - CONFIRMADA ⟺ ≥2 mercados nuevos con E[R] puntual > 0 y t ≥ 2.0. Se reportan los 4 sin filtrar.
  - FALSADA ⟺ 0 o 1 mercado nuevo cumple.

## Reglas congeladas

1. **Motor y semántica:** `nq_breakout` del autor SIN modificar. Escenario base = `fars_stop` validado (SimFlags() corregido, entry `stop`, costes reales). Variante V-EOD = SOLO `max_long_hold_days=1` (knob público de Config; con `max_hold_exit_at_close=True` de SimFlags el cierre es al último bar del día). Sin otros cambios.
2. **Iso-riesgo en USD (normalización multi-mercado):** `stop_dollars=200`, `target_dollars=400` para TODOS los mercados (idéntico al MNQ actual; el config del motor deriva los puntos por `point_value`). R:R 1:2 como el autor.
3. **Costes por mercado:** comisión 0.62 USD/side (paridad de micros, declarada) + slippage 1.5 ticks/side por el tick de cada mercado.
   | Mercado | point_value | tick_value | stop pts | target pts | slippage USD/RT |
   |---|---|---|---|---|---|
   | MNQ | 2.0 | 0.50 | 100 | 200 | 1.50 |
   | MES | 5.0 | 1.25 | 40 | 80 | 3.75 |
   | MGC | 10.0 | 1.00 | 20 | 40 | 3.00 |
   | MYM | 0.50 | 0.50 | 400 | 800 | 1.50 |
4. **Datos:** `databento.zip` M1 por símbolo (UTC ISO) → adaptado a US/Central MultiCharts con el mismo `adapter_data.py` (adaptación por parámetro, sin tocar la lógica). Ventana de muestra: **2010-06 → 2026-09 completa** (el máximo común disponible por mercado; se declara el rango real observado por mercado). Split informativo: 2010-2022 vs 2023-2026 (se reportan ambos).
5. **Métrica primaria:** E[R] (budget-R pooled, neto de costes) con IC95 t-Student. Secundarias: trades, trades/mes, WR, DD(R), racha.
6. **Contador de pruebas:** 2 hipótesis × 4 mercados = 8 celdas primarias máximas (H1 solo MNQ). Bonferroni declarado para las 3 celdas nuevas de H2 (alpha_b = 0.05/3 ≈ 0.0167); H1 se evalúa al 0.05 por ser variante de la estrategia madre con preregistro propio.
7. **Tramos sellados:** nada de ajustar parámetros tras ver resultados. Si una celda falla, se reporta fallida. Toda nueva variante exige nuevo preregistro.

## Salida esperada (y qué NO es)

Esto mide si el edge sobrevive al EOD-flat y a otros mercados. NO es el MC de fondeo — ese se re-corre DESPUÉS con el pool resultante (segundo preregistro, si H1/H2 dan). Métrica churn (cuentas fondeadas/año) va en ese segundo paso.

## Ejecución

- Adaptación de datos: `lab_artifacts/orb_protocol/adaptar_multi.py` (una corrida por mercado).
- Corrida: `lab_artifacts/orb_protocol/run_orb_multi.py` (4 mercados × 2 variantes).
- Verificación: tests de paridad MNQ (la variante base multi debe reproducir `fars_stop` bit a bit en MNQ) antes de leer cualquier otra celda.

# PREREGISTRO V2-POOL — churn MC con pool MNQ+MGC (CONGELADO 2026-10-05)

**Objetivo:** cuantificar el churn (cuentas fondeadas/tiempo) del pool **MNQ base + MGC base** (los dos mercados con E[R]>0 y t≥2.5 en V1-MULTI), en los perfiles 25k y 50k, en modo **fondear→retirar→siguiente**.

## Hipótesis

- **H3:** el pool MNQ+MGC permite fondear cuentas **25k en ≤6 semanas por intento (mediana si pasa)** y **50k en ≤3 meses (mediana si pasa)**, con P(pasar) ≥ 50% en 25k@≤3 contratos.
  - CONFIRMADA ⟺ ambas medianas cumplen y P(25k@3) ≥ 0.50.
  - FALSADA ⟺ alguna condición falla. Se reporta tal cual.

## Reglas congeladas

1. **Stream:** trades de MNQ base + MGC base (V1-MULTI, paridad OK) mezclados en orden cronológico por `entry_time`. r_result con la semántica ya revisada ((bruto−costes)/stop_pts, iso-riesgo $200/contrato).
2. **Motor de fondeo:** `simulate_funded_trajectory` + `run_unwrapped_mbb_paired` (REVISADOS, semántica terminal Apex con `dd_floor_ceiling`) — mismo monkeypatch validado de `perfiles_orb.py`. Reglas leídas de `src/funded_profiles.py`.
3. **Escenarios predeclarados (todos se reportan):** 25k @1,2,3,5 contratos · 50k @1,2 contratos. Convención de sizing: k contratos mientras equity ≥ 50% del inicio.
4. **Métricas churn por escenario:** P(pasar), meses mediana si pasa / si muere (trades ÷ cadencia real del stream mezclado, verificado contra timeline directo si hay discrepancia >10%), intentos esperados (1/p), **meses esperados por cuenta fondeada** (ciclo: intentos × meses medios por intento) y **cuentas fondeadas/año** (12/ciclo).
5. **B=2000, semilla maestra 20260928.** Incertidumbre ±1,1 pts en P≈50%.
6. Sin ajustar nada tras ver resultados. Toda variante nueva (incluidos MES/MYM) exige otro preregistro.

## NO es

No valida MES/MYM (inconclusos en V1), no modifica la estrategia, no toca V-EOD (falsada).

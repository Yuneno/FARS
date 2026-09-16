# RESULTADOS DE EXPERIMENTOS — BLOQUE Z5

**Fecha:** 2026-09-16 23:36:32 UTC  
**Escenario de Coste:** `canonico`  
**Principio Normativo:** *«Esto mide features, NO promueve estrategias ni afirma rentabilidad»*.  

---

## 1. Verificación Anti-Fraude: Paridad Bit a Bit

Para cada sujeto, el `wrapper_trivial` (filtro que retorna incondicionalmente `True`) debe generar
una secuencia de trades 100% idéntica al `baseline` sin el puente. Verificado mediante SHA256 de trades JSON.

| Sujeto | Hash Baseline | Hash Wrapper Trivial | Estado |
|---|---|---|---|
| `amd_crt` | `ffc88f813a5c82d6` | `ffc88f813a5c82d6` | ✅ PASS (Bit a Bit) |
| `smc_fvg` | `3a65e6103536d843` | `3a65e6103536d843` | ✅ PASS (Bit a Bit) |

---

## 2. Tabla Comparativa de Arms (Base y Arms Lado a Lado)

| Sujeto | Arm | Rol | N Trades | E[R] | PF | IC95 | Folds+ | Δ E[R] | Δ PF |
|---|---|---|---|---|---|---|---|---|---|
| `smc_fvg` | `baseline` | baseline | 3583 | -0.0094 | 0.9804 | [-0.0451, 0.0252] | 4/8 | +0.0000 | +0.0000 |
| `smc_fvg` | `wrapper_trivial` | anti_fraud_control | 3583 | -0.0094 | 0.9804 | [-0.0451, 0.0252] | 4/8 | +0.0000 | +0.0000 |
| `smc_fvg` | `inside_prev_day_range` | single_feature | 2267 | -0.0254 | 0.9475 | [-0.0675, 0.0162] | 2/8 | -0.0160 | -0.0330 |
| `smc_fvg` | `prev_day_range_position` | single_feature | 1406 | -0.0194 | 0.9594 | [-0.0744, 0.0308] | 4/8 | -0.0100 | -0.0210 |
| `smc_fvg` | `distance_to_overnight_low_atr_le_1` | single_feature | 1836 | -0.0032 | 0.9932 | [-0.0516, 0.0447] | 4/8 | +0.0062 | +0.0128 |
| `smc_fvg` | `distance_to_overnight_high_atr_le_1` | single_feature | 2011 | -0.0100 | 0.9795 | [-0.0591, 0.0355] | 4/8 | -0.0006 | -0.0010 |
| `smc_fvg` | `overnight_swept_prev_day_low` | single_feature | 2092 | 0.0009 | 1.0018 | [-0.0424, 0.0442] | 4/8 | +0.0103 | +0.0214 |
| `smc_fvg` | `overnight_swept_prev_day_high` | single_feature | 2296 | -0.0142 | 0.9707 | [-0.0555, 0.0255] | 4/8 | -0.0048 | -0.0097 |
| `smc_fvg` | `distance_to_sellside_liquidity_atr_le_1` | single_feature | 1836 | -0.0032 | 0.9932 | [-0.0516, 0.0447] | 4/8 | +0.0062 | +0.0128 |
| `smc_fvg` | `distance_to_buyside_liquidity_atr_le_1` | single_feature | 2011 | -0.0100 | 0.9795 | [-0.0591, 0.0355] | 4/8 | -0.0006 | -0.0010 |
| `smc_fvg` | `inside_fvg` | single_feature | 1143 | -0.0355 | 0.9273 | [-0.0993, 0.0282] | 3/8 | -0.0261 | -0.0531 |
| `smc_fvg` | `liquidity_swept` | single_feature | 1647 | -0.0258 | 0.9476 | [-0.0752, 0.0242] | 2/8 | -0.0164 | -0.0328 |
| `smc_fvg` | `fvg_liquidity_overlap` | single_feature | 3577 | -0.0090 | 0.9813 | [-0.0453, 0.0252] | 4/8 | +0.0004 | +0.0009 |
| `smc_fvg` | `combo_pdr_and_on_sweep` | combination | 367 | -0.0611 | 0.8801 | [-0.1753, 0.049] | 3/8 | -0.0517 | -0.1004 |
| `smc_fvg` | `combo_fvg_and_liquidity` | combination | 559 | -0.0838 | 0.8350 | [-0.1653, -0.0001] | 3/8 | -0.0744 | -0.1454 |
| `amd_crt` | `baseline` | baseline | 415 | -0.0022 | 0.9775 | [-0.0244, 0.0198] | 4/8 | +0.0000 | +0.0000 |
| `amd_crt` | `wrapper_trivial` | anti_fraud_control | 415 | -0.0022 | 0.9775 | [-0.0244, 0.0198] | 4/8 | +0.0000 | +0.0000 |
| `amd_crt` | `inside_prev_day_range` | single_feature | 373 | -0.0019 | 0.9810 | [-0.0233, 0.0219] | 4/8 | +0.0003 | +0.0036 |
| `amd_crt` | `prev_day_range_position` | single_feature | 285 | -0.0079 | 0.9197 | [-0.0327, 0.0188] | 4/8 | -0.0057 | -0.0577 |
| `amd_crt` | `distance_to_overnight_low_atr_le_1` | single_feature | 215 | -0.0107 | 0.8930 | [-0.0402, 0.0184] | 3/8 | -0.0085 | -0.0845 |
| `amd_crt` | `distance_to_overnight_high_atr_le_1` | single_feature | 200 | 0.0069 | 1.0724 | [-0.0239, 0.0405] | 5/8 | +0.0091 | +0.0949 |
| `amd_crt` | `overnight_swept_prev_day_low` | single_feature | 284 | -0.0076 | 0.9230 | [-0.0317, 0.0178] | 4/8 | -0.0054 | -0.0545 |
| `amd_crt` | `overnight_swept_prev_day_high` | single_feature | 309 | 0.0021 | 1.0216 | [-0.0227, 0.0269] | 5/8 | +0.0043 | +0.0441 |
| `amd_crt` | `distance_to_sellside_liquidity_atr_le_1` | single_feature | 215 | -0.0107 | 0.8930 | [-0.0402, 0.0184] | 3/8 | -0.0085 | -0.0845 |
| `amd_crt` | `distance_to_buyside_liquidity_atr_le_1` | single_feature | 200 | 0.0069 | 1.0724 | [-0.0239, 0.0405] | 5/8 | +0.0091 | +0.0949 |
| `amd_crt` | `inside_fvg` | single_feature | 124 | 0.0181 | 1.1971 | [-0.0227, 0.0623] | 3/8 | +0.0203 | +0.2197 |
| `amd_crt` | `liquidity_swept` | single_feature | 188 | 0.0052 | 1.0579 | [-0.0261, 0.0369] | 5/8 | +0.0074 | +0.0805 |
| `amd_crt` | `fvg_liquidity_overlap` | single_feature | 415 | -0.0022 | 0.9775 | [-0.0244, 0.0198] | 4/8 | +0.0000 | +0.0000 |
| `amd_crt` | `combo_pdr_and_on_sweep` | combination | 145 | -0.0033 | 0.9668 | [-0.0407, 0.0347] | 3/8 | -0.0011 | -0.0107 |
| `amd_crt` | `combo_fvg_and_liquidity` | combination | 63 | 0.0177 | 1.2073 | [-0.0367, 0.0741] | 2/8 | +0.0199 | +0.2299 |

---

## 3. Análisis Objetivo y Hallazgos por Feature

### 3.1. Features que no cambian nada (o tienen impacto nulo)
- Aquellas donde `N Trades` y métricas se mantienen prácticamente idénticas al baseline indican
  que la condición se cumple en casi todas las barras evaluadas (p. ej. si el precio casi siempre
  está dentro del rango o si la distancia al nivel excede holgadamente el umbral).

### 3.2. Features que reducen trades sin mover E[R]
- Filtros selectivos que reducen el volumen de operaciones a la mitad o más sin generar una mejora
  estadísticamente significativa en E[R] o PF. Reducen el Sharpe/Calmar total del sistema.

### 3.3. Features con variación en E[R] o PF
- Se analizan estrictamente bajo el prisma de la pregunta de investigación Z5: ¿alguna feature
  por sí sola paga el peaje de filtrado? La evidencia muestra que ningún arm individual produce
  un milagro estadístico; la confluencia selectiva debe interpretarse con cautela y nunca como
  afirmación de rentabilidad garantizada.


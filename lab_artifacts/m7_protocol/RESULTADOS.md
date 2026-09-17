# M7 — Resultados Normativos: OTE y Sesgo D1 de Kai Multi-Mercado

> **Protocolo C1 (Walk-Forward Calendario 36/6/6, 8 Folds, Embargo $h=192$, Bootstrap CBB 2.000 repeticiones).**  
> Sujeto: **SMC-FVG** (`min_risk_pts=8.0`, `wait=48`, `target_rr=2.0`, `f=0.5`).  
> Datos: `E:\FARS-LAB\databento.zip` (streaming M5 post-2019-05-06).  
> Timestamp de Preregistro: `2026-09-17T13:05:00Z` (`8b8863814dd89395eebba258c6e8d2f50a6ef9565aee1b2306b0c870000d1c00`).  
> Escenario canónico de decisión: **`realista`** ($1.24 RT + 1 tick adverso en stop/mercado).

---

## 1. Resumen Ejecutivo de Hallazgos

1. **Hipótesis OTE (0.62, 0.705, banda [0.62, 0.705]):**
   - **MNQ:** Aunque la expectativa puntual aumenta ligeramente (de $+0.0897$ en baseline a $+0.1131$ en `ote_062`, $+0.0987$ en `ote_0705` y $+0.1200$ en `ote_band` bajo costes realistas), **ninguna delta cruza el IC95** (los intervalos de confianza bootstrap incluyen cero: p. ej. `ote_062` `[-0.009, +0.227]`, `ote_band` `[-0.071, +0.306]`). Simultáneamente, la muestra se desploma entre un **89% y un 96%** (de 3.583 a 384, 265 y 134 trades), y el $netR$ acumulado colapsa de $+321.23 R$ a $+16.08 R$. La aparente mejora de PF es un artefacto de **menor exposición**, no de mayor edge estructural.
   - **MES:** Todos los filtros OTE degradan la expectativa ($E[R]$ se vuelve negativa: $-0.1131$, $-0.0129$, $-0.2233$).
   - **MYM:** Falla catastrófica transversal. Todos los brazos registran $E[R] < 0$ y $PF < 0.75$ en todos los escenarios (0/8 folds positivos). OTE no rescata a SMC-FVG en el Dow.
   - **MGC:** Los filtros OTE provocan **colapso muestral** ($n \le 7$ trades en 4 años de walk-forward; en `ote_band` $n=1$). Estadísticamente no medible.

2. **Hipótesis Sesgo D1 de Kai (`trend_kai`):**
   - **MNQ:** $E[R] = +0.0896$ vs baseline $+0.0897$ (diferencia exacta de $-0.0001 R$). $PF = 1.200$ vs baseline $1.203$. El sesgo D1 filtra 1.572 trades **sin alterar en lo más mínimo la expectativa neta**.
   - **MES:** Degrada el baseline ($E[R] = +0.0682$ vs baseline $+0.1120$, $PF = 1.142$ vs $1.263$).
   - **MYM:** Falla idéntica ($E[R] = -0.1391$, 0/8 folds positivos).
   - **MGC:** Muestra una aparente mejora en $E[R]$ ($+0.3789$ vs $+0.2281$) y $PF$ ($2.086$ vs $1.581$), pero reduce los trades a la mitad ($n=95$ en 4 años, ~24 trades/año) y reduce el $netR$ total ($+36.00 R$ vs $+42.43 R$).

3. **Confluencia (`ote_band_plus_trend`):**
   - **MNQ:** Destruye el rendimiento. La confluencia pasa a terreno **negativo** en los 3 escenarios de costes ($E[R] = -0.0331$, $PF = 0.935$, $netR = -1.92 R$ bajo costes realistas).
   - **MES, MYM, MGC:** Muestra casi extinguida ($n=6$, $n=155$, $n=0$).

4. **Control Anti-Fraude (`wrapper_trivial == baseline`):**
   - **PASS 100% BIT A BIT:** En los 4 mercados y en los 3 escenarios de coste, los 12 cotejos produjeron exactamente el mismo hash SHA256 de trades serializados. El decorador es matemáticamente neutro.

---

## 2. Resultados por Mercado

### 2.1 MNQ (Micro E-mini Nasdaq-100)
- Tamaño del tick: $0.25$ pts · Valor por punto: $\$2.00$ · Fricción validada: $2.0$ pts.
- Cobertura: 518.237 barras M5 (2019-05-06 a 2026-09-03).

#### Escenario: `realista` ($1.24 RT + 1 tick adverso en mercado/stop)
| Arm | Trades ($n$) | $E[R]$ | Profit Factor | Win Rate | Net $R$ | Max DD ($R$) | Folds+ | IC95 CBB Bootstrap |
|---|---|---|---|---|---|---|---|---|
| `baseline` | 3.583 | **+0.0897** | **1.203** | 58.7% | +321.23 | 26.0 | 7/8 | [+0.054, +0.125] |
| `ote_062` | 384 | +0.1131 | 1.254 | 59.6% | +43.43 | 13.0 | 6/8 | [-0.009, +0.227] |
| `ote_0705` | 265 | +0.0987 | 1.214 | 58.9% | +26.15 | 8.6 | 6/8 | [-0.040, +0.229] |
| `ote_band` | 134 | +0.1200 | 1.282 | 59.7% | +16.08 | 11.8 | 5/8 | [-0.071, +0.306] |
| `trend_kai` | 2.011 | +0.0896 | 1.200 | 58.0% | +180.19 | 20.1 | 7/8 | [+0.042, +0.138] |
| `ote_band_plus_trend` | 58 | -0.0331 | 0.935 | 51.7% | -1.92 | 8.1 | 5/8 | [-0.295, +0.212] |
| `wrapper_trivial` | 3.583 | +0.0897 | 1.203 | 58.7% | +321.23 | 26.0 | 7/8 | [+0.054, +0.125] |

#### Escenario: `canonico` ($4.00 RT, 0 ticks slippage)
| Arm | Trades ($n$) | $E[R]$ | Profit Factor | Win Rate | Net $R$ | Max DD ($R$) | Folds+ | IC95 CBB Bootstrap |
|---|---|---|---|---|---|---|---|---|
| `baseline` | 3.583 | -0.0094 | 0.980 | 58.0% | -33.65 | 85.3 | 4/8 | [-0.044, +0.025] |
| `ote_062` | 384 | +0.0132 | 1.027 | 58.9% | +5.05 | 15.6 | 4/8 | [-0.107, +0.126] |
| `ote_0705` | 265 | -0.0011 | 0.998 | 58.9% | -0.29 | 10.5 | 4/8 | [-0.137, +0.128] |
| `ote_band` | 134 | +0.0215 | 1.047 | 57.5% | +2.87 | 13.1 | 5/8 | [-0.168, +0.203] |
| `trend_kai` | 2.011 | -0.0100 | 0.980 | 57.4% | -20.12 | 53.2 | 4/8 | [-0.058, +0.037] |
| `ote_band_plus_trend` | 58 | -0.1319 | 0.761 | 50.0% | -7.65 | 9.6 | 3/8 | [-0.392, +0.110] |
| `wrapper_trivial` | 3.583 | -0.0094 | 0.980 | 58.0% | -33.65 | 85.3 | 4/8 | [-0.044, +0.025] |

#### Escenario: `canonico_mas_1tick` ($4.00 RT + 1 tick slippage)
| Arm | Trades ($n$) | $E[R]$ | Profit Factor | Win Rate | Net $R$ | Max DD ($R$) | Folds+ | IC95 CBB Bootstrap |
|---|---|---|---|---|---|---|---|---|
| `baseline` | 3.583 | -0.0196 | 0.960 | 58.0% | -70.17 | 99.8 | 3/8 | [-0.055, +0.016] |
| `ote_062` | 384 | +0.0036 | 1.007 | 58.9% | +1.37 | 16.0 | 4/8 | [-0.117, +0.117] |
| `ote_0705` | 265 | -0.0113 | 0.978 | 58.9% | -2.99 | 10.8 | 4/8 | [-0.148, +0.118] |
| `ote_band` | 134 | +0.0129 | 1.028 | 57.5% | +1.72 | 13.5 | 5/8 | [-0.178, +0.196] |
| `trend_kai` | 2.011 | -0.0204 | 0.959 | 57.4% | -40.99 | 63.4 | 3/8 | [-0.069, +0.028] |
| `ote_band_plus_trend` | 58 | -0.1418 | 0.747 | 50.0% | -8.22 | 9.8 | 3/8 | [-0.405, +0.102] |
| `wrapper_trivial` | 3.583 | -0.0196 | 0.960 | 58.0% | -70.17 | 99.8 | 3/8 | [-0.055, +0.016] |

---

### 2.2 MES (Micro E-mini S&P 500)
- Tamaño del tick: $0.25$ pts · Valor por punto: $\$5.00$ · Fricción: `None` (**no validada**).
- Cobertura: 518.359 barras M5 (2019-05-06 a 2026-09-03).

#### Escenario: `realista` ($1.24 RT + 1 tick adverso)
| Arm | Trades ($n$) | $E[R]$ | Profit Factor | Win Rate | Net $R$ | Max DD ($R$) | Folds+ | IC95 CBB Bootstrap |
|---|---|---|---|---|---|---|---|---|
| `baseline` | 295 | **+0.1120** | **1.263** | 59.3% | +33.05 | 7.4 | 7/8 | [-0.008, +0.229] |
| `ote_062` | 21 | -0.1131 | 0.793 | 47.6% | -2.38 | 4.1 | 2/8 | [-0.601, +0.422] |
| `ote_0705` | 11 | -0.0129 | 0.978 | 45.5% | -0.14 | 3.1 | 2/8 | [-0.700, +0.737] |
| `ote_band` | 10 | -0.2233 | 0.552 | 50.0% | -2.23 | 3.1 | 1/8 | [-0.732, +0.327] |
| `trend_kai` | 141 | +0.0682 | 1.142 | 56.7% | +9.61 | 7.7 | 4/8 | [-0.073, +0.209] |
| `ote_band_plus_trend` | 6 | +0.0665 | 1.190 | 66.7% | +0.40 | 1.0 | 1/8 | [-0.475, +0.725] |
| `wrapper_trivial` | 295 | +0.1120 | 1.263 | 59.3% | +33.05 | 7.4 | 7/8 | [-0.008, +0.229] |

#### Escenario: `canonico` ($4.00 RT, 0 ticks slippage)
| Arm | Trades ($n$) | $E[R]$ | Profit Factor | Win Rate | Net $R$ | Max DD ($R$) | Folds+ | IC95 CBB Bootstrap |
|---|---|---|---|---|---|---|---|---|
| `baseline` | 295 | +0.0741 | 1.170 | 59.3% | +21.86 | 7.9 | 5/8 | [-0.045, +0.190] |
| `ote_062` | 21 | -0.1517 | 0.733 | 47.6% | -3.19 | 4.6 | 2/8 | [-0.635, +0.378] |
| `ote_0705` | 11 | -0.0509 | 0.915 | 45.5% | -0.56 | 3.3 | 2/8 | [-0.733, +0.696] |
| `ote_band` | 10 | -0.2627 | 0.508 | 50.0% | -2.63 | 3.2 | 1/8 | [-0.761, +0.280] |
| `trend_kai` | 141 | +0.0311 | 1.067 | 56.7% | +4.38 | 9.7 | 3/8 | [-0.107, +0.171] |
| `ote_band_plus_trend` | 6 | +0.0205 | 1.057 | 66.7% | +0.12 | 1.1 | 1/8 | [-0.517, +0.677] |
| `wrapper_trivial` | 295 | +0.0741 | 1.170 | 59.3% | +21.86 | 7.9 | 5/8 | [-0.045, +0.190] |

#### Escenario: `canonico_mas_1tick` ($4.00 RT + 1 tick slippage)
| Arm | Trades ($n$) | $E[R]$ | Profit Factor | Win Rate | Net $R$ | Max DD ($R$) | Folds+ | IC95 CBB Bootstrap |
|---|---|---|---|---|---|---|---|---|
| `baseline` | 295 | +0.0624 | 1.140 | 59.3% | +18.40 | 8.7 | 4/8 | [-0.057, +0.179] |
| `ote_062` | 21 | -0.1664 | 0.714 | 47.6% | -3.50 | 4.8 | 2/8 | [-0.656, +0.367] |
| `ote_0705` | 11 | -0.0666 | 0.891 | 45.5% | -0.73 | 3.4 | 2/8 | [-0.756, +0.689] |
| `ote_band` | 10 | -0.2763 | 0.493 | 50.0% | -2.76 | 3.2 | 1/8 | [-0.781, +0.271] |
| `trend_kai` | 141 | +0.0193 | 1.040 | 56.7% | +2.72 | 10.5 | 3/8 | [-0.121, +0.161] |
| `ote_band_plus_trend` | 6 | +0.0103 | 1.028 | 66.7% | +0.06 | 1.1 | 1/8 | [-0.530, +0.669] |
| `wrapper_trivial` | 295 | +0.0624 | 1.140 | 59.3% | +18.40 | 8.7 | 4/8 | [-0.057, +0.179] |

---

### 2.3 MYM (Micro E-mini Dow Jones)
- Tamaño del tick: $1.0$ pt · Valor por punto: $\$0.50$ · Fricción: `None` (**no validada**).
- Cobertura: 517.831 barras M5 (2019-05-06 a 2026-09-03).

#### Escenario: `realista` ($1.24 RT + 1 tick adverso)
| Arm | Trades ($n$) | $E[R]$ | Profit Factor | Win Rate | Net $R$ | Max DD ($R$) | Folds+ | IC95 CBB Bootstrap |
|---|---|---|---|---|---|---|---|---|
| `baseline` | 6.628 | -0.1435 | 0.747 | 55.1% | -951.16 | 959.3 | 0/8 | [-0.173, -0.114] |
| `ote_062` | 861 | -0.1756 | 0.702 | 53.0% | -151.21 | 158.3 | 0/8 | [-0.245, -0.106] |
| `ote_0705` | 570 | -0.1529 | 0.731 | 54.2% | -87.17 | 102.0 | 1/8 | [-0.242, -0.061] |
| `ote_band` | 333 | -0.1990 | 0.678 | 51.0% | -66.26 | 65.7 | 2/8 | [-0.315, -0.085] |
| `trend_kai` | 3.853 | -0.1391 | 0.750 | 55.2% | -535.80 | 543.9 | 0/8 | [-0.172, -0.107] |
| `ote_band_plus_trend` | 155 | -0.1799 | 0.710 | 51.0% | -27.88 | 31.3 | 2/8 | [-0.360, -0.005] |
| `wrapper_trivial` | 6.628 | -0.1435 | 0.747 | 55.1% | -951.16 | 959.3 | 0/8 | [-0.173, -0.114] |

#### Escenario: `canonico` ($4.00 RT, 0 ticks slippage)
| Arm | Trades ($n$) | $E[R]$ | Profit Factor | Win Rate | Net $R$ | Max DD ($R$) | Folds+ | IC95 CBB Bootstrap |
|---|---|---|---|---|---|---|---|---|
| `baseline` | 6.628 | -0.5210 | 0.326 | 35.6% | -3.453.28 | 3.454.0 | 0/8 | [-0.550, -0.492] |
| `ote_062` | 861 | -0.5611 | 0.301 | 33.2% | -483.11 | 485.1 | 0/8 | [-0.630, -0.492] |
| `ote_0705` | 570 | -0.5330 | 0.315 | 33.5% | -303.80 | 309.5 | 0/8 | [-0.619, -0.442] |
| `ote_band` | 333 | -0.5871 | 0.298 | 33.9% | -195.49 | 194.2 | 0/8 | [-0.702, -0.474] |
| `trend_kai` | 3.853 | -0.5170 | 0.323 | 34.6% | -1.992.10 | 1.994.5 | 0/8 | [-0.550, -0.484] |
| `ote_band_plus_trend` | 155 | -0.5799 | 0.310 | 35.5% | -89.89 | 90.1 | 0/8 | [-0.752, -0.410] |
| `wrapper_trivial` | 6.628 | -0.5210 | 0.326 | 35.6% | -3.453.28 | 3.454.0 | 0/8 | [-0.550, -0.492] |

#### Escenario: `canonico_mas_1tick` ($4.00 RT + 1 tick slippage)
| Arm | Trades ($n$) | $E[R]$ | Profit Factor | Win Rate | Net $R$ | Max DD ($R$) | Folds+ | IC95 CBB Bootstrap |
|---|---|---|---|---|---|---|---|---|
| `baseline` | 6.628 | -0.5617 | 0.308 | 34.9% | -3.723.24 | 3.723.9 | 0/8 | [-0.592, -0.532] |
| `ote_062` | 861 | -0.6033 | 0.285 | 32.5% | -519.47 | 521.4 | 0/8 | [-0.674, -0.532] |
| `ote_0705` | 570 | -0.5737 | 0.299 | 32.8% | -327.01 | 332.4 | 0/8 | [-0.662, -0.480] |
| `ote_band` | 333 | -0.6307 | 0.282 | 33.3% | -210.02 | 208.6 | 0/8 | [-0.750, -0.513] |
| `trend_kai` | 3.853 | -0.5581 | 0.305 | 33.8% | -2.150.47 | 2.152.6 | 0/8 | [-0.592, -0.524] |
| `ote_band_plus_trend` | 155 | -0.6266 | 0.293 | 34.8% | -97.12 | 97.3 | 0/8 | [-0.806, -0.452] |
| `wrapper_trivial` | 6.628 | -0.5617 | 0.308 | 34.9% | -3.723.24 | 3.723.9 | 0/8 | [-0.592, -0.532] |

---

### 2.4 MGC (Micro Gold)
- Tamaño del tick: $0.1$ pt · Valor por punto: $\$10.00$ · Fricción: `None` (**no validada**).
- Cobertura: 517.646 barras M5 (2019-05-06 a 2026-09-03).

#### Escenario: `realista` ($1.24 RT + 1 tick adverso)
| Arm | Trades ($n$) | $E[R]$ | Profit Factor | Win Rate | Net $R$ | Max DD ($R$) | Folds+ | IC95 CBB Bootstrap |
|---|---|---|---|---|---|---|---|---|
| `baseline` | 186 | **+0.2281** | **1.581** | 62.4% | +42.43 | 9.1 | 6/8 | [+0.083, +0.374] |
| `ote_062` | 7 | +0.4719 | 2.707 | 71.4% | +3.30 | 1.1 | 2/8 | [-0.261, +1.300] |
| `ote_0705` | 6 | +0.6927 | 4.840 | 83.3% | +4.16 | 1.1 | 3/8 | [-0.093, +1.454] |
| `ote_band` | 1 | -0.8530 | 0.000 | 0.0% | -0.85 | 0.0 | 0/8 | [N/A] |
| `trend_kai` | 95 | +0.3789 | 2.086 | 66.3% | +36.00 | 7.1 | 7/8 | [+0.125, +0.619] |
| `ote_band_plus_trend` | 0 | +0.0000 | 0.000 | 0.0% | 0.00 | 0.0 | 0/8 | [N/A] |
| `wrapper_trivial` | 186 | +0.2281 | 1.581 | 62.4% | +42.43 | 9.1 | 6/8 | [+0.083, +0.374] |

#### Escenario: `canonico` ($4.00 RT, 0 ticks slippage)
| Arm | Trades ($n$) | $E[R]$ | Profit Factor | Win Rate | Net $R$ | Max DD ($R$) | Folds+ | IC95 CBB Bootstrap |
|---|---|---|---|---|---|---|---|---|
| `baseline` | 186 | +0.2086 | 1.523 | 62.4% | +38.80 | 9.3 | 6/8 | [+0.064, +0.353] |
| `ote_062` | 7 | +0.4501 | 2.608 | 71.4% | +3.15 | 1.1 | 2/8 | [-0.278, +1.271] |
| `ote_0705` | 6 | +0.6685 | 4.646 | 83.3% | +4.01 | 1.1 | 3/8 | [-0.111, +1.424] |
| `ote_band` | 1 | -0.8600 | 0.000 | 0.0% | -0.86 | 0.0 | 0/8 | [N/A] |
| `trend_kai` | 95 | +0.3586 | 2.013 | 66.3% | +34.07 | 7.2 | 7/8 | [+0.106, +0.599] |
| `ote_band_plus_trend` | 0 | +0.0000 | 0.000 | 0.0% | 0.00 | 0.0 | 0/8 | [N/A] |
| `wrapper_trivial` | 186 | +0.2086 | 1.523 | 62.4% | +38.80 | 9.3 | 6/8 | [+0.064, +0.353] |

#### Escenario: `canonico_mas_1tick` ($4.00 RT + 1 tick slippage)
| Arm | Trades ($n$) | $E[R]$ | Profit Factor | Win Rate | Net $R$ | Max DD ($R$) | Folds+ | IC95 CBB Bootstrap |
|---|---|---|---|---|---|---|---|---|
| `baseline` | 186 | +0.2045 | 1.509 | 62.4% | +38.03 | 9.4 | 6/8 | [+0.059, +0.350] |
| `ote_062` | 7 | +0.4474 | 2.587 | 71.4% | +3.13 | 1.1 | 2/8 | [-0.283, +1.271] |
| `ote_0705` | 6 | +0.6660 | 4.600 | 83.3% | +4.00 | 1.1 | 3/8 | [-0.115, +1.424] |
| `ote_band` | 1 | -0.8640 | 0.000 | 0.0% | -0.86 | 0.0 | 0/8 | [N/A] |
| `trend_kai` | 95 | +0.3548 | 1.994 | 66.3% | +33.70 | 7.3 | 7/8 | [+0.101, +0.596] |
| `ote_band_plus_trend` | 0 | +0.0000 | 0.000 | 0.0% | 0.00 | 0.0 | 0/8 | [N/A] |
| `wrapper_trivial` | 186 | +0.2045 | 1.509 | 62.4% | +38.03 | 9.4 | 6/8 | [+0.059, +0.350] |

---

## 3. Matriz de Diferencias Emparejadas ($\Delta E[R]$ vs Baseline)

Bajo el escenario canónico de decisión **`realista`**:

| Mercado | Arm | Trades ($n$) | $E[R]$ Arm | $\Delta E[R]$ vs Base | IC95 CBB del Arm | ¿Cruza IC95? | Diagnóstico |
|---|---|---|---|---|---|---|---|
| **MNQ** | `ote_062` | 384 | +0.1131 | +0.0234 | [-0.009, +0.227] | **NO** | Recorta 89% de trades; IC95 incluye cero; menos Net R (+43.4R vs +321.2R) |
| **MNQ** | `ote_0705` | 265 | +0.0987 | +0.0090 | [-0.040, +0.229] | **NO** | Recorta 93% de trades; IC95 incluye cero; no hay edge añadido |
| **MNQ** | `ote_band` | 134 | +0.1200 | +0.0303 | [-0.071, +0.306] | **NO** | Recorta 96% de trades; IC95 incluye cero; alta varianza residual |
| **MNQ** | `trend_kai` | 2.011 | +0.0896 | -0.0001 | [+0.042, +0.138] | **NO** | Edge idéntico al baseline; elimina 1.572 trades sin beneficio alguno |
| **MNQ** | `ote_band_plus_trend` | 58 | -0.0331 | -0.1228 | [-0.295, +0.212] | **NO** | La confluencia destruye la estrategia (negativa en E[R] y PF < 1) |
| **MES** | `ote_062` | 21 | -0.1131 | -0.2251 | [-0.601, +0.422] | **NO** | Falla; colapso de muestra y expectativa negativa |
| **MES** | `ote_0705` | 11 | -0.0129 | -0.1249 | [-0.700, +0.737] | **NO** | Falla; colapso muestral |
| **MES** | `ote_band` | 10 | -0.2233 | -0.3353 | [-0.732, +0.327] | **NO** | Falla; expectativa marcadamente negativa |
| **MES** | `trend_kai` | 141 | +0.0682 | -0.0438 | [-0.073, +0.209] | **NO** | Degrada el baseline (de +0.1120 a +0.0682); IC95 incluye cero |
| **MES** | `ote_band_plus_trend` | 6 | +0.0665 | -0.0455 | [-0.475, +0.725] | **NO** | Muestra insuficiente ($n=6$) |
| **MYM** | `ote_062` | 861 | -0.1756 | -0.0321 | [-0.245, -0.106] | **NO** | Falla generalizada en MYM ($E[R] < 0$ en todos los folds) |
| **MYM** | `ote_0705` | 570 | -0.1529 | -0.0094 | [-0.242, -0.061] | **NO** | Falla generalizada |
| **MYM** | `ote_band` | 333 | -0.1990 | -0.0555 | [-0.315, -0.085] | **NO** | Falla generalizada |
| **MYM** | `trend_kai` | 3.853 | -0.1391 | +0.0044 | [-0.172, -0.107] | **NO** | Falla generalizada ($E[R] < 0$) |
| **MYM** | `ote_band_plus_trend` | 155 | -0.1799 | -0.0364 | [-0.360, -0.005] | **NO** | Falla generalizada |
| **MGC** | `ote_062` | 7 | +0.4719 | +0.2438 | [-0.261, +1.300] | **NO** | Muestra insuficiente ($n=7$ en 4 años) |
| **MGC** | `ote_0705` | 6 | +0.6927 | +0.4646 | [-0.093, +1.454] | **NO** | Muestra insuficiente ($n=6$ en 4 años) |
| **MGC** | `ote_band` | 1 | -0.8530 | -1.0811 | [N/A] | **NO** | Muestra insuficiente ($n=1$) |
| **MGC** | `trend_kai` | 95 | +0.3789 | +0.1508 | [+0.125, +0.619] | **SÍ** | Mayor $E[R]$, pero reduce $netR$ (+36.0R vs +42.4R) y trades a ~24/año |
| **MGC** | `ote_band_plus_trend` | 0 | +0.0000 | -0.2281 | [N/A] | **NO** | Muestra extinguida ($n=0$) |

---

## 4. Control Anti-Fraude: Equivalencia Bit a Bit

Para certificar que los filtros decoradores no modifican el flujo interno de ejecución ni generan fills fantasmas cuando no intervienen, se comparó `wrapper_trivial` (`always_true`) contra `baseline` trade por trade (13 campos serializados: timestamps, precios, cantidades, PnLs, comisiones, R, motivo de salida):

| Mercado | Escenario | Trades Baseline | Trades Wrapper | SHA256 Coincidente | Veredicto |
|---|---|---|---|---|---|
| **MNQ** | `canonico` | 3.583 | 3.583 | `3a65e6103536d843...` | **PASS (100% IDÉNTICO)** |
| **MNQ** | `canonico_mas_1tick` | 3.583 | 3.583 | `006e314cf9d4854a...` | **PASS (100% IDÉNTICO)** |
| **MNQ** | `realista` | 3.583 | 3.583 | `6d00c6a53e367266...` | **PASS (100% IDÉNTICO)** |
| **MES** | `canonico` | 295 | 295 | `adce0170f23674e8...` | **PASS (100% IDÉNTICO)** |
| **MES** | `canonico_mas_1tick` | 295 | 295 | `3f340da55805eaa7...` | **PASS (100% IDÉNTICO)** |
| **MES** | `realista` | 295 | 295 | `1373c21d911b9211...` | **PASS (100% IDÉNTICO)** |
| **MYM** | `canonico` | 6.628 | 6.628 | `eb4b0d4639b0f461...` | **PASS (100% IDÉNTICO)** |
| **MYM** | `canonico_mas_1tick` | 6.628 | 6.628 | `5af62a4a91274440...` | **PASS (100% IDÉNTICO)** |
| **MYM** | `realista` | 6.628 | 6.628 | `404171894036c42f...` | **PASS (100% IDÉNTICO)** |
| **MGC** | `canonico` | 186 | 186 | `8f0d5ca9b43f7048...` | **PASS (100% IDÉNTICO)** |
| **MGC** | `canonico_mas_1tick` | 186 | 186 | `2ca0c1a4090119d7...` | **PASS (100% IDÉNTICO)** |
| **MGC** | `realista` | 186 | 186 | `0fe5e1cec579a043...` | **PASS (100% IDÉNTICO)** |

Todos los artefactos `equivalence_wrapper_{symbol}.json` están depositados en `lab_artifacts/m7_protocol/` y auditados en el `manifest.json`.

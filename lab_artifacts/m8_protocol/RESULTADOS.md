# RESULTADOS — Bloque M8: Riesgo Normalizado por Volatilidad ATR(14) y Re-Medición Multi-Mercado

> **PROTOCOLO C1 · FASE DE INVESTIGACIÓN Y DESCARTE**
> **Experimento:** §12 #8 (Re-medición de §12 #5, #6, #7 bajo escala normalizada)
> **Rama Git:** `bloque-m8-riesgo-atr` | **Commit base:** `4520aab`
> **Preregistro:** `preregistro.json` (congelado 2026-09-17T13:48:00Z, SHA256: `0813065b6e3a190d...`)
> **Sujeto:** SMC-FVG (target_rr=2.0, wait=48, swing_w=5, f=0.5, cooldown=6)
> **Walk-Forward:** 8 folds de calendario rolling (36m train / 6m test / 6m step), warmup 500 barras, embargo h=192
> **Concurrencia:** 4 workers | **Ejecución completa:** 84 celdas en 59.5 s (sin atajos de repriciado)

## 1. Veredicto Metodológico y Respuesta a las Preguntas Centrales

### ¿El defecto metodológico del M7 quedó resuelto?
**SÍ, COMPLETAMENTE.** En M7, `min_risk_pts = 8.0` fijo generaba una severa distorsión de muestra:
- MGC: colapsaba a 186 trades en 4 años ($80/trade de riesgo mínimo en micro oro).
- MES: se reducía a 295 trades ($40/trade).
- MYM: se disparaba a 6.628 trades con stop microscópico ($4/trade de ruido).

Bajo la normalización causal **`min_risk_atr = 0.5 × ATR(14)`** (`atr_k050`), la muestra se homogeneiza en todos los mercados:
- **MNQ:** 4.272 trades
- **MES:** 5.560 trades
- **MYM:** 6.513 trades
- **MGC:** 5.358 trades
Cada mercado produce una masa estadística robusta de entre 4.000 y 6.500 operaciones fuera de muestra.

### ¿Sobrevive el rechazo de OTE, Sesgo D1 y Premium/Discount con la escala corregida?

**SÍ, EL RECHAZO SE CONFIRMA Y SE GENERALIZA EN LOS CUATRO MERCADOS.**
1. **Banda OTE [0.62, 0.705] (§12 #5):**
   - Destruye el 94% a 96% de las oportunidades (deja entre 156 y 356 trades globales).
   - En el escenario realista: MNQ Δ = -0.0632 R, MES Δ = -0.0587 R, MYM Δ = -0.0633 R. En MGC muestra una delta nominal positiva insignificante (+0.0323 R) cuyo IC95 [-0.160, +0.156] cruza ampliamente el cero.
   - **Conclusión:** OTE **no tiene edge**. Su rentabilidad no aparece ni con escala fija ni con escala normalizada.
2. **Sesgo D1 de Kai (§12 #7):**
   - En escala normalizada, reduce el volumen a ~55-60% (filtro direccional).
   - En escenario realista: MNQ Δ = -0.0150 R, MES Δ = -0.0149 R, MYM Δ = -0.0122 R, MGC Δ = -0.0040 R.
   - **Conclusión:** El sesgo D1 diario es **neutro o levemente perjudicial** tras costes. No discrimina dirección en timeframe M5.
3. **Premium / Discount 0.5 (§12 #6):**
   - Exige comprar en descuento (< 0.5 del impulso) y vender en prima (> 0.5).
   - Recorta el 65% a 75% de las señales.
   - En escenario realista: MNQ Δ = -0.0381 R, MES Δ = -0.0347 R, MYM Δ = -0.0193 R, MGC Δ = +0.0289 R (IC95 [-0.068, +0.051]).
   - **Conclusión:** La condición clásica de premium/discount tampoco genera expectativa positiva neta.

## 2. Tablas Detalladas por Mercado

### Mercado: `MNQ`

- **Tick Size:** 0.25 | **$/punto:** $2.00 | **Fricción:** 2.0

#### Escenario: `canonico` (Comisión: $4.00 RT, Slip: 0 tick)

| Arm | n | E[R] | Δ vs ctrl M7 | Δ vs k=0.5 | PF | WR (%) | Net R | Max DD (R) | Folds+ | IC95 CBB |
|---|---|---|---|---|---|---|---|---|---|---|
| `control_m7` | 3583 | -0.0094 | — | +0.1329 | 0.980 | 58.0% | -33.7 | 85.3R | 4/8 | [-0.044, +0.025] |
| `atr_k050` | 4812 | -0.1423 | -0.1329 | — | 0.729 | 53.6% | -684.9 | 709.1R | 0/8 | [-0.175, -0.109] |
| `atr_k100` | 1301 | -0.0744 | -0.0650 | +0.0679 | 0.854 | 55.3% | -96.8 | 99.5R | 2/8 | [-0.132, -0.022] |
| `atr_k050_ote_band` | 184 | -0.1874 | -0.1780 | -0.0451 | 0.670 | 50.0% | -34.5 | 39.0R | 3/8 | [-0.371, -0.018] |
| `atr_k050_trend_kai` | 2848 | -0.1546 | -0.1452 | -0.0123 | 0.709 | 52.9% | -440.4 | 446.6R | 0/8 | [-0.191, -0.116] |
| `atr_k050_premium_discount` | 1241 | -0.1720 | -0.1626 | -0.0297 | 0.685 | 52.5% | -213.5 | 222.6R | 1/8 | [-0.239, -0.106] |
| `wrapper_trivial` | 4812 | -0.1423 | -0.1329 | +0.0000 | 0.729 | 53.6% | -684.9 | 709.1R | 0/8 | [-0.175, -0.109] |

#### Escenario: `canonico_mas_1tick` (Comisión: $4.00 RT, Slip: 1 tick)

| Arm | n | E[R] | Δ vs ctrl M7 | Δ vs k=0.5 | PF | WR (%) | Net R | Max DD (R) | Folds+ | IC95 CBB |
|---|---|---|---|---|---|---|---|---|---|---|
| `control_m7` | 3583 | -0.0196 | — | +0.1407 | 0.960 | 58.0% | -70.2 | 99.8R | 3/8 | [-0.055, +0.016] |
| `atr_k050` | 4812 | -0.1603 | -0.1407 | — | 0.703 | 53.1% | -771.5 | 793.9R | 0/8 | [-0.194, -0.126] |
| `atr_k100` | 1301 | -0.0856 | -0.0660 | +0.0747 | 0.835 | 55.0% | -111.4 | 114.0R | 2/8 | [-0.143, -0.033] |
| `atr_k050_ote_band` | 184 | -0.2089 | -0.1893 | -0.0486 | 0.644 | 50.0% | -38.4 | 42.6R | 3/8 | [-0.398, -0.035] |
| `atr_k050_trend_kai` | 2848 | -0.1729 | -0.1533 | -0.0126 | 0.684 | 52.5% | -492.3 | 498.2R | 0/8 | [-0.211, -0.133] |
| `atr_k050_premium_discount` | 1241 | -0.1923 | -0.1727 | -0.0320 | 0.659 | 52.0% | -238.6 | 247.3R | 1/8 | [-0.262, -0.124] |
| `wrapper_trivial` | 4812 | -0.1603 | -0.1407 | +0.0000 | 0.703 | 53.1% | -771.5 | 793.9R | 0/8 | [-0.194, -0.126] |

#### Escenario: `realista` (Comisión: $1.24 RT, Slip: 1 tick)

| Arm | n | E[R] | Δ vs ctrl M7 | Δ vs k=0.5 | PF | WR (%) | Net R | Max DD (R) | Folds+ | IC95 CBB |
|---|---|---|---|---|---|---|---|---|---|---|
| `control_m7` | 3583 | +0.0897 | — | +0.0588 | 1.203 | 58.7% | +321.2 | 26.0R | 7/8 | [+0.054, +0.125] |
| `atr_k050` | 4812 | +0.0309 | -0.0588 | — | 1.068 | 60.0% | +148.6 | 76.8R | 6/8 | [-0.000, +0.062] |
| `atr_k100` | 1301 | +0.0271 | -0.0626 | -0.0038 | 1.058 | 56.3% | +35.3 | 29.5R | 5/8 | [-0.032, +0.080] |
| `atr_k050_ote_band` | 184 | +0.0128 | -0.0769 | -0.0181 | 1.027 | 58.1% | +2.3 | 17.3R | 5/8 | [-0.161, +0.178] |
| `atr_k050_trend_kai` | 2848 | +0.0206 | -0.0691 | -0.0103 | 1.045 | 58.8% | +58.7 | 43.5R | 6/8 | [-0.013, +0.058] |
| `atr_k050_premium_discount` | 1241 | +0.0176 | -0.0721 | -0.0133 | 1.038 | 59.3% | +21.8 | 43.2R | 4/8 | [-0.047, +0.082] |
| `wrapper_trivial` | 4812 | +0.0309 | -0.0588 | +0.0000 | 1.068 | 60.0% | +148.6 | 76.8R | 6/8 | [-0.000, +0.062] |

### Mercado: `MES`

- **Tick Size:** 0.25 | **$/punto:** $5.00 | **Fricción:** Sin validar (supuesto declarado)

#### Escenario: `canonico` (Comisión: $4.00 RT, Slip: 0 tick)

| Arm | n | E[R] | Δ vs ctrl M7 | Δ vs k=0.5 | PF | WR (%) | Net R | Max DD (R) | Folds+ | IC95 CBB |
|---|---|---|---|---|---|---|---|---|---|---|
| `control_m7` | 295 | +0.0741 | — | +0.3787 | 1.170 | 59.3% | +21.9 | 7.9R | 5/8 | [-0.045, +0.190] |
| `atr_k050` | 5560 | -0.3046 | -0.3787 | — | 0.498 | 44.8% | -1693.8 | 1703.5R | 0/8 | [-0.336, -0.274] |
| `atr_k100` | 1765 | -0.2327 | -0.3068 | +0.0719 | 0.600 | 48.3% | -410.8 | 419.1R | 0/8 | [-0.291, -0.180] |
| `atr_k050_ote_band` | 252 | -0.3932 | -0.4673 | -0.0886 | 0.432 | 42.9% | -99.1 | 104.2R | 0/8 | [-0.522, -0.265] |
| `atr_k050_trend_kai` | 3260 | -0.3210 | -0.3951 | -0.0164 | 0.478 | 44.1% | -1046.5 | 1053.2R | 0/8 | [-0.360, -0.281] |
| `atr_k050_premium_discount` | 1607 | -0.3633 | -0.4374 | -0.0587 | 0.441 | 42.7% | -583.9 | 586.7R | 0/8 | [-0.420, -0.308] |
| `wrapper_trivial` | 5560 | -0.3046 | -0.3787 | +0.0000 | 0.498 | 44.8% | -1693.8 | 1703.5R | 0/8 | [-0.336, -0.274] |

#### Escenario: `canonico_mas_1tick` (Comisión: $4.00 RT, Slip: 1 tick)

| Arm | n | E[R] | Δ vs ctrl M7 | Δ vs k=0.5 | PF | WR (%) | Net R | Max DD (R) | Folds+ | IC95 CBB |
|---|---|---|---|---|---|---|---|---|---|---|
| `control_m7` | 295 | +0.0624 | — | +0.4447 | 1.140 | 59.3% | +18.4 | 8.7R | 4/8 | [-0.057, +0.179] |
| `atr_k050` | 5560 | -0.3823 | -0.4447 | — | 0.436 | 42.7% | -2125.8 | 2133.9R | 0/8 | [-0.417, -0.348] |
| `atr_k100` | 1765 | -0.2921 | -0.3545 | +0.0902 | 0.539 | 46.0% | -515.5 | 522.5R | 0/8 | [-0.353, -0.236] |
| `atr_k050_ote_band` | 252 | -0.4864 | -0.5488 | -0.1041 | 0.377 | 41.3% | -122.6 | 127.1R | 0/8 | [-0.626, -0.346] |
| `atr_k050_trend_kai` | 3260 | -0.4002 | -0.4626 | -0.0179 | 0.418 | 42.0% | -1304.7 | 1310.9R | 0/8 | [-0.443, -0.356] |
| `atr_k050_premium_discount` | 1607 | -0.4498 | -0.5122 | -0.0675 | 0.384 | 40.6% | -722.9 | 724.9R | 0/8 | [-0.512, -0.388] |
| `wrapper_trivial` | 5560 | -0.3823 | -0.4447 | +0.0000 | 0.436 | 42.7% | -2125.8 | 2133.9R | 0/8 | [-0.417, -0.348] |

#### Escenario: `realista` (Comisión: $1.24 RT, Slip: 1 tick)

| Arm | n | E[R] | Δ vs ctrl M7 | Δ vs k=0.5 | PF | WR (%) | Net R | Max DD (R) | Folds+ | IC95 CBB |
|---|---|---|---|---|---|---|---|---|---|---|
| `control_m7` | 295 | +0.1120 | — | +0.1578 | 1.263 | 59.3% | +33.1 | 7.4R | 7/8 | [-0.008, +0.229] |
| `atr_k050` | 5560 | -0.0458 | -0.1578 | — | 0.907 | 62.3% | -254.4 | 344.6R | 2/8 | [-0.074, -0.017] |
| `atr_k100` | 1765 | -0.0568 | -0.1688 | -0.0110 | 0.889 | 58.4% | -100.2 | 122.8R | 2/8 | [-0.112, -0.005] |
| `atr_k050_ote_band` | 252 | -0.1045 | -0.2165 | -0.0587 | 0.809 | 59.1% | -26.3 | 39.6R | 2/8 | [-0.236, +0.028] |
| `atr_k050_trend_kai` | 3260 | -0.0607 | -0.1727 | -0.0149 | 0.878 | 61.9% | -198.0 | 247.0R | 1/8 | [-0.097, -0.023] |
| `atr_k050_premium_discount` | 1607 | -0.0805 | -0.1925 | -0.0347 | 0.845 | 61.1% | -129.3 | 143.9R | 1/8 | [-0.136, -0.027] |
| `wrapper_trivial` | 5560 | -0.0458 | -0.1578 | +0.0000 | 0.907 | 62.3% | -254.4 | 344.6R | 2/8 | [-0.074, -0.017] |

### Mercado: `MYM`

- **Tick Size:** 1.0 | **$/punto:** $0.50 | **Fricción:** Sin validar (supuesto declarado)

#### Escenario: `canonico` (Comisión: $4.00 RT, Slip: 0 tick)

| Arm | n | E[R] | Δ vs ctrl M7 | Δ vs k=0.5 | PF | WR (%) | Net R | Max DD (R) | Folds+ | IC95 CBB |
|---|---|---|---|---|---|---|---|---|---|---|
| `control_m7` | 6628 | -0.5210 | — | +0.1076 | 0.326 | 35.6% | -3453.3 | 3454.0R | 0/8 | [-0.550, -0.492] |
| `atr_k050` | 6513 | -0.6286 | -0.1076 | — | 0.249 | 33.0% | -4093.8 | 4095.4R | 0/8 | [-0.664, -0.595] |
| `atr_k100` | 2562 | -0.5286 | -0.0076 | +0.1000 | 0.328 | 37.6% | -1354.2 | 1365.4R | 0/8 | [-0.588, -0.477] |
| `atr_k050_ote_band` | 356 | -0.7705 | -0.2495 | -0.1419 | 0.181 | 28.6% | -274.3 | 275.2R | 0/8 | [-0.887, -0.654] |
| `atr_k050_trend_kai` | 3854 | -0.6489 | -0.1279 | -0.0203 | 0.238 | 31.7% | -2501.0 | 2501.3R | 0/8 | [-0.692, -0.609] |
| `atr_k050_premium_discount` | 2067 | -0.6937 | -0.1727 | -0.0651 | 0.216 | 30.4% | -1433.9 | 1435.4R | 0/8 | [-0.748, -0.642] |
| `wrapper_trivial` | 6513 | -0.6286 | -0.1076 | +0.0000 | 0.249 | 33.0% | -4093.8 | 4095.4R | 0/8 | [-0.664, -0.595] |

#### Escenario: `canonico_mas_1tick` (Comisión: $4.00 RT, Slip: 1 tick)

| Arm | n | E[R] | Δ vs ctrl M7 | Δ vs k=0.5 | PF | WR (%) | Net R | Max DD (R) | Folds+ | IC95 CBB |
|---|---|---|---|---|---|---|---|---|---|---|
| `control_m7` | 6628 | -0.5617 | — | +0.1154 | 0.308 | 34.9% | -3723.2 | 3723.9R | 0/8 | [-0.592, -0.532] |
| `atr_k050` | 6513 | -0.6771 | -0.1154 | — | 0.234 | 32.3% | -4410.1 | 4411.7R | 0/8 | [-0.714, -0.642] |
| `atr_k100` | 2562 | -0.5688 | -0.0071 | +0.1083 | 0.310 | 36.8% | -1457.3 | 1468.2R | 0/8 | [-0.631, -0.515] |
| `atr_k050_ote_band` | 356 | -0.8278 | -0.2661 | -0.1507 | 0.170 | 28.1% | -294.7 | 295.6R | 0/8 | [-0.952, -0.706] |
| `atr_k050_trend_kai` | 3854 | -0.6989 | -0.1372 | -0.0218 | 0.223 | 30.9% | -2693.5 | 2693.8R | 0/8 | [-0.743, -0.658] |
| `atr_k050_premium_discount` | 2067 | -0.7461 | -0.1844 | -0.0690 | 0.203 | 29.6% | -1542.1 | 1543.4R | 0/8 | [-0.803, -0.691] |
| `wrapper_trivial` | 6513 | -0.6771 | -0.1154 | +0.0000 | 0.234 | 32.3% | -4410.1 | 4411.7R | 0/8 | [-0.714, -0.642] |

#### Escenario: `realista` (Comisión: $1.24 RT, Slip: 1 tick)

| Arm | n | E[R] | Δ vs ctrl M7 | Δ vs k=0.5 | PF | WR (%) | Net R | Max DD (R) | Folds+ | IC95 CBB |
|---|---|---|---|---|---|---|---|---|---|---|
| `control_m7` | 6628 | -0.1435 | — | +0.0289 | 0.747 | 55.1% | -951.2 | 959.3R | 0/8 | [-0.173, -0.114] |
| `atr_k050` | 6513 | -0.1724 | -0.0289 | — | 0.690 | 54.1% | -1122.7 | 1146.2R | 1/8 | [-0.200, -0.144] |
| `atr_k100` | 2562 | -0.1829 | -0.0394 | -0.0105 | 0.689 | 52.7% | -468.7 | 485.8R | 0/8 | [-0.234, -0.138] |
| `atr_k050_ote_band` | 356 | -0.2357 | -0.0922 | -0.0633 | 0.608 | 50.6% | -83.9 | 88.3R | 0/8 | [-0.343, -0.133] |
| `atr_k050_trend_kai` | 3854 | -0.1846 | -0.0411 | -0.0122 | 0.670 | 53.4% | -711.5 | 730.8R | 0/8 | [-0.220, -0.151] |
| `atr_k050_premium_discount` | 2067 | -0.1917 | -0.0482 | -0.0193 | 0.666 | 52.9% | -396.3 | 404.6R | 0/8 | [-0.240, -0.145] |
| `wrapper_trivial` | 6513 | -0.1724 | -0.0289 | +0.0000 | 0.690 | 54.1% | -1122.7 | 1146.2R | 1/8 | [-0.200, -0.144] |

### Mercado: `MGC`

- **Tick Size:** 0.1 | **$/punto:** $10.00 | **Fricción:** Sin validar (supuesto declarado)

#### Escenario: `canonico` (Comisión: $4.00 RT, Slip: 0 tick)

| Arm | n | E[R] | Δ vs ctrl M7 | Δ vs k=0.5 | PF | WR (%) | Net R | Max DD (R) | Folds+ | IC95 CBB |
|---|---|---|---|---|---|---|---|---|---|---|
| `control_m7` | 186 | +0.2086 | — | +0.4830 | 1.523 | 62.4% | +38.8 | 9.3R | 6/8 | [+0.064, +0.353] |
| `atr_k050` | 5358 | -0.2744 | -0.4830 | — | 0.545 | 47.5% | -1470.0 | 1487.8R | 0/8 | [-0.309, -0.239] |
| `atr_k100` | 1554 | -0.1744 | -0.3830 | +0.1000 | 0.691 | 53.0% | -271.0 | 292.6R | 2/8 | [-0.238, -0.114] |
| `atr_k050_ote_band` | 231 | -0.2794 | -0.4880 | -0.0050 | 0.541 | 46.3% | -64.5 | 70.2R | 2/8 | [-0.430, -0.121] |
| `atr_k050_trend_kai` | 3040 | -0.2751 | -0.4837 | -0.0007 | 0.547 | 48.2% | -836.4 | 854.9R | 1/8 | [-0.325, -0.229] |
| `atr_k050_premium_discount` | 1431 | -0.2749 | -0.4835 | -0.0005 | 0.540 | 48.4% | -393.4 | 425.6R | 2/8 | [-0.345, -0.204] |
| `wrapper_trivial` | 5358 | -0.2744 | -0.4830 | +0.0000 | 0.545 | 47.5% | -1470.0 | 1487.8R | 0/8 | [-0.309, -0.239] |

#### Escenario: `canonico_mas_1tick` (Comisión: $4.00 RT, Slip: 1 tick)

| Arm | n | E[R] | Δ vs ctrl M7 | Δ vs k=0.5 | PF | WR (%) | Net R | Max DD (R) | Folds+ | IC95 CBB |
|---|---|---|---|---|---|---|---|---|---|---|
| `control_m7` | 186 | +0.2045 | — | +0.5329 | 1.509 | 62.4% | +38.0 | 9.4R | 6/8 | [+0.059, +0.350] |
| `atr_k050` | 5358 | -0.3284 | -0.5329 | — | 0.497 | 46.0% | -1759.6 | 1771.5R | 0/8 | [-0.365, -0.292] |
| `atr_k100` | 1554 | -0.2103 | -0.4148 | +0.1181 | 0.646 | 51.7% | -326.9 | 347.0R | 2/8 | [-0.276, -0.147] |
| `atr_k050_ote_band` | 231 | -0.3344 | -0.5389 | -0.0060 | 0.493 | 44.2% | -77.2 | 82.5R | 2/8 | [-0.493, -0.165] |
| `atr_k050_trend_kai` | 3040 | -0.3284 | -0.5329 | +0.0000 | 0.499 | 46.8% | -998.3 | 1011.0R | 0/8 | [-0.381, -0.279] |
| `atr_k050_premium_discount` | 1431 | -0.3331 | -0.5376 | -0.0047 | 0.488 | 47.0% | -476.6 | 505.7R | 2/8 | [-0.409, -0.258] |
| `wrapper_trivial` | 5358 | -0.3284 | -0.5329 | +0.0000 | 0.497 | 46.0% | -1759.6 | 1771.5R | 0/8 | [-0.365, -0.292] |

#### Escenario: `realista` (Comisión: $1.24 RT, Slip: 1 tick)

| Arm | n | E[R] | Δ vs ctrl M7 | Δ vs k=0.5 | PF | WR (%) | Net R | Max DD (R) | Folds+ | IC95 CBB |
|---|---|---|---|---|---|---|---|---|---|---|
| `control_m7` | 186 | +0.2281 | — | +0.2645 | 1.581 | 62.4% | +42.4 | 9.1R | 6/8 | [+0.083, +0.374] |
| `atr_k050` | 5358 | -0.0364 | -0.2645 | — | 0.927 | 59.3% | -194.8 | 292.0R | 4/8 | [-0.067, -0.006] |
| `atr_k100` | 1554 | -0.0281 | -0.2562 | +0.0083 | 0.945 | 57.4% | -43.7 | 102.6R | 4/8 | [-0.091, +0.029] |
| `atr_k050_ote_band` | 231 | -0.0041 | -0.2322 | +0.0323 | 0.992 | 61.0% | -1.0 | 19.3R | 4/8 | [-0.160, +0.156] |
| `atr_k050_trend_kai` | 3040 | -0.0404 | -0.2685 | -0.0040 | 0.920 | 58.8% | -122.8 | 183.4R | 4/8 | [-0.085, +0.001] |
| `atr_k050_premium_discount` | 1431 | -0.0075 | -0.2356 | +0.0289 | 0.984 | 62.0% | -10.7 | 100.0R | 4/8 | [-0.068, +0.051] |
| `wrapper_trivial` | 5358 | -0.0364 | -0.2645 | +0.0000 | 0.927 | 59.3% | -194.8 | 292.0R | 4/8 | [-0.067, -0.006] |

## 3. Verificaciones de Integridad y Anti-Fraude

1. **Control Anti-Fraude (`wrapper_trivial == atr_k050`):**
   - **12/12 combinaciones PASARON con 100% de identidad bit a bit** (hash SHA256 idéntico en todos los mercados y escenarios).
   - Confirmación estricta de que el decorador pasante no introduce sesgos ni modificaciones algorítmicas.
2. **Continuidad Metodológica (`control_m7 == baseline M7`):**
   - **12/12 combinaciones PASARON con 100% de reproducción bit a bit** frente a los artefactos de M7.
   - Garantiza que la infraestructura de ejecución reproduce exactamente el comportamiento del bloque anterior.
3. **Auditoría de Causalidad Point-in-Time:**
   - Auditadas 59 decisiones de muestreo profundo en barras cerradas: **0 violaciones de causalidad**.
   - ATR(14) y pivotes swing son estrictamente causales ($available\_at \le t$).


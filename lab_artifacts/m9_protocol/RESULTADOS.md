# RESULTADOS — Bloque M9: Screening de Entradas Crudas Multi-Mercado

> **Protocolo:** C1 Walk-Forward (8 calendar rolling folds 36/6/6, purga real + embargo $h=192$, bootstrap CBB $B=1000$).
> **Espacio:** 5 entradas crudas $\times$ 4 mercados (MNQ, MES, MYM, MGC) $\times$ 3 escenarios de coste = **60 celdas evaluadas**.
> **Escenario Decisorio Declarado:** `realista` ($1.24 RT + 1 tick slippage en patas de mercado; límite/TP 0 tick).
> **Criterio de Supervivencia Declarado:** Límite inferior de IC95 (CBB) en escenario `realista` $> 0$ en $\ge 2$ de los 4 mercados.

---

## 1. Veredicto Global de Supervivencia

| ID Entrada | Nombre Descriptivo | Mercados IC95 LB > 0 (Realista) | ¿Supervive? (≥2) | Veredicto |
|---|---|:---:|:---:|---|
| `e1_ts_d1` | E1 · TS-D1 (Turtle Soup D1) | 0/4 (Ninguno) | NO | DESCARTADA (SIN PULSO) |
| `e2_ts_d20` | E2 · TS-D20 (Turtle Soup D20) | 0/4 (Ninguno) | NO | DESCARTADA (SIN PULSO) |
| `e3_mom_break` | E3 · MOM-BREAK (Momentum 20-D Break) | 0/4 (Ninguno) | NO | DESCARTADA (SIN PULSO) |
| `e4_mr_level` | E4 · MR-LEVEL (Mean Reversion Session Pools) | 0/4 (Ninguno) | NO | DESCARTADA (SIN PULSO) |
| `e5_vol_break` | E5 · VOL-BREAK (Volatility Breakout D1) | 0/4 (Ninguno) | NO | DESCARTADA (SIN PULSO) |

> **Conclusión Primaria:** **NINGUNA** de las 5 entradas crudas evaluadas supera el criterio de supervivencia declarado.
> Todas las entradas presentan límites inferiores negativos de IC95 (CBB) en todos los mercados bajo fricción realista.

---

## 2. Matriz Completa — Escenario Decisorio (`realista`)

| Mercado | ID Entrada | $n$ | $E[R]$ | PF | Win Rate | Max DD ($R$) | Folds Positivos | IC95 CBB ($E[R]$) |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **MNQ** | `e1_ts_d1` | 2322 | -0.0398 | 0.920 | 33.1% | 146.2R | 2/8 | `[-0.082, +0.004]` |
| **MNQ** | `e2_ts_d20` | 605 | -0.0070 | 0.986 | 34.4% | 29.0R | 4/8 | `[-0.096, +0.086]` |
| **MNQ** | `e3_mom_break` | 838 | -0.0685 | 0.865 | 32.1% | 60.7R | 1/8 | `[-0.139, +0.002]` |
| **MNQ** | `e4_mr_level` | 3736 | -0.0383 | 0.928 | 33.3% | 189.1R | 2/8 | `[-0.076, +0.000]` |
| **MNQ** | `e5_vol_break` | 3218 | -0.0356 | 0.927 | 33.5% | 136.0R | 1/8 | `[-0.069, +0.000]` |
| **MES** | `e1_ts_d1` | 2248 | -0.0896 | 0.741 | 29.3% | 220.7R | 0/8 | `[-0.118, -0.059]` |
| **MES** | `e2_ts_d20` | 605 | -0.0989 | 0.725 | 27.8% | 64.0R | 0/8 | `[-0.162, -0.038]` |
| **MES** | `e3_mom_break` | 841 | -0.1170 | 0.675 | 27.7% | 104.2R | 0/8 | `[-0.165, -0.066]` |
| **MES** | `e4_mr_level` | 3646 | -0.0741 | 0.797 | 30.5% | 297.7R | 0/8 | `[-0.098, -0.049]` |
| **MES** | `e5_vol_break` | 3361 | -0.0752 | 0.774 | 29.8% | 255.9R | 0/8 | `[-0.100, -0.051]` |
| **MYM** | `e1_ts_d1` | 2210 | -0.0416 | 0.819 | 32.4% | 104.3R | 2/8 | `[-0.062, -0.019]` |
| **MYM** | `e2_ts_d20` | 489 | -0.0474 | 0.816 | 32.1% | 35.0R | 3/8 | `[-0.105, +0.008]` |
| **MYM** | `e3_mom_break` | 695 | -0.0675 | 0.745 | 29.3% | 50.7R | 1/8 | `[-0.112, -0.023]` |
| **MYM** | `e4_mr_level` | 3629 | -0.0397 | 0.844 | 32.6% | 154.5R | 1/8 | `[-0.057, -0.021]` |
| **MYM** | `e5_vol_break` | 3107 | -0.0413 | 0.821 | 31.5% | 132.1R | 0/8 | `[-0.059, -0.023]` |
| **MGC** | `e1_ts_d1` | 2284 | -0.0482 | 0.864 | 32.3% | 133.5R | 1/8 | `[-0.080, -0.014]` |
| **MGC** | `e2_ts_d20` | 442 | -0.1002 | 0.750 | 30.8% | 54.9R | 2/8 | `[-0.167, -0.027]` |
| **MGC** | `e3_mom_break` | 664 | -0.0583 | 0.849 | 33.1% | 58.9R | 3/8 | `[-0.127, +0.013]` |
| **MGC** | `e4_mr_level` | 3406 | -0.0532 | 0.858 | 32.6% | 210.4R | 0/8 | `[-0.083, -0.023]` |
| **MGC** | `e5_vol_break` | 3161 | -0.0567 | 0.841 | 31.8% | 193.0R | 0/8 | `[-0.083, -0.029]` |

---

## 3. Desglose Comparativo por Mercado y Escenario de Costes

### 3.1. Mercado MNQ

| Entrada | Escenario | $n$ | $E[R]$ | PF | Win Rate | Max DD ($R$) | Folds+ | IC95 CBB ($E[R]$) |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `e1_ts_d1` | canonico | 2319 | -0.0771 | 0.852 | 33.6% | 215.8R | 1/8 | `[-0.122, -0.034]` |
| `e1_ts_d1` | canonico_mas_1tick | 2322 | -0.0903 | 0.829 | 33.0% | 242.3R | 0/8 | `[-0.133, -0.046]` |
| `e1_ts_d1` | realista | 2322 | -0.0398 | 0.920 | 33.1% | 146.2R | 2/8 | `[-0.082, +0.004]` |
| `e2_ts_d20` | canonico | 604 | -0.0357 | 0.929 | 35.1% | 34.4R | 4/8 | `[-0.123, +0.058]` |
| `e2_ts_d20` | canonico_mas_1tick | 605 | -0.0574 | 0.889 | 34.4% | 45.7R | 3/8 | `[-0.146, +0.035]` |
| `e2_ts_d20` | realista | 605 | -0.0070 | 0.986 | 34.4% | 29.0R | 4/8 | `[-0.096, +0.086]` |
| `e3_mom_break` | canonico | 838 | -0.1078 | 0.798 | 32.5% | 93.3R | 1/8 | `[-0.179, -0.036]` |
| `e3_mom_break` | canonico_mas_1tick | 838 | -0.1190 | 0.780 | 32.1% | 102.7R | 1/8 | `[-0.190, -0.048]` |
| `e3_mom_break` | realista | 838 | -0.0685 | 0.865 | 32.1% | 60.7R | 1/8 | `[-0.139, +0.002]` |
| `e4_mr_level` | canonico | 3730 | -0.0742 | 0.866 | 33.7% | 310.9R | 0/8 | `[-0.111, -0.037]` |
| `e4_mr_level` | canonico_mas_1tick | 3736 | -0.0879 | 0.844 | 33.3% | 361.8R | 0/8 | `[-0.126, -0.050]` |
| `e4_mr_level` | realista | 3736 | -0.0383 | 0.928 | 33.3% | 189.1R | 2/8 | `[-0.076, +0.000]` |
| `e5_vol_break` | canonico | 3213 | -0.0700 | 0.863 | 34.1% | 244.1R | 0/8 | `[-0.105, -0.035]` |
| `e5_vol_break` | canonico_mas_1tick | 3218 | -0.0861 | 0.834 | 33.4% | 296.2R | 0/8 | `[-0.120, -0.050]` |
| `e5_vol_break` | realista | 3218 | -0.0356 | 0.927 | 33.5% | 136.0R | 1/8 | `[-0.069, +0.000]` |

### 3.2. Mercado MES

| Entrada | Escenario | $n$ | $E[R]$ | PF | Win Rate | Max DD ($R$) | Folds+ | IC95 CBB ($E[R]$) |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `e1_ts_d1` | canonico | 2240 | -0.1002 | 0.719 | 31.9% | 243.2R | 0/8 | `[-0.131, -0.070]` |
| `e1_ts_d1` | canonico_mas_1tick | 2248 | -0.1442 | 0.625 | 29.3% | 340.2R | 0/8 | `[-0.173, -0.114]` |
| `e1_ts_d1` | realista | 2248 | -0.0896 | 0.741 | 29.3% | 220.7R | 0/8 | `[-0.118, -0.059]` |
| `e2_ts_d20` | canonico | 602 | -0.1224 | 0.674 | 29.2% | 77.4R | 0/8 | `[-0.186, -0.059]` |
| `e2_ts_d20` | canonico_mas_1tick | 605 | -0.1530 | 0.616 | 27.8% | 96.2R | 0/8 | `[-0.215, -0.093]` |
| `e2_ts_d20` | realista | 605 | -0.0989 | 0.725 | 27.8% | 64.0R | 0/8 | `[-0.162, -0.038]` |
| `e3_mom_break` | canonico | 832 | -0.1317 | 0.647 | 29.9% | 113.8R | 0/8 | `[-0.183, -0.083]` |
| `e3_mom_break` | canonico_mas_1tick | 841 | -0.1711 | 0.571 | 27.7% | 147.6R | 0/8 | `[-0.219, -0.120]` |
| `e3_mom_break` | realista | 841 | -0.1170 | 0.675 | 27.7% | 104.2R | 0/8 | `[-0.165, -0.066]` |
| `e4_mr_level` | canonico | 3637 | -0.0863 | 0.771 | 32.7% | 338.7R | 0/8 | `[-0.109, -0.061]` |
| `e4_mr_level` | canonico_mas_1tick | 3646 | -0.1285 | 0.681 | 30.3% | 488.3R | 0/8 | `[-0.152, -0.104]` |
| `e4_mr_level` | realista | 3646 | -0.0741 | 0.797 | 30.5% | 297.7R | 0/8 | `[-0.098, -0.049]` |
| `e5_vol_break` | canonico | 3322 | -0.0868 | 0.748 | 32.5% | 291.1R | 0/8 | `[-0.110, -0.063]` |
| `e5_vol_break` | canonico_mas_1tick | 3361 | -0.1297 | 0.651 | 29.7% | 438.7R | 0/8 | `[-0.154, -0.106]` |
| `e5_vol_break` | realista | 3361 | -0.0752 | 0.774 | 29.8% | 255.9R | 0/8 | `[-0.100, -0.051]` |

### 3.3. Mercado MYM

| Entrada | Escenario | $n$ | $E[R]$ | PF | Win Rate | Max DD ($R$) | Folds+ | IC95 CBB ($E[R]$) |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `e1_ts_d1` | canonico | 2197 | -0.0807 | 0.686 | 33.5% | 185.3R | 0/8 | `[-0.102, -0.059]` |
| `e1_ts_d1` | canonico_mas_1tick | 2210 | -0.0967 | 0.638 | 32.3% | 217.0R | 0/8 | `[-0.118, -0.074]` |
| `e1_ts_d1` | realista | 2210 | -0.0416 | 0.819 | 32.4% | 104.3R | 2/8 | `[-0.062, -0.019]` |
| `e2_ts_d20` | canonico | 488 | -0.0867 | 0.695 | 34.0% | 51.2R | 2/8 | `[-0.144, -0.031]` |
| `e2_ts_d20` | canonico_mas_1tick | 489 | -0.1025 | 0.652 | 32.1% | 58.7R | 1/8 | `[-0.160, -0.047]` |
| `e2_ts_d20` | realista | 489 | -0.0474 | 0.816 | 32.1% | 35.0R | 3/8 | `[-0.105, +0.008]` |
| `e3_mom_break` | canonico | 692 | -0.1079 | 0.631 | 30.3% | 77.3R | 0/8 | `[-0.155, -0.064]` |
| `e3_mom_break` | canonico_mas_1tick | 695 | -0.1225 | 0.596 | 29.1% | 87.7R | 0/8 | `[-0.167, -0.078]` |
| `e3_mom_break` | realista | 695 | -0.0675 | 0.745 | 29.3% | 50.7R | 1/8 | `[-0.112, -0.023]` |
| `e4_mr_level` | canonico | 3615 | -0.0801 | 0.715 | 33.5% | 294.9R | 0/8 | `[-0.098, -0.062]` |
| `e4_mr_level` | canonico_mas_1tick | 3629 | -0.0948 | 0.674 | 32.4% | 348.3R | 0/8 | `[-0.113, -0.076]` |
| `e4_mr_level` | realista | 3629 | -0.0397 | 0.844 | 32.6% | 154.5R | 1/8 | `[-0.057, -0.021]` |
| `e5_vol_break` | canonico | 3101 | -0.0807 | 0.687 | 33.0% | 251.7R | 0/8 | `[-0.099, -0.063]` |
| `e5_vol_break` | canonico_mas_1tick | 3107 | -0.0964 | 0.642 | 31.4% | 300.3R | 0/8 | `[-0.114, -0.078]` |
| `e5_vol_break` | realista | 3107 | -0.0413 | 0.821 | 31.5% | 132.1R | 0/8 | `[-0.059, -0.023]` |

### 3.4. Mercado MGC

| Entrada | Escenario | $n$ | $E[R]$ | PF | Win Rate | Max DD ($R$) | Folds+ | IC95 CBB ($E[R]$) |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `e1_ts_d1` | canonico | 2270 | -0.0766 | 0.795 | 33.7% | 195.2R | 0/8 | `[-0.109, -0.043]` |
| `e1_ts_d1` | canonico_mas_1tick | 2284 | -0.1008 | 0.742 | 32.3% | 248.3R | 0/8 | `[-0.133, -0.066]` |
| `e1_ts_d1` | realista | 2284 | -0.0482 | 0.864 | 32.3% | 133.5R | 1/8 | `[-0.080, -0.014]` |
| `e2_ts_d20` | canonico | 438 | -0.1270 | 0.699 | 31.7% | 66.6R | 1/8 | `[-0.197, -0.052]` |
| `e2_ts_d20` | canonico_mas_1tick | 442 | -0.1524 | 0.652 | 30.5% | 76.8R | 1/8 | `[-0.220, -0.078]` |
| `e2_ts_d20` | realista | 442 | -0.1002 | 0.750 | 30.8% | 54.9R | 2/8 | `[-0.167, -0.027]` |
| `e3_mom_break` | canonico | 660 | -0.0696 | 0.825 | 35.3% | 67.8R | 3/8 | `[-0.138, +0.002]` |
| `e3_mom_break` | canonico_mas_1tick | 664 | -0.1108 | 0.737 | 33.0% | 85.9R | 3/8 | `[-0.179, -0.039]` |
| `e3_mom_break` | realista | 664 | -0.0583 | 0.849 | 33.1% | 58.9R | 3/8 | `[-0.127, +0.013]` |
| `e4_mr_level` | canonico | 3384 | -0.0792 | 0.797 | 34.0% | 292.9R | 0/8 | `[-0.109, -0.047]` |
| `e4_mr_level` | canonico_mas_1tick | 3406 | -0.1055 | 0.742 | 32.6% | 378.4R | 0/8 | `[-0.135, -0.076]` |
| `e4_mr_level` | realista | 3406 | -0.0532 | 0.858 | 32.6% | 210.4R | 0/8 | `[-0.083, -0.023]` |
| `e5_vol_break` | canonico | 3145 | -0.0688 | 0.814 | 34.2% | 231.4R | 0/8 | `[-0.096, -0.043]` |
| `e5_vol_break` | canonico_mas_1tick | 3161 | -0.1094 | 0.721 | 31.8% | 356.3R | 0/8 | `[-0.136, -0.082]` |
| `e5_vol_break` | realista | 3161 | -0.0567 | 0.841 | 31.8% | 193.0R | 0/8 | `[-0.083, -0.029]` |

---

## 4. Sensibilidad a Fricción y Deslizamiento

Comparación del impacto del coste y slippage entre el escenario `canonico` ($4 RT sin slippage) y `canonico_mas_1tick` ($4 RT + 1 tick slippage en todas las salidas):

| Mercado | Entrada | $E[R]_{canon}$ | $E[R]_{canon+1t}$ | $\Delta E[R]$ (slp) | $PF_{canon}$ | $PF_{canon+1t}$ | $\Delta PF$ |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|
| MNQ | `e1_ts_d1` | -0.0771 | -0.0903 | -0.0132 | 0.852 | 0.829 | -0.022 |
| MNQ | `e2_ts_d20` | -0.0357 | -0.0574 | -0.0217 | 0.929 | 0.889 | -0.040 |
| MNQ | `e3_mom_break` | -0.1078 | -0.1190 | -0.0112 | 0.798 | 0.780 | -0.018 |
| MNQ | `e4_mr_level` | -0.0742 | -0.0879 | -0.0137 | 0.866 | 0.844 | -0.022 |
| MNQ | `e5_vol_break` | -0.0700 | -0.0861 | -0.0161 | 0.863 | 0.834 | -0.028 |
| MES | `e1_ts_d1` | -0.1002 | -0.1442 | -0.0440 | 0.719 | 0.625 | -0.094 |
| MES | `e2_ts_d20` | -0.1224 | -0.1530 | -0.0306 | 0.674 | 0.616 | -0.058 |
| MES | `e3_mom_break` | -0.1317 | -0.1711 | -0.0394 | 0.647 | 0.571 | -0.076 |
| MES | `e4_mr_level` | -0.0863 | -0.1285 | -0.0422 | 0.771 | 0.681 | -0.089 |
| MES | `e5_vol_break` | -0.0868 | -0.1297 | -0.0429 | 0.748 | 0.651 | -0.098 |
| MYM | `e1_ts_d1` | -0.0807 | -0.0967 | -0.0160 | 0.686 | 0.638 | -0.048 |
| MYM | `e2_ts_d20` | -0.0867 | -0.1025 | -0.0158 | 0.695 | 0.652 | -0.043 |
| MYM | `e3_mom_break` | -0.1079 | -0.1225 | -0.0146 | 0.631 | 0.596 | -0.036 |
| MYM | `e4_mr_level` | -0.0801 | -0.0948 | -0.0147 | 0.715 | 0.674 | -0.041 |
| MYM | `e5_vol_break` | -0.0807 | -0.0964 | -0.0157 | 0.687 | 0.642 | -0.045 |
| MGC | `e1_ts_d1` | -0.0766 | -0.1008 | -0.0242 | 0.795 | 0.742 | -0.053 |
| MGC | `e2_ts_d20` | -0.1270 | -0.1524 | -0.0254 | 0.699 | 0.652 | -0.047 |
| MGC | `e3_mom_break` | -0.0696 | -0.1108 | -0.0412 | 0.825 | 0.737 | -0.088 |
| MGC | `e4_mr_level` | -0.0792 | -0.1055 | -0.0263 | 0.797 | 0.742 | -0.056 |
| MGC | `e5_vol_break` | -0.0688 | -0.1094 | -0.0406 | 0.814 | 0.721 | -0.093 |

---

## 5. Auditoría de Causalidad y Controles Anti-Fraude

- **Equivalencia de Wrappers Triviales (20/20 verificaciones):** 100% de identidad bit a bit (`trades_sha256`) entre cada entrada directa y su envoltorio trivial en los 4 mercados.
- **Auditoría de Causalidad Point-in-Time:** 0 violaciones detectadas sobre las decisiones auditadas en MNQ (45), MES (36), MYM (35) y MGC (40).


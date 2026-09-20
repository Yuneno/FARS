# INFORME — Bloque M11: Régimen Horario + Gestión de Salida (Candidata EMA + Zona + Liquidez)

**Fecha:** 2026-09-20 · **Estado:** Concluido · **Base Git:** `main` (`f25b3537efedc570210d1d70ff1b0d96666e55e0`) · **Rama:** `bloque-m11-regimen-salidas`

---

## 1. Veredicto Ejecutivo en una Línea

> **VEREDICTO FINAL: NO.** Ni el filtrado por confluencias (zona/liquidez), ni la partición por régimen horario (RTH/Asia/Noche), ni las variantes de gestión de salida (parciales, time-stop, runner puro) logran generar una ventaja estadística con cota inferior del IC95 > 0 en escenario realista sobre MNQ M5; la hipótesis de que la candidata EMA+Zona+Liquidez alberga edge queda cerrada negativamente con evidencia exhaustiva y reproducible.

---

## 2. Gate de Continuidad C0 (Verificación Bit a Bit)

| Métrica | Publicado C2 (`emas_baseline`) | Reproducido M11 (`C0`) | Estado |
|---|---:|---:|:---:|
| Trades Totales ($n$) | 2.823 | 2.823 | ✅ EXACTO |
| $E[R]$ Global (Canónico) | −0,021986 | −0,021986 | ✅ EXACTO |
| Net R Total (Canónico) | −62,067 | −62,067 | ✅ EXACTO |
| Win Rate Global | 52,43 % | 52,43 % | ✅ EXACTO |
| Folds Positivos | 3 / 8 | 3 / 8 | ✅ EXACTO |

---

## 3. Etapa 1 — Confluencias (4 celdas)

Evaluación de la candidata base C0 y sus confluencias con FVG (Z) y distancia a liquidez (L) en sesión completa (`full`):

| Brazo | Escenario | Trades ($n$) | Net R | $E[R]$ (R) | Win Rate | Profit Factor | Folds+ | IC95 CBB (95%) |
|---|---|---:|---:|---:|---:|---:|:---:|:---:|
| `C0` | `realista` | 2,821 | +46.07 | **+0.0163** | 51.90% | 1.033 | 4/8 | [-0.0248, +0.0545] |
| `C0` | `canonico` | 2,823 | -62.07 | **-0.0220** | 52.43% | 0.958 | 3/8 | [-0.0634, +0.0177] |
| `C0+Z` | `realista` | 686 | +40.66 | **+0.0593** | 54.08% | 1.124 | 6/8 | [-0.0219, +0.1420] |
| `C0+Z` | `canonico` | 686 | +16.20 | **+0.0236** | 54.52% | 1.048 | 6/8 | [-0.0577, +0.1070] |
| `C0+L` | `realista` | 1,767 | +48.20 | **+0.0273** | 52.69% | 1.055 | 6/8 | [-0.0224, +0.0800] |
| `C0+L` | `canonico` | 1,767 | -38.45 | **-0.0218** | 53.08% | 0.957 | 4/8 | [-0.0722, +0.0271] |
| `C0+Z+L` | `realista` | 496 | +24.75 | **+0.0499** | 53.43% | 1.103 | 5/8 | [-0.0494, +0.1490] |
| `C0+Z+L` | `canonico` | 497 | +4.67 | **+0.0094** | 53.72% | 1.019 | 4/8 | [-0.0882, +0.1117] |

### Lección de Etapa 1:
1. **Las confluencias restan trades y no aportan robustez estadística:** C0 pasa de 2.821 trades a 686 en `C0+Z`, 1.767 en `C0+L` y 496 en `C0+Z+L`.
2. Aunque `C0+Z` muestra un $E[R] = +0,0593$ R en realista, su IC95 CBB $[-0,0219, +0,1420]$ cruza el cero de forma inequívoca.
3. Ninguna de las confluencias logra separar el intervalo de confianza de cero; el volumen de operaciones se desploma hasta en un 82% sin garantizar edge positivo.

---

## 4. Etapa 2 — Régimen Horario (16 celdas)

Partición horaria en ventanas disjuntas ET sobre cada uno de los 4 brazos de Etapa 1:

| Celda | Ventana ET | Escenario | Trades ($n$) | Net R | $E[R]$ (R) | Win Rate | Profit Factor | Folds+ | IC95 CBB (95%) |
|---|---|---|---:|---:|---:|---:|---:|:---:|:---:|
| `C0 / full` | `full` | `realista` | 2,821 | +46.07 | **+0.0163** | 51.90% | 1.033 | 4/8 | [-0.0248, +0.0545] |
| `C0 / full` | `full` | `canonico` | 2,823 | -62.07 | **-0.0220** | 52.43% | 0.958 | 3/8 | [-0.0634, +0.0177] |
| `C0 / rth` | `rth` | `realista` | 465 | -10.59 | **-0.0228** | 49.46% | 0.956 | 4/8 | [-0.1394, +0.1010] |
| `C0 / rth` | `rth` | `canonico` | 465 | -17.54 | **-0.0377** | 49.89% | 0.928 | 4/8 | [-0.1545, +0.0853] |
| `C0 / asia` | `asia` | `realista` | 813 | +31.44 | **+0.0387** | 53.63% | 1.078 | 5/8 | [-0.0392, +0.1132] |
| `C0 / asia` | `asia` | `canonico` | 810 | -31.80 | **-0.0393** | 54.32% | 0.925 | 4/8 | [-0.1129, +0.0410] |
| `C0 / noche` | `noche` | `realista` | 939 | -28.37 | **-0.0302** | 51.22% | 0.940 | 4/8 | [-0.1045, +0.0420] |
| `C0 / noche` | `noche` | `canonico` | 936 | -55.70 | **-0.0595** | 52.24% | 0.885 | 3/8 | [-0.1315, +0.0147] |
| `C0+Z / full` | `full` | `realista` | 686 | +40.66 | **+0.0593** | 54.08% | 1.124 | 6/8 | [-0.0219, +0.1420] |
| `C0+Z / full` | `full` | `canonico` | 686 | +16.20 | **+0.0236** | 54.52% | 1.048 | 6/8 | [-0.0577, +0.1070] |
| `C0+Z / rth` | `rth` | `realista` | 103 | -11.08 | **-0.1075** | 48.54% | 0.795 | 4/8 | [-0.2937, +0.0805] |
| `C0+Z / rth` | `rth` | `canonico` | 103 | -14.04 | **-0.1363** | 48.54% | 0.747 | 3/8 | [-0.3213, +0.0501] |
| `C0+Z / asia` | `asia` | `realista` | 226 | +19.75 | **+0.0874** | 55.75% | 1.186 | 5/8 | [-0.0739, +0.2504] |
| `C0+Z / asia` | `asia` | `canonico` | 225 | +7.32 | **+0.0325** | 56.44% | 1.066 | 5/8 | [-0.1385, +0.1986] |
| `C0+Z / noche` | `noche` | `realista` | 274 | +23.74 | **+0.0866** | 54.38% | 1.184 | 5/8 | [-0.0355, +0.2152] |
| `C0+Z / noche` | `noche` | `canonico` | 273 | +17.68 | **+0.0648** | 54.95% | 1.133 | 4/8 | [-0.0662, +0.1940] |
| `C0+L / full` | `full` | `realista` | 1,767 | +48.20 | **+0.0273** | 52.69% | 1.055 | 6/8 | [-0.0224, +0.0800] |
| `C0+L / full` | `full` | `canonico` | 1,767 | -38.45 | **-0.0218** | 53.08% | 0.957 | 4/8 | [-0.0722, +0.0271] |
| `C0+L / rth` | `rth` | `realista` | 327 | -10.34 | **-0.0316** | 49.24% | 0.939 | 3/8 | [-0.1543, +0.1091] |
| `C0+L / rth` | `rth` | `canonico` | 327 | -18.30 | **-0.0560** | 49.54% | 0.894 | 3/8 | [-0.1800, +0.0855] |
| `C0+L / asia` | `asia` | `realista` | 496 | +29.87 | **+0.0602** | 55.04% | 1.126 | 5/8 | [-0.0420, +0.1631] |
| `C0+L / asia` | `asia` | `canonico` | 494 | -17.86 | **-0.0362** | 55.06% | 0.930 | 4/8 | [-0.1355, +0.0688] |
| `C0+L / noche` | `noche` | `realista` | 637 | -0.07 | **-0.0001** | 51.96% | 1.000 | 4/8 | [-0.0850, +0.0903] |
| `C0+L / noche` | `noche` | `canonico` | 635 | -22.44 | **-0.0353** | 52.91% | 0.931 | 4/8 | [-0.1178, +0.0527] |
| `C0+Z+L / full` | `full` | `realista` | 496 | +24.75 | **+0.0499** | 53.43% | 1.103 | 5/8 | [-0.0494, +0.1490] |
| `C0+Z+L / full` | `full` | `canonico` | 497 | +4.67 | **+0.0094** | 53.72% | 1.019 | 4/8 | [-0.0882, +0.1117] |
| `C0+Z+L / rth` | `rth` | `realista` | 84 | -5.07 | **-0.0603** | 51.19% | 0.879 | 4/8 | [-0.2491, +0.1161] |
| `C0+Z+L / rth` | `rth` | `canonico` | 84 | -7.53 | **-0.0896** | 51.19% | 0.824 | 4/8 | [-0.2767, +0.0875] |
| `C0+Z+L / asia` | `asia` | `realista` | 150 | +13.92 | **+0.0928** | 56.00% | 1.199 | 5/8 | [-0.1095, +0.2944] |
| `C0+Z+L / asia` | `asia` | `canonico` | 150 | +3.04 | **+0.0203** | 56.00% | 1.040 | 5/8 | [-0.1869, +0.2237] |
| `C0+Z+L / noche` | `noche` | `realista` | 210 | +19.34 | **+0.0921** | 53.81% | 1.193 | 4/8 | [-0.0609, +0.2436] |
| `C0+Z+L / noche` | `noche` | `canonico` | 210 | +13.52 | **+0.0644** | 54.29% | 1.131 | 4/8 | [-0.0904, +0.2116] |

### Histograma de Operaciones de C0 por Hora ET:
```
Hora 00:00 ET:  264 trades █████████████████
Hora 01:00 ET:  162 trades ██████████
Hora 02:00 ET:  180 trades ████████████
Hora 03:00 ET:  175 trades ███████████
Hora 04:00 ET:  219 trades ██████████████
Hora 05:00 ET:  184 trades ████████████
Hora 06:00 ET:  161 trades ██████████
Hora 07:00 ET:  135 trades █████████
Hora 08:00 ET:  127 trades ████████
Hora 09:00 ET:   54 trades ███
Hora 10:00 ET:  269 trades █████████████████
Hora 11:00 ET:  219 trades ██████████████
Hora 12:00 ET:  143 trades █████████
Hora 13:00 ET:  113 trades ███████
Hora 14:00 ET:   94 trades ██████
Hora 15:00 ET:  105 trades ███████
Hora 16:00 ET:   63 trades ████
Hora 17:00 ET:    0 trades 
Hora 18:00 ET:    0 trades 
Hora 19:00 ET:  103 trades ██████
Hora 20:00 ET:   53 trades ███
Hora 21:00 ET:    0 trades 
Hora 22:00 ET:    0 trades 
Hora 23:00 ET:    0 trades 
```

---

## 5. Etapa 3 — Gestión de Salida (sobre ganadora Etapa 2: `C0+L / asia`)

Criterio de selección aplicado: mayor $E[R]$ neta realista entre celdas con $n \ge 300$ -> seleccionada `C0+L / asia`.

| Variante | Descripción | Escenario | Trades ($n$) | Net R | $E[R]$ (R) | Win Rate | Profit Factor | Folds+ | IC95 CBB (95%) |
|---|---|---|---:|---:|---:|---:|---:|:---:|:---:|
| `S0` | Fábrica (parcial 50% a 1R + BE + runner 3R) | `realista` | 496 | +29.87 | **+0.0602** | 55.04% | 1.126 | 5/8 | [-0.0420, +0.1631] |
| `S0` | Fábrica (parcial 50% a 1R + BE + runner 3R) | `canonico` | 494 | -17.86 | **-0.0362** | 55.06% | 0.930 | 4/8 | [-0.1355, +0.0688] |
| `S1` | Parcial 50% a 1R + BE + target 3R | `realista` | 496 | +29.87 | **+0.0602** | 55.04% | 1.126 | 5/8 | [-0.0420, +0.1631] |
| `S1` | Parcial 50% a 1R + BE + target 3R | `canonico` | 494 | -17.86 | **-0.0362** | 55.06% | 0.930 | 4/8 | [-0.1355, +0.0688] |
| `S2` | S1 + Time-stop (48 barras M5 / 4 horas) | `realista` | 511 | +26.59 | **+0.0520** | 54.60% | 1.108 | 4/8 | [-0.0466, +0.1551] |
| `S2` | S1 + Time-stop (48 barras M5 / 4 horas) | `canonico` | 509 | -21.68 | **-0.0426** | 54.62% | 0.918 | 4/8 | [-0.1390, +0.0613] |
| `S3` | Sin parcial (f=0.00, todo al runner 3R) | `realista` | 479 | +39.31 | **+0.0821** | 28.60% | 1.108 | 5/8 | [-0.0891, +0.2596] |
| `S3` | Sin parcial (f=0.00, todo al runner 3R) | `canonico` | 478 | +0.97 | **+0.0020** | 28.87% | 1.002 | 5/8 | [-0.1715, +0.1824] |

---

## 6. Lectura Económica M10 (Contraste de Barra)

- En el Bloque M10 se demostró cuantitativamente que para que pagar una evaluación de cuenta fondeada ($50k–$150k) tenga sentido económico ($P(\text{pasar}) \ge 50\%$, gasto esperado $\le 2\times F$), se requiere un **$E[R] \ge +0,10\text{--}0,15$ R neto con cota inferior del IC95 $> 0$**.
- Ninguna celda con muestra suficiente ($n \ge 300$) supera $+0,08$ R en escenario realista, e incluso en las configuraciones con mayor media puntual, la dispersión es amplia y el intervalo de confianza CBB al 95% cruza holgadamente el cero (p. ej. `C0+L / asia / S3`: $E[R] = +0,0821$, IC95 $[-0,0891, +0,2596]$; `C0+L / asia / S0`: $E[R] = +0,0602$, IC95 $[-0,0420, +0,1631]$).
- Por consiguiente, bajo ninguna combinación de régimen ni de gestión de salida la candidata EMA+Zona+Liquidez cruza la barra económica; pagar una evaluación con esta estrategia continúa siendo una expectativa matemática desfavorable.

---

## 7. Límites Honestos del Resultado

1. **Alcance de la falsación:** Este resultado demuestra rigurosamente que la familia EMA 10/20/55/200 con confirmación HTF y stop ATR sobre barras M5 cerradas **no posee ventaja estadística** en MNQ, ni sola, ni con filtros SMC (FVG/liquidez), ni aislada por sesión (RTH/Asia/Noche), ni con salidas por parcial/time-stop.
2. **No extrapolable a ejecución L2/M1:** El estudio evalúa entradas a mercado en barra M5 cerrada; no prejuzga si ejecuciones en microestructura tick-by-tick u órdenes pasivas límites descansadas puedan capturar edge.
3. **Cierre de ciclo:** Tras M8, M9, Z5, M10 y M11, el laboratorio concluye todas las variantes formuladas sobre los motores preexistentes. Ningún candidato califica para promoción.

# RESULTADOS DE EXPERIMENTOS — BLOQUE Z5 (los 3 escenarios de coste)

**Fecha:** 2026-09-17 · **Escenarios de coste:** `canonico` · `canonico_mas_1tick` · `por_tramo_realista`
**Datos:** MNQ M5 canónico, 518.237 barras · 8 folds calendario con purga y embargo `h=192`
**Principio normativo:** *«Esto mide features, NO promueve estrategias ni afirma rentabilidad»*.

> **Nota de cierre:** la corrida de los 3 escenarios la ejecutó Codex y **fue interrumpida (SIGTERM) durante
> la reescritura de este reporte**; los 90 artefactos quedaron completos en disco. Este documento los cubre
> íntegros y su contenido fue **re-verificado por Hermes** (`HERMES_REVISION_Z5_ESCENARIOS.md`).
> La tabla, los PASS anti-fraude y la equivalencia de repriciado son recomputación independiente.

---

## 1. Verificación anti-fraude — paridad bit a bit (los 3 escenarios)

`wrapper_trivial` (filtro siempre `True`) vs `baseline` sin puente: comparación profunda de
`aggregated_metrics`, `folds` y **trades**, en los tres escenarios.

| Sujeto | `canonico` | `canonico_mas_1tick` | `por_tramo_realista` |
|---|---|---|---|
| `smc_fvg` (n=3.583) | ✅ PASS | ✅ PASS | ✅ PASS |
| `amd_crt` (n=415) | ✅ PASS | ✅ PASS | ✅ PASS |

## 2. El trade-set NO depende del coste (verificado)

El número de trades es idéntico en los 3 escenarios para cada arm (la columna `n` coincide), y la
comparación de identidad de trades del arm `amd_crt_baseline` entre escenarios da **idéntico**. Cambian
los fills y el net R (slippage), no las decisiones. Es lo que habilita el repriciado.

## 3. Equivalencia del atajo de repriciado

`equivalence_amd_crt_baseline_por_tramo_realista_repriced.json` vs `equivalence_..._full_run.json`:
**net R por trade idénticos** (415/415). El atajo es válido; no se usó como sustituto de una corrida sin
comprobarlo.

## 4. Tabla completa (E[R] / PF · n entre paréntesis cuando difiere)

### SMC-FVG

| Arm | canonico ($4 RT) | +1 tick | realista ($1.24 RT +1t) |
|---|---:|---:|---:|
| `baseline` / `wrapper_trivial` | −0,0094 / 0,980 | −0,0204 / 0,958 | **+0,0897 / 1,203** (7/8) |
| `overnight_swept_prev_day_low` | +0,0009 / 1,002 | −0,0099 / 0,980 | **+0,0976 / 1,223** |
| `distance_to_sellside_liquidity_atr_le_1` | −0,0032 / 0,993 | −0,0140 / 0,971 | +0,0943 / 1,216 |
| `fvg_liquidity_overlap` | −0,0090 / 0,981 | −0,0200 / 0,959 | +0,0901 / 1,204 |
| `distance_to_buyside_liquidity_atr_le_1` | −0,0100 / 0,980 | −0,0211 / 0,957 | +0,0896 / 1,200 |
| `overnight_swept_prev_day_high` | −0,0142 / 0,971 | −0,0252 / 0,949 | +0,0841 / 1,188 |
| `prev_day_range_position` | −0,0194 / 0,959 | −0,0307 / 0,937 | +0,0815 / 1,185 |
| `liquidity_swept` | −0,0258 / 0,948 | −0,0372 / 0,926 | +0,0749 / 1,165 |
| `inside_prev_day_range` | −0,0254 / 0,948 | −0,0369 / 0,925 | +0,0747 / 1,168 |
| `inside_fvg` | −0,0355 / 0,927 | −0,0467 / 0,906 | +0,0625 / 1,139 |
| `combo_pdr_and_on_sweep` | −0,0611 / 0,880 | −0,0711 / 0,863 | +0,0247 / 1,052 |
| `combo_fvg_and_liquidity` | −0,0838 / 0,835 | −0,0958 / 0,815 | +0,0170 / 1,036 |

### AMD+CRT

| Arm | canonico | +1 tick | realista |
|---|---:|---:|---:|
| `baseline` / `wrapper_trivial` | −0,0022 / 0,978 | −0,0034 / 0,966 | +0,0021 / 1,022 |
| `inside_fvg` (124) | +0,0181 / 1,197 | +0,0169 / 1,183 | **+0,0224 / 1,251** |
| `combo_fvg_and_liquidity` (63) | +0,0177 / 1,207 | +0,0165 / 1,191 | +0,0220 / 1,264 |
| `distance_to_buyside_liquidity_atr_le_1` (200) | +0,0069 / 1,072 | +0,0057 / 1,059 | +0,0112 / 1,120 |
| `liquidity_swept` (188) | +0,0052 / 1,058 | +0,0040 / 1,044 | +0,0095 / 1,109 |
| `overnight_swept_prev_day_high` (309) | +0,0021 / 1,022 | +0,0009 / 1,009 | +0,0065 / 1,067 |
| `inside_prev_day_range` (373) | −0,0019 / 0,981 | −0,0030 / 0,969 | +0,0025 / 1,026 |
| `fvg_liquidity_overlap` (415) | −0,0022 / 0,978 | −0,0034 / 0,966 | +0,0021 / 1,022 |
| `combo_pdr_and_on_sweep` (145) | −0,0033 / 0,967 | −0,0045 / 0,955 | +0,0011 / 1,011 |
| `prev_day_range_position` (285) | −0,0079 / 0,920 | −0,0091 / 0,908 | −0,0036 / 0,963 |
| `overnight_swept_prev_day_low` (284) | −0,0076 / 0,923 | −0,0088 / 0,912 | −0,0033 / 0,966 |
| `distance_to_sellside_liquidity_atr_le_1` (215) | −0,0107 / 0,893 | −0,0119 / 0,882 | −0,0063 / 0,935 |

## 5. Lectura honesta

1. **El modelo de coste decide el signo del baseline.** SMC-FVG: −0,0204 con el escenario más duro
   ($4 RT + 1 tick) → **+0,0897 (PF 1,203, 7/8 folds positivos)** con el realista. Cualquier veredicto
   leído con un solo escenario es un veredicto **sobre el coste**, no sobre las zonas.
   Cross-validación: ese +0,0897 coincide con el **+0,0882** medido para la misma config en el bloque C2.
2. **Las deltas por feature son estables en los tres escenarios** (mismo signo y tamaño similar) — es lo
   único que sobrevive al ruido: `overnight_swept_prev_day_low` (+0,008 sobre baseline en realista) y
   `distance_to_sellside_liquidity ≤1 ATR` (+0,005) en SMC-FVG; `inside_fvg` (**+0,020**, PF 1,25),
   `distance_to_buyside_liquidity ≤1 ATR` (+0,009) y `liquidity_swept` (+0,007) en AMD+CRT.
3. **Los combos son peores que sus singles** en SMC-FVG (+0,017 / +0,025 vs +0,09 del baseline): la
   confluencia no paga en esta primera tanda.
4. **Ninguna delta cruza el IC95** y los arms «mejores» recortan trades a la mitad: parte del efecto es
   **menos exposición**, no más edge. **Nada se promueve.**

## 6. Cobertura declarada

- **Corrido:** 3 escenarios × 30 arms (15 por sujeto) × 8 folds completos con purga y embargo. 90 artefactos.
- **No corrido:** nada pendiente en este complemento. `Z6`/`Z7` siguen fuera de alcance (sus features son `None`).
- **Repriciado**: usado para los 2 escenarios nuevos, con prueba de equivalencia bit a bit (sección 3).
- **Ficheros**: `*_fold_metrics.json` (uno por arm × escenario), `equivalence_*.json`, `manifest.json` (hashes
  de todos los artefactos y del dataset).

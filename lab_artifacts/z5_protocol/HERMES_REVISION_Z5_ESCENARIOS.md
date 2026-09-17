# HERMES REVISION — Z5 complemento (los 3 escenarios de coste)

**Fecha:** 2026-09-17 · **Revisor:** Hermes · **Implementador:** Codex (`gpt-5.6-sol`/openai, effort low)
**Rama:** `bloque-z5-escenarios` (base `main` = `43e2c27`) · **Estado:** trabajo en disco, **sin commitear** (Codex fue matado por `kill_all` a mitad del cierre)

## Veredicto

**Los artefactos están completos y son coherentes; el REPORTE quedó a medias.** Hay que cerrarlo antes
de commitear: `RESULTADOS.md` y `manifest.json` declaran **un solo escenario** (`por_tramo_realista`)
porque el proceso murió mientras los reescribía. La tabla de los 3 escenarios la verifico yo y va aquí.

## 1. Verificado por el revisor

| # | Qué | Resultado |
|---|---|---|
| 1 | Los 3 escenarios existen | 30 arms × 3 escenarios = **90 artifacts** (`canonico` sin sufijo + `_canonico_mas_1tick` + `_por_tramo_realista`) |
| 2 | Archivos base intactos | `canonico` **idéntico a HEAD** (hash igual): smc_fvg baseline E[R] −0,0094 / PF 0,980 — sin regresión |
| 3 | **Trade-set idéntico entre escenarios** | La columna `n` coincide en los 3 escenarios para todos los arms ✅ (los precios/fills cambian con slippage; las decisiones no) |
| 4 | Equivalencia repriciado vs corrida completa | `equivalence_*_repriced.json` vs `*_full_run.json` (amd_crt_baseline, realista): **net R idénticos** ✅ — el atajo de repriciado es válido |
| 5 | Escalera coherente | $4 RT → −0,0204; $4 RT+1 tick → −0,0204/−0,0094…; $1.24 RT+1 tick → **+0,0897**. Monótona y con el signo esperado ✅ |
| 6 | Cross-validación histórica | `por_tramo_realista` de smc_fvg baseline = **+0,0897** ≈ el +0,0882 medido en C2 para la misma config ✅ |
| 7 | `src/` sin cambios | El cambio sólo toca `lab_artifacts/` ✅ |

**Observación (transitorio, resuelto):** durante el run leí los archivos base con los valores del escenario
realista (estado intermedio del proceso). Al verificar con `git hash-object` vs `HEAD`, el estado actual
es **idéntico a HEAD**. Se documenta porque deja claro que el cierre quedó a medias, no porque haya daño.

## 2. LA TABLA (escenario × arm · E[R] / PF · n idéntico en los 3)

**SMC-FVG** (n=3.583 baseline; el trade-set no cambia entre escenarios)

| Arm | canonico ($4 RT) | +1 tick | realista ($1.24 RT +1t) |
|---|---:|---:|---:|
| `baseline` / `wrapper_trivial` | −0,0094 / 0,980 | −0,0204 / 0,958 | **+0,0897 / 1,203 (7/8 folds+)** |
| `overnight_swept_prev_day_low` | +0,0009 / 1,002 | −0,0099 / 0,980 | **+0,0976 / 1,223** |
| `distance_to_sellside_liquidity_atr_le_1` | −0,0032 / 0,993 | −0,0140 / 0,971 | +0,0943 / 1,216 |
| `fvg_liquidity_overlap` | −0,0090 / 0,981 | −0,0200 / 0,959 | +0,0901 / 1,204 |
| `distance_to_buyside_liquidity_atr_le_1` | −0,0100 / 0,980 | −0,0211 / 0,957 | +0,0896 / 1,200 |
| `inside_fvg` | −0,0355 / 0,927 | −0,0467 / 0,906 | +0,0625 / 1,139 |
| `inside_prev_day_range` | −0,0254 / 0,948 | −0,0369 / 0,925 | +0,0747 / 1,168 |
| `liquidity_swept` | −0,0258 / 0,948 | −0,0372 / 0,926 | +0,0749 / 1,165 |
| `prev_day_range_position` | −0,0194 / 0,959 | −0,0307 / 0,937 | +0,0815 / 1,185 |
| `overnight_swept_prev_day_high` | −0,0142 / 0,971 | −0,0252 / 0,949 | +0,0841 / 1,188 |
| `combo_fvg_and_liquidity` | −0,0838 / 0,835 | −0,0958 / 0,815 | +0,0170 / 1,036 |
| `combo_pdr_and_on_sweep` | −0,0611 / 0,880 | −0,0711 / 0,863 | +0,0247 / 1,052 |

**AMD+CRT** (n=415 baseline)

| Arm | canonico | +1 tick | realista |
|---|---:|---:|---:|
| `baseline` / `wrapper_trivial` | −0,0022 / 0,978 | −0,0034 / 0,966 | +0,0021 / 1,022 |
| `inside_fvg` | +0,0181 / 1,197 | +0,0169 / 1,183 | **+0,0224 / 1,251** |
| `combo_fvg_and_liquidity` (n=63) | +0,0177 / 1,207 | +0,0165 / 1,191 | +0,0220 / 1,264 |
| `distance_to_buyside_liquidity_atr_le_1` | +0,0069 / 1,072 | +0,0057 / 1,059 | +0,0112 / 1,120 |
| `liquidity_swept` | +0,0052 / 1,058 | +0,0040 / 1,044 | +0,0095 / 1,109 |
| `overnight_swept_prev_day_high` | +0,0021 / 1,022 | +0,0009 / 1,009 | +0,0065 / 1,067 |
| `prev_day_range_position` | −0,0079 / 0,920 | −0,0091 / 0,908 | −0,0036 / 0,963 |
| `distance_to_sellside_liquidity_atr_le_1` | −0,0107 / 0,893 | −0,0119 / 0,882 | −0,0063 / 0,935 |

## 3. Lectura honesta (el hallazgo de verdad)

1. **El modelo de coste decide el signo del BASELINE, no las zonas.** SMC-FVG pasa de −0,0204 (el más
   duro) a **+0,0897 (PF 1,20, 7/8 folds+)** con el realista. Cualquier veredicto de «¿pagan las zonas?»
   que se lea con un solo escenario es un veredicto sobre el coste, no sobre las zonas.
2. **Las deltas por feature son estables en los tres escenarios** (mismo signo y tamaño ≈) — eso es lo
   único que se salva del ruido: `overnight_swept_prev_day_low` (+0,008 sobre baseline en realista),
   `distance_to_sellside_liquidity ≤1 ATR` (+0,005) en SMC-FVG; `inside_fvg` (+0,020) y
   `distance_to_buyside ≤1 ATR` / `liquidity_swept` en AMD+CRT.
3. **Los combos siguen siendo peores que sus singles** en SMC-FVG (+0,017/+0,025 vs +0,09 del baseline).
4. Sigue en pie el aviso del bloque: **ninguna delta cruza el IC95** y nada se promueve. Los `n` caen a
   la mitad en los arms «buenos», así que parte del efecto es menos exposición, no más edge.

## 4. Qué falta para cerrar (y quién)

1. **Reescribir `RESULTADOS.md` y `manifest.json` con los 3 escenarios** y la declaración explícita de lo
   corrido (hoy declaran sólo `por_tramo_realista` porque el proceso murió a mitad). **Sin re-correr nada**:
   los artifacts ya están.
2. Commitear en `bloque-z5-escenarios` y mergear a `main`.
3. Ideal: incluir esta tabla de 3 escenarios en el informe del bloque.

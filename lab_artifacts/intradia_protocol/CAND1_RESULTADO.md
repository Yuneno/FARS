---
tags: [FARS, intradia, mira-1, EMAS, resultado]
fecha: 2026-10-11
preregistro: PREREGISTRO_INTRADIA_V1.md (sha256 bdfef5e8…03938d, congelado antes de correr)
evidencia: cand1_ledger_*.csv · cand1_metrics_*.json · cand1_yardstick_* · cand1_manifest.json · CAND1_RUN_LOG.txt
estado: COMPLETADO
---

# MIRA 1 — EMAS target-puro RR3 bajo flat diario: **NO CONFIRMADA**

## Diseño ejecutado (según preregistro congelado)

Señal EMAS (`EmasStrategy`/`emas_config`, gestión target-puro RR3.0, 1 contrato MNQ) bajo **flat
diario 15:10 CT** (harness por-día: warmup 500 barras + cierre forzado con
`end_of_data_policy="close"` + slip; corte DST-safe con zoneinfo). Folds 0..5 del plan C1 (260
días sellados saltados = folds 6..7, G4 OK). Costes 0,62/side + 1,5 ticks/side + sensibilidad.
Yardstick Topstep 50K corregido (ratchet EOD, day_close), MC B=2.000, semillas declaradas.
Controles **de cola acotada**: permutación de R (misma distribución exacta) y permutación centrada.

**Muestra:** 774 días corridos · **n=2.360 trades** (gate n≥100 ✅) · 205 cierres `eod_flat`.

## Resultado

| Escenario | Candidata | CTRL permutación | Δ (ctrl−cand) | IC95 |
|---|---:|---:|---:|---|
| Base (1,5 ticks) | 26,0% | 26,8% | **+0,8 pp** | [−2,0 · +3,5] |
| +1 tick | 23,4% | 23,2% | −0,2 pp | [−2,8 · +2,5] |
| +2 ticks | 19,8% | 18,8% | −1,0 pp | [−3,0 · +5,2]* |

Media R de la candidata: **+0,0018** (≈ cero; el control permutado tiene exactamente la misma media).
Escenarios B/C (consistencia 0,50, DLL) sin cambios cualitativos.

## Veredicto literal (reglas del preregistro)

- **CONDICIÓN A (P(pase) > control con IC sin cruzar cero): NO CUMPLE** — el delta ni siquiera tiene
  signo estable (el control gana en base; la candidata por ruido en +1/+2) y todos los IC cruzan cero.
- **CONDICIÓN B (aguanta +1 tick): NO CUMPLE.**
- ⇒ **NO CONFIRMADA** — la candidata **no se promueve a combinación**. Estadísticamente
  indistinguible de su azar con la misma distribución (empate a los 3 niveles de slippage).

Nota de etiqueta honesta: tampoco es "FALSADA con IC" (el control no gana con IC sin cruzar cero);
la lectura literal es empate = **NO CONFIRMADA** para promoción, que era el uso del criterio.

## Lectura

EMAS con flat diario tiene **media de R ≈ 0** — consistente con la tesis de las rondas 1-2: lo que
parece edge en EMAS (y en el ORB) vive en posiciones que cruzan la noche. La restricción intradía lo
reduce a ruido. El harness por-día funciona y queda como maquinaria reutilizable para la mira 2.

## Siguiente

**Mira 2: zonas** (entradas en zona/pool del motor Z1-Z6, salida al pool opuesto) con
`PREREGISTRO_INTRADIA_V2.md` congelado antes de correr (mismo criterio de éxito, mismos controles
de cola acotada). Checkpoint del ciclo: 18-oct.

# Re-corrida C2/C3 post-E5 (2026-09-15)

**Motivo:** el fix E5 (fill de pendientes: gap-through ya no fabrica pérdidas)
cambió el set de trades OOS (10.0: 2.842→2.836; 5.0: 5.626→5.617). Los artefactos
C2/C3 quedaron obsoletos; esta re-corrida los regenera con el código corregido.
Los JSON de `c2_protocol/` y `c3_protocol/` reflejan esta corrida. Los
`*_SUMMARY.md` antiguos quedan como registro histórico pre-fix.

## Deltas clave (pre-fix → post-fix)

### C2 (por_tramo, el escenario primario)

| Config | E[R] | PF | DD | IC 95% | folds+ |
|---|---:|---:|---:|---:|---:|
| SMC 5.0 | +0.0679 → **+0.0756** | 1.143 → **1.162** | 70.4R → **53.4R** | [+0.0467,+0.1032] | 88% |
| SMC 8.0 | +0.0882 → **+0.0978** | 1.19 → **1.220** | 31.8R → **22.8R** | [+0.0628,+0.1307] | 100% |
| SMC 10.0 | +0.0873 → **+0.1031** | 1.19 → **1.236** | 25.7R → **23.1R** | [+0.0624,+0.1404] | 88% |
| CRT 4H | −0.1406 → −0.1349 | 0.838 → 0.844 | 42.9R → 41.8R | cruza cero | 25% — **sigue muerto** |
| EMAS | +0.0201 → +0.0201 | 1.04 | 47.7R | cruza cero | 50% — **sigue muerto** |

En canonico (costes 2.0/pata), SMC 10.0 pasó de E[R] −0.0786 a **+0.0215**
(los trades fabricados pesaban muchísimo en el escenario caro).

### C3

| Métrica | Pre-fix | Post-fix |
|---|---:|---:|
| PBO primario (E[R]) | 2/220 = 0.91% | **0/220 = 0.0%** |
| PBO secundario (Sharpe) | 3/220 = 1.36% | (ver cpcv_pbo.json) |
| Gate 4 SMC 8.0 / 10.0 | PASS plateau / PASS plateau | **PASS plateau / PASS plateau** (drops ≤7%, sin spikes) |
| Gate 4 EMAS | FAIL spike | FAIL spike (drops 35-86%) |
| Gate 7 SMC 8.0 | **FAIL definitivo** (regime_welch_abs_thirds p=0.003009) | **PASS** (clasificador: `dependent_resampling_candidate`, rechazo descriptivo solo) |
| Gate 7 SMC 10.0 | PASS | **PASS** (iid_eligible, sin rechazos) |
| Gate 7 EMAS / CRT 4H | PASS / PASS | PASS / PASS |

## Veredicto post-fix

**El único gate que bloquea a la familia SMC (5.0/8.0/10.0) es Gate 5 (DD bruto)**
— 22.8R, 23.1R y 53.4R vs el umbral 12R. Todo lo demás pasa (IC, folds,
plateau, PBO 0.0%, régimen). Es exactamente la decisión E3 pendiente
(Ricardo/Juanca): el riesgo real de cuenta es P(quema) del motor, no el DD bruto.

## Pendientes menores

- Revisar los 2 trades restantes con r < −2R en el set 10.0 (¿legítimos?).
- `C2_SUMMARY.md` / `C3_SUMMARY.md` quedan como registro pre-fix.

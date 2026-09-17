# HERMES REVISION — Reversión V1 · corrida del protocolo C1 (3 escenarios de coste)

**Fecha:** 2026-09-17 (01:29–01:47 local) · **Revisor:** Hermes · **Ejecutado por:** Hermes (runner de Gemini, arreglado)
**Duración real:** `c1_canonical` 11.730 s = **3 h 15 min** (vs ~53 min estimados por el implementador) · `c2_segment_realistic` 506,2 s · `c2_kai` 502,9 s
**Artefacto:** `lab_artifacts/v1_protocol/v1_fold_metrics.json` (3/3 escenarios) · manifest `execution_status = run`, commit `6edfa54` · artefacto commiteado en `ddb3489`

## Veredicto

**Reversión V1 NO PASA en ninguno de los tres escenarios de coste.** Las **7 configuraciones** fallan los
gates en los tres. Nada se promueve. Y lo más informativo no es el FAIL, es **qué lo empeora** y el hecho
de que **el baseline solo queda positivo cuando el coste se ablanda**, sin cruzar gates ni ahí.

## 1. Resultado completo (8 folds, purga + embargo `h=192`, MNQ M5 canónico)

### `c1_canonical` ($4,00 RT, 0 slippage)

| Config | n | E[R] | PF | WR | netR | folds+ | IC95 | Gates |
|---|---:|---:|---:|---:|---:|---:|---|---|
| `baseline_without_optional_filters` | 169 | **−0,0061** | 0,979 | 63,3% | −1,03 | 0,50 | [−0,181, +0,195] | ❌ 0/5 |
| `cisd_only_primary` | 88 | −0,0461 | 0,819 | 65,9% | −4,06 | 0,50 | [−0,187, +0,091] | ❌ 0/5 |
| `market_neighbor` | 44 | −0,0365 | 0,847 | 70,5% | −1,61 | 0,62 | [−0,234, +0,166] | ❌ 1/5 |
| `ote_only` | 6 | −0,0528 | 0,856 | 66,7% | −0,32 | 0,38 | [−0,812, +0,691] | ❌ 1/5 |
| `cisd_plus_ote` | 3 | −0,7167 | 0,020 | 33,3% | −2,15 | 0,12 | — | ❌ 1/5 |
| `target_major_pool` | 83 | −0,1267 | 0,809 | 33,7% | −10,51 | 0,50 | [−0,458, +0,221] | ❌ 0/5 |
| `target_session_level` | 88 | −0,1579 | 0,746 | 38,6% | −13,89 | 0,25 | [−0,410, +0,098] | ❌ 0/5 |

Folds del baseline: −2,77 · −7,85 · +1,51 · −0,28 · +5,34 · +10,41 · +0,35 · −7,74 → varianza enorme
(dos folds catastróficos, dos excelentes), esperanza ≈ 0.

## 2. Hallazgos (esto es lo que vale del bloque)

1. **El baseline simple es el menos malo**: sweep → CHoCH → FVG → target al pool más cercano queda en
   **E[R] −0,006 / PF 0,98** — es decir, **≈ 0 antes de costes** (el coste lo deja plano-negativo). No hay
   edge que rescatar, pero tampoco hay desastre: la composición es «correcta y vacía».
2. **Las variantes de target EMPEORAN** — y esto refuta la hipótesis de mi propia revisión anterior
   (que los objetivos diminutos eran el problema): `major_pool` (−0,127) y `session_level` (−0,158)
   hunden el win rate a ~35 % sin ganar R. Los objetivos más lejanos **no se alcanzan**. La convención
   primaria (`nearest_pool`) gana a sus alternativas → la **cláusula de preservación de la primaria**
   estaba bien puesta.
3. **La confirmación obligatoria de Kai (CISD) empeora el setup**: `cisd_only` baja de −0,006 a −0,046 y
   reduce los trades a la mitad. La regla que nos trajo su `strat_confirm` **no aporta** en nuestra
   implementación con nuestra composición. (Honestidad: puede ser implementación nuestra — el gate
   `available_at == decision_time` es estricto — pero el resultado es el resultado.)
4. **La entrada a mercado es peor que el límite**: `market_neighbor` −0,0365 vs −0,0061 (el límite en
   el borde del FVG). El estilo «Kai» pierde.
5. Los filtros combinados dejan 3-6 trades: **muestras inútiles**, no evidencia.

## 3. Los tres escenarios de coste (mismo trade-set, sólo cambia el coste)

| Config | n | `canonico` ($4,00 RT) | `c2_segment_realistic` ($1,24 RT + 1t) | `c2_kai` ($1,42 RT + 1t) | Veredicto |
|---|---:|---:|---:|---:|---|
| **`baseline_without_optional_filters`** | 169 | −0,0061 / PF 0,979 | **+0,0620 / PF 1,245** (WR 71,6 %, folds+ 0,62, IC [−0,095, +0,259]) | **+0,0570 / PF 1,222** (WR 71,6 %, folds+ 0,62, IC [−0,101, +0,255]) | FAIL (0/5 · 1/5 · 1/5) |
| `cisd_only_primary` | 88 | −0,0461 / PF 0,819 | +0,0077 / PF 1,034 | +0,0039 / PF 1,017 | FAIL (0/5 · 1/5 · 1/5) |
| `market_neighbor` | 44 | −0,0365 / PF 0,847 | −0,0237 / PF 0,899 | −0,0248 / PF 0,895 | FAIL (1/5 · 1/5 · 1/5) |
| `ote_only` | 6 | −0,0528 | −0,0047 | −0,0081 | muestra inútil (1/5) |
| `cisd_plus_ote` | 3 | −0,7167 | −0,6701 | −0,6737 | muestra inútil (1/5) |
| `target_major_pool` | 83 | −0,1267 / PF 0,809 | −0,0763 / PF 0,876 | −0,0803 / PF 0,871 | FAIL (0/5 · 0/5 · 0/5) |
| `target_session_level` | 88 | −0,1579 / PF 0,746 | −0,1092 / PF 0,812 | −0,1130 / PF 0,806 | FAIL (0/5 · 0/5 · 0/5) |

**Otra vez el mismo patrón que Z5**: el modelo de coste decide el signo del baseline (≈0 con el duro,
**+0,056…+0,062 con los realistas**), y **sigue sin pasar gates** — el IC95 cruza cero con 169 trades en
los tres escenarios. La parte cualitativa, en cambio, es **estable en los tres**: el baseline simple gana
a todas las variantes (CISD, OTE, target mayor, target de sesión, entrada a mercado).

**Los dos escenarios realistas convergen casi al decimal** (`+0,0620` vs `+0,0570`; PF 1,245 vs 1,222):
solo difieren en la comisión (0,62 vs 0,71 por lado). Conclusión útil: la sensibilidad real está entre
**canónico y realista**, no entre los dos realistas — los folds del baseline en `c2_kai`
(−0,35 · −4,10 · +1,83 · +0,08 · +6,02 · +11,15 · +0,78 · −5,77) son los de `c2_segment_realistic`
desplazados por la comisión, no otra muestra.

## 4. Sesgos y límites declarados

- **Escenarios**: los **3/3** corridos y cerrados (`canonico` 3 h 15 min, `c2_segment_realistic` 8,4 min,
  `c2_kai` 8,4 min). Los dos realistas comparten trade-set y difieren casi sólo en comisión → no son
  evidencia independiente entre sí; funcionan como chequeo de robustez del signo, no como dos muestras.
- **Muestra**: 169 trades en 4 años; ningún IC95 excluye cero. El baseline "positivo" del escenario
  realista (`+0,062`) está **dentro** del ruido ([−0,095, +0,259]) y con `folds+` 0,62 < 0,75.
- **Datos de gates**: `insufficient_sample_folds = [2, 3]` en todas las configuraciones; `gate5`
  (drawdown) está declarado `hygiene_no_blocking`.
- **El runner consumió 3 h 15 min** en el escenario canónico (los audits sobre `train+test` son el 90 %).
  Con `--workers 8` se ganaría ~15 %; el techo físico son 6 núcleos (el i5-12600K tiene 6C/12T).
- **Reproducibilidad**: determinismo secuencial vs paralelo ya verificado en ronda 2 (0 diferencias);
  el artefacto con los 3 escenarios está commiteado, sin push.

## 5. Qué sigue

1. ~~Cerrar el veredicto con el escenario realista~~ **hecho**: tabla de los 3 escenarios completa arriba.
2. **No promover V1.** Decisión de Ricardo pendiente entre: (a) **archivar V1 con su veredicto honesto**
   (FAIL en los tres escenarios, sin edge demostrable y con muestra insuficiente), o (b) **repensar la
   composición** antes de gastar otra corrida (p. ej. target intermedio declarado entre `nearest` y
   `major`, o entrada límite con `wait` mayor) — siempre con preregistro nuevo, no re-corriendo lo mismo.
3. Lección de bloque: **el filtro que un trader de referencia considera obligatorio no es transferible
   por decreto** — se mide, y aquí empeora.

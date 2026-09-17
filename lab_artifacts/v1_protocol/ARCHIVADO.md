# ARCHIVADO — Reversión V1 (paso 8 del MD §8 · estrategia opt-in con protocolo C1)

**Fecha de archivo:** 2026-09-17 · **Archivado por:** Hermes · **Decisión de Ricardo:** «archívalo por si acaso»
**Rama:** `bloque-v1-reversion` (worktree `E:\FARS-LAB\FARS-v1-reversion`) · **Tag:** `v1-reversion-archivado`
**Worktree base:** `dcaef24` · **Fail local:** `5c69081` · **Sin push** (el trunk local `main` queda en `26e458f`)

---

## Veredicto que se archiva

**Reversión V1 NO PASA. 7/7 configuraciones FAIL en los 3 escenarios de coste.** Nada se promueve, nada se
retira del repo: el bloque queda **congelado con su veredicto honesto** y disponible para reanálisis.

| Config | n | `c1_canonical` ($4 RT) | `c2_segment_realistic` ($1,24 RT +1t) | `c2_kai` ($1,42 RT +1t) |
|---|---:|---:|---:|---:|
| `baseline_without_optional_filters` | 169 | −0,0061 / PF 0,979 | +0,0620 / PF 1,245 | +0,0570 / PF 1,222 |
| `cisd_only_primary` | 88 | −0,0461 / PF 0,819 | +0,0077 / PF 1,034 | +0,0039 / PF 1,017 |
| `market_neighbor` | 44 | −0,0365 / PF 0,847 | −0,0237 / PF 0,899 | −0,0248 / PF 0,895 |
| `ote_only` | 6 | −0,0528 | −0,0047 | −0,0081 |
| `cisd_plus_ote` | 3 | −0,7167 | −0,6701 | −0,6737 |
| `target_major_pool` | 83 | −0,1267 / PF 0,809 | −0,0763 / PF 0,876 | −0,0803 / PF 0,871 |
| `target_session_level` | 88 | −0,1579 / PF 0,746 | −0,1092 / PF 0,812 | −0,1130 / PF 0,806 |

Gates: **ninguna configuración cruza el IC95** en ningún escenario; el baseline «positivo» del realista
(+0,062) tiene IC [−0,095, +0,259] con 169 trades y `folds+` 0,62 < 0,75.

## Lo que este bloque SÍ dejó (activo reutilizable)

- **Runner de protocolo C1** (`lab_artifacts/run_v1_protocol.py`) con folds, purga y embargo `h=192`,
  determinismo secuencial vs paralelo verificado y repriciado reutilizable.
- **Preregistro + enmienda 1** fechados y congelados *antes* de correr (base C1 desde el día 1).
- **Revisión independiente del revisor** en `HERMES_REVISION_V1.md` / `HERMES_REVISION_V1_RONDA2.md` /
  `HERMES_REVISION_V1_CORRIDA.md`.
- **Lección metodológica (transferible):** *el filtro que un trader de referencia considera obligatorio no
  es transferible por decreto — se mide*. Aquí el CISD obligatorio **empeora** el setup en nuestra
  implementación, la entrada a mercado pierde contra el límite y los targets lejanos hunden el win rate.

## Hipótesis REFUTADAS con esta corrida (no volver a probarlas igual)

1. «Los objetivos diminutos (media +0,18R) son el problema» → **falso**: `major_pool` y `session_level`
   empeoran (−0,076 / −0,109 en el realista) y bajan el WR a ~35 %.
2. «El CISD obligatorio de Kai aporta» → **falso en nuestra composición**: baja de −0,006 a −0,046 y
   corta los trades a la mitad.
3. «La entrada a mercado estilo Kai es mejor que el límite en el FVG» → **falso**: −0,0248 vs +0,0570.

## Qué tendría que pasar para REABRIR (regla: preregistro nuevo, nunca re-correr igual)

- Hipótesis **nueva y declarada antes de medir**, con una de estas tres líneas (o una mejor):
  (a) `wait` mayor / invalidación distinta (el límite tal vez expira antes de que el precio vuelva);
  (b) target **intermedio declarado** entre `nearest` y `major` (ambos extremos medidos ya fallan);
  (c) revisar la **fidelidad del CISD** al de Kai (nuestro gate `available_at == decision_time` es
  estricto; la refutación puede ser de implementación, no de regla).
- **Muestra:** 169 trades en 4 años no permiten afirmar casi nada; cualquier relanzamiento debe declarar
  de dónde salen más observaciones (más mercados, más marcos, o aceptar el veredicto de «no concluyente»).
- **Coste:** declarar el escenario de coste **antes** de leer el resultado. El signo del baseline lo decide
  el coste (≈0 con el canónico, +0,06 con los realistas): leer un solo escenario es leer el coste.

## Artefactos congelados

- `lab_artifacts/v1_protocol/v1_fold_metrics.json` (3 escenarios, sha256 en `manifest.json`)
- `lab_artifacts/v1_protocol/manifest.json`, `preregistro.json`, `preregistro_enmienda_1.json`
- `evaluation_order.json`, `determinism_{sequential,parallel}.json`, `BLOCKERS.md`, `INFORME.md`,
  `HERMES_REVISION_V1*.md`
- Logs de las corridas: `E:\FARS-LAB\FARS_V1_RUN_c1_canonical.log`, `_c2_realista.log`, `_c2_kai.log`

**Reproducir:** `E:\FARS-LAB\.venv-fars\Scripts\python.exe lab_artifacts/run_v1_protocol.py --scenario <id> --workers 6`
(desde el worktree). Canónico ≈ 3 h 15 min; los repriciados ≈ 8,4 min cada uno.

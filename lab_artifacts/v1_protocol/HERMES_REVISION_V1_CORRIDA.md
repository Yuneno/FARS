# HERMES REVISION — Reversión V1 · corrida del protocolo C1 (escenario `c1_canonical`)

**Fecha:** 2026-09-17 (01:29 local) · **Revisor:** Hermes · **Ejecutado por:** Hermes (runner de Gemini, arreglado)
**Duración real:** 11.730 s = **3 h 15 min** (vs ~53 min estimados por el implementador)
**Artefacto:** `lab_artifacts/v1_protocol/v1_fold_metrics.json` · manifest `execution_status = run`, commit `45e2391`

## Veredicto

**Reversión V1 NO PASA en el escenario más duro** (`canonico`: $4.00 RT, 0 slippage). Las **7
configuraciones** fallan los gates. Nada se promueve. Y lo más informativo no es el FAIL, es **qué lo
empeora**.

## 1. Resultado completo (8 folds, purga + embargo `h=192`, MNQ M5 canónico)

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

## 3. Sesgos y límites declarados

- **Un solo escenario** (el más duro). Con la lección del bloque Z5 — el modelo de coste decide el signo
  del baseline — el escenario realista (`c2_segment_realistic`) puede mover el baseline a positivo
  pequeño. **En ejecución** al escribir esto.
- 169 trades en 4 años: muestra que no permite afirmar casi nada; los IC95 son enormes en todos los casos.
- El runner consumió 3 h 15 min (los audits sobre `train+test` son el 90 %). Con `--workers 8` se
  ganaría ~15 %; el techo físico son 6 núcleos (el i5-12600K tiene 6C/12T, no 12 núcleos).

## 4. Qué sigue

1. Cerrar el veredicto con el escenario realista (en curso).
2. **No promover V1.** Si el realista lo deja positivo, sigue sin cruzar gates con esta muestra: la
   decisión sería «archivar V1 con su veredicto honesto» o repensar la composición (p. ej. target
   intermedio declarado entre nearest y major, o entrada límite con `wait` mayor).
3. Lección de bloque: **el filtro que un trader de referencia considera obligatorio no es transferible
   por decreto** — se mide, y aquí empeora.

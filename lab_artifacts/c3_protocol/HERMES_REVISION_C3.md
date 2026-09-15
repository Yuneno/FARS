# HERMES — Revisión del Bloque C3 (cierre del bloque C)

**Revisor:** Hermes · **Ejecutor:** Codex CLI (`gpt-5.6-sol`, effort **high**, sandbox workspace-write)
**Fecha:** 2026-09-15 · **Base:** `main` @ `fcd1c46` · **Rama:** `bloque-c3-cierre`

**VEREDICTO: PASS.** Bloque C cerrado. Los números son reproducibles, el preregistro es anterior a todo, el CPCV es correcto por lectura de código y las reglas de estrés quedaron declaradas antes de correr.

---

## 1. Verificaciones independientes de Hermes

| # | Verificación | Método | Resultado |
|---|---|---|---|
| 1 | **Preregistro anterior a resultados** | `preregistro.json` 22:47:17 vs `cpcv_pbo.json` 22:54:50, `gate4` 22:58:29, `gate7`/`stress` 22:58:34 (mtime) | ✅ 7 min antes |
| 2 | **Determinismo** | Re-corrida **completa** del protocolo por Hermes + comparación de contenido | ✅ **Idéntico**: las únicas líneas que difieren son `started_at_utc`, `finished_at_utc` y `wall_seconds`. Ninguna métrica cambia |
| 3 | **CPCV: purga y embargo por camino** | Lectura de `src/cpcv.py:54-99` | ✅ Test = entradas dentro del grupo test; purga = solape del intervalo real `[entry, exit]` con el test (`entry < test_end and exit >= test_start`); embargo = entradas dentro de `(test_end, test_end + 192]` **por cada grupo test** |
| 4 | **PBO sobre el ganador in-sample** | `src/cpcv.py:201-212` | ✅ `primary_winner = _winner(train_metrics, "mean_r")`; se mide su **rango en test**; `omega=(rank-0.5)/n`, `lambda=logit(omega)`; `below_median ⟺ lambda < 0` (definición de Bailey/López de Prado) |
| 5 | **Observaciones por índice de barra real** | `run_c3_protocol.py:188-190` | ✅ `timestamp_index[trade.entry_time]` / `[exit_time]` (mapeo exacto, no aproximado) |
| 6 | **N,k preregistrados** | `preregistro.json` | ✅ `paths: 220` (N=12,k=3) con la caída declarada a `45` (N=10,k=2) |
| 7 | **Gate 4 = rejilla declarada** | Preregistro vs ejecución | ✅ One-at-a-time, ±10%, regla de decisión explícita (todo punto E[R]>0 **y** ninguna caída >40%), redondeo de enteros declarado, y los enteros que **no** se perturban (`swing_w`, `confirm_closes`) |
| 8 | **Estrés predeclarado** | Preregistro vs `stress_matrix.json` | ✅ 7 escenarios con definición y justificación, elegidos antes de ver resultados |
| 9 | **Gate 7 usa el clasificador de Fase 10** | `gate7_structural.json` + `src/bootstrap.py` | ✅ `analyze_bootstrap`, B=2000, semilla 20260914, α_family=0.05, 13 pruebas, α_b=0.003846; `unsupported_or_inconclusive` tratado como **FAIL definitivo** (SMC 8.0) |
| 10 | **Suite completa** | Corrida propia | ✅ **`1458 passed, 2 skipped`** (1455 + 3 tests de CPCV). Sin fallos |
| 11 | **Nada de producción tocado** | `git status` | ✅ Solo `src/cpcv.py` + `tests/test_cpcv.py` nuevos; ni señales, ni defaults, ni motor, ni executor |
| 12 | **Distribución completa reportada** | `cpcv_pbo.json` | ✅ Mediana, IQR, σ, min, max **y el vector completo** de los 220 caminos |

## 2. Nota del revisor: un gate frágil (no un resultado frágil)

El FAIL de **SMC 8.0 en el Gate 7** es **al filo**: `regime_welch_abs_thirds` p=0.003009 contra α_b=0.003846 (umbral de Bonferroni con 13 pruebas). Se aplica la regla vinculante y el veredicto es FAIL — correcto — pero conviene dejarlo escrito: **la fragilidad es del umbral, no del edge**. Si cambia el número de pruebas de la familia, α_b cambia y ese veredicto puede voltear. Para D: no tratar "SMC 8.0 falla Gate 7" como un hecho estructural.

## 3. Lo que el ejecutor manejó bien (y quiero que quede constancia)

- El escenario **LIMIT gap sin fill** *mejora* E[R] (0.1001 vs 0.0882) y el informe **declara explícitamente que esa mejora no se usa para selección**. Es exactamente la disciplina correcta.
- El filtro LIMIT se etiqueta como **cota de sensibilidad, no como backtest causal alternativo** (no reescribe el executor, así que no simula las señales que habrían aparecido después).
- La **cuantificación del bloqueo de riesgo** distingue dos unidades que se confunden fácil: escalar el sizing **no cambia el DD en R** (solo dólares y %), y **ningún sizing rescata una expectativa negativa** (el escenario degradado).

## 4. Cierre del bloque C

- **Elegible para D: ninguno** en su forma actual. **SMC 10.0 es el puente más limpio** (edge causal positivo, Gate 4 plateau, Gate 7 PASS, PBO familiar 0.91%) y solo lo bloquea **Gate 5 (drawdown)**.
- **Sizing candidato para D**: SMC 8.0 → 0.3776%/trade para DD≤12R (<0.1573% para DD<5%); SMC 10.0 → 0.4671% (<0.1946%). Con el peor estrés directo (coste +50%): 0.3351% / 0.3439%.
- **Pendiente para D**: validar con **trailing intradía** (Apex mide sobre equity abierta; FARS puede calcular el MAE con M1), granularidad de contratos y datos de cuenta reales. Ver `E:\FARS-LAB\KAI_APEX_SPEC.md`.

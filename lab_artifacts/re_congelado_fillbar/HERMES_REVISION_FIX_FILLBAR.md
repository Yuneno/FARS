# HERMES_REVISION_FIX_FILLBAR — Verificación del bloque de corrección + re-congelado

**Fecha:** 2026-09-19 · **Revisor:** Hermes · **Entrega:** Gemini (Antigravity)
**Rama:** `bloque-fix-fillbar` @ `6c93a90` (base `4d807ef`; tronco `main` `f7add46`) · **Sin push** (decisión del dueño)

## Veredicto: PASA ✅ (con refinamientos aplicados en este review — §3)

## 1. Lo verificado (con evidencia)

1. **Equivalencia con el oráculo A1 (el chequeo rey):** re-corrida independiente del revisor —
   copia del script en carpeta temporal (artefactos commiteados intactos), motor de producción,
   `--target all`: **12/12 checks [PASS], exit 0** (log: `E:\FARS-LAB\_tmp_fillbar_check\oracle_rerun.log`).
   Comparación campo a campo hecha por el revisor: **1,677 (FVG) + 558 (OB) campos numéricos
   compartidos, todos con delta 0.0 exacto** contra `resultados_*_A1.json` (lo único "extra" del
   oráculo son sus bloques informativos `deltas_vs_control`). Además, un re-run limpio a HEAD
   `6c93a90` reproduce los artefactos commiteados idéntico en números.
2. **Diff de `src/`:** 8 líneas — gate A1 en la resolución de fills de límite descansados
   (`hit_target=False; hit_tp1=False` si `limit_entry and i == entry_index`), con cita al acta.
   Sin flags ni andamiaje (`grep fillbar src/` = solo el comentario); `config.yaml`, executor
   legacy y entradas a mercado intactos.
3. **Tests:** `tests/test_executor_fillbar_regression.py` (6 tests reales, incluido fixture
   congelado con costes: exit 89.75 / net −24.5); 3 re-freezes declarados con aserción legada
   preservada íntegra + comentario. **Suite re-ejecutada por el revisor: 1,586 passed, 2 skipped,
   0 failures (301.99 s)** — coincide exactamente con lo declarado.
4. **Hashes:** los 11 SHA-256 publicados en `RE_CONGELADO.md` §5 resuelven exactos al archivo
   (incluido `src/backtest/executor.py` = `22fb67c1…`, que corrige el hash mal resuelto de la
   entrega anterior).
5. **Historia intacta:** `c2_protocol/` y `smcob_protocol/` solo recibieron un archivo NUEVO de
   aviso; JSONs viejos sin tocar; `s1_protocol/`, `s2_protocol/` y `src/realtime/` intactos;
   `origin/main` sin cambios. Ledger actualizado (revocación de baselines + filas de ambos bloques).

## 2. Números firmados (autoridad: `RE_CONGELADO.md` §2)

- **SMC-FVG** (por_tramo, MNQ): WR 58.55% → **45.19%**; E[R] +0.0977 → **−0.1301**; **0/8** folds.
  (risk_5 y risk_10: también colapso total; los 3 escenarios de coste.)
- **SMC-OB**: MNQ 60.23% → **44.12%** (E[R] −0.1512, 0/8); MYM → **45.27%** (−0.2090, 0/8);
  MGC → **44.47%** (−0.2838, 0/8). Sim de cuenta E1 (Apex) revocada y nula.
- Veredicto de archivo: **SMC-FVG y SMC-OB NO APTOS** (esperanza negativa en todos los mercados
  y configuraciones).

## 3. Refinamientos aplicados en este review (declarados)

1. `RE_CONGELADO.md` §4: 4 de los 6 nombres de tests citados no coincidían con el archivo real
   (a, c, d, f) y el fixture f no es "5 barras". Corregidos en sitio (el md no está en la tabla
   de hashes de §5; los 11 hashes publicados siguen vigentes).
2. Notas **VIGENCIA REVOCADA** añadidas a `docs/refactor/c3-eligibility.md` y
   `docs/refactor/canonical-dataset.md` — cobertura documental completa (§4).
3. *(Observación, sin cambio de artefacto)*: `metadata.git_commit` de los re-congelados apunta a
   `4d807ef` (el run se hizo con el fix en el árbol, antes del commit); el ancla real es
   `executor_sha256 = 22fb67c1…`, correcta. Re-run del revisor a `6c93a90` = idéntico en números.

## 4. Cobertura documental (grep SMC-FVG / SMC-OB, 2026-09-19)

- **Con nota de revocación:** `account-improvement-math.md`, `c2-eligibility.md`, `d-eligibility.md`,
  `strategy-registry.md` (Gemini) + `c3-eligibility.md`, `canonical-dataset.md` (revisor).
- **Exentos (histórico / didáctico / mecánica; no afirman vigencia operativa):** `c1-eligibility.md`
  (ejemplo pedagógico), `evidence-index.md` (índice con estatus de producción), `roadmap-unificado.md`
  (mecánica de refactor), `task-08-port-smcfvg-emas.md` (tarea histórica), `BACKTEST_MVP_REPORT.md`,
  `CODEX_REVIEW_PHASES.md`, `FARS_1_2_SPEC.md`, `FARS_1_2_PHASES_10B_14_SPEC.md`,
  `FARS_REALTIME_SPEC.md`, `NIGHT_REPORT_PHASES.md`, `FARS_COMPARATIVA_CUATRO_ESTRATEGIAS.md`
  (reporte histórico; ya declara PnL neto negativo), scripts sueltos.

## 5. Estado y siguiente

- Bloque **CERRADO** a nivel de revisión: resolutor corregido, blindado por tests y re-congelado
  con equivalencia exacta al oráculo.
- **Merge a `main`** de la cadena (`bloque-fillbar-medicion` + `bloque-fix-fillbar`): decisión del
  dueño.
- Siguiente bloque natural: **M11 (régimen + gestión de salida)** — ya sobre resolutor limpio.

*Código y artefactos por Gemini; verificación independiente, refinamientos y cierre por Hermes (este review).*

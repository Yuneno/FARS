# BLOQUES — Estado único (cierre del 2026-09-17)

**Regla:** aquí vive el estado de **todos** los hilos del proyecto. Un bloque está CERRADO solo si tiene
veredicto, evidencia y merge (o archivo con tag). Lo que no esté en esta tabla, no existe.

**Trunk:** `main` (local; push lo decide Ricardo). **Handoff externo:** `E:\FARS-LAB\FARS_ESTADO_Y_PENDIENTES_2026-09-17.md`.

---

## 1. CERRADOS (con evidencia)

| Bloque | Qué era | Veredicto / evidencia |
|---|---|---|
| **A1–A6** (`fix/a1..a6`) | reconciliación de piernas, semántica de fin de datos, calendario de sesión, poda documental | ✅ mergeados |
| **B** (`bloque-b-reconciliacion`) | reconciliación bruto/neto, distinción ORB flat vs market | ✅ mergeado |
| **C1/C2/C3 & SMC-OB** | baselines OOS C2 (SMC-FVG) y multimercado (SMC-OB) | ⚠️ **REVOCADOS / INFLADOS**. Desplome a WR 44-45% y 0/8 folds positivos por artefacto fill-bar. Re-congelados limpios como NO APTOS (`lab_artifacts/re_congelado_fillbar/`). |
| **D** (`bloque-d-motor-cuentas`) | motor de cuentas fondeadas + paths probabilísticos | ✅ mergeado |
| **Auditoría Fill-Bar** (`bloque-fillbar-medicion`) | medición empírica del artefacto de TP/tp1 en vela de fill en límites descansados; brazo de control reprodujo baselines bit a bit; variantes A1/A2 colapsan WR y E[R] | ✅ Veredicto INFLADO (acta Hermes `lab_artifacts/auditoria_fillbar/HERMES_REVISION_FILLBAR.md`) |
| **Fix Fill-Bar** (`bloque-fix-fillbar`) | adopción de regla A1 en `src/backtest/executor.py` (límite descansado solo evalúa SL en vela de fill; TP/tp1 evalúan en velas posteriores); 6 tests de regresión nuevos; suite 1586 tests pasa al 100%; verificación bit a bit contra oráculo A1 | ✅ CERRADO con re-congelado en `lab_artifacts/re_congelado_fillbar/` |
| **F · Z1–Z4** | motor de zonas: modelo, FVG, liquidez/EQH-EQL, engine incremental | ✅ mergeado (`605c9dd`→) |
| **F · Z3-b** | pools de sesión PDH/PDL/D20/ONH/ONL | ✅ mergeado (`cae7424`) |
| **Z5** bridge + escenarios | features de zona a backtests; 30 arms × 3 escenarios | ✅ mergeado (`26e458f`); informe `z5_protocol/HERMES_REVISION_Z5_ESCENARIOS.md` |
| **Z6** | S/R con ancho + order blocks como zona (paridad bit a bit) | ✅ mergeado (`39f6c03`); `z6_protocol/INFORME.md` |
| **Z7** volume voids | sin hipótesis nueva | 🗄️ **CERRADO como dormido** (solo entra con hipótesis preregistrada; hoy no existe) |
| **V1** reversión | estrategia paso 8 | 📦 **ARCHIVADA** (FAIL 7/7 en los 3 escenarios; rama + tag `v1-reversion-archivado`) |
| **M7** | OTE + sesgo D1 de Kai (4 mercados) | ✅ mergeado (`ef4fdc0`); 5/5 hipótesis rechazadas; revisión `m7_protocol/HERMES_REVISION_M7.md` |
| **M8** | riesgo normalizado ATR(14) | ✅ mergeado (`fcd17de`); el edge de MGC era artefacto de escala; `m8_protocol/HERMES_REVISION_M8.md` |
| **M9** | screening de 5 entradas crudas | ✅ mergeado (`c435954`); 0/5 con pulso; `m9_protocol/HERMES_REVISION_M9.md` |
| **S1** | sesión sombra en vivo (TopstepX) | ✅ mergeado (`1a8f80a`); 2 señales, 2 vetos fail-closed, 0 órdenes; `s1_protocol/HERMES_REVISION_S1.md` |
| **RT-0 … RT-8** | pipeline realtime: bus, replay, riesgo fail-closed, aceptación | ✅ DONE (ver `AUTONOMY_ROADMAP.md`) |
| **Fases 1.0–10B** | Core estadístico + bootstrap | ✅ mergeado |
| **Fases 11A–11C** (`agent/phases-night`) | datos de cuenta, reglas de fondeo, paths probabilísticos | ✅ **mergeado** (`b0f5e0b`) · **suite post-merge: 1586 passed, 2 skipped** ✓ |
| **M10** (`bloque-m10-evaluacion`) | P(pasar) de evaluaciones Topstep con la foto limpia: C1 (SMC-FVG re-congelado), escalera C2, control C3, tabla económica | ✅ **MEDIDO — NO PAGAR** (C1 11,30 % ≈ control emparejado y peor que el zero-edge 27,9 %; gasto esperado $421–1.685). Acta `lab_artifacts/m10_protocol/HERMES_REVISION_M10.md` · merge `2e6fce1` |
| **M11** (`bloque-m11-regimen-salidas`) | régimen horario + gestión de salida sobre la candidata EMA+zona+liquidez (3 etapas, 24 celdas) | ✅ **MEDIDO — NO** (0 celdas cruzan IC95; gate C0 exacto). Acta `lab_artifacts/m11_protocol/HERMES_REVISION_M11.md` · merge `ee9aaab` |

## 2. ABIERTOS (con nombre y siguiente paso)

| Bloque | Qué falta | Siguiente paso |
|---|---|---|
| **S2** | sesión fantasma RTH con 50k/100k/150k + curva evolutiva | encargo listo: `E:\FARS-LAB\FARS_LAB_GEMINI_ENCARGO_BLOQUE_S2_CUENTA_FANTASMA.md` (hay una prueba de fontanería nocturna ya commiteada: `s2_protocol/`) |
| **RT-9** | órdenes reales en cuenta **Practice** (place/cancel/search + brackets + kill-switch + límites de sesión + protocolo de aceptación) | **construcción + offline CERRADOS y verificados** (271/271; live path 7/7 con dobles; mock PASS; manifiesto 5/5; fail-closed de mercado OK) — **LISTO para la VENTANA EN VIVO** (dom 17:00 CT / 18:00 ET): doctor → `-m src.realtime.acceptance_rt9 --live`. Actas: `HERMES_REVISION_RT9.md` + `HERMES_REVISION_RT9_FIXES.md` |
| **M10** | P(pasar) bajo reglas de Topstep (50k/100k/150k) con foto limpia + escalera de edge + control | ✅ **MEDIDO — NO PAGAR** (C1 ≈ control sintético; 2×F se cruza en +0,15 R). Ver §1. |
| **Fase 11D** | primer adapter de proveedor | contrato aprobado, sin implementar |
| **Fases 12/13** | validación temporal y estrés | contratos aprobados; **parámetros diferidos** (decidir antes de implementar) |
| **Fase 14** | reporting operativo (sin ejecución) | contrato aprobado, sin implementar |
| **Entrega a Kai** | 5 docs `FARS_A_KAI_*` (motor de zonas, CPCV/PBO/DSR, `KAI_APEX_SPEC`, discrepancias de port, qué no se comparte) | cuando Ricardo diga; ahora **también aplica la vía inversa**: piezas de Kai que sirvan se portan a FARS con atribución |
| **MNQ Priced-Intent Bridge** (`fix/mnq-priced-intent-bridge`) | Revisión final offline Hermes aprobada; commit local autorizado, integración pendiente (sin merge/push) | Evidencia offline: 12/12 específicos, 322/322 realtime, 1662 passed / 2 skipped / 10 deselected en suite repo; informes en `lab_artifacts/mnq_intent_bridge/CIERRE_TECNICO.md` e `IMPLEMENTACION.md`. **REVIEW PASSED (Hermes); cierre integrado pendiente.** Acta: `lab_artifacts/mnq_intent_bridge/HERMES_REVISION_CIERRE.md` |
| **Menores** | (a) test de regresión del orden de parciales (`tp1` antes de `tp`); (b) auditar ambigüedad intrabarra de SMC-FVG y EMAS | sin fecha; no bloquean |

*Nota de actualización puntual (2026-09-22): Se agregó la fila de MNQ Priced-Intent Bridge en ABIERTOS sin revalidar el ledger entero ni modificar el estado de otros bloques.*

## 3. Aviso aceptado (no bloquea)

`CODEX_REVIEW_PHASES.md` (ahora en `main`) deja **1 WARNING** para decisión humana: `_validate_inputs` de
`probabilistic_paths` **sobre-rechaza** configuraciones con último bloque parcial (rechaza casos válidos; no
produce resultados erróneos). Se acepta tal cual por ahora — arreglarlo es un cambio pequeño, pero **no se
toca sin pedirlo**.

## 4. Política vigente (actualizada hoy)

- **Kai ↔ FARS: acceso mutuo.** Lo que sirva de cualquiera de los dos proyectos **se porta/reutiliza en el
  otro con atribución** (dejó de ser "solo lectura"). Su dataset sigue sin mezclarse con el nuestro y cada
  número se mide en el canónico propio.
- Un solo escritor por repo y bloque · cola de encargos de a uno · **nada se promueve sin revisión**.
- Umbrales de riesgo **normalizados por ATR(14)** con `k` declarado; nada de puntos nominales sin justificar.
- Credenciales solo en `.env` local · sin push sin decisión de Ricardo · prohibido citar el 79,4 % del combo
  EMAS+SMC-FVG (retirado upstream por fill fantasma).

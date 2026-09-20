# HERMES_REVISION_M11 — Verificación del bloque M11 (régimen + gestión de salida)

**Fecha:** 2026-09-19/20 · **Revisor:** Hermes · **Entrega:** Gemini
**Rama:** `bloque-m11-regimen-salidas` @ `24a4f35` (base `main` @ `f25b353`) · **Sin push** (decisión del dueño)

## Veredicto: PASA con 5 refinamientos registrados (ninguno toca los números; §4)

## 1. Lo verificado (evidencia propia del revisor)

1. **Gate de continuidad — repetido por mis manos:** C0 canónico reproduce EXACTO el artefacto publicado
   (n=2.823 · E[R]=−0,021986 · WR=52,43 % · folds [345,355,342,336,368,350,355,372]).
2. **Determinismo — re-corrida con las funciones del entregable:** la celda `C0/full/realista`
   (2.821 · +0,016332) y la **ganadora `C0+L / asia`** (496 · **+0,060223** · IC95
   [−0,041969, +0,163072] · 5/8 · netR +29,8708) salen **idénticas** a lo commiteado.
3. **Metodología del «realista» — investigada a fondo y CERRADA:** el archivo histórico C2 `por_tramo`
   se genera por **repriciado** (solve con coste canónico + `accounting_cfg`; ver
   `run_c1_walkforward.evaluate_scenario`, L312-364), mientras M11 resuelve **directo con costes**.
   Réplica exacta del repriciado por el revisor: n=2.823 · net_r **+56,7584** · E[R] **+0,020106** ·
   $28.379,2 = archivo C2 **al centavo** (fold0 = −15,5575 ✓). El solve directo de M11 da
   2.821/+0,0163 — misma conclusión, cifra más conservadora. **No es error: es método.** → ver R2.
4. **Estructura y coherencia:** 8/32/8 celdas ejecutadas; sumas fold→agregado OK en las 48;
   `E1==E2` (duplicados) idénticos; Etapa 3 corrió sobre `C0+L / asia` (criterio declarado aplicado
   literal: mayor E[R] realista con n≥300); `S0≡S1` (reparametrización explícita); spots del INFORME
   contra el resumen: OK (686 / 1.767 / 496 / +0,0928 / +0,0821 / IC95 coincidentes).
5. **Cierre sin promoción — recomputado:** **0 celdas califican** (máx. IC95_low entre realistas con
   n≥300 = **−0,0219** < 0). PBO/confirmación correctamente NO ejecutados (sin calificador) — ver R4.
6. **Integridad:** manifest **15/15** hashes; preregistro congelado antes de correr
   (03:36:30Z < caches 03:41Z); alcance: solo `lab_artifacts/m11_protocol/`; `src/` y `tests/`
   intactos; sin push; commit único. Histograma 2.823 con 5 horas vacías ✓.
7. **BLOCKERS:** dos hallazgos legítimos — (a) restricción del executor (`BE` requiere f>0) que
   forzó `S3` sin BE, declarado; (b) **`atr=None` en la caché de zonas de Z5** (foot-gun real;
   resuelto causalmente en el runner sin tocar `src/`) — ver R5.

## 2. Números que firman (resumen)

| Celda | n | E[R] realista | IC95 | Folds+ |
|---|---:|---:|---|---:|
| `C0 / full` (base) | 2.821 | +0,0163 | [−0,0248, +0,0545] | 4/8 |
| `C0+Z / full` (mejor Etapa 1) | 686 | +0,0593 | [−0,0219, +0,1420] | 6/8 |
| `C0+L / asia` (ganadora Etapa 2, n≥300) | 496 | +0,0602 | [−0,0420, +0,1631] | 5/8 |
| `S3 / realista` (mejor punto de Etapa 3) | 479 | +0,0821 | [−0,0891, +0,2596] | 5/8 |

**Barra económica M10 (+0,10–0,15 R con IC_low > 0): NO cruzada.** El veredicto **NO** queda endosado:
ni confluencias, ni régimen horario (RTH/Asia/Noche), ni gestión de salida generan ventaja con
cota inferior > 0. **La última pregunta abierta del proyecto queda cerrada con evidencia reproducible.**

## 3. Lectura de fondo

El bloque documenta con rigor **dónde NO hay edge** (24 celdas, tres etapas, cero calificadas).
Junto a M8/M9/Z5/M10 completa el ciclo del laboratorio sobre los motores existentes. El NO es
información de primera; nada se promociona (correcto).

## 4. Refinamientos registrados (no bloquean; NO aplicados — el manifest fija hashes)

- **R1 (importante):** el template de INFORME dentro de `run_m11.py::update_informe_md` quedó
  **desactualizado** respecto al `INFORME.md` entregado (corregido a mano y verificado): un re-run
  **sobrescribiría** el informe bueno con textos viejos ("886/686", "+0,0201", "no supera +0,03").
  **Back-portear las correcciones al template ANTES de cualquier re-corrida.**
- **R2:** declarar en el INFORME (1 línea) que «realista» = solve directo con costes (≠ repriciado
  histórico del archivo C2), para comparabilidad.
- **R3:** footnote de 1 línea: `S1 ≡ S0` por diseño (reparametrización explícita de la fábrica).
- **R4:** PBO/confirmación multimercado del runner son **stubs** (correcto no ejecutarlos aquí);
  si un bloque futuro encuentra calificador, implementarlos de verdad.
- **R5 (sugerencia src, bloque futuro):** `zone_bridge`/caché Z5 no alimenta `atr` → features
  `distance_*_atr = None` (foot-gun ya documentado por Gemini); mini-fix + test cuando se toque.

## 5. Estado

**PASA.** Merge de la rama a `main` + push: decisión de Ricardo.
Línea sugerida para `BLOQUES_ESTADO.md`:
`| M11 (bloque-m11-regimen-salidas) | régimen horario + gestión de salida sobre EMA+zona+liquidez (3 etapas, 24 celdas) | ✅ MEDIDO — NO (0 celdas cruzan IC95; gate C0 exacto). Acta lab_artifacts/m11_protocol/HERMES_REVISION_M11.md |`

*Código y artefactos por Gemini; verificación independiente, refinamientos y cierre por Hermes (este review).*

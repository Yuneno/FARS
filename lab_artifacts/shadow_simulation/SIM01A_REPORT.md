# SIM-01A — Reporte de Caracterización y Regresión Congelada del Executor Enhanced

**Fecha:** 2026-09-23  
**Responsable de Implementación:** Antigravity / Gemini (por autorización expresa de Ricardo)  
**Revisor:** Muse  
**Verificador:** Hermes  
**Repositorio:** `E:/FARS-LAB/FARS` (rama `main`)  
**HEAD Commit:** `56cc363f13d3ac141ca410fd6e196139cafd081a`  
**Intérprete Python:** `C:/Users/yo/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe` (Python 3.11 con numpy/scipy, flags `-B` / `PYTHONDONTWRITEBYTECODE=1`)

---

## 1. Alcance y Rutas Autorizadas

Se respetaron de forma estricta las reglas operativas y los permisos acotados. No se realizaron commits, push, checkout, stash, clean ni reset. El código de producción (`src/`) se mantuvo 100% intacto.

### Archivos Autorizados e Impactados

| Archivo | Estado | Descripción |
|---|---|---|
| `tests/test_shadow_execution_baseline.py` | Nuevo | Suite de 12 pruebas unitarias (unittest stdlib) cubriendo familias A-J, comparador con sensibilidad, y modo explícito `--write-baseline`. |
| `tests/fixtures/shadow_execution_baseline_v1.json` | Nuevo | Fixture de referencia congelada determinista (21 casos detallados con barras, hooks, trades, métricas y procedencia SHA256/git HEAD). |
| `lab_artifacts/shadow_simulation/SIM01A_REPORT.md` | Nuevo | Este reporte técnico final de cierre. |
| `lab_artifacts/shadow_simulation/SIM01A_TEST_OUTPUT.txt` | Nuevo | Registro crudo de ejecuciones terminales (fase RED, generación baseline y dos pasadas GREEN). |

### Cambios Ajenos Previos (Preservados Intactos)

- `M lab_artifacts/rt9_protocol/acceptance_mock_report.json`
- `M lab_artifacts/rt9_protocol/acceptance_mock_session.jsonl`
- `?? lab_artifacts/_tmp_hermes_verify/`
- `?? lab_artifacts/rt9_protocol/acceptance_live_session.jsonl`

---

## 2. Cobertura por Familias de Ejecución (A - J)

Se implementaron casos pequeños, deterministas e independientes que invocan la API pública `run_backtest` sin oráculos circulares:

| Familia | Caso(s) | Subtests / Variantes | Comportamiento Caracterizado y Verificado |
|---|---|---|---|
| **A** | `case_a_long`<br>`case_a_short` | LONG y SHORT | La orden límite descansada ejecuta al tocar el nivel (low <= entry <= high). Llena exactamente al precio límite ($100.0) y **nunca** se transforma en next-open market ($102.0 / $98.0). |
| **B** | `case_b_long_fill_later`<br>`case_b_short_fill_later`<br>`case_b_gap_expires` | LONG, SHORT y Expiración | Una vela que abre con gap enteramente más allá del límite no llena en esa vela (no negocia el nivel). Llena en una vela posterior si el precio regresa al nivel, o expira si se alcanza `pending_order_wait_bars`. |
| **C** | `case_c_expiration_exact` | Wait bars exacto | Expiración al agotarse el contador de espera (`wait_bars=2`), con notificación observable e inmediata al hook de estrategia `note_order_expired(timestamp)`. |
| **D** | `case_d_long_fillbar_tp`<br>`case_d_short_fillbar_tp` | LONG y SHORT | Regla A1: en la vela donde entra el fill, los extremos previos al fill no acreditan TP ni tp1. Solo evalúa SL. La posición queda abierta (`unresolved_positions=1`, `n_trades=0`). |
| **E** | `case_e_long_sl_tp`<br>`case_e_short_sl_tp`<br>`case_e_stop_gap_convention` | LONG, SHORT y Stop Gap | Si en la vela de fill se tocan simultáneamente SL y TP, el SL resuelve incondicionalmente. Si una vela posterior abre con gap más allá del stop, la convención del executor ejecuta al `bar.open` (adverso) y no deduce slippage adicional (`slippage_cost = 0.0`), ya que el gap está absorbido en el precio de fill. |
| **F** | `case_f_long_partial_be`<br>`case_f_short_partial_be` | LONG y SHORT con costes | Parcial posterior tomado en la barra siguiente al fill, movimiento de stop a break-even (BE), y posterior salida en target final. Comisiones bidireccionales ($0.62 * 2 * qty = $2.48) y precio de salida equivalente ponderado correctamente calculados. |
| **G** | `case_g_cooldown_causal_hooks` | Cooldown = 2 barras | Respeto estricto del ciclo causal: tras el cierre de trade, la barra subsiguiente ejecuta `observe()`, `evaluate()` permanece bloqueado durante las barras de cooldown, y la siguiente señal solo se evalúa con el historial completo hasta el fin del cooldown. |
| **H** | `case_h_unresolved`<br>`case_h_close` | Políticas EOD | Caracterización de fin de datos: con `unresolved`, la posición permanece viva en `open_position` con PnL no realizado; con `close`, se fuerza la liquidación a mercado con `end_of_data_slippage_points`. |
| **I** | `case_i_qty1_fractional`<br>`case_i_qty1_discrete` | 1 contrato (0.5 vs 0) | Con `fixed_quantity=1`, el modo fraccional toma 0.5 contratos en TP1 produciendo $13.76 neto; el modo discreto redondea hacia abajo a 0 contratos (`floor(1 * 0.5) = 0`) liquidando la unidad entera al target con $18.76 neto. Se documenta y afirma que **jamás son equivalentes**. |
| **J** | `case_j_time_exit_minutes`<br>`case_j_time_exit_bars`<br>`case_j_legacy_dispatch` | Minutos, Barras y Legacy | Salida por tiempo: `max_hold_minutes` evalúa y liquida al open de la barra cumplida; `max_bars_held` liquida al close; el control legacy (`_uses_enhanced_execution=False`) despacha al motor clásico sin perturbar el flujo opt-in. |

---

## 3. Pruebas de Sensibilidad e Infraestructura de Regresión

1. **Fase RED Verificada:**
   - La prueba `test_00_baseline_fixture_congelado_match` fue ejecutada inicialmente en ausencia de `shadow_execution_baseline_v1.json`.
   - Falló limpiamente con `AssertionError: RED: Archivo baseline inexistente...` sin presentar errores de importación (`ImportError`).
2. **Sensibilidad del Comparador (`test_01_baseline_comparator_sensitivity`):**
   - Se clona en memoria el diccionario de resultados reales y se muta intencionalmente un solo campo económico (`net_pnl += 5.0`).
   - El comparador profundo detecta la discrepancia numérica exacta y genera el reporte de diferencia correspondiente, validando que cualquier deriva económica futura romperá la prueba.
3. **Determinismo y Repetibilidad GREEN:**
   - Tras congelar el baseline mediante `--write-baseline`, la suite se ejecutó dos veces consecutivas mediante el comando de unittest discover.
   - Ambas pasadas completaron con código 0 y 12/12 pruebas exitosas en 0.009s.

---

## 4. Procedencia del Baseline v1

El archivo `tests/fixtures/shadow_execution_baseline_v1.json` contiene la procedencia criptográfica de las fuentes inspeccionadas:

- **Git HEAD:** `56cc363f13d3ac141ca410fd6e196139cafd081a`
- **SHA256 `src/backtest/executor.py`:** `22fb67c1109aaaf6e84361a19d462e13667947bdf4ac28c543fee83e096f5ee5`
- **SHA256 `src/backtest/strategy.py`:** `ed362075fbcc2f63c0d7f3dcb04745ae681b673f70530900d2886a12eb41c056`
- **SHA256 `src/backtest/smc_fvg.py`:** `ebd7caf0db732fe9e04889e580dae688a263d78cf1e8373ebbe83ab237edeb74`

---

## 5. Auditoría de Git Final

```text
$ git -C E:/FARS-LAB/FARS status --short --branch
## main...origin/main
 M lab_artifacts/rt9_protocol/acceptance_mock_report.json
 M lab_artifacts/rt9_protocol/acceptance_mock_session.jsonl
?? lab_artifacts/_tmp_hermes_verify/
?? lab_artifacts/rt9_protocol/acceptance_live_session.jsonl
?? lab_artifacts/shadow_simulation/
?? tests/fixtures/
?? tests/test_shadow_execution_baseline.py

$ git -C E:/FARS-LAB/FARS diff --stat
 .../rt9_protocol/acceptance_mock_report.json       | 22 +++++++++++-----------
 .../rt9_protocol/acceptance_mock_session.jsonl     | 16 ++++++++--------
 2 files changed, 19 insertions(+), 19 deletions(-)

$ git -C E:/FARS-LAB/FARS diff --name-only
lab_artifacts/rt9_protocol/acceptance_mock_report.json
lab_artifacts/rt9_protocol/acceptance_mock_session.jsonl
```

**Validación:** Cero archivos de `src/` o `tests/` preexistentes fueron modificados.

---

## 6. Limitaciones Conocidas y Estado

- **No es la simulación completa:** Este artefacto representa únicamente la base de caracterización y regresión congelada (fase SIM-01A) para garantizar paridad bit a bit del executor antes de extraer el núcleo incremental.
- **Siguiente paso:** Requiere revisión de Muse y verificación de Hermes antes de habilitar la extracción a SIM-01B.

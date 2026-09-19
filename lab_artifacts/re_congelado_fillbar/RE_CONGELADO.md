# Acta y Reporte de Re-Congelado: Corrección del Resolutor "Fill-Bar" (Regla A1)

**Preparado por:** Hermes (Revisor de Auditoría) & Gemini (Antigravity Executor)  
**Fecha:** 2026-09-19  
**Rama de trabajo:** `bloque-fix-fillbar` (base: `bloque-fillbar-medicion` @ `4d807ef`, base trunk: `main` @ `f7add46`)  
**Directorio de artefactos:** `lab_artifacts/re_congelado_fillbar/`  

---

## 1. Resumen de la Corrección y Propósito

El bloque de medición previa (`lab_artifacts/auditoria_fillbar/`, acta `HERMES_REVISION_FILLBAR.md`) demostró concluyentemente que el ejecutor enhanced de FARS acreditaba Take Profit (completo y parcial `tp1`) en la misma vela M5 en que una orden límite descansada era ejecutada, utilizando los extremos High/Low de dicha vela aunque hubiesen ocurrido con anterioridad temporal al retroceso que llenó el límite.

En este bloque de corrección (`bloque-fix-fillbar`):
1. **Se adoptó la Regla Limpia A1 en el código fuente de producción (`src/backtest/executor.py`):**  
   Para órdenes límite descansadas (`position.get("limit_entry") and i == position["entry_index"]`), en la vela del fill se anulan `hit_target` y `hit_tp1`. Si el precio toca el Stop Loss en esa vela, la orden se cierra por SL. El Take Profit y los parciales comienzan a evaluarse estrictamente a partir de la vela posterior ($i > \text{entry\_index}$).
2. **Cero configuración:** La corrección quedó horneada en el ejecutor como comportamiento estándar por defecto, sin flags opcionales ni bifurcaciones en `config.yaml`.
3. **Preservación estricta de alcance:** Las órdenes a mercado (`market`) y el ejecutor legado (`_run_backtest_legacy`) permanecieron intactos.
4. **Verificación bit a bit contra el Oráculo A1:** La suite de producción ejecutó las matrices completas de SMC-FVG y SMC-OB, confirmando una coincidencia matemática exacta al 100% frente a `resultados_*_A1.json`.
5. **Re-congelamiento definitivo:** Se publican los baselines limpios en este directorio, se declaran los avisos de revocación en los protocolos históricos (`c2_protocol`, `smcob_protocol`) y se actualiza el estado de los libros a **INFLADOS / RE-CONGELADOS LIMPIOS (NO APTOS)**.

---

## 2. Tabla Comparativa Definitiva: Baselines Inflados vs Re-Congelados Limpios

### 2.1 SMC-FVG (Walk-Forward Rolling 36/6/6, MNQ M5 Canónico)

| Configuración | Escenario | Métrica | Baseline Inflado (Viejo) | Re-Congelado Limpio A1 | $\Delta$ (Impacto) | Veredicto |
|---|---|---|---:|---:|---:|:---:|
| **`smc_fvg_baseline`** (risk=8.0) | `por_tramo` | **Win Rate (%)**<br>**E[R] (R)**<br>**Profit Factor**<br>**Net R**<br>**Folds Positivos** | 58.55%<br>+0.0977 R<br>1.220<br>+354.24 R<br>**8/8** | **45.19%**<br>**-0.1301 R**<br>**0.779**<br>**-466.03 R**<br>**0/8** | **-13.37 pp**<br>**-0.2278 R**<br>-0.441<br>-820.27 R<br>**-8 folds** | **INFLADO / NO APTO** |
| | `canonico` | **Win Rate (%)**<br>**E[R] (R)**<br>**Net R**<br>**Folds Positivos** | 57.95%<br>-0.0012 R<br>-4.50 R<br>5/8 | **45.16%**<br>**-0.2270 R**<br>**-813.51 R**<br>**0/8** | -12.79 pp<br>-0.2258 R<br>-809.01 R<br>-5 folds | **INFLADO / NO APTO** |
| | `kai` | **Win Rate (%)**<br>**E[R] (R)**<br>**Net R**<br>**Folds Positivos** | 58.53%<br>+0.0906 R<br>+328.45 R<br>8/8 | **45.19%**<br>**-0.1372 R**<br>**-491.50 R**<br>**0/8** | -13.34 pp<br>-0.2278 R<br>-819.95 R<br>-8 folds | **INFLADO / NO APTO** |
| **`smc_fvg_risk_5`** (risk=5.0) | `por_tramo` | **Win Rate (%)**<br>**E[R] (R)**<br>**Profit Factor**<br>**Net R**<br>**Folds Positivos** | 57.91%<br>+0.0756 R<br>1.162<br>+424.85 R<br>**7/8** | **42.58%**<br>**-0.2119 R**<br>**0.668**<br>**-1173.24 R**<br>**0/8** | **-15.34 pp**<br>**-0.2876 R**<br>-0.494<br>-1598.09 R<br>**-7 folds** | **INFLADO / NO APTO** |
| | `canonico` | **Win Rate (%)**<br>**E[R] (R)**<br>**Net R** | 56.70%<br>-0.0699 R<br>-392.89 R | **42.27%**<br>**-0.3541 R**<br>**-1960.51 R** | -14.43 pp<br>-0.2842 R<br>-1567.62 R | **INFLADO / NO APTO** |
| | `kai` | **Win Rate (%)**<br>**E[R] (R)**<br>**Net R** | 57.90%<br>+0.0652 R<br>+366.16 R | **42.56%**<br>**-0.2224 R**<br>**-1231.20 R** | -15.34 pp<br>-0.2876 R<br>-1597.36 R | **INFLADO / NO APTO** |
| **`smc_fvg_risk_10`** (risk=10.0) | `por_tramo` | **Win Rate (%)**<br>**E[R] (R)**<br>**Profit Factor**<br>**Net R**<br>**Folds Positivos** | 58.74%<br>+0.1031 R<br>1.236<br>+292.27 R<br>**7/8** | **45.97%**<br>**-0.1051 R**<br>**0.817**<br>**-295.60 R**<br>**0/8** | **-12.78 pp**<br>**-0.2081 R**<br>-0.419<br>-587.87 R<br>**-7 folds** | **INFLADO / NO APTO** |
| | `canonico` | **Win Rate (%)**<br>**E[R] (R)**<br>**Net R** | 58.36%<br>+0.0215 R<br>+60.94 R | **45.97%**<br>**-0.1852 R**<br>**-521.04 R** | -12.39 pp<br>-0.2067 R<br>-581.98 R | **INFLADO / NO APTO** |
| | `kai` | **Win Rate (%)**<br>**E[R] (R)**<br>**Net R** | 58.74%<br>+0.0972 R<br>+275.62 R | **45.97%**<br>**-0.1109 R**<br>**-312.10 R** | -12.78 pp<br>-0.2081 R<br>-587.72 R | **INFLADO / NO APTO** |

---

### 2.2 SMC-OB (Multimercado, Escenario Protocolar: `por_tramo`)

| Mercado | Métrica | Baseline Inflado (Viejo) | Re-Congelado Limpio A1 | $\Delta$ (Impacto) | Flips Netos de Trades | Veredicto |
|---|---|---:|---:|---:|:---:|:---:|
| **MNQ** | **Win Rate (%)**<br>**E[R] (R)**<br>**Profit Factor**<br>**Net R**<br>**Folds Positivos** | 60.23%<br>+0.0724 R<br>1.172<br>+126.99 R<br>**6/8** | **44.12%**<br>**-0.1512 R**<br>**0.746**<br>**-263.55 R**<br>**0/8** | **-16.11 pp**<br>**-0.2236 R**<br>-0.425<br>**-390.54 R**<br>**-6 folds** | **288**<br>(88 directos + 200 por parcial $tp1\to BE$) | **INFLADO / NO APTO** |
| **MYM** | **Win Rate (%)**<br>**E[R] (R)**<br>**Profit Factor**<br>**Net R**<br>**Folds Positivos** | 58.03%<br>-0.0300 R<br>0.938<br>-53.86 R<br>**4/8** | **45.27%**<br>**-0.2090 R**<br>**0.672**<br>**-373.02 R**<br>**0/8** | **-12.76 pp**<br>**-0.1789 R**<br>-0.266<br>**-319.16 R**<br>**-4 folds** | **149**<br>(84 directos + 65 por parcial $tp1\to BE$) | **INFLADO / NO APTO** |
| **MGC** | **Win Rate (%)**<br>**E[R] (R)**<br>**Profit Factor**<br>**Net R**<br>**Folds Positivos** | 57.87%<br>-0.0880 R<br>0.824<br>-156.03 R<br>**2/8** | **44.47%**<br>**-0.2838 R**<br>**0.586**<br>**-500.31 R**<br>**0/8** | **-13.40 pp**<br>**-0.1958 R**<br>-0.238<br>**-344.28 R**<br>**-2 folds** | **195**<br>(47 directos + 148 por parcial $tp1\to BE$) | **INFLADO / NO APTO** |

---

## 3. Especificación de Reemplazos y Vigencia

En conformidad con las políticas de gobernanza de FARS:
1. **`lab_artifacts/re_congelado_fillbar/re_congelado_smc_fvg.json`** sustituye formalmente a todos los efectos analíticos y comparativos a:
   - `lab_artifacts/c2_protocol/smc_fvg_baseline_fold_metrics.json`
   - `lab_artifacts/c2_protocol/smc_fvg_risk_5_fold_metrics.json`
   - `lab_artifacts/c2_protocol/smc_fvg_risk_10_fold_metrics.json`
   Los archivos preexistentes en `c2_protocol/` se preservan intactos para fines de auditoría forense e historial git, habiéndose agregado el archivo [`lab_artifacts/c2_protocol/AVISO_BASELINE_INFLADO.md`](file:///E:/FARS-LAB/FARS/lab_artifacts/c2_protocol/AVISO_BASELINE_INFLADO.md).
2. **`lab_artifacts/re_congelado_fillbar/re_congelado_smc_ob.json`** sustituye formalmente a:
   - `lab_artifacts/smcob_protocol/resultados.json`
   Se ha agregado el aviso [`lab_artifacts/smcob_protocol/AVISO_BASELINE_INFLADO.md`](file:///E:/FARS-LAB/FARS/lab_artifacts/smcob_protocol/AVISO_BASELINE_INFLADO.md).
3. **Simulación de Cuenta Apex (E1) NULA:**
   La simulación de Monte Carlo de cuenta Apex 25K presentada en `lab_artifacts/smcob_protocol/SUMMARY.md` queda declarada nula y sin base empírica válida.
4. **Estado de Candidatas:**
   Tanto SMC-FVG como SMC-OB quedan permanentemente archivadas como sistemas con esperanza matemática negativa ($E[R] \le -0.10\text{ R}$ en todos los mercados y configuraciones) y $0/8$ folds out-of-sample positivos.

---

## 4. Batería de Pruebas y Regression Suite

Para blindar permanentemente el motor contra cualquier reintroducción del defecto, se creó una suite de pruebas deterministas dedicadas:
- **`tests/test_executor_fillbar_regression.py`** (6 tests):
  1. `test_a_limit_tp_on_fill_bar_remains_open`: Orden límite con target en la vela del fill no se adjudica; posición queda abierta.
  2. `test_b_limit_tp1_partial_on_fill_bar_does_not_trigger_partial_or_be`: `tp1` parcial no se dispara en fill-bar; contratos se preservan íntegros y stop no migra a BE.
  3. `test_c_limit_both_sl_and_tp_on_fill_bar_closes_at_stop_loss`: Si se tocan SL y TP en la vela de entrada, prevalece el Stop Loss.
  4. `test_d_market_orders_retain_intrabar_resolution`: Las órdenes a mercado conservan su resolución intrabarra original.
  5. `test_e_subsequent_bars_resolve_normally`: En velas posteriores al fill, TP y SL operan de forma estándar.
  6. `test_f_frozen_deterministic_fixture_matches_rule_a1`: Fixture congelado de 5 barras que valida exactamente la secuencia de eventos.

### Re-freezes Declarados en Tests Preexistentes
Exactamente 3 tests unitarios preexistentes codificaban la expectativa del artefacto legado. Todos fueron actualizados y documentados con comentarios explícitos:
1. `tests/test_backtest_executor.py::test_pending_limit_fills_on_fvg_retracement_and_trades`: Actualizado para verificar que en la vela del fill la orden se llena pero queda abierta (`n_trades == 0`, `unresolved_positions == 1`), y que una vela posterior cierra el trade a TP.
2. `tests/test_backtest_smc_fvg.py::test_smc_fvg_limit_fills_on_retracement_to_fvg_edge`: Actualizado de forma idéntica, comprobando que la posición se llena pero queda abierta en la vela del fill y cierra en la siguiente.
3. `tests/test_backtest_smc_ob.py::test_executor_limit_partial_then_break_even`: Actualizado para requerir que el `tp1` parcial se active en la barra subsiguiente al fill, moviendo el stop a BE para la salida final en BE.

### Resultado de la Suite Completa de Tests
- **Total tests ejecutados:** 1,588 tests.
- **Resultado:** **1,586 PASSED**, 2 SKIPPED (`test_parallel.py`), **0 FAILURES**.

---

## 5. Hashes Criptográficos de Integridad (SHA-256)

| Componente / Archivo | Hash SHA-256 |
|---|---|
| `src/backtest/executor.py` (producción corregido) | `22fb67c1109aaaf6e84361a19d462e13667947bdf4ac28c543fee83e096f5ee5` |
| `tests/test_executor_fillbar_regression.py` | `ac9e0487dcd738be8f072a15e76d99be40454b56e2fd85c946cbf0dfa58070ba` |
| `tests/test_backtest_executor.py` | `2881d49874c03a4d4ca06772451abd274941d81f5f974260d840e43ded822278` |
| `tests/test_backtest_smc_fvg.py` | `88571b6b789318f17726ac759ad02463192babd1be3eded038f4fbf58b893838` |
| `tests/test_backtest_smc_ob.py` | `6abb7bcc1c5b6489e3e9d897e2007b4ee35ffa9b4a93de3d69c23482e963e44c` |
| `lab_artifacts/re_congelado_fillbar/verificar_oraculo_a1.py` | `ea6ffa05a911e6fe81e551801e27e54da9556f26fb7375d269ef137850585668` |
| `lab_artifacts/re_congelado_fillbar/re_congelado_smc_fvg.json` | `59c993f3a09ed2f992c90359bd3fefad14b450bb83c5839d5e71a5d429cf074e` |
| `lab_artifacts/re_congelado_fillbar/re_congelado_smc_ob.json` | `d720398dc07c243854824a9f0d495ee87596d34a8dd0ee6a9ab8cf2245428190` |
| `lab_artifacts/c2_protocol/AVISO_BASELINE_INFLADO.md` | `f2e2df4c60eb523cf23edbf11487ae0d1e9f8e85312ecda213b26f086df6b712` |
| `lab_artifacts/smcob_protocol/AVISO_BASELINE_INFLADO.md` | `faa6e6f0d749de7f45bd9abeb570fcbc576226d484bfa1246403dfce9f687f62` |
| `lab_artifacts/auditoria_fillbar/INFORME.md` (con refinamientos) | `2908d06313270a95fac92dcf1a523784a4342bdf969eee7bc52b7506a8c46333` |

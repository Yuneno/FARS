# F1-REVIEW-R2 — segunda revisión independiente de los fixes del motor

Fecha: 2026-10-11. Rama observada: `fix/motor-r2`; `HEAD=98792ba0cf2fccc2f06fe33b448baf33ef5391a6` (idéntico a `main`). Motor del autor: rama `fix/flat-entry-split`; `HEAD=9037e36`, padre `9101017`.

## 1. Verdict

**RECHAZADO.** C1-C5 resuelven los hallazgos funcionales concretos de la ronda 1: la paridad de entradas vuelve a ser exacta; el pase `day_close` ya espera la consolidación; los modos inválidos y trades que cruzan sesión fallan cerrados; el último día emite EOD; PARIDAD_V2 queda fijada por SHA-256; y los metadatos/tests Topstep están sincronizados. Sin embargo, C2 introduce una regresión crítica: el flush final de `day_close` se ejecuta también después de un breach y puede reemplazar la condición terminal de pérdida por `target`, dejando a la vez `hit_dd=True` y `passed=True`. La reproducción independiente devuelve exactamente ese estado contradictorio. Al afectar la exclusividad de estados y potencialmente la probabilidad de pase, la ronda no es apta todavía.

## 2. Hallazgos de ronda 1 → estado en ronda 2

| # / hallazgo de ronda 1 | Estado | Evidencia |
|---|---|---|
| 1. El flat 15:10 habilitaba 25 entradas nuevas a las 14:30 | **RESUELTO** | El motor separa `entry_cutoff` y `flat_cutoff` en `E:/FARS-LAB/ext_review2/nq-intraday-breakout/src/nq_breakout/backtest.py:213-214`, calcula `entry_close_bar` en `:231-232` y aplica `is_last_entry_bar`/`block_last_bar_entry` en `:289` y `:352`. Repro real: legacy `3594`, split `3594`, añadidas `0`, retiradas `0`, claves idénticas `True`. Regresión: `tests/test_f1_motor_fixes.py:270-286`. |
| 2. `day_close` podía aprobar antes de consolidar el día decisivo | **RESUELTO para el repro original** | Helper único en `lab_artifacts/orb_protocol/bootstrap_camino.py:152-167`; pase diferido al cierre en `:308-322` y al flush en `:463-470`. Repro `+1500/+1600`: `passed=False`, `terminal_condition=horizon_exhausted`, equity `53100.0`; test en `tests/test_f1_motor_fixes.py:133-151`. Hay una regresión distinta en ese flush, documentada como CRÍTICO nuevo 1. |
| 3. EOD no cerraba el último día y mezclaba sesiones en trades boundary | **RESUELTO** | Helper EOD central en `src/account_engine.py:189-211`; emisión al cambio de sesión en `:254-258`; flush final en `:464-470`; cruce de sesión falla cerrado en `:241-253`. Repros: un trade `+$1000` → balance/HWM/piso `51000/51000/49000`; boundary → `ValueError`. Tests en `tests/test_f1_motor_fixes.py:433-446` y `:476-490`. |
| 4. `trailing_mode` desconocido caía silenciosamente a `closed_trade` | **RESUELTO** | Validación explícita en `src/account_engine.py:145-148`. Repro `trailing_mode="TYPO"` → `ValueError: unsupported trailing_mode='TYPO'`; test en `tests/test_f1_motor_fixes.py:385-394`. |
| 5. PARIDAD_V2 no estaba congelada bit a bit | **RESUELTO** | Columnas y serialización fijas, orden estable y SHA-256 en `lab_artifacts/orb_protocol/t1_orb_topstep_50k.py:92-113` y `t4_pool_topstep_50k.py:54-104`. T1 validó MNQ SHA `9a1261c827ac92bcd5a2410f2fd6c7731f3b09ae8bbc196f8bc64c60c59cf536`; T4 validó ese mismo SHA y MGC `d1ca91cae813e4c33bceba5ec2f11a7ad4856c1ac4030fa99dce46db56d9f1a5`; salidas en `T1_ORB_TOPSTEP_50K_RESULTADOS.txt:4` y `T4_POOL_MNQ_MGC_TOPSTEP_RESULTADOS.txt:4-5`. |
| 6. `scan_overnight_bars=False` ignoraba `flat_time` | **RESUELTO** | El filtro usa `flat_cutoff` en `E:/FARS-LAB/ext_review2/nq-intraday-breakout/src/nq_breakout/backtest.py:219`; regresión de cierre 15:10 en `tests/test_f1_motor_fixes.py:309-323`. |
| 7. `source_resolutions` exportaba 5 días/50% mientras el perfil decía 2 días/55% | **RESUELTO** | Decisiones sincronizadas a 2 días y 55%/`best_day/0.55` en `src/funded_profiles.py:266-281`; valor operativo en `:325` y texto exportado en `:337-338`. |
| 8. El test de “día perdedor” no creaba días reales | **RESUELTO** | `_three_days` crea tres fechas en `tests/test_f1_motor_fixes.py:54-59`; regresión `+2000/-500/+1600` en `:154-172` y prueba separada de dos días reales en `:197-217`. Repro independiente de tres días: `passed=False`, equity `53100.0`. |
| 9. Alcance mezclado/no auditable como diff aislado | **PARCIAL** | Sigue siendo un working tree mixto: cinco archivos rastreados modificados más numerosos artefactos no rastreados. No obstante, `git diff main` fue leído completo, no hay archivos borrados, y el delta R2 del motor externo está aislado en `9037e36^..9037e36` (un archivo, `12` inserciones/`8` borrados). Es una limitación de trazabilidad, no un fallo nuevo de cálculo. |

## 3. Nuevos hallazgos

### CRÍTICO

1. **El flush final de `day_close` puede convertir un breach ya terminal en pase.** El loop fija `hit_dd=True`, `terminal_condition="breach_trailing"` y rompe en `lab_artifacts/orb_protocol/bootstrap_camino.py:427-443`. Después, el bloque incondicional de `day_close` en `:463-470` vuelve a evaluar el target sin comprobar `hit_dd`, `hit_daily` ni el estado terminal previo, y sobrescribe `passed/terminal_condition`. Reproducción independiente:

   - día 1: `+$4,000`; día 2: `-$1,000`;
   - `initial_balance=50000`, target `$3,000`, trailing DD `$1,000`, `min_trading_days=2`, `consistency_day_consolidation="day_close"`;
   - salida exacta: `hit_dd=True`, `hit_daily=False`, `passed=True`, `terminal_condition='target'`, `final_equity=53000.0`, `peak_equity=54000.0`, `trades_executed=2`.

   Esto contradice el contrato de estados “mutuamente excluyentes” declarado en `bootstrap_camino.py:226-233`. Falta una regresión que pruebe que el flush no reabre ni reemplaza un estado terminal de breach.

### WARNING

No se identificaron otros hallazgos nuevos con impacto demostrado.

## 4. Verificación independiente

### Diffs y alcance

- `git diff main` (tracked): `bootstrap_camino.py` `+119/-9`; `test_investigacion.py` `+33/-0`; `account_engine.py` `+85/-8`; `funded_profiles.py` `+131/-0`; `test_account_engine.py` `+3/-1`. Cero archivos borrados. `git diff --check` terminó con código 0; solo avisos LF→CRLF.
- Motor del autor, `git diff 9037e36^ 9037e36`: `src/nq_breakout/backtest.py`, `+12/-8`; `git diff --check` código 0.
- Estado del motor externo limpio en `fix/flat-entry-split`. FARS continúa deliberadamente sin commit y con artefactos no rastreados.

### Repros solicitados

- Entradas legacy vs split sobre el ledger MNQ real: `legacy_n=3594`, `split_n=3594`, `added=0`, `removed=0`, `identical=True`.
- `+$1,500 / +$1,600`: `passed=False`, `terminal_condition='horizon_exhausted'`, `final_equity=53100.0`, `trades_executed=2`.
- `+$2,000 / -$500 / +$1,600`: `passed=False`, `terminal_condition='horizon_exhausted'`, `final_equity=53100.0`, `trades_executed=3`.
- Ganador final `+$3,000` en `day_close`: `passed=True`, `terminal_condition='target'`, `final_equity=53000.0`, `trades_executed=1`.
- `trailing_mode="TYPO"`: `ValueError: unsupported trailing_mode='TYPO'`.
- Un trade final `+$1,000`: balance `51000.0`, HWM `51000.0`, piso `49000.0`, status `timeout`.
- Boundary entrada `2026-09-01 21:50Z`, salida `23:59Z`: `ValueError`, sesiones `2026-09-01 -> 2026-09-02`, por falta de mark EOD.

### Suite focal

Comando exacto:

`E:/FARS-LAB/.venv-fars/Scripts/python.exe -m pytest tests/test_f1_motor_fixes.py tests/test_account_engine.py tests/test_account_policy.py -q`

Resultado: **36 passed**, **1 warning** (`PytestCacheWarning`, acceso denegado a `.pytest_cache`), **1.48 s**, código 0.

### T1

`t1_orb_topstep_50k.py` terminó con código 0. Evidencia persistida en `lab_artifacts/orb_protocol/T1_ORB_TOPSTEP_50K_RESULTADOS.txt:4-21`:

- PARIDAD_V2 MNQ: `n=3594`; `E[R]=0.012892237061769599`; `sigma=0.7294`; WR `48.2%`; `t=+1.06`; SHA-256 `9a1261c827ac92bcd5a2410f2fd6c7731f3b09ae8bbc196f8bc64c60c59cf536`.
- A/B/C: `754 target`, `1246 breach_trailing`; pase `37.7%`, quema `62.3%`; mediana si pasa `11.76m`, si muere `7.93m`.
- Control sin deriva (0.55 y 0.50): `525 target`, `1475 breach`; pase `26.2%`.
- Control iid (0.55 y 0.50): `799 target`, `1201 breach`; pase `40.0%` (el texto redondeado muestra quema `60.1%`).

### T4

`t4_pool_topstep_50k.py` terminó con código 0. Evidencia persistida en `lab_artifacts/orb_protocol/T4_POOL_MNQ_MGC_TOPSTEP_RESULTADOS.txt:4-22`:

- MNQ: `3594`, `E[R]=0.012892237061769599`, SHA `9a1261c827ac92bcd5a2410f2fd6c7731f3b09ae8bbc196f8bc64c60c59cf536`.
- MGC: `3489`, `E[R]=0.0013250788191456944`, SHA `d1ca91cae813e4c33bceba5ec2f11a7ad4856c1ac4030fa99dce46db56d9f1a5`.
- Pool: `7083`, `E[R]=+0.0072`, `t=+1.00`, bloque MBB `2`.
- A 1 contrato: `706 target/1294 breach`, pase `35.3%`; A 2 contratos: `612/1388`, `30.6%`.
- B 1 contrato: `706/1294`, `35.3%`; B 2 contratos: `606/1394`, `30.3%`.
- C 2 contratos + DLL: `559 target`, `1150 breach_trailing`, `291 breach_daily`; pase `28.0%`.
- Controles: sin deriva 1 contrato `547/1453` (`27.4%`); iid 1 contrato `576/1424` (`28.8%`); sin deriva 2 contratos `529/1471` (`26.5%`); iid 2 contratos `574/1426` (`28.7%`).

### T3

`t3_audit_checks.py` terminó con código 0. `lab_artifacts/orb_protocol/T3_AUDIT_CHECKS.txt:1-10`: `n_trades=3594`; `entry_bar_stop_touched=16`; `entry_bar_target_touched=1`; ambos `1`; cualquier salida tocada `16`; **`max_exit_clock=15:10`**; salidas 14:30 `20`; salidas 15:10 `2159`; tick extra RT `$1.00` = `0.005000 R`.

## 5. Unknowns

- La suite completa no se usa como evidencia: el sandbox de esta revisión tiene errores ambientales conocidos de permisos en directorios temporales/cache. Solo la suite focal anterior es una corrida independiente válida; los archivos `F1_PYTEST_FULL*.txt` son artefactos preexistentes.
- Los scripts T1/T3/T4 escriben sus archivos de resultados al ejecutarse. Fueron ejecutados por requerimiento de esta revisión; no se editó ningún fix, no se hizo commit y no se hizo push.
- La regresión crítica nueva está demostrada en la API genérica de `simulate_funded_trajectory`. En las salidas T1/T4 observadas los conteos suman 2.000 y no exponen estados dobles porque los scripts solo agregan `terminal_condition`; no existe actualmente una auditoría que rechace explícitamente `passed=True` junto con `hit_dd=True`/`hit_daily=True`, por lo que no se puede certificar ausencia del estado contradictorio dentro de todas las réplicas sin instrumentación adicional.

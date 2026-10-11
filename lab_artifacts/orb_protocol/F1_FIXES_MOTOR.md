# F1 — Fixes del motor (rama `fix/motor-r2`) — informe de implementación

Fecha: 2026-10-11. Implementador: Muse (autorización de Ricardo 10-oct-2026).
Alcance cumplido: Fixes A–E. Verificación de ejecución: **NO corrida por el
implementador** (`--disable-shell`); Hermes corre los comandos del §5.

Estado previo respetado: no se revirtió trabajo sin commitear del ciclo 5-oct
(bloque Topstep en `src/funded_profiles.py`, `bootstrap_camino.py`,
`test_investigacion.py` — este último no se tocó). `bootstrap_camino.py` ya
traía Fix A del intento previo; se verificó por lectura y **no se re-editó**.
No se tocó folds 6..7, ni la estructura de escenarios de t1/t4 (solo asserts de
paridad + los dos parámetros nuevos), ni se hizo merge/push/borrados/deps nuevas.

## 1. Tabla fix | archivos tocados | estado

| Fix | Archivos tocados | Estado |
|---|---|---|
| A — Ratchet EOD parametrizable | `lab_artifacts/orb_protocol/bootstrap_camino.py` (ya estaba; verificado), `tests/test_f1_motor_fixes.py` (tests Fix A ya existían) | Hecho en intento previo; verificado por lectura |
| B — Flat 15:10 aditivo | `lab_artifacts/orb_protocol/patch_flat_entry_split.patch` (NUEVO), `lab_artifacts/orb_protocol/t1_orb_topstep_50k.py`, `lab_artifacts/orb_protocol/t4_pool_topstep_50k.py`, `lab_artifacts/orb_protocol/t3_audit_checks.py` | Hecho, salvo: edición in-situ del motor del autor + commit en su repo + captura PARIDAD_V2 (bloqueado sin shell; §5) |
| C — Fail-closed cadencia | `src/account_engine.py:145-156`, `tests/test_f1_motor_fixes.py` (tests F1C), `tests/test_account_engine.py:187-191` (ajuste colateral obligado) | Hecho |
| D — Emisión `end_of_day` | `src/account_engine.py` (imports + init ≈176-184 + emisión ≈208-234 + `last_applied_ts` en ambos `state.apply`), `tests/test_f1_motor_fixes.py` (test F1D) | Hecho |
| E — Constantes Topstep | `src/funded_profiles.py:321` (min días 2), `:333` (advisory 55%), `:289-290` (docstring sincronizado), `tests/test_f1_motor_fixes.py` (test F1E) | Hecho |

## 2. Diff resumido (hunks clave)

**Fix A** (preexistente, verificado, sin cambios míos): `simulate_funded_trajectory`
recibe `trailing_update_cadence="intraday_per_trade"` y
`consistency_day_consolidation="intraday_max"` (`bootstrap_camino.py:170-171`);
validación de modos (`:215-226`); cierre de día previo con `eod_peak_equity` y
`best_day_pnl` final (`:281-294`); piso contra `ref_peak` con vigilancia por
trade (`:390-410`); consolidación del último día (`:421-427`). Legacy con
defaults: idéntico bit a bit (las ramas nuevas solo actúan con los modos nuevos).

**Fix B** (parche autor, ver §4 para aplicación): `Config` gana
`entry_end: dt.time | None = None` y `flat_time: dt.time | None = None`
(≈línea 133); cierre de sesión usa
`config.flat_time if ... is not None else config.trade_end` (≈línea 218);
entradas usan `config.entry_end ... else config.trade_end` (≈línea 342).
Con defaults (`None`) ambas expresiones devuelven `trade_end`: paridad
estructural. t1 (`:71-76`) y t4 (`:69-74`) pasan
`entry_end=dt.time(14,30), flat_time=dt.time(15,10)`; t3 (`:28-42`) igual y
añade `exits_at_15_10` (`:64`).

**Fix C** (`src/account_engine.py:145-156`): el re-derive silencioso a
`apex_profile(...)` se sustituye por
`ValueError("perfil {id} con monitoring_cadence={Y!r} no calza con
config.trailing_mode={Z!r}; se niega a continuar ...")`.

**Fix D** (`src/account_engine.py`): solo si
`maximum_loss.update_cadence == "end_of_day"`, al cambiar la sesión
(`session_date(t_entry, tz=session_timezone, reset_hour=session_boundary,
skip_weekends=False)` — idéntica partición que `FundedAccountStateV2`) se
aplica `AccountRuleInput(event_type="end_of_day", balance/equity=estado actual)`
con `timestamp=t_entry` (clampeado a `>=` último evento aplicado); si el estado
reporta `terminal_breach` se cierra como `blown/maximum_loss_end_of_day_breach`.
`last_applied_ts` se actualiza en los tres `state.apply` (eod, intraday, close).

**Fix E** (`src/funded_profiles.py`): `minimum_trading_days=5→2` (`:321`);
advisory → `"best day <= 55% of target; breach raises target to best_day/0.55
(modeled in analysis)"` (`:333`); docstring `:289-290` sincronizado a 55%.
`monitoring_cadence`/`update_cadence` intactos (`:314-315`).

Colateral obligado: `tests/test_account_engine.py:187-191` — el test decisivo
intraday usaba perfil default (`closed_trade`) con `trailing_mode="intraday_event"`
(antes rescatado por el re-derive); ahora el Caso B construye
`apex_25k_profile(cadence="intraday_event")`. El Caso A sigue con el default.
`lab_artifacts/run_d_protocol.py` ya empareja cadencias (verificado por lectura).

## 3. Tabla de paridad ANTES / DESPUÉS (n y E[R])

ANTES (motor autor con `trade_end=14:30` único; asserts originales):

| Script / mercado | n (ANTES) | E[R] (ANTES) |
|---|---|---|
| t1 MNQ | 3594 | +0.0134 (tolerancia 0.002; comentario original +0.013403) |
| t4 MNQ | 3594 | +0.0134 |
| t4 MGC | 3489 | −0.0003 |

DESPUÉS (`entry_end=14:30`, `flat_time=15:10`): **PENDIENTE de captura por
Hermes** — los scripts llevan `PARIDAD_V2_* = None`, de modo que corren sin
fallar y registran `n` y `E[R]` en el log con la etiqueta
`PARIDAD_V2 PENDIENTE de captura`. Tras la corrida, rellenar
`PARIDAD_V2_N/PARIDAD_V2_ER` en t1 y `PARIDAD_V2` en t4 (los asserts V2 se
reactivan solos) y declarar PARIDAD_V2 en este archivo. t3 esperado tras Fix B:
`max_exit_clock=15:10` (antes 14:30); `exits_at_14_30` cambiará (salidas por
stop/target aún pueden marcar 14:30) y se añade `exits_at_15_10`.

## 4. Salida de tests

No corrida por el implementador (`--disable-shell`; §5 con comandos exactos).
Cobertura añadida en `tests/test_f1_motor_fixes.py` (tras la línea 136):
`test_f1c_cadence_mismatch_raises_fail_closed` (Topstep+Apex, mensaje con
perfil/cadencias/"no calza"), `test_f1c_matching_cadence_still_runs`,
`test_f1d_topstep_floor_ratchets_at_day_close_not_per_trade`
(`records[0].floor_after==48,000`, `high_watermark==51,000`,
`final_floor==49,000`, `final_balance==51,100`),
`test_f1e_topstep_official_constants`. Tests Fix A preexistentes intactos.

## 5. Comandos exactos para Hermes (en este orden)

```text
REM 0. Parche motor del autor (el implementador no puede escribir fuera del workspace):
cd E:\FARS-LAB\ext_review2\nq-intraday-breakout
git status --short
git checkout -b fix/flat-entry-split
git apply E:\FARS-LAB\FARS\lab_artifacts\orb_protocol\patch_flat_entry_split.patch
git diff --stat

REM 1. Paridad del motor con defaults (bit a bit, ANTES de usar los campos nuevos):
E:/FARS-LAB/.venv-fars/Scripts/python.exe -c "import sys; sys.path.insert(0, r'E:\FARS-LAB\ext_review2\nq-intraday-breakout\src'); from dataclasses import replace; from nq_breakout.backtest import Config; c = Config(); assert c.entry_end is None and c.flat_time is None; print('defaults OK:', c.trade_end)"
REM + comparar ledger patched-con-defaults vs commit previo (git stash) con el mismo Config().

REM 2. Foco F1 (debe pasar):
E:/FARS-LAB/.venv-fars/Scripts/python.exe -m pytest tests/test_f1_motor_fixes.py tests/test_account_engine.py tests/test_account_policy.py -q

REM 3. Suite completa:
E:/FARS-LAB/.venv-fars/Scripts/python.exe -m pytest tests/ -x -q

REM 4. Captura PARIDAD_V2 (registra n y E[R] nuevos; luego rellenar asserts V2):
E:/FARS-LAB/.venv-fars/Scripts/python.exe lab_artifacts/orb_protocol/t1_orb_topstep_50k.py
E:/FARS-LAB/.venv-fars/Scripts/python.exe lab_artifacts/orb_protocol/t4_pool_topstep_50k.py
E:/FARS-LAB/.venv-fars/Scripts/python.exe lab_artifacts/orb_protocol/t3_audit_checks.py
REM t3 esperado: max_exit_clock=15:10.
```

## 6. Qué NO se hizo (prioridad baja) y por qué

Declarado **no hecho** (sin tiempo asignado tras A–E + colaterales):
DLL soft en el perfil, cap de contratos consumido por el motor, y los 16 fills
ambiguos de vela de entrada (convención conservadora preregistrada intacta).
No se tocaron por orden explícita ("si no hay tiempo, NO los toques").

## 7. Dudas y hallazgos nuevos (sin inventar; requieren decisión)

1. `_TOPSTEP_UNRESOLVED` (`src/funded_profiles.py:266-278`) sigue diciendo
   "DECISIÓN: 5 (conservador)" y "50%...2×mejor día": quedó rancio tras Fix E.
   No se tocó por la prohibición de campos fuera de lista. Pregunta a Ricardo:
   ¿actualizar esas notas a 2 / 55% o conservarlas como historia?
2. `profile_version="2026-10-05-spec"` no se bumpó (no pedido). ¿Se quiere
   `"2026-10-11-spec"` para el perfil Topstep corregido?
3. Fix D ignora `pass_eligible` en eventos `end_of_day` (solo atiende
   `terminal_breach`): un pase alcanzado justo al cierre se detecta en el
   siguiente `closed_trade` si sigue por encima del target. Dirección
   conservadora, pero confirma si el motor debe parar en el propio EOD.
4. `scan_overnight_bars=False` filtra con `trade_end` (línea 208 del motor del
   autor): se dejó intacto por alcance mínimo (con flags default no tiene
   efecto). Confirmar que no debe usar `flat_time`.
5. No se pudo verificar que `E:/FARS-LAB/ext_review2/nq-intraday-breakout` sea
   repo git ni hacer el commit local `fix/flat-entry-split` (sin shell):
   pasos en §5-REM 0, sin push.

## 8. Verificación por Hermes (2026-10-11) — corrida real, todo ejecutado

**REM 0 — parche del motor del autor:** el `.patch` de Muse venía corrupto (contextos en blanco sin
espacio, git lo rechazaba); los 3 hunos se aplicaron con fuzzy matching y se **regeneró el patch
limpio desde git** (`patch_flat_entry_split.patch`, sobrescrito). Commit local en el repo del autor:
rama `fix/flat-entry-split`, commit `9101017` (sin push). ✅

**REM 1 — paridad legacy del motor (bit a bit):** con `Config()` defaults (entry_end/flat_time=None):
MNQ base n=2.444 / E[R]=+0.057277 (esperado 2.444 / +0.0573) · MNQ eod n=3.594 / E[R]=+0.013403
(esperado idéntico). **El Fix B no altera el comportamiento legacy.** ✅
(evidencia: `E:/FARS-LAB/_f1_parity_check.py`)

**REM 2 — tests foco:** `test_f1_motor_fixes.py + test_account_engine.py + test_account_policy.py`:
**28/28 passed**. ✅

**REM 3 — suite completa:** **1779 passed, 2 skipped** (0 fallos) en 5:14 min
(`F1_PYTEST_FULL.txt`). Nota: Codex corrió la suite en su sandbox y le salieron 217 "errors"
ambientales (mismo total 1.779; pitfall conocido de temp-dirs del sandbox) — la corrida limpia es
la de este documento. ⚠️ reconciliado

**REM 4 — PARIDAD_V2 capturada y asserts ACTIVOS:**

| Mercado | ANTES (14:30) | DESPUÉS (flat 15:10 real) |
|---|---|---|
| MNQ | n=3.594 · E[R]=+0.0134 | **n=3.619 · E[R]=+0.0128** |
| MGC | n=3.489 · E[R]=−0,0003 | **n=3.498 · E[R]=+0,0013** |

`t3_audit_checks.py`: **max_exit_clock=15:10** ✅ (antes 14:30) · exits_at_15_10=2.181 ·
exits_at_14_30=20 (stops/targets antes del cierre — correcto) · ambiguity de vela de entrada: 16
(igual, prioridad baja declarada no hecha).

**Cableaje adicional (completado por Hermes):** Muse conectó `entry_end`/`flat_time` en t1/t4 pero
no el `trailing_update_cadence` — se cableó `trailing_update_cadence="end_of_day"` +
`consistency_day_consolidation="day_close"` en el `correr()` de t1/t4 (completar el Fix A).

**Resultados FINALES con motor corregido** (`F1_T1_FINAL.txt` / `F1_T4_FINAL.txt`, asserts OK):

| Escenario | P(pasar) | P(quemar) | Δ(sin-deriva) | Δ(iid emparejado) |
|---|---:|---:|---:|---:|
| T1 ORB MNQ · 1 contr | 38,1% | 61,9% | −13,1 pp | **−0,4 pp (empate)** |
| T4 Pool · 1 contr | 35,9% | 64,1% | −8,8 pp | −7,0 pp* |
| T4 Pool · 2 contr | 32,0% | 68,0% | −4,3 pp | −2,7 pp* |

\* el iid normal tiene cola izquierda sin acotar (artefacto conocido); el denominador honesto del
pool es el sin-deriva, cuya ventaja viene de μ=+0.0071 (t=+0,99, ruido). **Lectura: con el motor
corregido y el flat real, el ORB sigue empatado con su propio azar (Δ(iid)=−0,4 pp).**

## 9-11. Rondas 2 y 3 de correcciones (cierre de F1)

**Ronda 2 (Muse C1-C5 sobre el RECHAZO de Codex ronda 1):** C1 separó `entry_cutoff`/`flat_cutoff`
en el motor del autor (patch aplicado por Hermes con fuzzy matching — el `.patch` de Muse venía
corrupto otra vez; commit local `fix/flat-entry-split`); C2 diferó el pase a la consolidación
`day_close` con helper único; C3 hizo el EOD explícito (flush final + fail-closed ante
`trailing_mode` desconocido y trades que cruzan sesión); C4 congeló PARIDAD_V2 con **SHA-256 del
ledger canónico + precisión completa + pandas 3.0.5**; C5 sincronizó metadatos Topstep (2 días /
55%) y arregló tests. Verificación Hermes: entradas legacy vs split **idénticas (0/0, n=3.594)**
(`_f1_c1_check.py`), foco 36/36, suite **1.787 passed**.

**Ronda 3 (Codex r2 encontró regresión nueva → Muse la corrigió → Codex r3):** el flush final de
`day_close` podía sobrescribir un `breach_trailing` con `target` (estados terminales solapados —
pitfall conocido del lab). Corregido con guards `not hit_dd/not hit_daily/not passed` + aserción de
exclusión mutua + regresiones. Verificación: foco **52/52**, suite **1.803 passed, 2 skipped**,
T1/T4 con PARIDAD_V2 + SHA OK. **Codex ronda 3: APTO** (repro R2 ya no produce doble estado; todos
los conjuntos de terminales suman 2.000 sin estados dobles; SHA validado).

**Estado final de F1: CERRADO — criterio de "hecho" cumplido** (scripts con asserts pasando sobre el
motor corregido + Codex sin hallazgos críticos abiertos). Números finales T1 (escenario A):
**P(pasar)=37,7% · P(quemar)=62,3% · Δ(iid emparejado)=+2,3 pp (el control gana)** — consistente con
todas las rondas: el ORB con flat diario no tiene edge certificable. F1 pendiente solo de **merge a
main (requiere OK de Ricardo por Telegram)**.

Notas de proceso: Muse colgó una vez en headless esperando aprobación de un comando (usar
`--disable-shell` y dejar los tests a Hermes); los `.patch` escritos a mano por Muse vienen
corruptos (aplicar hunos con fuzzy matching y regenerar desde git); el sandbox de Codex da
errores ambientales en la suite completa (la corrida limpia de Hermes es la evidencia).

## 9. Ronda 2 — correcciones Codex (F1_REVIEW_CODEX.md §4) — 2026-10-11

Implementador: Muse (misma autorización de Ricardo; `--disable-shell`, sin commits).
Alcance: SOLO C1–C5. No se tocó folds 6..7, escenarios de t1/t4 (solo bloque
paridad/digest), ni dependencias; sin merge/push/borrados.

| Corrección | Archivos | Estado | Test añadido |
|---|---|---|---|
| C1 (§4.1 + MEDIO 6) separar último bar de ENTRADA vs de SESIÓN | `patch_flat_entry_split_r2.patch` (NUEVO; el `.py` del autor está fuera del workspace y la herramienta lo bloquea — Hermes: `git apply --check` + `git apply` en `fix/flat-entry-split`) | Hecho (parche pendiente de aplicar por Hermes) | `test_f1b_split_entries_match_legacy_keys` (claves (entry_time, direction) idénticas legacy vs split, no vacuo), `test_f1b_scan_false_uses_flat_cutoff` (short 14:25 cierra EOD 15:10 con `scan_overnight_bars=False`) en `tests/test_f1_motor_fixes.py` |
| C2 (§4.2) diferir el pase day_close a la consolidación | `lab_artifacts/orb_protocol/bootstrap_camino.py` (helper `_consistency_effective_target` + pase al cerrar día previo + `not day_close` intradía + pase en flush final) | Hecho | `test_f1a_day_close_no_premature_pass` (1500/1600 → NO pasa), `test_f1a_day_close_three_days_best_day_holds` (2000/−500/1600, ambos modos), `test_f1a_day_close_last_day_flush_passes` (un trade final sí pasa al flush); `test_f1a_day_close_uses_final_day_pnl` actualizado a NO-pase (el pase previo era el bug) |
| C3 (§4.3) EOD explícito + fail-closed real | `src/account_engine.py` (`ValueError` ante `trailing_mode` desconocido; helper `_emit_end_of_day`; emisión al cambio de sesión + flush final; `ValueError` si un trade cruza la frontera sin mark EOD) | Hecho | `test_f1c_unknown_trailing_mode_raises`, `test_f1d_single_final_winning_trade_closes_last_day` (HWM $51.000/piso $49.000), `test_f1d_cross_session_trade_raises_fail_closed` (21:50Z→23:59Z); `test_f1d_topstep_floor_ratchets_at_day_close_not_per_trade` actualizado a HWM $51.100/piso $49.100 finales (el flush cierra el día 2; `records[0].floor_after==48.000` intacto) |
| C4 (§4.4) paridad con digest | `t1_orb_topstep_50k.py`, `t4_pool_topstep_50k.py` (SHA-256 de `to_csv(index=False)` con columnas fijas y orden por entry_time/exit_time/direction + E[R] full-precision; asserts 1e-12) | Hecho (valores `None`/PENDIENTE) | Sin test nuevo (verificación por asserts del propio script tras recaptura) |
| C5 (§4.5 + MEDIO 7/8) metadatos y tests | `src/funded_profiles.py` (`_TOPSTEP_UNRESOLVED` sincronizado a 2 días y 55%/`best_day/0.55`; elección: SINCRONIZAR, documentada en el código); `tests/test_f1_motor_fixes.py` | Hecho | `test_f1a_day_close_losses_do_not_reset_best_day` ahora con 2 días reales (`_two_days(1)`) |

**PARIDAD_V2 INVALIDADA (intencionado):** los `3619/0.0128` (t1) y `(3619, 0.0128)/(3498, 0.0013)`
(t4) incluían las 25 entradas indebidas — se pasan a `None`/PENDIENTE (n, E[R] y SHA-256).
Tras aplicar C1, n esperado == legacy (MNQ 3594); E[R] y digests los recaptura Hermes.
NO reutilizar los valores 3619.

**Comandos exactos para Hermes (en este orden):**

```text
REM 0. Aplicar C1 en el motor del autor:
cd E:\FARS-LAB\ext_review2\nq-intraday-breakout
git status --short
git checkout fix/flat-entry-split
git apply --check E:\FARS-LAB\FARS\lab_artifacts\orb_protocol\patch_flat_entry_split_r2.patch
git apply E:\FARS-LAB\FARS\lab_artifacts\orb_protocol\patch_flat_entry_split_r2.patch
git diff --stat

REM 1. Foco F1 + colaterales (debe pasar):
E:/FARS-LAB/.venv-fars/Scripts/python.exe -m pytest tests/test_f1_motor_fixes.py tests/test_account_engine.py tests/test_account_policy.py -q

REM 2. Suite completa:
E:/FARS-LAB/.venv-fars/Scripts/python.exe -m pytest tests/ -x -q

REM 3. Recaptura PARIDAD_V2 (tras C1): leer n, E[R] full y LEDGER_SHA256 del log y
REM rellenar PARIDAD_V2_N/ER/SHA256 en t1 y PARIDAD_V2 en t4 (los asserts se reactivan solos):
E:/FARS-LAB/.venv-fars/Scripts/python.exe lab_artifacts/orb_protocol/t1_orb_topstep_50k.py
E:/FARS-LAB/.venv-fars/Scripts/python.exe lab_artifacts/orb_protocol/t4_pool_topstep_50k.py
E:/FARS-LAB/.venv-fars/Scripts/python.exe lab_artifacts/orb_protocol/t3_audit_checks.py
REM Esperado t1: n=3594 (entradas == legacy), E[R] distinto de +0.0134 (salidas 15:10).
```

**Dudas (sin inventar):**

1. C1 no se pudo editar in-situ (escritura fuera del workspace bloqueada) — se entrega
   como `patch_flat_entry_split_r2.patch`; verificar con `git apply --check` antes de aplicar.
2. T1/T4 con day_close+EOD ahora difieren el pase (C2): las tasas 38,1%/35,9%/32,0% de §8
   cambiarán al recorrer — es el comportamiento corregido, no una regresión.
3. `test_f1d_topstep_floor_ratchets_at_day_close_not_per_trade` cambió sus valores finales
   (51.100/49.100) por el flush C3 — cambio intencionado, documentado en el propio test.
4. Los digests SHA-256 dependen del formato `to_csv` de pandas (fechas `YYYY-MM-DD HH:MM:SS`,
   floats repr) — congelar también la versión de pandas si la paridad debe sobrevivir
   cambios de entorno.

## 10. Ronda 3 — regresión flush/estados solapados (F1_REVIEW_CODEX_R2) — 2026-10-11

Implementador: Muse (misma autorización de Ricardo; `--disable-shell`, sin commits).
Alcance MÍNIMO: solo `lab_artifacts/orb_protocol/bootstrap_camino.py`,
`tests/test_f1_motor_fixes.py` y esta sección. Semánticas C1–C5 intactas
(guards y aserción son pass-through cuando no hay breach).

**El bug:** el flush final de `consistency_day_consolidation="day_close"` (C2)
reevaluaba el pase sin mirar si la réplica ya había terminado en breach, de
modo que una trayectoria que rompía el trailing y cerraba en nivel de target
quedaba con `hit_dd=True` + `passed=True` + `terminal_condition="target"`
(estado doble; los agregados solo cuentan `terminal_condition` y no lo delatan).

**Diff resumido (`bootstrap_camino.py`):**

- Helper nuevo `_validate_terminal_exclusivity(hit_dd, hit_daily, passed,
  terminal_condition)`: terminal desconocido → `AssertionError`; si
  `hit_dd or hit_daily`, el terminal debe ser un breach y `passed=False`;
  si `terminal=="target"`, exige `passed` sin hits; `horizon_exhausted` con
  `passed` o breach sin su flag también elevan. Falla ruidosamente.
- Closure nuevo `_pass_declarable()` en `simulate_funded_trajectory`:
  `not hit_dd and not hit_daily and not passed and terminal_condition ==
  "horizon_exhausted"` — el pase solo se declara sobre réplica viva, nunca
  sobrescribe un terminal ya asignado.
- Los TRES puntos de pase lo usan: consolidación del día previo, chequeo
  intradía legacy y flush final (la consolidación contable de
  `best_day_pnl`/`eod_peak_equity` sigue corriendo siempre; solo la
  DECLARACIÓN del pase queda condicionada).
- Llamada a `_validate_terminal_exclusivity(...)` justo antes del `return`.

**Tests añadidos (`tests/test_f1_motor_fixes.py`, sección Ronda 3):**

- (a) `test_f1r3_trailing_breach_survives_qualifying_day_close_flush`:
  `[10,10,10,5,-20]` mismo día (subida a $57.000, crash a $53.000 = target
  exacto con max DD $2.000, day_close) → `breach_trailing`, `passed=False`,
  `hit_dd=True`, sin estado doble. Con el código pre-ronda-3 este caso daba
  `passed=True`/`target` (repro de la regresión).
- (b) `test_f1r3_terminal_exclusivity_assertion_rejects_double_state`
  (10 combinaciones contradictorias → `AssertionError`) +
  `test_f1r3_terminal_exclusivity_assertion_accepts_clean_states`
  (los 4 terminales limpios no elevan).
- (c) `test_f1r3_day_close_flush_still_passes_legit_climb`: día 1 +$1.000,
  día 2 +$2.500 cruzando el target $3.000 sin breach (min 2 días) →
  `target` en el flush (el guard no bloquea el pase legítimo).

**Nota de alcance:** con `stop_on_daily_loss=False` (modo suave, sin uso en
ningún caller ni test del repo — todos usan el default `True` o umbrales
grandes "sin DLL"), un `hit_daily` superviviente al horizonte ahora eleva la
aserción en vez de devolver `horizon_exhausted` + `hit_daily`. Es el
comportamiento pedido (falla ruidosamente en vez de estado ambiguo); si se
quiere rehabilitar el modo suave, decidirlo explícitamente en otra ronda.

**Comandos exactos para Hermes (en este orden):**

```text
REM 1. Foco F1 + colaterales (debe pasar; incluye los 4 tests ronda 3):
E:/FARS-LAB/.venv-fars/Scripts/python.exe -m pytest tests/test_f1_motor_fixes.py tests/test_account_engine.py tests/test_account_policy.py -q

REM 2. Suite completa (C1-C5 + ronda 3 sin regresiones):
E:/FARS-LAB/.venv-fars/Scripts/python.exe -m pytest tests/ -x -q
```

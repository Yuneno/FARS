# F1-REVIEW — revisión independiente de fixes del motor

Fecha de revisión: 2026-10-11. Rama FARS observada: `fix/motor-r2`,
`HEAD=98792ba0cf2fccc2f06fe33b448baf33ef5391a6`. Motor del autor observado en
`fix/flat-entry-split`, `HEAD=910101749a03b49a2044595a66d3b9f2fe9b851c`.

## 1. Verdict

**RECHAZADO.** Los defaults legacy de ambos motores sí conservan el
comportamiento observado, pero la configuración Topstep nueva no conserva el
conjunto de entradas: habilita exactamente 25 entradas nuevas a las 14:30. Por
separado, la consolidación `day_close` puede declarar un pase antes de incorporar
el P&L final del día que lo produce, por lo que sesga la probabilidad de pase.
También quedan incompletos el cierre EOD del último día y el fail-closed ante un
`trailing_mode` desconocido. Estos defectos afectan resultados y reglas críticas;
no corresponde congelar ni aceptar PARIDAD_V2 todavía.

## 2. Hallazgos por severidad

### CRÍTICO

1. **El flat 15:10 cambia las entradas, contra el contrato de Fix B.**
   `E:/FARS-LAB/ext_review2/nq-intraday-breakout/src/nq_breakout/backtest.py:224-227`
   redefine `is_last_bar` mediante `flat_time`; `:345-353` usa ese mismo
   `is_last_bar` para `block_last_bar_entry`. Con `entry_end=14:30` y
   `flat_time=15:10`, 14:30 ya no es el último bar y queda habilitado para
   entrar. `lab_artifacts/orb_protocol/t1_orb_topstep_50k.py:71-77` activa esa
   combinación. Reproducción independiente sobre MNQ: legacy `n=3594`; split
   `n=3619`; `entries_at_14_30=25`; 25 claves `(entry_time,direction)` añadidas
   y 0 retiradas. Con `entry_end=14:25`, `n=3594`, 0 añadidas y 0 retiradas.

2. **`day_close` puede aprobar antes de consolidar el día decisivo.**
   `lab_artifacts/orb_protocol/bootstrap_camino.py:281-289` solo consolida el
   día anterior al observar otro día, pero `:412-419` evalúa el pase después de
   cada trade usando únicamente `best_day_pnl` ya consolidado. `:421-427`
   consolida el último día después de haber parado, sin volver a evaluar el
   target. Reproducción: día 1 `+$1,500`, día 2 `+$1,600`, target `$3,000`,
   ratio `0.5` devuelve `passed=True`, `terminal_condition='target'` y equity
   `$53,100`; al cierre, el mejor día `$1,600` exige target `$3,200`, por lo que
   no debía pasar. Este error contamina directamente T1/T4 y explica que las
   cifras posteriores no sean una evidencia aceptable de probabilidad de pase.

### ALTO

3. **La emisión EOD de `account_engine` no cierra el último día y mezcla sesiones
   si un trade cruza el boundary.** `src/account_engine.py:199-235` detecta el
   cambio con la sesión de `t_entry`, y solo emite al aparecer una entrada de
   otra sesión; no existe flush EOD después del loop. Una sola operación
   `+$1,000` termina con balance `$51,000`, pero HWM `$50,000` y piso `$48,000`
   (debían reflejar `$51,000/$49,000` al cierre). Caso boundary: entrada
   `2026-09-01 21:50Z` pertenece a sesión `2026-09-01`, cierre a las `23:59Z`
   pertenece a `2026-09-02`, y la siguiente entrada también a `2026-09-02`;
   aun así, antes de esa segunda operación el motor ratchea el piso de `$48,000`
   a `$49,000` usando el P&L ya realizado en la sesión nueva. Sin equity EOD de
   una posición que cruza sesión, el motor debe fallar cerrado o recibir un mark
   EOD explícito; no puede reconstruirlo desde el balance posterior.

4. **El fail-closed acepta modos desconocidos.** `src/account_engine.py:145-147`
   mapea cualquier valor distinto de `"intraday_event"` a `"closed_trade"`.
   Reproducción con Topstep y `trailing_mode="TYPO"`: ejecutó 1 trade, balance
   `$50,100`, status `timeout`, sin excepción. El mismatch entre los dos modos
   soportados sí lanza `ValueError`, pero la validación se puede eludir con un
   valor inválido.

5. **PARIDAD_V2 no está congelada bit a bit y la salida guardada no se reproduce.**
   `lab_artifacts/orb_protocol/t1_orb_topstep_50k.py:90-98` solo fija `n=3619`
   y una media redondeada `0.0128` con tolerancia absoluta `0.002`; no fija el
   ledger ni su hash. La nueva corrida dio 762/2000 pases (38.1%), frente a
   755/2000 (37.8%) en `F1_T1_RERUN.txt`; también difieren controles y medianas.
   Los SHA-256 de los dos textos fueron, respectivamente,
   `E612CAD3639BAB4B285C89614C395688605915F8E883FFB2A7B50B80549CC8E8` y
   `364FA3F427A736BD171ABC8B9E6CD674672A3D4D74DB74CE4BAD2FF66A859EB3`.
   Sí hay evidencia de una corrida real para capturar `3619/0.0128`:
   `F1_T1_RERUN.txt` contiene esos valores antes de activar el assert; el problema
   no es que fueran inventados, sino que la congelación es insuficiente y congela
   un ledger que ya incluye las 25 entradas indebidas.

### MEDIO

6. **`flat_time` se ignora con `scan_overnight_bars=False`.** El motor externo
   filtra el feed hasta `config.trade_end` en `backtest.py:211-214`, aunque el
   cierre de sesión se calcula hasta `flat_time` en `:224-227`. Con ese flag,
   `flat_time=15:10` no dispone de barras posteriores a 14:30.

7. **El perfil exporta resoluciones contradictorias.**
   `src/funded_profiles.py:266-277` todavía declara decisión de 5 días y regla
   50%/2×, mientras `:289-290`, `:321` y `:333` declaran 2 días y 55%/0.55.
   Además, `:334` publica las notas rancias dentro de `source_resolutions`.

8. **El test de “día perdedor no baja best day” no crea dos días.**
   `tests/test_f1_motor_fixes.py:119-129` usa `_same_day(2)`, pese a que el
   docstring dice día 1/día 2. La implementación sí superó una prueba
   independiente real de tres días: `+$2,000`, `-$500`, `+$1,600` dejó
   `passed=False` a `$53,100`; falta convertir esa evidencia en regresión.

### BAJO

9. **Alcance no auditable como un diff aislado.** `git status --short` contiene
   5 archivos rastreados modificados y numerosos artefactos no rastreados,
   incluidos código y resultados previos a F1. `git diff --name-status main`
   no muestra archivos borrados y ninguna ruta cambiada contiene `fold`; por
   tanto no hay evidencia de borrado o edición de folds 6..7, pero el estado
   mezclado impide atribuir todos los archivos no rastreados exclusivamente a
   F1. `test_investigacion.py` también difiere de `main`, aunque
   `F1_FIXES_MOTOR.md` lo declara preexistente.

## 3. Verificación independiente

- Diff y alcance leídos: `git diff` contra `main`,
  `patch_flat_entry_split.patch`, `F1_FIXES_MOTOR.md`, diff real del motor
  externo y `git status` de ambos repos. Motor externo limpio en
  `fix/flat-entry-split`; FARS mantiene los cambios sin commit. Cero borrados.
- Suite focal:
  `pytest tests/test_f1_motor_fixes.py tests/test_account_engine.py tests/test_account_policy.py -q`
  → **28 passed**, 1 warning de cache, **1.51 s**.
- Suite completa independiente: se intentó dos veces. Ambas ejecuciones quedaron
  invalidadas por `PermissionError [WinError 5]` al crear/recorrer directorios
  temporales del sandbox, incluso con `--basetemp` bajo `%TEMP%`; no se usa como
  evidencia de regresión. El artefacto preexistente `F1_PYTEST_FULL.txt` informa
  **1779 passed, 2 skipped en 314.27 s**, pero no es una corrida independiente
  de esta revisión.
- `t3_audit_checks.py` → `n_trades=3619`,
  `entry_bar_stop_touched=16`, `entry_bar_target_touched=1`,
  `entry_bar_both_touched=1`, `max_exit_clock=15:10`,
  `exits_at_14_30=20`, `exits_at_15_10=2181`. Inspección de las 20 salidas
  14:30: **16 stop y 4 target**; no fueron flats indebidos.
- t1 ejecutado hasta código 0: `n=3619`, `E[R]=+0.0128`, `sigma=0.7279`,
  WR `48.2%`, `t=+1.06`; escenarios A/B/C: **762 target, 1238 breach**, tasa
  de pase **38.1%**. No fue bit a bit igual al resultado guardado (ver hallazgo 5).
- Paridad legacy del motor externo contra su commit padre, con el ledger MNQ
  completo: `old_n=3594`, `new_default_n=3594`, `DataFrame.equals=True`; ambos
  SHA-256 `86b5e7f2667b0b1aa0987c66653edef6ab52a6b8cf28163b5de46775e0bca2f9`.
- Paridad legacy de `simulate_funded_trajectory`: **100/100** casos seeded con
  defaults produjeron diccionarios exactamente iguales a `main`.
- Ratchet EOD explícito, mismo día `+$1,000` y `-$500`, seguido de `+$100` al
  día siguiente: pisos por record `[48000.0, 48000.0, 48500.0]`, HWM
  `50500.0`, balance final `50600.0`. El piso no subió intradía y se calculó
  desde balance de cierre, no desde no-realizado.
- `best_day_pnl` no bajó con día perdedor en la prueba independiente de tres
  días; no obstante, el pase prematuro del hallazgo 2 sigue abierto.
- Constantes Topstep observadas: `minimum_trading_days=2`; advisory activo
  contiene `55%` y `best_day/0.55`, con notas contradictorias descritas arriba.

## 4. Correcciones mínimas necesarias (diff sugerido; NO aplicado)

### 4.1 Separar “último bar de entrada” de “último bar de sesión”

```diff
--- a/src/nq_breakout/backtest.py
+++ b/src/nq_breakout/backtest.py
@@
-    if not flags.scan_overnight_bars:
-        df = df[(df['time'] >= config.window_start) & (df['time'] <= config.trade_end)].copy()
+    entry_cutoff = config.entry_end if config.entry_end is not None else config.trade_end
+    flat_cutoff = config.flat_time if config.flat_time is not None else config.trade_end
+    if not flags.scan_overnight_bars:
+        df = df[(df['time'] >= config.window_start) & (df['time'] <= flat_cutoff)].copy()
@@
-    session_mask = ... <= (config.flat_time if ... else config.trade_end)
+    session_mask = (df['time'] >= config.window_start) & (df['time'] <= flat_cutoff)
     session_close_bar = ...
+    entry_mask = (df['time'] >= config.window_start) & (df['time'] <= entry_cutoff)
+    entry_close_bar = df[entry_mask].groupby('trading_day')['date'].max().to_dict()
@@
         is_last_bar = session_close_bar.get(row_trading_day) == row_timestamp
+        is_last_entry_bar = entry_close_bar.get(row_trading_day) == row_timestamp
@@
-        allowed_bar = not (flags.block_last_bar_entry and is_last_bar)
+        allowed_bar = not (flags.block_last_bar_entry and is_last_entry_bar)
@@
-                and config.trade_start <= row_time <= (...)
+                and config.trade_start <= row_time <= entry_cutoff
```

Agregar regresión que compare exactamente las claves de entrada del ledger
legacy y split; no basta comparar `n`/media.

### 4.2 Diferir el pase cuando la consistencia se consolida al cierre

```diff
--- a/lab_artifacts/orb_protocol/bootstrap_camino.py
+++ b/lab_artifacts/orb_protocol/bootstrap_camino.py
@@ al cerrar el día previo
                 if use_day_close_consistency:
                     best_day_pnl = max(best_day_pnl, day_pnl)
+                    effective = max(
+                        profit_target_usd,
+                        best_day_pnl / consistency_max_day_ratio
+                        if consistency_max_day_ratio is not None else profit_target_usd,
+                    )
+                    if equity >= initial_balance + effective and len(days_traded) >= min_trading_days:
+                        passed = True
+                        terminal_condition = "target"
+                        break
@@ chequeo intradía actual
-        if equity >= initial_balance + effective_target_usd ...:
+        if not use_day_close_consistency and equity >= initial_balance + effective_target_usd ...:
@@ después de consolidar el último día
     if use_day_close_consistency:
         best_day_pnl = max(best_day_pnl, day_pnl)
+        # Recalcular effective_target y pass aquí para el último día.
```

El helper del target efectivo debe ser único para evitar divergencia entre los
dos puntos de cierre. Añadir los casos `1500/1600` (no pasa a 53100) y
`2000/-500/1600` (día perdedor no baja el máximo).

### 4.3 Hacer EOD explícito y fail-closed de verdad

```diff
--- a/src/account_engine.py
+++ b/src/account_engine.py
@@
-    required_cadence = "intraday_event" if config.trailing_mode == "intraday_event" else "closed_trade"
+    if config.trailing_mode not in ("closed_trade", "intraday_event"):
+        raise ValueError(f"unsupported trailing_mode={config.trailing_mode!r}")
+    required_cadence = config.trailing_mode
```

Para perfiles EOD, validar además que entrada y salida pertenecen a la misma
sesión; si no se proporciona un mark EOD de la posición abierta, lanzar error en
vez de atribuir el balance posterior al día anterior. Centralizar la emisión en
un helper y llamarlo (a) antes del primer evento de una sesión nueva y (b) una
vez al terminar el loop para cerrar el último día. Añadir regresiones para un
único trade ganador final y para cierre 23:59 cruzando el boundary.

### 4.4 Congelar paridad real

Guardar y verificar un digest SHA-256 determinista del ledger canónico (columnas,
orden y serialización fijados), además de `n` y `E[R]` con precisión completa.
Regenerar PARIDAD_V2 solo después de corregir las 25 entradas y los pases EOD.

### 4.5 Sincronizar metadatos Topstep

Cambiar `src/funded_profiles.py:266-277` a 2 días y 55%/`best_day/0.55`, o
eliminar esas decisiones históricas de `source_resolutions` para no exportar dos
reglas incompatibles.

## 5. Unknowns

- No se pudo obtener una suite completa independiente limpia por restricciones
  de permisos del sandbox; la suite focal sí es reproducible y pasa.
- “Folds 6..7” no aparece como ruta o identificador en el diff ni en los
  artefactos localizables por `rg`; se confirma ausencia de borrados/cambios con
  nombre `fold`, no la identidad semántica de artefactos externos no señalados.
- El motor de cuenta no recibe marcas EOD de posiciones abiertas. Hasta definir
  ese contrato, cualquier trade que cruce la frontera de sesión es no evaluable
  con exactitud para HWM EOD.
- Los scripts t1/t3 escriben archivos de resultados al ejecutarse. Esos archivos
  ya eran no rastreados; durante esta revisión no se editó ningún fix ni se hizo
  commit/push.

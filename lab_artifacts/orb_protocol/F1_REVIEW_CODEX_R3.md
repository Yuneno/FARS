# F1-REVIEW-R3 — revisión acotada del flush final `day_close`

Fecha: 2026-10-11. Rama observada: `fix/motor-r2`; `HEAD=98792ba0cf2fccc2f06fe33b448baf33ef5391a6`.

## Verdict

**APTO.** La revisión se limitó al hallazgo crítico nuevo de la ronda 2. El contraejemplo exacto ya no produce el estado doble `hit_dd=True` y `passed=True`: conserva el breach como único terminal. La suite focal pasa, T1 mantiene `PARIDAD_V2` y el SHA esperado, y todos sus conteos terminales suman 2.000 sin que la aserción de exclusión terminal detecte estados contradictorios.

## Hallazgo de ronda 2

| Hallazgo ronda 2 | Estado | Evidencia independiente |
|---|---|---|
| El flush final de `day_close` podía sobrescribir `breach_trailing` con `target`, devolviendo simultáneamente `hit_dd=True` y `passed=True`. | **RESUELTO** | El repro exacto de ronda 2 devuelve `hit_dd=True`, `hit_daily=False`, `passed=False`, `terminal_condition='breach_trailing'`, `final_equity=53000.0`, `peak_equity=54000.0`, `trades_executed=2`; `passed and (hit_dd or hit_daily) = False`. |

## 1. Reproducción exacta de ronda 2

Parámetros reproducidos: día 1 `+$4,000`, día 2 `-$1,000`; balance inicial `$50,000`; target `$3,000`; trailing DD `$1,000`; `min_trading_days=2`; `consistency_day_consolidation='day_close'`.

Resultado exacto:

```text
hit_dd=True
hit_daily=False
passed=False
terminal_condition='breach_trailing'
final_equity=53000.0
peak_equity=54000.0
trades_executed=2
double_state=False
```

El guard común `_pass_declarable()` está en `bootstrap_camino.py:305-313` y exige réplica viva (`not hit_dd`, `not hit_daily`, `not passed` y terminal aún `horizon_exhausted`). Se usa en los tres puntos de evaluación de pase: cierre de día previo (`:372`), evaluación intradía legacy (`:509`) y flush final (`:528`). La validación terminal se ejecuta antes del retorno en `:537`.

Las regresiones específicas están en `tests/test_f1_motor_fixes.py:512-600`: breach seguido de flush calificable, rechazo/aceptación de combinaciones terminales y pase legítimo en el flush final. El test previo de ganador final también permanece en `:178-197`.

## 2. Suite focal

Comando:

```text
E:/FARS-LAB/.venv-fars/Scripts/python.exe -m pytest tests/test_f1_motor_fixes.py tests/test_account_engine.py tests/test_account_policy.py -q
```

Resultado: **52 passed**, **1 warning**, **1.50 s**, código de salida **0**.

El único warning fue `PytestCacheWarning` por denegación de acceso al directorio `.pytest_cache`; no corresponde a un fallo de prueba.

## 3. T1 — PARIDAD_V2 y SHA

Comando:

```text
E:/FARS-LAB/.venv-fars/Scripts/python.exe lab_artifacts/orb_protocol/t1_orb_topstep_50k.py
```

Resultado: código de salida **0** y `PARIDAD_V2 MNQ OK`.

- `n=3594`
- `E[R]=0.012892237061769599`
- `sigma=0.7294`
- `WR=48.2%`
- `t=+1.06`
- `LEDGER_SHA256=9a1261c827ac92bcd5a2410f2fd6c7731f3b09ae8bbc196f8bc64c60c59cf536`
- `B=2000`, bloque MBB `3`, `seed_mbb=2602526572`, `seed_iid=1656862755`

## 4. Conteos terminales T1

| Conjunto | `target` | `breach_trailing` | `breach_daily` | `horizon_exhausted` | Total |
|---|---:|---:|---:|---:|---:|
| A — base oficial, consistencia 0.55 | 754 | 1246 | 0 | 0 | **2000** |
| B — conservador, consistencia 0.50 | 754 | 1246 | 0 | 0 | **2000** |
| C — consistencia 0.50 + DLL `$1,000` | 754 | 1246 | 0 | 0 | **2000** |
| Control sin deriva, consistencia 0.55 | 525 | 1475 | 0 | 0 | **2000** |
| Control iid, consistencia 0.55 | 799 | 1201 | 0 | 0 | **2000** |
| Control sin deriva, consistencia 0.50 | 525 | 1475 | 0 | 0 | **2000** |
| Control iid, consistencia 0.50 | 799 | 1201 | 0 | 0 | **2000** |

Cada una de las **14.000** trayectorias T1 atravesó `_validate_terminal_exclusivity(...)` antes de retornar. T1 terminó sin `AssertionError`; cada réplica aportó exactamente una `terminal_condition`, y cada conjunto suma exactamente 2.000. No se observaron estados dobles.

## Alcance y conclusión

No se revisaron ni reabrieron otros hallazgos. No se editó ningún fix, no se hizo commit y no se hizo push. La ejecución solicitada de T1 regeneró su archivo de resultados; este informe es el único documento de revisión creado en esta ronda.

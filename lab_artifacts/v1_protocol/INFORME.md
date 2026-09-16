# FARS — Informe Codex Reversal V1

## Estado

Implementación opt-in preparada sobre `bloque-v1-reversion` desde `dcaef24`. No se modificó ninguna
estrategia existente, executor, riesgo ni fondeo. No hubo push, dependencias nuevas ni promoción.

El veredicto de protocolo es **`no-evaluable`**: no se ejecutó una corrida parcial. El benchmark de
5.000 barras tardó 5,05 s; el runner completo (cinco variantes, ocho folds, auditorías y tres escenarios)
se estima en al menos 5–6 horas, posiblemente más por crecimiento del inventario del `ZoneEngine`.

## Implementación

- `src/backtest/reversal_v1.py`: cadena causal pool `swept` → CHoCH/MSS canónico con desplazamiento
  declarado → FVG canónico → CISD opcional → OTE opcional → pool opuesto activo más cercano.
- Entrada primaria límite (`wait=48`) y vecino mercado-next-open; stop en extremo del sweep + 1 punto;
  salida temporal 96 barras. Sin pool opuesto o sin FVG/CHoCH: abstención.
- `tests/test_backtest_reversal_v1.py`: 12 pruebas de determinismo, causalidad/anatomía, filtros,
  configuración e import opt-in.
- `lab_artifacts/run_v1_protocol.py`: 36/6/6, purga por intervalos, embargo `h=192`, escalera C1/C2,
  E[R] y PF juntos, variantes individuales y combinación.

## Pruebas

- Focalizadas: **75 passed** (12 nuevas + SMC-FVG + CRT4H + ZoneEngine + sesiones).
- Suite recolectada: 1.576. Resultado bajo sandbox: **1.362 passed, 2 skipped, 212 setup errors**.
  Los 212 errores son `PermissionError` de la fixture `tmp_path`; dos `--basetemp` alternativos sufrieron
  la misma ACL. Por honestidad no se declara suite completa verde.

## Bloqueos relevantes

1. El executor no ofrece API para cancelar una orden pendiente. La estrategia registra la invalidación,
   pero `evaluate() -> None` no retira el `pending` local del executor; cambiarlo estaba prohibido.
2. La vigencia sweep→CHoCH no está especificada; se declara que el último sweep persiste hasta consumo
   o reemplazo, sin añadir un hiperparámetro.
3. El destino externo `E:\gemini avances\FARS_CODEX_V1_REVERSION_INFORME.md` no es escribible en este
   sandbox. Este archivo contiene el informe equivalente para que Hermes lo copie.
4. Pytest dejó dos directorios con ACL no enumerable; el sandbox rechazó su eliminación. Detalle y rutas
   exactas en `BLOCKERS.md`.

## Artefactos

- Preregistro UTC anterior a toda medición: `2026-09-16T22:43:51.6010401Z`.
- Dataset: 518.237 barras, SHA-256 `fbed6061205b8299af140f85e36b472f5f1d88084977ad9c4ca9aa1f817b8a96`.
- `manifest.json` declara `not_run_compute_budget`; `v1_fold_metrics.json` no existe por diseño, ya que
  no hubo una corrida completa. No hay cifras E[R]/PF inventadas.

## Comandos para Hermes

```powershell
E:\FARS-LAB\.venv-fars\Scripts\python.exe -m pytest -q
E:\FARS-LAB\.venv-fars\Scripts\python.exe lab_artifacts\run_v1_protocol.py --scenario c1_canonical
E:\FARS-LAB\.venv-fars\Scripts\python.exe lab_artifacts\run_v1_protocol.py --scenario c2_segment_realistic
E:\FARS-LAB\.venv-fars\Scripts\python.exe lab_artifacts\run_v1_protocol.py --scenario c2_kai
```

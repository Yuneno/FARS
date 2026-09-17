# INFORME — Bloque Z6: S/R con ancho + order blocks como zona (paso 9 · test 10.5)

**Fecha:** 2026-09-17 · **Rama:** `bloque-z6-sr-ob` (worktree `E:\FARS-LAB\FARS-z6`, base `main` = `e5d8a30`)
**Autoría:** código y tests escritos por **Codex** (`gpt-5.6-sol`, sandbox workspace-write). El proceso **cortó
por límite de uso de la cuenta** (exit 1, "usage limit… retry Sep 19") justo al terminar el smoke y antes de
escribir este informe. **Verificación, suite completa, informe y commit: Hermes.** Sin push.

## 1. Qué se implementó

| Pieza | Archivo |
|---|---|
| S/R con ancho como zona (cluster determinista de pivotes confirmados; reutiliza el clustering greedy 1D de liquidez) | `src/zones/levels.py` (nuevo) |
| Order block como zona, extraído de la lógica inline del port SMC-OB | `src/zones/order_blocks.py` (nuevo) |
| Features de Z6 rellenas en `context()` (Z7 sigue `None`) | `src/zones/engine.py` |
| Registro/export del tipo y del builder | `src/zones/models.py`, `src/zones/__init__.py` |
| Tests Z6 (spec §10.5, prefix invariance, determinismo) | `tests/test_zones_z6.py` (nuevo) |
| Smoke sobre MNQ canónico + su artefacto | `lab_artifacts/z6_protocol/run_z6_smoke.py`, `smoke.json` |

## 2. Evidencia (verificada por Hermes, comandos incluidos)

1. **Tests Z6:** `python -m pytest tests/test_zones_z6.py -q` → **4 passed** (1,16 s).
2. **Paridad bit a bit OB↔port** (`smoke.json`, MNQ canónico `databento/MNQ_M5.csv`, 518.237 barras):
   `ob_port_zones = 7053`, `ob_extracted_zones = 7053`, **`ob_parity_differences = 0`** — y
   `ob_only_port = ob_only_extracted = 0`. Es el criterio §10.5 cumplido con evidencia, no con afirmación.
3. **Causalidad:** `causality_violations = 0` en las 53.400 zonas S/R (26.990 resistencia, 26.410 soporte) y
   las 7.053 OB. `sr_tolerance = 1.0` (convención declarada, ver `BLOCKERS.md` §1).
4. **Determinismo:** el test compara `to_dict()` de todas las zonas de dos engines independientes sobre la
   misma serie → idénticos; el smoke deja `artifact_payload_sha256`.
5. **Prefix invariance:** test específico para S/R + OB (una zona creada no cambia de identidad ni de
   `available_at` al añadir barras futuras; la expansión de cluster es la permitida por spec).
6. **Sin regresiones ajenas:** en el worktree solo aparecen modificados/creados los archivos de Z6; los
   artefactos de Z5 (`lab_artifacts/z5_protocol/`, `lab_artifacts/f_protocol/`) quedan **intactos**.
7. **Suite completa del worktree:** ver §3.

## 3. Suite completa

`E:\FARS-LAB\.venv-fars\Scripts\python.exe -m pytest -q` (desde `E:\FARS-LAB\FARS-z6`) →
**1575 passed, 2 skipped** (282 s) · logs: `E:\FARS-LAB\FARS_Z6_SUITE.log` (primera pasada, con 1 fallo de
oráculo) y `FARS_Z6_SUITE2.log` (pasada final, verde).

### 3.1 Re-freeze declarado del oráculo Z3-b (hecho por Hermes al cerrar)

La primera pasada dio **1 failed**: `test_zones_session_levels.py::test_session_pools_false_oracle_parity`.
No era una regresión de Z3-b: es que **Z6 añade los tipos `support`/`resistance`/`order_block` al inventario
del engine por diseño**, y ese test congelaba el inventario total (`n_zones == 382`). Verificado con sonda
independiente sobre MNQ canónico (5.000 barras, `session_pools=False`):

| Medida | Antes de Z6 | Con Z6 |
|---|---:|---:|
| Zonas `fvg` + `liquidity` (inventario legado) | 382 | **382** ✓ |
| Activas `fvg` + `liquidity` | 220 (`liquidity` 198 + `fvg` 22) | **220 (198 + 22)** ✓ |
| Inventario total | 382 | 730 (+134 resistance, +141 support, +73 order_block) |
| Resto de features del oráculo (fvg_age, fill, distancias, touches, swept, overlap) | — | **idénticas** ✓ |
| `nearest_zone_type` | `liquidity` | `resistance` (único cambio de contexto) |

**Cambio aplicado (mínimo y declarado):** el test verifica la paridad sobre los tipos legados (`fvg` +
`liquidity`) con **los mismos números congelados**, y se re-congela `nearest_zone_type` a `resistance`.
El cambio va comentado en el propio test, aquí y en el commit. **No se tocó una sola línea de `src/`.**

## 4. Reproducir

```text
cd E:/FARS-LAB/FARS-z6
E:/FARS-LAB/.venv-fars/Scripts/python.exe -m pytest tests/test_zones_z6.py -q
E:/FARS-LAB/.venv-fars/Scripts/python.exe lab_artifacts/z6_protocol/run_z6_smoke.py
E:/FARS-LAB/.venv-fars/Scripts/python.exe -m pytest -q     # suite completa
```

## 5. Lo que NO se hizo (honestidad)

- **No se tocó** el port SMC-OB (`src/backtest/smc_ob.py`) ni `zone_bridge.py`: la paridad se probó comparando
  salidas, como pedía el encargo.
- **No hay medición de rentabilidad** en este bloque (no le corresponde): Z6 pone vocabulario de zonas y
  features; medir es trabajo de un protocolo C1 aparte.
- Preguntas abiertas de método → `BLOCKERS.md` (tolerancia declarada del cluster S/R, lifecycle de S/R
  testeado, alcance de la fortaleza).

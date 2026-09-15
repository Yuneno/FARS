# FARS - Indice de Evidencia y Trazabilidad

Este documento establece el indice formal de trazabilidad y reproducibilidad para los artefactos de evaluacion historica y replay en FARS, de acuerdo con el encargo del Bloque A1.

---

## 1. M1 Canonico Reproducible (A1.2)

### 1.1 Especificacion y Hash Auditado
El dataset canonico para MNQ corresponde a los datos reales posteriores al lanzamiento del contrato de futuros Micro E-mini Nasdaq-100 (CME: MNQ), descartando todo backfill sintetico anterior al **2019-05-06**.

- **Fuente Original:** `E:\FARS-LAB\databento.zip`
- **Miembro:** `databento/MNQ_M1.csv`
- **Filtro Temporal Causal:** `timestamp >= 2019-05-06` (UTC)
- **Ruta de Referencia:** `databento_mnq/databento/MNQ_M1_2019-05-06.csv`
- **SHA-256 Verificado:** `9cbf7da1a1019c02a6b3c14c5e13cb63d583f1548fc19ac1cb8781165fd5dc5a`
- **Total de Filas (M1):** `2,589,531`
- **Total de Bytes:** `181,245,992`
- **Estado de Verificacion:** **100% IDENTICO BIT A BIT** con el hash declarado en `docs/refactor/canonical-dataset.md`. Discrepancia = 0.

### 1.2 Comando Reproducible de Reconstruccion y Verificacion
Para extraer o verificar en streaming el SHA-256 del dataset canonico M1 sin escribir archivos temporales:

```python
python -c "import zipfile, hashlib; z = zipfile.ZipFile('E:/FARS-LAB/databento.zip'); f = z.open('databento/MNQ_M1.csv'); header = f.readline(); hasher = hashlib.sha256(); total_bytes = len(header); hasher.update(header); [hasher.update(line) for line in f if line[:10] >= b'2019-05-06']; print('SHA256:', hasher.hexdigest())"
```

Comando reproducible para materializar el archivo fisico en disco:
```python
python -c "import zipfile, pathlib; z = zipfile.ZipFile('E:/FARS-LAB/databento.zip'); out = pathlib.Path('E:/FARS-LAB/MNQ_M1_2019-05-06.csv'); f = z.open('databento/MNQ_M1.csv'); header = f.readline(); out.write_bytes(header); [out.open('ab').write(line) for line in f if line[:10] >= b'2019-05-06']"
```

### 1.3 Dataset Canónico M5 Derivado (Convención Única)
- **Fuente:** `E:\FARS-LAB\databento.zip`
- **Miembro:** `databento/MNQ_M5.csv` (filtrado causal `timestamp >= 2019-05-06`)
- **Total de Barras:** `518,237` velas M5 (7.33 años, 2019-05-06 a 2026-09-03)
- **SHA-256 Canónico del Miembro:** `fbed6061205b8299af140f85e36b472f5f1d88084977ad9c4ca9aa1f817b8a96`
- **Script de Verificación:** `lab_artifacts/verify_canonical_dataset.py` (valida hash y conteo exacto)
- **Aclaración de Entornos:** La ruta `E:\FARS-LAB\lab_artifacts\` (fuera del repositorio) es un directorio de trabajo local externo, conceptualmente y físicamente separado de `E:\FARS-LAB\FARS\lab_artifacts\` (artefactos formales versionados).

---

## 2. Indice de Evidencia de Estrategias y Replay (A1.1 / A5.3)

Clasificación según taxonomía formal:
- `verificado`: Auditado causalmente, sin defectos conocidos, reproduce métricas bit a bit.
- `superseded`: Resultado preliminar histórico sustituido formalmente por correcciones metodológicas o de ejecución.
- `reportado-sin-revision`: Resultados preliminares de corridas anteriores; auditoría de ejecución aún no realizada.
- `bloqueado`: Sin sustituto viable o muestra insuficiente para emitir conclusión válida.
- `pendiente necesario`: Bloqueante para la fase de producción/evaluación financiable.
- `diferido`: No prioritario para el baseline inmediato.

| Artefacto | Ruta | Commit | Configuración | Hash de Datos | Comando Reproducible | Clasificación |
|---|---|---|---|---|---|---|
| **SMC-FVG (Discreto)** | `lab_artifacts/CODEX_OMNIROUTE_RUN/juanca_smc_fvg_out/juanca_smc_fvg_benchmark.json` | `ea380c8` | MNQ M5 canónico, 518.237 barras, $50k inicial, riesgo $500 (1%), `discrete_partial_contracts=True`, TP1 1R, TP2 1.5R, BE en 1R, $4.00 RT fricción | `fbed6061...` (M5) | `python lab_artifacts/CODEX_OMNIROUTE_RUN/benchmark_juanca_smc_fvg.py` | `reportado-sin-revision` |
| **EMAS (Discreto)** | `lab_artifacts/CODEX_OMNIROUTE_RUN/juanca_emas_out/juanca_emas_benchmark.json` | `836b92b` | MNQ M5 canónico, EMAs 10/20/55/200 + HTF M15/H1, $50k inicial, riesgo $500 (1%), `discrete_partial_contracts=True`, $4.00 RT fricción | `fbed6061...` (M5) | `python lab_artifacts/CODEX_OMNIROUTE_RUN/benchmark_juanca_emas.py` | `reportado-sin-revision` |
| **CRT-TBS (Champion, Flat)** | `lab_artifacts/CODEX_OMNIROUTE_RUN/four_strategies_benchmark.json` | `731f980` | MNQ M5 canónico, H4 bias, H1 CRT, M5 confirmation, `target_mode="fixed_rr"`, `fixed_rr=2.0`, `time_exit_mode="flat"`, $4.00 RT fricción | `fbed6061...` (M5) | `python lab_artifacts/CODEX_OMNIROUTE_RUN/benchmark_four_strategies.py --strategy crt_tbs_champion` | `superseded` |
| **CRT-TBS (Default Literal)** | `lab_artifacts/CODEX_OMNIROUTE_RUN/four_strategies_benchmark.json` | `731f980` | MNQ M5 canónico, `target_mode="crt"`, `min_rr=1.50`, produce 0 trades (paradoja geométrica demostrada) | `fbed6061...` (M5) | `python lab_artifacts/CODEX_OMNIROUTE_RUN/benchmark_four_strategies.py --strategy crt_tbs_default` | `verificado` |
| **ORB (Experimental, Flat)** | `lab_artifacts/CODEX_OMNIROUTE_RUN/four_strategies_benchmark.json` | `731f980` | MNQ M5 canónico, 09:30-10:00 NY, stop opuesto, target 2R, `max_hold=192` barras M5, `time_exit_mode="flat"`, $4.00 RT fricción | `fbed6061...` (M5) | `python lab_artifacts/CODEX_OMNIROUTE_RUN/benchmark_four_strategies.py --strategy orb` | `superseded` |
| **CRT 4H de Kai** | `docs/refactor/canonical-dataset.md` | `d09da0d` | Dataset cortado M5 post-2019, n=372 trades, muestra insuficiente para meta-labeling | `fbed6061...` (M5) | `python -m docs.refactor.check_mnq_equivalence` | `bloqueado` |
| **Replay Histórico RT** | `lab_artifacts/CODEX_OMNIROUTE_RUN/realtime_replay_verification.json` | `2dfcd78` | ReplayEngine, FrozenClock, 2,000 barras canónicas MNQ M5, AsyncIOEventBus, SmcFvgStrategy (20 señales idénticas a backtest) | `fbed6061...` (M5) | `python lab_artifacts/CODEX_OMNIROUTE_RUN/verify_realtime_replay.py` | `verificado` |
| **CRT-TBS & ORB (Flat vs Market)** | `lab_artifacts/flat_vs_market_comparison.json` | `bdc9480` | Comparativa directa `time_exit_mode="market"` vs `"flat"`, atribución de PnL a expiraciones de tiempo | `fbed6061...` (M5) | `python lab_artifacts/run_flat_vs_market.py` | `verificado` |
| **Auditoría Intrabarra M1 (A2.4/A3.3)** | `lab_artifacts/intrabar_canonical_audit.json` | `22c7d0e` | Resolución causal M1 selectivo en dos pasadas sobre M5 canónico, ambigüedad etiquetada | `fbed6061...` (M5) | `python lab_artifacts/run_intrabar_canonical.py` | `verificado` |
| **Slippage Salida Temporal (A.3)** | `lab_artifacts/a3/time_exit_slippage.json` | `22c7d0e` | Impacto de 0.0 vs 0.25 pts slippage adverso en time_exit para CRT-TBS y ORB, ledger reconciliado | `fbed6061...` (M5) | `python lab_artifacts/a3/run_time_exit_slippage.py` | `verificado` |
| **Validación Temporal C1 (Walk-Forward)** | `lab_artifacts/c1_protocol/manifest.json` | `22c7d0e` | 8 folds rolling calendáricos (36m/6m), purga/embargo auditados, veredicto formal FAIL en CRT-TBS y ORB | `fbed6061...` (M5) | `python lab_artifacts/run_c1_walkforward.py` | `verificado` |

---

## 3. Resolucion de Archivos sin Commit (A1.3 / T3)

| Archivo Original | Decision | Justificacion |
|---|---|---|
| `lab_artifacts/CODEX_OMNIROUTE_RUN/four_strategies_out/four_strategies_benchmark.json` | **Eliminar directorio** | Copia redundante bit a bit (SHA `0af572b4...`) del archivo ya versionado `lab_artifacts/CODEX_OMNIROUTE_RUN/four_strategies_benchmark.json`. |
| `scratch/baseline_before.json`, `baseline_after.json`, `baseline_check.json` | **Mover a `lab_artifacts/`** | Evidencia de equivalencia historica y regresion bit a bit (SHA `71debb93...`) pre y post refactor. Versionado como evidencia. |
| `scratch/display_table.py` | **Mover a `.gitignore`** | Script de conveniencia para formatear tablas Markdown en consola durante el analisis exploratorio. |
| `scratch/generate_final_report.py` | **Mover a `.gitignore`** | Script auxiliar temporal utilizado para ensamblar el informe `CUATRO_ESTRATEGIAS_EVALUACION_COMPARATIVA.md`. |

---

## 4. Politica de Fin de Dataset por Corrida Canonica (A3.2)

De acuerdo con el hallazgo A3.2 de la revision de Hermes, se audita y declara con precision la politica `end_of_data_policy` utilizada por cada corrida canonica sobre el dataset MNQ M5 (`timestamp >= 2019-05-06`, 518,237 barras) y la semantica real del motor de ejecucion (`src/backtest/executor.py:540-542`):

```python
should_close = (config.end_of_data_policy == "close") or (
    config.end_of_data_policy == "unresolved" and not position["distance_mode"]
)
```

### Semantica de Ejecucion al Fin de Dataset
En el executor (`src/backtest/executor.py`), `unresolved_positions=0` **no** significa que no existiera una posicion abierta al llegar a la ultima barra. La politica por defecto (`"unresolved"`) cierra forzosamente al `close` de la ultima barra aquellas posiciones cuyas señales **no** operan en `distance_mode` (`stop_target_as_points=False`), generando un trade ejecutado con `exit_reason="end_of_data"` y dejando `position = None` (por lo que `unresolved_positions` se computa como `0` y `open_position=None`). Unicamente las señales con `stop_target_as_points=True` permanecen marcadas como abiertas en `open_position` y reportan `unresolved_positions=1`.

| Corrida Canonica | Artefacto | Politica Declarada | Posiciones al Final del Dataset | Trades `end_of_data` Generados | `unresolved_positions` | Justificacion de Homogeneidad (`close` vs `unresolved`) |
|---|---|---|:---:|:---:|:---:|---|
| **CRT-TBS (Champion)** | `lab_artifacts/flat_vs_market_comparison.json` | `end_of_data_policy="unresolved"` | **0 abiertas** (plana) | **0** | **0** | Totalmente liquida al final del dataset (93 trades = 42 `stop_loss` + 25 `take_profit` + 26 `time_exit`). `close` y `unresolved` dan exactamente los mismos 93 trades. |
| **ORB (Experimental)** | `lab_artifacts/flat_vs_market_comparison.json` | `end_of_data_policy="unresolved"` | **1 abierta** | **1** (n=1, qty=1, net=+$130.00, comm=$4.00) | **0** | Al no ser `distance_mode` (`stop_target_as_points=False`), `should_close` es `True` tanto bajo `"close"` como bajo `"unresolved"` (`not position["distance_mode"]`). Ambas politicas producen identicos 2,544 trades. |
| **Auditoria Intrabarra M1** | `lab_artifacts/intrabar_canonical_audit.json` | `end_of_data_policy="unresolved"` | CRT: 0 / ORB: 1 | CRT: 0 / ORB: 1 | **0** | Idem. Homogeneidad garantizada por la semantica del executor. |
| **Slippage Salida Temporal A3** | `lab_artifacts/a3/time_exit_slippage.json` | `end_of_data_policy="unresolved"` | CRT: 0 / ORB: 1 | CRT: 0 / ORB: 1 | **0** | Idem. El trade `end_of_data` forma la 4ª pata del ledger reconciliado de ORB. |

Conclusiones clave:
1. **CRT-TBS** concluyo el dataset sin ninguna posicion abierta (0 abiertas, 0 `end_of_data`).
2. **ORB** tenia exactamente **1 posicion abierta** en la barra 518,237, la cual fue liquidada al precio de cierre de la barra final generando un trade `end_of_data` (n=1, net +$130.00, qty=1, commission $4.00), representando el 0.66% del net PnL acumulado.
3. La identidad de resultados entre `end_of_data_policy="close"` y `"unresolved"` para ambas estrategias queda demostrada rigurosamente por el codigo: CRT-TBS no tenia posiciones abiertas, y ORB no usa `distance_mode`, por lo que en ambos casos la rama de ejecucion es invariante ante la politica.

---

## 5. Registro de Deuda Tecnica — Bloque D (A3.4 / Fuera de Alcance)

- **Ubicacion:** `src/account_sim.py:243`
- **Defecto Identificado:**
  ```python
  final_status = "PASSED_SIMULATION" if target_reached else "PASSED_SIMULATION"
  ```
- **Analisis:** Ambas ramas del operador ternario asignan `"PASSED_SIMULATION"`, ignorando la condicion booleana `target_reached` cuando no hay violacion de drawdown o perdida diaria.
- **Accion / Estado:** **Fuera de alcance del Bloque A.3.** Corresponde a la maquina de estados de evaluacion y financiamiento del **Bloque D**. Se registra aqui formalmente para preservar trazabilidad y garantizar su correccion durante el Bloque D sin modificar el comportamiento del simulador en esta etapa.

---

## 6. Indice de Artefactos de la Revision A.3

| Artefacto | Ruta | Commit / Rama | Descripcion | Hash SHA-256 |
|---|---|---|---|---|
| **Slippage Salida Temporal** | `lab_artifacts/a3/time_exit_slippage.json` | `fix/a3-hallazgos` | Impacto de 0.0 vs 0.25 pts en CRT-TBS y ORB, distribucion de cantidad, friction_R, delta por pata reconciliado | `6cc4acf0bdcd12e145265028cff668683e71bed611cca4fb7911b0254f9cc0d0` |
| **Auditoria Intrabarra M1 (A3.3)** | `lab_artifacts/intrabar_canonical_audit.json` | `fix/a3-hallazgos` | Tasas de ambiguedad etiquetadas (`ambiguous_pct_of_all_bars` y `ambiguous_pct_of_bars_in_position`), `bars_in_position` | `4d772416a0eb8b98de425835f954154498e1b4b2b1cb9a8da06a288d962c262d` |


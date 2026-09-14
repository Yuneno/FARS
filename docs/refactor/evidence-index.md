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

### 1.3 Dataset Canonico M5 Derivado
- **Miembro:** `databento/MNQ_M5.csv` (filtrado `timestamp >= 2019-05-06`)
- **Total de Barras:** `518,237` velas M5 (7.33 anos, 2019-05-06 a 2026-09-03)
- **Total de Bytes:** `36,641,505`
- **SHA-256 Canonico M5:** `5b190bca6638f68842206329b0d1c248af7bb6f022fd8fe955cde0d92539655c`

---

## 2. Indice de Evidencia de Estrategias y Replay (A1.1)

Clasificacion segun taxonomia formal:
- `verificado`: Auditado causalmente, sin defectos conocidos, reproduce metricas bit a bit.
- `reportado-sin-revision`: Resultados preliminares de corridas anteriores; requiere auditoria o correccion de defectos de ejecucion (ej. D1, D2, D3, D4).
- `pendiente necesario`: Bloqueante para la fase de produccion/evaluacion financiable.
- `diferido`: No prioritario para el baseline inmediato.

| Artefacto | Ruta | Commit | Configuracion | Hash de Datos | Comando Reproducible | Clasificacion |
|---|---|---|---|---|---|---|
| **SMC-FVG (Discreto)** | `lab_artifacts/CODEX_OMNIROUTE_RUN/juanca_smc_fvg_out/juanca_smc_fvg_benchmark.json` | `ea380c8` | MNQ M5 canonico, 518.237 barras, $50k inicial, riesgo $500 (1%), `discrete_partial_contracts=True`, TP1 1R, TP2 1.5R, BE en 1R, $4.00 RT friccion | `5b190bca...` (M5) | `python lab_artifacts/CODEX_OMNIROUTE_RUN/benchmark_juanca_smc_fvg.py` | `reportado-sin-revision` |
| **EMAS (Discreto)** | `lab_artifacts/CODEX_OMNIROUTE_RUN/juanca_emas_out/juanca_emas_benchmark.json` | `836b92b` | MNQ M5 canonico, EMAs 10/20/55/200 + HTF M15/H1, $50k inicial, riesgo $500 (1%), `discrete_partial_contracts=True`, $4.00 RT friccion | `5b190bca...` (M5) | `python lab_artifacts/CODEX_OMNIROUTE_RUN/benchmark_juanca_emas.py` | `reportado-sin-revision` |
| **CRT-TBS (Champion)** | `lab_artifacts/CODEX_OMNIROUTE_RUN/four_strategies_benchmark.json` | `731f980` | MNQ M5 canonico, H4 bias, H1 CRT, M5 confirmation, `target_mode="fixed_rr"`, `fixed_rr=2.0`, `time_exit_mode="flat"`, $4.00 RT friccion | `5b190bca...` (M5) | `python lab_artifacts/CODEX_OMNIROUTE_RUN/benchmark_four_strategies.py --strategy crt_tbs_champion` | `reportado-sin-revision` |
| **CRT-TBS (Default Literal)** | `lab_artifacts/CODEX_OMNIROUTE_RUN/four_strategies_benchmark.json` | `731f980` | MNQ M5 canonico, `target_mode="crt"`, `min_rr=1.50`, produce 0 trades (paradoja geometrica demostrada) | `5b190bca...` (M5) | `python lab_artifacts/CODEX_OMNIROUTE_RUN/benchmark_four_strategies.py --strategy crt_tbs_default` | `verificado` |
| **ORB (Experimental)** | `lab_artifacts/CODEX_OMNIROUTE_RUN/four_strategies_benchmark.json` | `731f980` | MNQ M5 canonico, 09:30-10:00 NY, stop opuesto, target 2R, `max_hold=192` barras M5, `time_exit_mode="flat"`, $4.00 RT friccion | `5b190bca...` (M5) | `python lab_artifacts/CODEX_OMNIROUTE_RUN/benchmark_four_strategies.py --strategy orb` | `reportado-sin-revision` |
| **Replay Historico RT** | `lab_artifacts/CODEX_OMNIROUTE_RUN/realtime_replay_verification.json` | `2dfcd78` | ReplayEngine, FrozenClock, 2,000 barras canonicas MNQ M5, AsyncIOEventBus, SmcFvgStrategy (20 senales identicas a backtest) | `5b190bca...` (M5) | `python lab_artifacts/CODEX_OMNIROUTE_RUN/verify_realtime_replay.py` | `verificado` |
| **CRT-TBS & ORB (Flat vs Market)** | `lab_artifacts/flat_vs_market_comparison.json` | `bdc9480` | Comparativa directa `time_exit_mode="market"` vs `"flat"`, atribucion de PnL a expiraciones | `5b190bca...` (M5) | `python lab_artifacts/run_flat_vs_market.py` | `verificado` |
| **Auditoria Intrabarra M1 (A2.4)** | `lab_artifacts/intrabar_canonical_audit.json` | `fix/a1-a2-baseline` | Resolucion causal M1 selectivo en dos pasadas sobre M5 canonico, cota conservadora vs resuelta | `5b190bca...` (M5) + `9cbf7da1...` (M1) | `python lab_artifacts/run_intrabar_canonical.py` | `verificado` |

---

## 3. Resolucion de Archivos sin Commit (A1.3 / T3)

| Archivo Original | Decision | Justificacion |
|---|---|---|
| `lab_artifacts/CODEX_OMNIROUTE_RUN/four_strategies_out/four_strategies_benchmark.json` | **Eliminar directorio** | Copia redundante bit a bit (SHA `0af572b4...`) del archivo ya versionado `lab_artifacts/CODEX_OMNIROUTE_RUN/four_strategies_benchmark.json`. |
| `scratch/baseline_before.json`, `baseline_after.json`, `baseline_check.json` | **Mover a `lab_artifacts/`** | Evidencia de equivalencia historica y regresion bit a bit (SHA `71debb93...`) pre y post refactor. Versionado como evidencia. |
| `scratch/display_table.py` | **Mover a `.gitignore`** | Script de conveniencia para formatear tablas Markdown en consola durante el analisis exploratorio. |
| `scratch/generate_final_report.py` | **Mover a `.gitignore`** | Script auxiliar temporal utilizado para ensamblar el informe `CUATRO_ESTRATEGIAS_EVALUACION_COMPARATIVA.md`. |

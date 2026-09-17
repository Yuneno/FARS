# Reversal V1 — bloqueos y preguntas no evaluables

## Cancelación causal de una orden límite pendiente

- **Pregunta exacta:** ¿qué API autorizada debe usar una estrategia para cancelar una orden límite ya entregada al executor cuando un cierre vuelve a cruzar el nivel de estructura antes del fill?
- **Hallazgo:** `src/backtest/executor.py` mantiene la orden en su estado local `pending`; mientras existe llama `strategy.evaluate(history)`, pero un retorno `None` no cancela esa orden. Sólo la retiran fill o expiración. No existe hook de cancelación y el encargo prohíbe modificar executor.
- **Mitigación:** la estrategia registra determinísticamente la invalidación en su propio estado y no vuelve a emitir la señal. El runner y el informe marcarán la ejecución efectiva de esta regla como `no-evaluable`; no se declarará satisfecha ni se presentará una corrida límite como completa hasta que Hermes autorice una API de cancelación en un bloque separado.

## Vigencia del setup entre sweep y CHoCH/FVG

- **Pregunta exacta:** ¿cuántas barras puede permanecer vigente un sweep mientras se espera CHoCH/MSS y el FVG posterior?
- **Hallazgo:** el encargo declara `wait` para la orden límite, pero no una caducidad del setup previo a la orden.
- **Mitigación:** no se añade un parámetro “razonable” ni se optimiza. V1 conserva el último sweep causal del mismo lado hasta que se consume por una señal o lo reemplaza un sweep posterior. Esta convención se expone en `parameters()` y debe ratificarse antes de una promoción (que este protocolo no permite).

## Cobertura/coste de la corrida canónica (Ronda 2 — Medición de Paralelización)

- **Pregunta exacta:** ¿Permite el runner paralelizado terminar el protocolo completo (7 configuraciones × 3 escenarios × 8 folds con purga y embargo) en el objetivo declarado de ≤ 60 minutos en este equipo?
- **Medición empírica y reproducible (Ronda 2):**
  - **Núcleos disponibles:** 12 núcleos lógicos (`os.cpu_count() == 12`).
  - **Barras canónicas M5:** 518.237 barras (`databento/MNQ_M5.csv`, SHA-256: `fbed6061205b8299af140f85e36b472f5f1d88084977ad9c4ca9aa1f817b8a96`).
  - **Throughput medido del runner paralelizado (spawn multiprocessing):**
    - 1 worker: ~550 barras/s
    - 4 workers: 1.130,5 barras/s (2,05x)
    - 6 workers: 1.436,7 barras/s (2,61x)
    - 8 workers: 1.662,8 barras/s (3,02x)
  - **Prueba de determinismo T1 (Folds 0 y 1, cisd_only_primary, c1_canonical):**
    - Secuencial: 167,23s (31 trades evaluados).
    - Paralelo (2 workers spawn): 73,22s (31 trades evaluados, speedup 2,28x).
    - **Resultado:** 100% bit a bit idéntico (artefactos: `determinism_sequential.json` y `determinism_parallel.json`).
  - **Desglose de cómputo del protocolo completo (36/6/6, 8 folds, 7 configuraciones):**
    1. **Ventanas de test OOS:** 35.667 barras/fold × 8 folds × 7 configuraciones = 1.997.352 barras por escenario. Con 6 workers (1.436,7 barras/s) toma **23,2 minutos por escenario** (~69,6 min para los 3 escenarios).
    2. **Auditoría de solape y embargo (`train_plus_test` continuo sin truncar):** 247.151 barras/fold × 8 folds × 7 configuraciones = **13.840.456 barras**. Con 6 workers toma **160,5 minutos (2,68 horas)**.
    3. **Tiempo total del protocolo completo:** 69,6 min + 160,5 min = **230,1 minutos (~3,84 horas de reloj)**.
- **Mitigación y Regla de Honestidad (§4):**
  Dado que el tiempo real medido excede el presupuesto de sesión (≤ 60 min), y el encargo prohíbe explícitamente truncar el protocolo o inventar métricas parciales presentadas como completas, se declara el estado de ejecución como `not_run_compute_budget` en `manifest.json`. El runner queda completamente listo, paralelizado con `ProcessPoolExecutor`, con las 7 configuraciones implementadas y con el determinismo verificado bit a bit. Cualquier operador puede ejecutarlo directamente con `python lab_artifacts/run_v1_protocol.py --scenario c1_canonical --workers 6`.

## Suite completa y temporales pytest bajo el sandbox

- **Pregunta exacta:** ¿puede Hermes ejecutar la suite fuera del perfil que niega enumerar cualquier `basetemp` creado por pytest?
- **Hallazgo:** la suite recolectó 1.576 tests antes de añadir la última prueba de determinismo end-to-end. Pasaron 1.362 y se saltaron 2; 212 quedaron en error de setup exclusivamente porque `tmp_path` recibió `PermissionError` al enumerar tanto el temp por defecto como dos `--basetemp` explícitos. Los 75 tests focalizados finales (incluidos los 12 nuevos) pasaron. Los dos directorios creados por pytest quedaron con ACL no enumerable y el sandbox también rechazó `Remove-Item` explícito.
- **Mitigación:** Hermes debe repetir `python -m pytest -q` fuera de este sandbox y retirar `pytest_tmp_v1` y `C:\Users\yo\AppData\Local\Temp\fars_v1_pytest_20260916` si aún existen. No se declara suite verde.

## Informe fuera del worktree

- **Pregunta exacta:** ¿quién materializa `E:\gemini avances\FARS_CODEX_V1_REVERSION_INFORME.md` si el sandbox sólo autoriza escritura en este worktree y temp?
- **Mitigación:** se deja el informe equivalente dentro de `lab_artifacts/v1_protocol/INFORME.md`; Hermes puede copiarlo al destino solicitado durante el aterrizaje.

# Reversal V1 — bloqueos y preguntas no evaluables

## Cancelación causal de una orden límite pendiente

- **Pregunta exacta:** ¿qué API autorizada debe usar una estrategia para cancelar una orden límite ya entregada al executor cuando un cierre vuelve a cruzar el nivel de estructura antes del fill?
- **Hallazgo:** `src/backtest/executor.py` mantiene la orden en su estado local `pending`; mientras existe llama `strategy.evaluate(history)`, pero un retorno `None` no cancela esa orden. Sólo la retiran fill o expiración. No existe hook de cancelación y el encargo prohíbe modificar executor.
- **Mitigación:** la estrategia registra determinísticamente la invalidación en su propio estado y no vuelve a emitir la señal. El runner y el informe marcarán la ejecución efectiva de esta regla como `no-evaluable`; no se declarará satisfecha ni se presentará una corrida límite como completa hasta que Hermes autorice una API de cancelación en un bloque separado.

## Vigencia del setup entre sweep y CHoCH/FVG

- **Pregunta exacta:** ¿cuántas barras puede permanecer vigente un sweep mientras se espera CHoCH/MSS y el FVG posterior?
- **Hallazgo:** el encargo declara `wait` para la orden límite, pero no una caducidad del setup previo a la orden.
- **Mitigación:** no se añade un parámetro “razonable” ni se optimiza. V1 conserva el último sweep causal del mismo lado hasta que se consume por una señal o lo reemplaza un sweep posterior. Esta convención se expone en `parameters()` y debe ratificarse antes de una promoción (que este protocolo no permite).

## Cobertura/coste de la corrida canónica

- **Pregunta exacta:** ¿está accesible localmente el ZIP canónico completo y permite terminar todas las variantes × escenarios sin recortar folds?
- **Mitigación:** el runner conservará 36/6/6, purga y embargo `h=192` completos. Si falta el dataset o el tiempo de cómputo excede la sesión, no generará métricas parciales como si fueran completas; registrará el motivo y una estimación reproducible.

## Suite completa y temporales pytest bajo el sandbox

- **Pregunta exacta:** ¿puede Hermes ejecutar la suite fuera del perfil que niega enumerar cualquier `basetemp` creado por pytest?
- **Hallazgo:** la suite recolectó 1.576 tests antes de añadir la última prueba de determinismo end-to-end. Pasaron 1.362 y se saltaron 2; 212 quedaron en error de setup exclusivamente porque `tmp_path` recibió `PermissionError` al enumerar tanto el temp por defecto como dos `--basetemp` explícitos. Los 75 tests focalizados finales (incluidos los 12 nuevos) pasaron. Los dos directorios creados por pytest quedaron con ACL no enumerable y el sandbox también rechazó `Remove-Item` explícito.
- **Mitigación:** Hermes debe repetir `python -m pytest -q` fuera de este sandbox y retirar `pytest_tmp_v1` y `C:\Users\yo\AppData\Local\Temp\fars_v1_pytest_20260916` si aún existen. No se declara suite verde.

## Informe fuera del worktree

- **Pregunta exacta:** ¿quién materializa `E:\gemini avances\FARS_CODEX_V1_REVERSION_INFORME.md` si el sandbox sólo autoriza escritura en este worktree y temp?
- **Mitigación:** se deja el informe equivalente dentro de `lab_artifacts/v1_protocol/INFORME.md`; Hermes puede copiarlo al destino solicitado durante el aterrizaje.

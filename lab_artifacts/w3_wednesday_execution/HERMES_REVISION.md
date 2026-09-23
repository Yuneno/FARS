# Hermes — cierre técnico W3

## Dictamen
REVIEW PASSED WITH OBSERVATIONS — caracterización offline de la ruta legacy, no implementación Wednesday ejecutable. W3 cerrado técnicamente; commit/integración pendientes y no autorizados en este cierre.

El usuario pidió completar directamente el proceso, sin detenerlo ni enviar otra ronda a Gemini. Hermes corrigió únicamente documentación de W3 y guardó evidencia; no editó producción ni tests. Encargo W3-F1 marcado resuelto/no ejecutar.

## Revisión y atribución
Muse emitió `CHANGES_REQUIRED (docs-only, bounded)` en un evento `run.terminal.completed` de E:/FARS-LAB/MUSE_W3_REVIEW_RUN.jsonl. Sus bloqueadores fueron el target erróneo de la matriz y la atribución a una función intrabar inexistente. Hermes comprobó ambos contra fórmulas y fuente, y los corrigió. Muse NO volvió a aprobar la versión corregida; el dictamen final es de Hermes. El contenedor del proceso fue reportado posteriormente `exited`, código -15, termination_source=agent_close; no se atribuye un exit 0 al CLI ni queda revisión corriendo. El veredicto textual sí quedó registrado antes de ese cierre.

Correcciones adicionales verificadas por Hermes:
- Legacy resuelve/valida antes de dimensionar; enhanced calcula qty antes de resolver. Corregida afirmación universal contraria y retirado pseudocódigo de propuesta incompleta.
- Targets nominales long 18451.25 / short 18148.75 verificados con Decimal. RR de la matriz es potencial al target, no cobrado ni neto.
- 16:05 es salida tardía, no satisfacción del contrato exacto 16:00.
- Declarados defaults de costes, alcance legacy/sintético y ausencia de certificación enhanced/M1/feed real.

## Ejecución independiente real
Con E:/FARS-LAB/.venv-fars/Scripts/python.exe:
1. `-m pytest tests/test_wednesday_execution_contract.py tests/test_wednesday_detector.py tests/test_wednesday_rth.py -q`: 69 passed, exit 0 (0.94s antes de correcciones; 0.82s después). Última salida guardada en HERMES_TESTS.log.
2. `-m pytest -m "not statistical" -q -rs`: 1731 passed, 2 skipped, 10 deselected, exit 0, 58.32s. Ejecutado independientemente antes de cambios solo documentales. Skips tests/test_parallel.py:192,233 por multiprocessing bloqueado en sandbox; statistical excluido expresamente. Salida completa en conversación/tool terminal; no confundir con log original de Gemini.
3. Assertions documentales y aritmética Decimal: PASS. Hashes de seis archivos W1/W2 coinciden con lectura Hermes previa a sus correcciones; snapshot HERMES_HASHES_CIERRE.json incluye test W3 para futuras dependencias. W1 además coincide con baseline histórico Hermes. Sin manifiesto Hermes pre-Gemini para W2 no se certifica retroactivamente toda su historia.
4. Git sigue en bloque-w1-wednesday-detector, base ad4df41d7bb05040920ed3ea4ed656b4e71e0e6c; diff tracked src/tests vacío. Nuevos archivos W1/W2/W3 siguen untracked. RT9/_tmp excluidos; no inspeccionados ni alterados por este cierre.

## Desviaciones aceptadas y límites
Las 12 pruebas W3 caracterizan legacy. Solo algunas están parametrizadas long/short; gap de precio/salidas temporales son long. Parte de los costes usa defaults declarados y algunas salidas carecen de assertions de todos sus campos. Es suficiente para demostrar la incompatibilidad stop estructural/target desde fill y los límites temporales actuales, no para aprobar una extensión de ejecución. No ampliar alcance ni repetir una ronda cosmética.

Próximo bloque propuesto, NO ejecutado ni aprobado aquí: target RR opt-in calculado desde fill y stop normalizado, con defaults preservados y validación/sizing por ruta. Decisión de salidas/datos faltantes separada. No hay datos reales, edge, estrategia operativa, commits, merge ni push en W3.

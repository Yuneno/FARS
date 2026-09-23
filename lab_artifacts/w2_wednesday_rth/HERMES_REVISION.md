# Hermes — cierre técnico W2-F1

PASS_WITH_OBSERVATIONS. Aprobado alcance offline sintético; pendiente commit/integración de jornada, sin broker ni rentabilidad validada.

Base/estado verificados: rama bloque-w1-wednesday-detector, HEAD ad4df41d7bb05040920ed3ea4ed656b4e71e0e6c. Nuevos archivos W1/W2 sin commit; RT9/_tmp ajenos excluidos. Status no equivale a certificación de bytes ajenos.

Muse meta xhigh, escritura/shell deshabilitados, exit 0: PASS_WITH_OBSERVATIONS, sin bloqueadores. Log E:\FARS-LAB\MUSE_W2_F1_REVIEW_RUN.jsonl. Chequeo session_date antes de mutación, tests spy/weekend/overnight y corrección documental confirmados por inspección. No ejecución de tests por Muse.

Verificación independiente Hermes con E:/FARS-LAB/.venv-fars/Scripts/python.exe:
- pytest tests/test_wednesday_detector.py tests/test_wednesday_rth.py -q: 57 passed in 0.70s.
- pytest -m "not statistical" -q -rs: 1719 passed, 2 skipped, 10 deselected in 55.04s, exit 0.
Skips: tests/test_parallel.py:192,233 por multiprocessing bloqueado; statistical explícitamente fuera.

SHA256 W1 recalculados coinciden con los registrados antes de F1:
- src/zones/wednesday.py: 2a98ac963c39599ff9cf179916961d5a74b03beef462d8de04bcbfba26baed25
- tests/test_wednesday_detector.py: 7f9372671309837e538ae156efecaa6c3fd0067cbb76026d9eb149d27ec1bd4c
- lab_artifacts/w1_wednesday_detector/IMPLEMENTACION.md: 54339f426a84c753507137ee28b65424ef8826593b489eaac9ac55fa9593af1e

Observaciones no bloqueantes: el test de memoria usa <=10 resúmenes para su fixture, no demuestra cota global de esa ventana inclusiva; no promoverla a garantía universal. Poda de claves por años ISO se activa solo cuando supera 52; con pocas emisiones puede conservar claves más antiguas, aunque el tamaño sigue acotado. Interpretar documentación temporal con esa salvedad, no como TTL estricto. No modificar código para ampliar alcance en este cierre.

Funcionalidad aprobada: resúmenes RTH con grilla completa, disponibilidad causal y detector W1, rechazo de discrepancia de calendario antes de mutar, sin relabeling overnight. No equivale a D1/Globex ni valida origen de feed real. No estrategia ejecutable, target, costes o medición histórica. No commit/merge/push en esta revisión.

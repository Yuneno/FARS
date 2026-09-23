# Integración de bridge y W1–W3

Autorización de Ricardo: commits, integración en main y publicación de trabajo aprobado; excluir RT9 pendiente, temporales y secretos.

## Landing verificado
- Bridge offline MNQ: ad4df41.
- W1 detector causal: 349cda0.
- W2 adapter RTH: 986bfd6.
- W3 caracterización de ejecución legacy: d0e90a9.
- main actualizado por `git merge --ff-only bloque-w1-wednesday-detector` desde e56e30a a d0e90a9, sin conflictos.
- Commit documental posterior incluye este registro, ledger y HANDOFF_LAPTOP_SHADOW.md. La publicación se comprueba contra SHA de refs/heads/main remoto; no confundir integración local con CI remoto.

Las frases pendientes de commit/integración y tablas de hashes de las actas W1/W2/W3 son históricas, anteriores al landing. Este registro las sustituye SOLO respecto al estado de integración. Aprobaciones siguen limitadas a alcance offline; no rentabilidad ni órdenes.

## Limpieza de landing
`git diff --cached --check` detectó espacios de fin de línea Markdown en informes IMPLEMENTACION W1/W2/W3 y línea vacía final en test_wednesday_detector.py. Se retiraron exclusivamente esos blancos; no cambió lógica. Por ello hashes byte-a-byte históricos de esos archivos ya no deben exigirse al checkout actual. Git autocrlf también puede variar bytes físicos; usar blobs Git para identificar código publicado.

## Pruebas independientes después del fast-forward y limpieza
Python E:/FARS-LAB/.venv-fars/Scripts/python.exe:
- pytest tests/test_wednesday_detector.py tests/test_wednesday_rth.py tests/test_wednesday_execution_contract.py -q: 69 passed, 4.72s, exit 0.
- pytest -m "not statistical" -q -rs: 1731 passed, 2 skipped, 10 deselected, 59.77s, exit 0.
Skips tests/test_parallel.py:192,233 por multiprocessing bloqueado en sandbox. Statistical no ejecutado. Resultados reales de terminal; no afirmar CI GitHub verificado.

RT9 acceptance_mock_report.json, acceptance_mock_session.jsonl y acceptance_live_session.jsonl preservados y excluidos del staging. _tmp_hermes_verify también excluido. Sin credenciales nuevas ni sesión broker en esta integración.

## Laptop
HANDOFF_LAPTOP_SHADOW.md es encargo de preflight y operación read-only, no certificación de runner listo. Laptop reportada de 4 núcleos: 1 worker y hilos numéricos a 1, sin LLM en bucle. Ricardo parará manualmente. No hay proceso iniciado en laptop desde esta máquina.

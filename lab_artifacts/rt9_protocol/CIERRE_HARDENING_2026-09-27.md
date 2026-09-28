# CIERRE — RT-9 hardening de lecturas (2026-09-27)

**Orquestador:** Hermes · **Implementador:** Muse 1.4.0 (provider meta, high) · **Revisor:** Codex (gpt-6-astra, medium) · **Suite:** verificada por Hermes fuera de sandbox

## Tarea
Endurecer PracticeOrderClient/ProjectXClient contra timeouts de lectura del gateway (incidente ventana RT-9 2026-09-27: 3 intentos, 2 muertos por ssl read timeout a 10 s).

## Cambios hechos (diff verificado por Hermes)
- `src/realtime/connectors/projectx.py`: timeout default 10.0 → 30.0.
- `src/realtime/orders/practice_client.py`: `DEFAULT_PRACTICE_TIMEOUT=30.0`, `READ_RETRY_MAX_ATTEMPTS=3`, backoff (2 s, 4 s), `_READ_RETRY_PATHS = _READ_ONLY_PATHS | {/api/Order/search, /api/Order/searchOpen}`, `_is_retryable_read_error` (solo TimeoutError o ProjectXError exacto con timeout/fallo de transporte), retries SOLO en `_post` de rutas de lectura; escrituras (place/cancel/cancel_all/closeContract) cero reintentos, semántica intacta.
- `tests/realtime/test_projectx_connector.py`: assertion del default 10.0 → 30.0.
- `tests/realtime/test_practice_read_retry.py`: NUEVO, 9 tests deterministas.

## Verificación (Hermes, fuera de sandbox, venv FARS)
- `pytest tests/realtime/test_practice_read_retry.py -m "not statistical" -q` → **9 passed**
- `pytest tests -m "not statistical" -q` → **1752 passed, 2 skipped, 10 deselected** (54.90 s)

## Dictamen Codex (log: E:\FARS-LAB\CODEX_RT9_HARDENING_REVIEW_RUN.log)
**REVIEW PASSED** — sin CRITICAL ni WARNING. Observaciones:
1. (latente) `_READ_ONLY_PATHS` heredado incluye `Auth/loginKey` y `Auth/validate`; `Auth/validate` puede renovar token → "read-only" no demuestra idempotencia. Practice NO llama esas rutas hoy. Futuro encargo: excluir Auth del retry set.
2. Peor caso de latencia: 3 intentos × 30 s + backoffs ≈ **96 s por lectura**, sin deadline global; escrituras esperan hasta 30 s. Si la ejecución Practice se inyecta en la sesión realtime, la llamada síncrona bloquea el event loop (`session.py:261`). No bloquea el harness actual.
3. Cobertura a añadir (sin defecto demostrado): fallo–fallo–éxito; timeout seguido de rechazo definitivo; subtipos con mensajes coincidentes; `place` ambiguo con búsquedas fallidas.
4. Verificación propia Codex: 9/9 nuevos pasan; tests/realtime 301 passed + 30 errores de setup ambientales del sandbox (permisos temp).

## Decisión del orquestador
- ACCEPT: retry sobre `Order/search{,Open}` (lecturas idempotentes, rutas exactas del incidente; el cliente de órdenes necesita buscar órdenes). Borde de escritura intacto.
- Observaciones 1–3 anotadas como deuda menor; no bloquean.

## Muse — fragmento de reporte literal (trace completo en la sesión Muse 01a0e4fc-4e25-7682-be43-56338046037c)
"...(a) crt4h, y los 5 checks RT8 fallidos — verificado corriendo el harness RT8: solo fallan checks con ese PermissionError); (b) git inaccesible por ownership (dubious ownership) → fallan los 4 tests que invocan git rev-parse. Hermes debe re-correr fuera del sandbox para el verde esperado.
- Importante: _READ_ONLY_PATHS del connector NO contiene /api/Order/search{,Open} (el connector read-only los prohíbe); los añadí por unión explícita porque son lecturas idempotentes y exactamente las rutas del incidente (paso 3). Si se exigiera retry estrictamente igual a _READ_ONLY_PATHS, el paso 3 quedaría sin reintentos — dejo la decisión a Hermes.
- Menor: muse.edit_file falla en archivos CRLF del repo; edité vía script Python preservando CRLF, diff mínimo sin reformato. Ruff reporta solo hallazgos preexistentes en esos archivos; mis líneas nuevas están limpias.
**No revisado**: acceptance en vivo (prohibido por el encargo); interfaces.py, events.py, risk.py intactos; sin variable de entorno (parámetro con default, como autoriza el encargo); ProjectXClient sin reintentos (alcance = practice client); el backoff es time.sleep bloqueante, coherente con el cliente síncrono urllib.
**Confianza**: alta.
**Output literal**: pytest tests/realtime/test_practice_read_retry.py -m "not statistical" -q → 9 passed in 0.85s · pytest (4 archivos) → 1 failed, 98 passed (fallo ambiental PermissionError WinError 10013) · pytest tests -m "not statistical" -q → 38 failed, 1714 passed, 2 skipped, 10 deselected in 105.38s (38 ambientales del sandbox: 33 sockets asyncio, 4 git rev-parse, 1 asyncio; cero atribuibles al cambio)."

## Estado git
Sin commits (Hermes los aterriza cuando Ricardo lo pida). Baseline pre-Muse y diff verificados: solo 3 archivos de src/tests tocados + 1 test nuevo. Basura de sandbox `pytest-of-yo/`, `tmp21f017g9/` en la raíz del repo — pendiente de limpieza con permiso (rm -rf).

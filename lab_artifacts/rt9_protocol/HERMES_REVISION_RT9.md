# HERMES_REVISION_RT9 — Bloque RT-9: Órdenes Reales en Cuenta Practice

> **Revisor:** Hermes — **Fecha:** 2026-09-20
> **Artefacto auditado:** commit `5750e74` (rama `bloque-rt9-ordenes-practice`), base declarada `6cbe1cb` (límites §4-bis ya revisados: acta `circuit_breakers_protocol/HERMES_REVISION_CIRCUIT_BREAKERS.md`)
> **Alcance:** construcción + aceptación OFFLINE. La ventana EN VIVO sigue pendiente de mercado abierto.

## Veredicto: **PASA (offline) — con 1 BLOQUEANTE para la ventana en vivo**

La construcción está muy sólida y verificada por re-ejecución independiente. **Pero el "modo live" del arnés no está cableado**: las instrucciones del INFORME/BLOCKERS para la ventana fallarían. Ese es el único bloqueante; hay 4 hallazgos más a cerrar antes de la ventana y varios refinamientos menores.

### Evidencia (re-ejecutada por Hermes, todo offline, cero red)

1. **Suite realtime: 263/263 passed (2.20s)** — coincide al número con lo declarado (+14 vs los 249 de la ronda §4-bis).
2. **Arnés mock re-corrido por Hermes:** `python -m src.realtime.acceptance_rt9 --mock` → **PASS, exit 0**, 8/8 pasos (salida guardada en `_tmp_rt9_check/mock_out.json`).
3. **Sondas propias (4/4 checks duros PASS):**
   - Allowlist vacía → `UnauthorizedAccountError` fail-closed ✓
   - Cuenta combate (`1.5KCHCR-…`) → prohibida ✓ · flag `PRACTICE_EXECUTION_ENABLED=False` → bloquea ✓
   - Anti-OrderPending: timeout + no encontrada en búsqueda → `OrderPendingAmbiguityError`, **1 solo POST** (sin re-envío) ✓
4. **Manifiesto:** 4/5 hashes resuelven contra los blobs (`git show 5750e74:<path> | sha256sum`); el 5º (`acceptance_mock_report.json`) resuelve contra la variante CRLF del worktree (captura por stdout en Windows) — ver R-a.
5. **Gates de la capa cliente correctos**: allowlist default = la cuenta Practice (nombre+id), patrones prohibidos, HTTPS obligatorio, `LIVE_EXECUTION_ENABLED` exigido False en constructor y en `_post`.

### Hallazgos (probados con sondas propias)

- **H1 — BLOQUEANTE VENTANA: `--live` es un stub.** El harness solo tiene runner mock; `--live` imprime *"requires open market window. Exiting fail-closed."* y sale con **exit 2** (probado). El INFORME §5 y BLOCKERS §2 afirman que el arnés "ejecutará la checklist de 8 pasos" en vivo — **hoy no ocurre**. Cablear el modo live: credenciales del `.env` (sin imprimir), `PRACTICE_EXECUTION_ENABLED=True` solo dentro del harness, chequeo de ventana de mercado, confirmación explícita del dueño, reportes JSONL por paso y kill-switch como último paso. (O corregir la documentación si se decide no cablear aún.)
- **H2 — El tope declarado "máx 1 micro" NO se aplica.** Sonda: `size=3` con stop de 5 pts (riesgo $30 ≤ $200) → **se despachó `size=3` al gateway**. El preregistro declara `max_position_size_contracts: 1`. Arreglo: clamp/rechazo si `size != 1` (adapter o cliente) + test.
- **H3 — Fail-open sin precios (R1 de la revisión §4-bis, sigue abierto).** Intent sin `entry/stop` → market `size=2` **sin pre-check de $200 y sin brackets**; con stop pero sin entry → el stop **no viaja** como protección. Arreglo: vetar despacho gateway si faltan `entry` y `stop` (o exigirlos con test explícito).
- **H4 — Flatten optimista.** Con el gateway fallando en cancel+close (excepción silenciada), `adapter.flatten()` **devuelve la posición como plana**, `flatten_count++`, posición local a 0 — sin verificación real. El arnés sí verifica (search→0), pero la ruta general no. Recomendado: verificar post-flatten (como el harness) o no marcar plano si el cierre no se confirmó.
- **H5 — Comando equivocado en las instrucciones live.** `python -m src.realtime.doctor` **no existe** (probado: `No module named`). El correcto es `fars-projectx doctor` (ya validado, selecciona `PRAC-V2-673085-85699223`).

### Refinamientos menores

- **R-a** Manifiesto RT-9 con normalización heterogénea (1/5: reporte CRLF vs blobs LF). Homogeneizar (declarar normalización o recomputar contra el blob).
- **R-b** `practice_client.py`: imports sin uso (`json`, `Decimal`); `raw.get("data")` puede devolver dict → `int()` lanzaría `TypeError` sin envolver (robustez de parseo); el `customTag` usa reloj real (OK live; considerar clock inyectado para determinismo).
- **R-c** `cancel_all_orders`/`flatten_all` silencian errores por-orden (`pass`) — surface o log para diagnóstico.
- **R-d** Regla declarada "15:10 CT flatten" (preregistro) sin mecanismo en código — declarar pendiente o implementar antes de sesiones cercanas al cierre.
- **R-e** `API_NOTES.md` cita la fuente sin URL/fecha de la doc oficial — añadir link.
- **R-f** Paso 8 del mock: el check de "credentials safe" solo mira payloads (los headers con Bearer no se loguean — bien); opcional reforzar.

### Notas

- Lo bueno a conservar: gates fail-closed probados, disciplina anti-`OrderPending` real (recovery por `customTag` + error de ambigüedad sin re-envío), brackets calculados a ticks exactos (50 pts → 200 ticks), kill-switch con posición (paso 6) verificado, §4-bis integrados, preregistro con la cuenta real (150k, nombre+id).
- Orden sugerido pre-ventana: **H1 → H2/H3 → H4/H5** (H2/H3/H5 son minutos; H1 es el trabajo real).
- La ventana en vivo (domingo noche / lunes) **no debe correrse con el harness actual** tal cual: fallaría en el paso 2 de sus propias instrucciones.

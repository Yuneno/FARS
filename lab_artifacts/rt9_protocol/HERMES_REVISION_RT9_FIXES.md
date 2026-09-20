# HERMES_REVISION_RT9_FIXES — Re-verificación de fixes pre-ventana (commit `9928b10`)

> **Revisor:** Hermes — **Fecha:** 2026-09-20
> **Artefacto auditado:** commit `9928b10` (fixes pedidos en `HERMES_REVISION_RT9.md`), rama `bloque-rt9-ordenes-practice`.

## Veredicto: fixes **VERIFICADOS** — con **1 defecto bloqueante de ventana** (fix de 1 línea)

Los hallazgos H2–H5 quedaron resueltos y re-verificados con sondas propias (22/23 PASS; el único FAIL es el defecto nuevo). **El wiring live (H1) quedó casi perfecto, pero tiene un bug de API que rompería la ventana.**

### Re-verificación (sondas Hermes, cero red)

- **Market hours checker:** 7/7 casos correctos (sáb cerrado · dom <17:00 CT cerrado · dom 17:30 abierto · halt diario 16–17 CT · viernes cierre 16:00) ✓
- **Cutoff 15:10 CT:** 4/4 (bloquea 15:10–17:00 en semana; no en finde) + veto `MARKET_CLOSE_CUTOFF_REACHED` en el adapter ✓
- **H2 — 1 micro:** clamp en adapter (`CLAMPED_TO_1_MICRO`, size=1 enviado) + rechazo directo del cliente (`DECLARED_LIMIT_VIOLATION`) ✓
- **H3 — sin precios:** vetos `MISSING_ENTRY_OR_STOP_PRICE` (sin ambos; stop sin entry) ✓
- **H4 — flatten verificado:** excepciones propagadas (no silencio); posición local preservada; `FLATTEN_UNCONFIRMED` si el cierre no se confirma ✓
- **Sin regresiones:** anti-OrderPending ambiguo ✓ · allowlist vacía ✓ · **suite realtime 270/270 (2.01 s)** re-ejecutada · **mock acceptance PASS re-corrido** (en cwd temporal, repo intacto).
- **Manifiesto nuevo:** 5/5 SHA-256 resuelven contra los blobs de `9928b10` ✓
- **Docs:** `fars-projectx doctor` corregido ✓ · API_NOTES con URLs de la documentación oficial ✓ · run_live con confirmación interactiva, kill-switch final y cleanup garantizado ✓

### DEFECTO BLOQUEANTE (ventana)

**F1 — `run_live_acceptance()` llama `px_client.search_accounts()`, que NO existe en `ProjectXClient`.**
Verificado: `hasattr(ProjectXClient, "search_accounts") == False`; el método real es `list_accounts` (el mismo que usa `doctor` y el lister). El live path fallaría con `AttributeError` en el paso "Search and verify Practice account allowlist" — **antes** de colocar ninguna orden (fail-closed accidental, pero la aceptación no correría).

**Fix (1 línea)** en `src/realtime/acceptance_rt9.py`:

```diff
-        accounts = px_client.search_accounts()
+        accounts = px_client.list_accounts()
```

(Los accesos `.account_id/.name/.can_trade/.simulated` sí existen en el conector.)

**Refinamiento sugerido (para que no se repita):** hacer `run_live_acceptance` testeable offline (inyectar el factory del `ProjectXClient` y el loader de credenciales) + un test que recorra el camino live con un doble → habría cazado este bug sin necesidad de ventana.

### Notas

- `session_token` es un **método** (no property): pasarlo como `token_provider` funciona correctamente (el cliente lo llama si es callable) ✓.
- Tras el fix de 1 línea: re-correr la suite (no afecta offline) y **la ventana queda lista** (dom 17:00 CT / 18:00 ET).
- Recordatorio operativo de la ventana: `fars-projectx doctor` → `-m src.realtime.acceptance_rt9 --live` (interactivo; sin `--yes` en la primera corrida), S2 apagado, solo cuenta Practice.

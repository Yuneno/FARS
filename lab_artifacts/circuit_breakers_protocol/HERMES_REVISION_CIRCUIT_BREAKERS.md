# HERMES_REVISION_CIRCUIT_BREAKERS — Bloque RT-9 (§4-bis): Circuit Breakers de Sesión

> **Revisor:** Hermes — **Fecha:** 2026-09-20
> **Artefacto auditado:** commit `6cbe1cb` (rama `bloque-circuit-breakers-sesion`), base declarada `main@5fb30ef`
> **Estado del worktree al auditar:** RT-9 ya en construcción encima (`practice_adapter.py` +154/−9; nuevo `src/realtime/orders/practice_client.py`) — **no** forma parte de esta revisión.

## Veredicto: **PASA**

Los cuatro límites declarados + la idempotencia funcionan exactamente como se documentan. Verificación independiente en árbol congelado del commit (sandbox), no solo la suite del autor.

### Evidencia

**1. Repro independiente de Hermes — 15/15 PASS** (`E:\FARS-LAB\_tmp_cb_check\repro_cb.py`, contra `6cbe1cb` congelado):
- **−$200:** denegación `DAILY_LOSS_LIMIT` → flatten 1 vez + `HALTED_DAILY`; decision repetida **no** re-flatnea; total flattens = 1.
- **+$500:** denegación `DAILY_PROFIT_TARGET_REACHED` → flatten + parada por HOY; pullback a +450 **sigue denegado** (latch); rollover UTC → `ACTIVE` y el engine vuelve a aprobar.
- **Tope $200/orden:** $300 → reduce a 1 micro y llena con nota `REDUCED_TO_1_MICRO`; $300 incluso a 1 micro → veta `MAX_RISK_PER_ORDER`.
- **−$1.000:** con balance real 150k → `DRAWDOWN_BUFFER_TOO_LOW` → `TOTAL_SHUTDOWN` + flatten; rollover **no** reactiva el shutdown.

**2. Suites (re-ejecutadas por Hermes):** `tests/realtime` **249 passed** (2.51s) y `tests/test_account.py + tests/test_engine.py` **96 passed** — coincide al número con lo declarado (345). Suite completa del congelado: **1595 passed + 4 re-verificados en vivo = 1599 efectivos** (los 4 aparentes fallos eran del sandbox sin `.git`; `tests/test_backtest_run_artifacts.py` da **8/8** en el repo vivo), 2 skipped, **0 regresiones** — línea base previa 1586; delta **+13 = los 9 del bloque + 4 de `test_risk_engine`**.

**3. Manifiesto:** 3/3 SHA-256 resuelven contra los blobs committeados (`git show 6cbe1cb:<path> | sha256sum`) y contra el worktree; bytes exactos (2205/9127/2317).
- *Nota de método (futuras auditorías):* `git archive` en esta máquina aplica CRLF (`autocrlf=true`) y produce hashes distintos — verificar siempre contra `git cat-file`/`git show`, no contra copias exportadas.

**4. Disciplina de alcance:** diff vs `main` = únicamente los 12 archivos declarados; `src/realtime/interfaces.py` intacto (`LIVE_EXECUTION_ENABLED = False`); cero red en el código nuevo; `create_practice_rules()` aislada; perfiles de fondeo reales sin tocar.

### Refinamientos (ninguno bloquea; cerrar en el wiring de RT-9)

- **R1 — Fail-open si faltan datos de riesgo:** un `OrderIntent` **sin** `entry/stop` se salta el pre-check de $200 (probado: llena size=3 sin cálculo). Sugerido: vetar si faltan precios (o exigirlos en el runner) — el tope vale lo que valen sus inputs.
- **R2 — Allowlist vacía = sin verificación:** con `account_allowlist=()` (default) el adapter acepta cualquier cuenta (probado: `CUENTA-NO-PRACTICE` → fill). El `orders/practice_client.py` de RT-9 ya hace lo correcto (fail-closed si vacía); igualar en el adapter o dejar el default = cuenta Practice. Ojo: el INFORME §3.4 afirma fail-closed "no presente en la allowlist" — hoy solo aplica si la allowlist está configurada.
- **R3 — Contrato de integración del runner (RT-9):** el flatten+halt se dispara cuando llega la **siguiente decision denegada**; sin nueva decisión no hay flatten (probado: posición abierta, −$200 tocado, 0 flattens). El runner debe (a) consultar `can_submit_order()` antes de enviar y (b) alimentar `on_risk_decision`/`on_event` — y evaluar riesgo ante snapshots, no solo ante señales.
- **R4 — Piso de drawdown vs balance real:** el piso −$1.000 se calcula del `initial_balance` declarado; con la cuenta real debe llamarse `create_practice_rules(initial_balance=150_000.0)` (con el default 50k el piso cae en 49k y **nunca** dispara). Declarar la referencia en el preregistro de RT-9.
- **R5 — Cosmética menor:** import `Callable` sin uso en `circuit_breaker.py`; `total_flattens_count` cuenta *disparos* (el detalle registra si había posición); el shutdown total registra `SYSTEM_HALTED` (kind válido) vs `SYSTEM_CIRCUIT_BREAKER` en el diario — unificar si se desea.

### Notas

- Base declarada `5fb30ef` ✓; padre real de `6cbe1cb` = `c9fe642` (commit docs del ledger, autoría Hermes — 1 línea de `BLOQUES_ESTADO.md`, contenido ajeno al bloque; viaja con el merge).
- Prerrequisito §6 del encargo RT-9 (cuenta Practice) **cumplido**: `FARS_PROJECTX_ACCOUNT_NAME = PRAC-V2-673085-85699223`, doctor OK.
- El veredicto aplica al commit congelado `6cbe1cb`; lo que el worktree tenga de más pertenece a RT-9 y se auditará con ese bloque.

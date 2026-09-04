# NIGHT_REPORT_PHASES.md

Fases estadísticas 11A / 11B / 11C — revisión nocturna de cierre.

**Fecha:** 2026-09-03
**Agente:** Hermes (implementación).
**Worktree:** `/Users/ricardomedina/Documents/FARS-phases`
**Rama:** `agent/phases-night`

---

## 1. Commit base

Hay que distinguir **dos bases**, porque la sesión nocturna no modificó código:

- **Commit base del dif de revisión (para Codex):** `c201aed` —
  `Separate bootstrap result schema version`. Es el **padre de `9bdc999`**
  (`Implement Phase 11A account data and Phase 11B rules`), es decir el último
  commit **anterior** a la implementación de las fases 11A/11B/11C. El diff
  `c201aed..HEAD` (restringido a los archivos de fase) contiene la implementación
  completa a revisar.
- **Commit base de la sesión nocturna:** `27c8972`. La sesión **comenzó y terminó
  ahí**: no se realizó ningún cambio de código, por lo que el diff de la noche es
  vacío.

Rama: `agent/phases-night` @ `27c8972` (HEAD sin cambios de código).

## 2. Estado encontrado de cada fase (contra el SPEC `FARS_1_2_PHASES_10B_14_SPEC.md`)

Todos los subphases declaran **"implemented; independent review pending"** en el SPEC
vivo (líneas 10B L132, 11A L184, 11B L272, 11C L394). No hay stamps falsos de
"review passed".

| Fase | Estado | Implementación | Tests | Resultado |
|---|---|---|---|---|
| 11A — Canonical monetary/account-event | implemented, review pending | `src/account_data.py` | `tests/test_account_data.py` | 46 passed |
| 11B — Generic rules engine v2 | implemented, review pending | `src/funded_rules_v2.py`, `src/funded_profiles.py` | `tests/test_funded_rules_v2.py` | 31 passed |
| 11C — Probabilistic paths & trade limits | implemented, review pending | `src/probabilistic_paths.py` | `tests/test_probabilistic_paths.py` | 16 passed |

## 3. Bugs confirmados y sospechas descartadas

### Contexto: review previo `CODE_AVANCE_REVIEW.md` → REVIEW FAILED @ `9bdc999`

El review independiente (Hermes) sobre la rama `code_avance` señaló:
1. **CRITICAL** — SPEC/README estampaban `10B`/`11A` como "review passed" sin
   artefacto independiente.
2. **WARNING #1** — Perfil Rapid 25K deshabilitado corría la lógica de
   profit-target/min-days; `not_evaluable` (prioridad 3) perdía ante
   `pass_blocked` (prioridad 2), haciendo que `primary_event` pareciera
   "casi pasó".
3. **WARNING #2** — Perfiles deshabilitados/provisionales mutaban balances,
   watermarks y trading-day sets (falta de aislamiento fail-closed).

### Bugs confirmados

**Ninguno en esta sesión.** Los tres findings previos **ya fueron corregidos** en el
commit actual (`27c8972`), concretamente en `7905705` ("Fix Phase 11 review
findings"):

- **CRITICAL — RESUELTO.** El SPEC vivo ahora dice "independent review pending".
- **WARNING #1 — RESUELTO.** `FundedAccountStateV2.apply()` retorna temprano
  (`src/funded_rules_v2.py` L758-772) cuando `self.profile.enabled` es `False`,
  emitiendo un único evento `not_evaluable`/`profile` **sin** correr la lógica de
  profit-target/min-days.
- **WARNING #2 — RESUELTO.** El camino deshabilitado no muta balance/equity/
  high-watermark/trading-days.

### Sospechas descartadas (reproducidas, no afinadas)

**Sospecha `not_evaluable` (W1/W2) — DESCARTADA, ya corregida.** Reproducción directa
con `rapid_25k_profile()` (`enabled=False`) + trade que alcanza el target (26500):

```
enabled: False | unresolved: 5
primary_event.kind: not_evaluable | rule: profile
events: [('not_evaluable', 'profile')]
pass_eligible: False
balance: 25000  equity: 25000  high_watermark: 25000
max_loss_threshold: 24000  trading_days: ()
W1 (primary=not_evaluable, NO 'casi pasó'): True
W2 (estado NO mutado): True
> CLAIM: AMBOS YA CORREGIDOS
```

Cubierto además por `test_rapid_25k_reference_profile_is_provisional_and_fail_closed`
(`tests/test_funded_rules_v2.py` L589-615), que pasa.

**Sondeos adicionales (rutas `not_evaluable` no cubiertas por `primary_event`) — SIN
DEFECTO:**
- Perfil ENABLED + cobertura intraday faltante (max-loss con
  `update/monitoring_cadence=intraday_event`, sin `CAP_INTRADAY_EVENTS`):
  `primary_event.kind == "not_evaluable"`, `pass_eligible == False` — conforme a
  SPEC §11B.2/§11C.3.
- 11A `cost_reconciliation`: CSV de cuenta sin campos de costo →
  `cost_reconciliation == "not_evaluable"` — conforme a contrato.

### Conclusión

**No se encontró ningún defecto reproducible en 11A/11B/11C.** Por la metodología
obligatoria ("Si no existe un defecto reproducible, no modifiques el código solo por
sospecha"), **no se modificó código de fase**. La periodicidad `not_evaluable` y la
mutación de estado ya estaban corregidas en el commit base.

## 4. Commits realizados

- **Cero commits de código** (no hubo defecto que corregir).
- **Un commit de documentación** del presente reporte (este archivo).

## 5. Archivos modificados

- **Código, tests, SPEC, config:** ninguno.
- **Único archivo nuevo:** `NIGHT_REPORT_PHASES.md` (este reporte).

## 6. Tests ejecutados y resultados exactos

Intérprete: conda base `python3` (Python 3.13.5), numpy 2.1.3, arch 8.0.0,
pytest 8.3.4 — el único entorno con dependencias instaladas; **no se tocó la red**.

```
cd /Users/ricardomedina/Documents/FARS-phases
/opt/anaconda3/bin/python -m pytest -p no:debugging tests/test_account_data.py -q        # 11A
/opt/anaconda3/bin/python -m pytest -p no:debugging tests/test_funded_rules_v2.py -q     # 11B
/opt/anaconda3/bin/python -m pytest -p no:debugging tests/test_probabilistic_paths.py -q # 11C
/opt/anaconda3/bin/python -m pytest -p no:debugging tests/test_package.py -q             # empaquetado
/opt/anaconda3/bin/python -m pytest -p no:debugging -m "not statistical" -q              # suite determinista completa
```

| Comando | Resultado |
|---|---|
| `tests/test_account_data.py` (11A) | **46 passed** in 0.25s |
| `tests/test_funded_rules_v2.py` (11B) | **31 passed** in 0.15s |
| `tests/test_probabilistic_paths.py` (11C) | **16 passed** in 2.52s |
| `tests/test_package.py` | **1 passed** in 0.29s |
| `-m "not statistical"` (completa, incluye realtime) | **909 passed, 10 deselected** in 38.69s |

## 7. Confirmaciones de alcance

- **Phase 11D permaneció completamente intacta.** No existe módulo de código 11D en el
  árbol (es especificación/trabajo futuro); `git status` limpio y `git diff --stat HEAD`
  vacío lo confirman. El SPEC no fue modificado.
- **No se modificaron RT, ProjectX, SMC-FVG ni Phases 12-14.** Ningún archivo de esos
  dominios cambió. La suite `tests/realtime/*` (ProjectX/RT) pasa sin modificación.
- **No hubo push ni merge** (ni rebase, reset ni clean). El repositorio original
  (`Documents/FARS`) no fue modificado.
- El worktree permanece en `27c8972`, limpio salvo este reporte (y su commit).

## 8. Pendientes, riesgos y decisiones que requieren revisión humana

1. **Suite estadística NO ejecutada.** El marcador `statistical` (10 tests, ~30+ min,
   características operativas de Phase 10A: FP rate/power/coverage con M=500-1000 y
   B=2000) exige el entorno de acceptance exacto (Python 3.12.13 +
   `requirements-acceptance.lock`). Se ejecutó solo la suite determinista. No debe
   presentarse el verde determinista como acceptance estadística.
2. **Revisión independiente pendiente** — por eso se invoca a Codex (ver sección 9).
   No estampar "review passed" sin un artefacto real.
3. **`CODE_AVANCE_REVIEW.md`** es un artefacto histórico de la rama `code_avance`
   (REVIEW FAILED @ `9bdc999`): se conserva como registro, fuera de alcance.
4. **Entorno divergente**: dev (3.13.5) != acceptance (3.12.13). Cualquier decisión de
   aceptación debe correrse en el venv de acceptance.
5. **Ninguna decisión de arquitectura estadística cambió** durante la sesión.

## 9. Review independiente (Codex)

Se invoca a Codex en modo read-only sobre la rama `agent/phases-night`, revisando el
diff `c201aed..HEAD` restringido a los archivos de fase. Resultado se anotará en
`CODEX_REVIEW_PHASES.md`.

---

*Fin del reporte.*

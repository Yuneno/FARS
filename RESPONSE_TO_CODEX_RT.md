# RESPONSE_TO_CODEX_RT

Date: 2026-09-03
Auditor file: `CODEX_REVIEW_RT_EXPLORATION.md` (HEAD at review: `7417e1b`)
Stable line: `agent/rt-night-2` @ `467318e` (untouched)
Explore after one correction cycle: `explore/rt-night`

Codex verdict on experiments: CHANGES_REQUESTED.

Hermes did not treat Codex as owner of the roadmap. Only DEFECTO_OBJETIVO
items that reproduced were patched, and only on `explore/rt-night`.

## F-1 — unique substring binds the wrong product

Codex class: DEFECTO_OBJETIVO
Hermes class: DEFECTO_OBJETIVO
Status: ACEPTADO y corregido

Evidence (Codex probe, reproduced by contract):

- `select_active_contract((MNQZ6 F.US.MNQ,), "NQ")` returned the MNQ row
  because `"NQ" in "MNQ"`.
- Same pattern for ES inside MES.

Fix on explore: `78fe379` — match exact id/name/symbol_id or dotted tokens,
not substrings. Test: `test_select_active_contract_rejects_nq_substring_of_mnq`.

Not a reason to restore the MNQ-only pin. The pin hid the bug; token match
is the fail-closed contract.

## F-2 — nested `_Stop` can halt without `SYSTEM_HALTED`

Codex class: DEFECTO_OBJETIVO
Hermes class: DEFECTO_OBJETIVO
Status: ACEPTADO y corregido

Evidence: ordinary quote conflict journals `SYSTEM_HALTED` (existing test).
If `_record_event` raises `_Stop` while emitting the halt marker, `ff7f415`
swallowed it with `pass`, so the journal had no halt event.

Fix on explore: `ead3614` — on nested `_Stop`, `recorder.record(system_event(...))`
bypasses the tracker. Test: `test_nested_stop_still_journals_system_halted`.

## F-3 — `listen` bypasses argparse

Codex class: INCOMPATIBILIDAD_REAL
Hermes class: INCOMPATIBILIDAD_REAL (not DEFECTO_OBJETIVO of Market Hub)
Status: ACEPTADO como incompatibilidad de CLI; NO corregido este ciclo

Evidence (Hermes, no live calls):

```text
python -m src.realtime.cli -h
  {doctor,contracts,bars}   # listen absent

python -m src.realtime.cli listen -h
  usage: fars-projectx-listen ...   # intercept works only if argv[0]==listen

python -m src.realtime.cli --env-file does-not-exist listen -h
  invalid choice: 'listen'  # exit 2
```

This is a discovery/UX contract miss, not a read-only or RT-9 break.
Addendum forbids auto-fixing non-DEFECTO_OBJETIVO. Keep experimental
until a real subparser exists.

Standalone `fars-projectx-listen` remains the discoverable capture CLI.

## Hallazgos que Codex no elevó (y no son defectos)

- RT-9 lock, `_READ_ONLY_PATHS`, no order methods, `equity=None`:
  confirmados. No hay DEFECTO.
- Tests verdes no bastan: de acuerdo (F-1/F-2 no estaban en la suite).
- Commit-list 4 vs 5 on the stable NIGHT_REPORT: DEFECTO documental
  archivado en `CODEX_REVIEW_RT.md`. No se reescribe `agent/rt-night-2`.

## Rechazados

Ningún hallazgo de Codex se rechazó. F-3 se pospone, no se niega.

## Parcialmente válidos

Ninguno. F-1 y F-2 eran completos. F-3 es completo como CLI issue.

## Pospuestos para decisión arquitectónica

- Registrar `listen` como subparser real de `fars-projectx` vs dejar solo
  `fars-projectx-listen`.
- Publicar al bus durante capture (no implementado, no recomendado ahora).
- Ampliar tokens de símbolo más allá de dotted components (por ejemplo
  `MNQU9` vs `MNQ`) si el broker no pone el root en `symbol_id`.

## Qué no se hizo

- No se tocó `agent/rt-night-2`.
- No se implementó User Hub, RT-9, equity inventada, ni bus live.
- No hubo segundo ciclo Codex. Un ciclo de corrección, como pediste.

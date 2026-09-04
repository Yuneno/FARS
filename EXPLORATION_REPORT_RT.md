# EXPLORATION_REPORT_RT

Date: 2026-09-03
Main line (intact): `agent/rt-night-2` @ `467318e`
Explore branch: `explore/rt-night`
Worktree: `/Users/ricardomedina/Documents/FARS-rt-night`
Base of night work: `a09433a`

Codex review (untracked): `CODEX_REVIEW_RT.md`
Codex verdict on main line: `PASS_WITH_OBSERVATIONS`

This branch does not rewrite `agent/rt-night` or `agent/rt-night-2`.
RT-9, User Hub, live orders, Phase 11D, and `.env` were not touched.

## Classification of the Codex finding

Codex reported one LOW observation: `NIGHT_REPORT_RT.md` lists four commits
while `a09433a..HEAD` on the main line has five (`467318e` missing).

- Class: DEFECTO OBJETIVO documental (conteo de commits), no de código.
- Not an incompatibilidad with RT contracts.
- Not a safety issue.
- Not auto-fixed on `agent/rt-night-2` (addendum: only demonstrated code
  defects auto-correct; this is handoff hygiene).
- Not a RESTRICCIÓN INJUSTIFICADA. Codex did not try to shrink Market Hub.

Codex did **not** flag the MNQ-only pin, the split CLIs, or the nested
`_Stop` on halt journaling. Those were conservative leftovers, not
safety locks.

## Oportunidades encontradas

1. Market Hub subscribe is per `contract_id`. Pinning listen to MNQ after
   argparse already has `--symbol` blocks NQ/MES without reducing order
   risk. Safety is User Hub off + `_READ_ONLY_PATHS` + `LIVE_EXECUTION_ENABLED`.
2. `fars-projectx` and `fars-projectx-listen` are two entry points. A
   `listen` subcommand is cheaper for operators and does not need doctor
   auth before capture.
3. `except _Stop: emit_system(SYSTEM_HALTED)` can raise `_Stop` again if
   the halt event itself classifies as CONFLICT. The loop should still
   exit halted with a journal marker.
4. Longer-term (not implemented): optional live bus publish during capture
   so paper/RT-6 can consume quotes without waiting for end-of-run replay.
   Keep origin=live and do not map prints to Core `Trade`.
5. Longer-term (not implemented): `access_token` in the websocket query
   is a SignalR convention. Logs already strip query via `_safe_url`.
   Do not drop the query token without a captured negotiate sample.

## Restricciones de Codex consideradas válidas

- RT-9 stays locked. Correct.
- ProjectX read-only (`execution_allowed=False`, allowlist `_post`, no
  order methods, no User Hub). Correct. Not excessive.
- Do not invent equity from balance/bars/fill PnL. Correct.
- Do not add `websocket` as a FARS dependency. Correct (Ricardo blocks
  surprise installs).
- Tests passing are not sufficient for approval. Correct method.

## Restricciones consideradas excesivas o injustificadas

Codex did not invent new safety limits this round. The excessive
restriction already in the night code (not from Codex) was:

- Hard `symbol != MNQ` reject. That is a capture-policy default, not
  fail-closed risk. Fail-closed belongs on empty/ambiguous contracts
  and on execution.

Keeping MNQ as **default** is fine. Forbidding every other unique
active contract is not required by RT-0/RT-1.

## Evidencia a favor o en contra de cada hallazgo

Codex commit-list observation:

- Evidence for: `git log a09433a..467318e` is five commits. Report text
  at that SHA still says four.
- Evidence against treating it as BLOCKER: no runtime path, no RT
  contract, no secret, no live call.

MNQ pin (Hermes, not Codex):

- Evidence it is unnecessary for safety: `subscribe_frames(contract_id)`
  and `StreamSequencer(contract_id)` are symbol-agnostic. User Hub still
  false. Tests: unique NQ listen writes `symbol=NQ` without orders.

Nested `_Stop` on halt:

- Evidence: two Quotes, same source+sequence, different `event_id` →
  SequenceTracker CONFLICT → `_Stop`. Halt system event uses a different
  source (`.../system`) so it records ORDERED. The extra `except _Stop`
  is defensive if that ever changes.

## Experimentos implementados

1. Unique-symbol Market Hub capture (default still MNQ).
2. Halt journal robustness for nested `_Stop`.
3. `fars-projectx listen` routes to `src.realtime.listen:main`.

Not implemented (stay experimental / later human call):

- Live bus attach during capture.
- Changing SignalR token transport.
- User Hub / RT-9.
- websocket as a declared dependency.
- Filling `AccountSnapshot.equity`.

## Commits experimentales (`explore/rt-night` only)

```text
b2d5f5c experiment(realtime): allow read-only Market Hub capture for any unique symbol
ff7f415 experiment(realtime): journal SYSTEM_HALTED even if halt recording conflicts
2d041e1 experiment(realtime): route fars-projectx listen to the Market Hub CLI
```

Plus this report commit after tests.

`agent/rt-night-2` remains `467318e`.

## Tests y resultados exactos

```text
/opt/anaconda3/bin/python -m pytest -p no:debugging tests/realtime -q --tb=short
246 passed in 2.24s

/opt/anaconda3/bin/python -m pytest -p no:debugging -m "not statistical" -q --tb=line
931 passed, 10 deselected in 38.67s
```

No live ProjectX calls. No `@statistical` group.

## Beneficios esperados

- One CLI can journal MNQ or NQ without a code fork.
- Operators can run `fars-projectx listen` without a second console
  script install, still read-only.
- Capture halt is observable in the JSONL even if halt-event classify
  fails.

## Riesgos o deuda técnica

- Broader `--symbol` still fail-closes on ambiguous search (MNQU9 vs
  MNQZ9). Operators must pass a token that matches exactly one active
  contract. Good.
- `fars-projectx listen` intercepts before argparse help lists it.
  `fars-projectx -h` will not show listen until a real subparser exists.
  Dual entry points can drift.
- Explore docs (`PROJECTX_CONNECTOR.md`) still say MNQ-only. Intentional:
  docs on the main line stay conservative until Ricardo integrates.
- `CODEX_REVIEW_RT.md` is untracked in this worktree.

## Qué integrar vs qué permanece experimental

Integrate (low risk, still read-only):

- `b2d5f5c` unique-symbol capture, if Ricardo wants NQ/MES journals.
- `ff7f415` nested `_Stop` guard (tiny, defensive).
- `2d041e1` only if you also add a real argparse subparser so `-h`
  lists it; otherwise keep the standalone `fars-projectx-listen`.

Keep experimental / do not integrate yet:

- Live bus during capture.
- Any User Hub / RT-9 / equity mapping.
- Treating Codex's commit-list nit as a code change.

Final integration belongs to Ricardo and the principal architect, not Codex.

## Confirmación

- No órdenes reales.
- RT-9 bloqueado.
- ProjectX/Topstep read-only.
- 11D no modificado.
- Sin `.env`, sin secretos impresos.
- Sin push, merge, rebase, reset o clean.
- `agent/rt-night-2` intacta.

# RT_INTEGRATION_DECISION

Prepared for Ricardo / principal architect. Not executed.
Stable: `agent/rt-night-2` @ `467318e`
Explore: `explore/rt-night` (do not merge from here without cherry-picks)

Do not integrate: live bus capture, RT-9, real orders, User Hub, invented
equity, or anything that weakens ProjectX read-only.

## Summary table

| Item | SHA(s) | Decision |
|---|---|---|
| Unique-symbol capture + token match | `b2d5f5c` then `78fe379` | INTEGRAR |
| SYSTEM_HALTED durability | `ff7f415` then `ead3614` | INTEGRAR |
| `fars-projectx listen` argv intercept | `2d041e1` | MANTENER_EXPERIMENTAL |
| Exploration/docs/archive reports | `59760ad` `7417e1b` `299c703` + later docs | MANTENER_EXPERIMENTAL (copy files if wanted) |

## 1. Unique-symbol Market Hub — INTEGRAR

SHAs: `b2d5f5c` then `78fe379`

Justification: `--symbol` already existed; the MNQ pin was product policy,
not a safety lock. Safety is User Hub off, REST allowlist, and
`LIVE_EXECUTION_ENABLED=False`. After `78fe379`, NQ cannot bind MNQ and ES
cannot bind MES. Empty/ambiguous still fail closed.

Benefit: one capture path for MNQ, NQ, MES, etc. when Contract/search
returns exactly one token match.

Risks: `MNQU9` as `name` without a dotted `MNQ` token still needs an exact
name/id hit. Operators should pass a token that exists as a component
(`MNQ`, `NQ`) or the exact name.

Dependencies: none beyond current ProjectX read-only client.

Tests:
- `test_select_mnq_rejects_ambiguous_and_inactive`
- `test_select_active_contract_picks_unique_symbol_and_rejects_empty`
- `test_select_active_contract_rejects_nq_substring_of_mnq`
- `test_run_listen_accepts_unique_nq`

Suggested (do not run):

```text
git cherry-pick b2d5f5c
git cherry-pick 78fe379
```

## 2. Halt marker durability — INTEGRAR

SHAs: `ff7f415` then `ead3614`

Justification: conflict halt must be visible in the JSONL. Swallowing
nested `_Stop` hid the marker. Force-record after nested `_Stop` keeps
the audit trail without opening execution.

Benefit: capture reports cannot claim halt without a `SYSTEM_HALTED` line.

Risks: force-record bypasses SequenceTracker for that one marker. The
event still uses the system stream and a new sequencer value. Acceptable
for a halt breadcrumb.

Dependencies: `b2d5f5c` not required; can land alone after `ff7f415`.

Tests:
- `test_capture_identity_conflict_halts_and_journals_system_event`
- `test_nested_stop_still_journals_system_halted`

Suggested (do not run):

```text
git cherry-pick ff7f415
git cherry-pick ead3614
```

## 3. `fars-projectx listen` intercept — MANTENER_EXPERIMENTAL

SHA: `2d041e1`

Justification: works only when `argv[0]=="listen"`. `fars-projectx -h`
omits it. `fars-projectx --env-file FILE listen` is argparse invalid
choice. Codex F-3. Standalone `fars-projectx-listen` already exists.

Do not INTEGRAR until a real subparser is added (separate experiment).
Do not RECHAZAR the idea; the intercept is just the wrong mechanism.

Benefit if later done right: one binary. Risk now: operators think listen
is a first-class subcommand and hit exit 2.

Tests today only cover the intercept happy path:
`test_cli_listen_delegates_without_doctor_auth`

No cherry-pick.

## 4. Items explicitly not for integration

- Live EventBus attach during capture
- RT-9 / LiveExecutionAdapter
- User Hub
- Mapping equity from balance, bars, or fill PnL
- Adding `websocket` to FARS dependencies
- Rewriting NIGHT_REPORT on `agent/rt-night-2` for the 4-vs-5 commit nit

## Recommended integration order (human)

1. `ff7f415` + `ead3614` (tiny, defensive)
2. `b2d5f5c` + `78fe379` (capability, now fail-closed on overlapping roots)
3. Leave `2d041e1` on explore until argparse owns `listen`

## Validation snapshot (explore, after correction cycle)

```text
tests/realtime                              248 passed in 2.15s
-m "not statistical"                        933 passed, 10 deselected in 39.43s
python -m src.realtime.cli -h               listen absent (expected until F-3)
python -m src.realtime.cli listen -h        works via intercept
python -m src.realtime.listen -h            works
python -m src.realtime.cli --env-file x listen -h   exit 2 invalid choice
```

No live trading calls. No credentials used.

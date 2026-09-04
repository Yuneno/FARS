# Codex technical review — FARS Realtime exploration

## Verdict

**CHANGES_REQUESTED**

The offline test suites pass and the ProjectX/live-execution locks remain intact, but the reviewed experiments have three contract-level defects: overlapping symbol names can select the wrong contract, the nested `_Stop` path can finish halted without journaling `SYSTEM_HALTED`, and `listen` is routed outside argparse so it is absent from `fars-projectx -h` and fails when a root option precedes it.

## Scope and SHAs

- Worktree: `/Users/ricardomedina/Documents/FARS-rt-night`
- Branch reviewed: `explore/rt-night`
- Stable line, read-only baseline: `agent/rt-night-2` at `467318ef7f7a5c62f43a32764781dfdbf7707fe8`
- Reviewed HEAD: `7417e1bf458b3de6ab4263ff5b70d0c466d337c2`
- Exact comparison: `git diff agent/rt-night-2..HEAD`
- Experiment commits inspected individually, in order:
  - `b2d5f5c04ed5a3938adf711d4175b703cbd9ab47` — `experiment(realtime): allow read-only Market Hub capture for any unique symbol`
  - `ff7f4158e01e44eccdbc60f72328e215426bfc84` — `experiment(realtime): journal SYSTEM_HALTED even if halt recording conflicts`
  - `2d041e1bbb5313150c3d852e3c20a81b15631442` — `experiment(realtime): route fars-projectx listen to the Market Hub CLI`
- Documentary commits noted but intentionally not reviewed:
  - `59760ad11a4b4ce92e93e5260215450ce80925e8`
  - `7417e1bf458b3de6ab4263ff5b70d0c466d337c2`
- Functional files changed by the three experiment commits:
  - `src/realtime/connectors/projectx_signalr.py`
  - `src/realtime/listen.py`
  - `src/realtime/cli.py`
  - `tests/realtime/test_projectx_signalr.py`
  - `tests/realtime/test_projectx_cli.py`

`git diff --check agent/rt-night-2..HEAD` exited `0` with no output.

## Findings

### F-1 — DEFECTO_OBJETIVO — A unique substring match can bind a requested symbol to a different product

`select_active_contract()` at `src/realtime/connectors/projectx_signalr.py:182-209` treats the requested token as a substring of `contract_id`, `name`, or `symbol_id`. It returns immediately when that filter yields one active row. This is not an exact symbol guarantee and is unsafe for overlapping futures symbols such as `NQ`/`MNQ` and `ES`/`MES`.

Offline probe against the current implementation:

```text
requested=NQ selected=MNQZ6 id=CON.F.US.MNQ.Z26
requested=ES selected=MESZ6 id=CON.F.US.MES.Z26
```

Thus an active MNQ row can be accepted for `--symbol NQ`, and an active MES row can be accepted for `--symbol ES`, whenever it is the only substring match returned. `run_listen()` then subscribes to and journals the wrong `contract_id` while metadata reports the requested symbol. This violates the stated “unique symbol” behavior and is not fail-closed.

The selector does correctly reject an empty token, no active matches, and multiple non-exact matches. It also uses a single exact `contract.name` as a tie-break when several substring matches exist. Those checks do not prevent the demonstrated single-row false positive. The added tests cover unique `NQ` and empty input, but not overlapping symbols or a response containing only the wrong substring product.

Required contract outcome: selection must establish that the selected contract belongs to the requested product, and reject zero, cross-product, or genuinely ambiguous results.

### F-2 — DEFECTO_OBJETIVO — Nested `_Stop` is swallowed without preserving `SYSTEM_HALTED`

The normal market-event identity-conflict path works: `_record_event()` sets `stats.halted`, raises `_Stop`, and the outer handler creates and records a new system-stream `SYSTEM_HALTED`. The new test at `tests/realtime/test_projectx_signalr.py:524-577` covers only this normal case and passes.

The behavior promised by `ff7f415`, however, is not implemented for the nested path. At `src/realtime/listen.py:368-373`, if recording the halt event itself raises `_Stop`, the handler merely executes `pass`. `_record_event()` raises before `recorder.record(event)`, so that halt event cannot already be in the journal.

A focused offline probe preloaded the tracker with system sequence `1`, triggered a quote identity conflict, and made the generated halt marker conflict at the same system sequence. Current result:

```text
halted=True conflicts=2 system_kinds=['connector_disconnected']
SYSTEM_HALTED_present=False
```

The exception no longer leaks out of `capture_until()`, but the journaled halt marker is absent. Therefore `SYSTEM_HALTED` is preserved for the ordinary conflict case, not “even if halt recording conflicts” and not under nested `_Stop`.

Required contract outcome: every conflict halt that returns from `capture_until()` as halted must leave a durable `SYSTEM_HALTED` marker, including the nested `_Stop` branch, or the function must surface a hard failure rather than imply that preservation succeeded.

### F-3 — INCOMPATIBILIDAD_REAL — `listen` bypasses argparse and is not a discoverable `fars-projectx` subcommand

`src/realtime/cli.py:119-126` checks whether raw `argv[0] == "listen"` before building or invoking the root parser. `_build_parser()` was not given a `listen` subparser. Consequences observed offline:

- `fars-projectx -h` lists only `doctor`, `contracts`, and `bars`; `listen` is not discoverable.
- `fars-projectx listen -h` happens to work through the interception, but its usage identifies the program as `fars-projectx-listen`, confirming that it is the standalone parser rather than a registered root subcommand.
- A valid root-option placement cannot reach the interception. `fars-projectx --env-file does-not-exist listen -h` exits `2` and reports `listen` as an invalid command.

Exact failure evidence:

```text
usage: fars-projectx [-h] [--env-file ENV_FILE] {doctor,contracts,bars} ...
fars-projectx: error: argument command: invalid choice: 'listen' (choose from doctor, contracts, bars)
```

The added unit test verifies only `cli.main(["listen", ...])` delegation and deliberately exercises the sole ordering that the interception recognizes. It does not test root help or normal argparse option ordering.

Required contract outcome: `listen` must be registered and visible as a real `fars-projectx` subcommand, with its help and argument validation reached through argparse rather than an `argv[0]` special case. Retaining the separate `fars-projectx-listen` entry point is compatible with that outcome.

## Per-commit review

### `b2d5f5c` — arbitrary read-only Market Hub symbol

- Intended behavior: widens `run_listen()` from an MNQ-only product guard to a non-empty `--symbol`; contract search remains `live=False`; metadata records the normalized requested symbol; `select_mnq_contract()` remains as a compatibility wrapper.
- Logical result: empty/no-match/inactive/many-match cases generally fail closed, and no execution or User Hub capability is introduced.
- Regression versus `agent/rt-night-2`: removing the MNQ pin exposes the existing substring strategy to cross-product symbol collisions. The demonstrated wrong-contract selection is F-1 and prevents approval.
- Affected contracts: `select_active_contract()`, the semantics of `--symbol`, selected `contract_id`, subscription target, and journal/meta identity.

### `ff7f415` — halt journaling under conflict

- Intended behavior: prevent a second `_Stop` raised while emitting `SYSTEM_HALTED` from escaping `capture_until()`.
- Logical result: the ordinary quote-conflict path records `SYSTEM_HALTED`, and the added test verifies that path. A nested `_Stop` is now contained.
- Regression/remaining contract failure: the nested branch silently returns with `stats.halted=True` after the halt event was rejected before recording. This is F-2. The change improves exception containment but does not meet its journaling claim.
- Affected contracts: conflict stopping, journal auditability, `stats.conflicts`, `stats.halted`, and `capture_until()` exception behavior.

### `2d041e1` — `fars-projectx listen` routing

- Intended behavior: delegate direct `fars-projectx listen ...` invocations to `src.realtime.listen.main()` without running the doctor authentication path; the standalone console script remains registered.
- Logical result: direct first-token delegation works, as its unit test shows, and the delegated listener retains its own error boundary and read-only checks.
- Regression/compatibility result: because dispatch occurs before `parse_args()` and the root parser knows nothing about `listen`, discovery and root-option ordering are broken as described in F-3.
- Affected contracts: `src.realtime.cli.main(argv)`, root CLI help, global-option placement, displayed program name, and argparse validation/error behavior.

## Required tests and exact results

No live network calls or credentials were used.

### Full realtime suite

Command:

```text
/opt/anaconda3/bin/python -m pytest -p no:debugging tests/realtime -q --tb=short
```

Result: exit `0`.

```text
platform darwin -- Python 3.13.5, pytest-8.3.4, pluggy-1.5.0
rootdir: /Users/ricardomedina/Documents/FARS-rt-night
configfile: pyproject.toml
plugins: anyio-4.7.0
collected 246 items
============================= 246 passed in 3.53s ==============================
```

### Focused ProjectX SignalR and CLI suite

Command:

```text
/opt/anaconda3/bin/python -m pytest -p no:debugging tests/realtime/test_projectx_signalr.py tests/realtime/test_projectx_cli.py -q --tb=short
```

Result: exit `0`.

```text
platform darwin -- Python 3.13.5, pytest-8.3.4, pluggy-1.5.0
rootdir: /Users/ricardomedina/Documents/FARS-rt-night
configfile: pyproject.toml
plugins: anyio-4.7.0
collected 24 items
============================== 24 passed in 2.39s ==============================
```

Passing these suites is not sufficient for approval because neither focused failure demonstrated in F-1/F-2 nor the CLI discovery failure in F-3 is asserted by the committed tests.

## Help-text evidence

### `/opt/anaconda3/bin/python -m src.realtime.cli -h`

Exit `0`; exact command list:

```text
usage: fars-projectx [-h] [--env-file ENV_FILE] {doctor,contracts,bars} ...

positional arguments:
  {doctor,contracts,bars}
    doctor              authenticate and verify the active account without
                        trading
    contracts           search available contracts
    bars                retrieve historical OHLCV bars
```

`listen` is absent.

### `/opt/anaconda3/bin/python -m src.realtime.cli listen -h`

Exit `0`; exact usage header:

```text
usage: fars-projectx-listen [-h] [--env-file ENV_FILE] [--hours HOURS]
                            [--seconds SECONDS] --journal JOURNAL --meta META
                            --report REPORT [--symbol SYMBOL]
                            [--hub-url HUB_URL] [--timeout TIMEOUT]

Read-only ProjectX Market Hub capture for FARS.
```

### `/opt/anaconda3/bin/python -m src.realtime.listen -h`

Exit `0`; exact usage header:

```text
usage: fars-projectx-listen [-h] [--env-file ENV_FILE] [--hours HOURS]
                            [--seconds SECONDS] --journal JOURNAL --meta META
                            --report REPORT [--symbol SYMBOL]
                            [--hub-url HUB_URL] [--timeout TIMEOUT]

Read-only ProjectX Market Hub capture for FARS.
```

The latter two outputs are identical because the root CLI delegates directly to the standalone listener parser.

## Fail-closed and affected safety contracts

- Execution locks: preserved. `LIVE_EXECUTION_ENABLED is False`; both `ProjectXClient` construction and Market Hub negotiation guard that value; `run_listen()` independently guards it.
- Client capability: preserved. `ProjectXClient.execution_allowed is False`, and `run_listen()` refuses a client whose `execution_allowed` is anything other than `False`.
- REST allowlist: unchanged from the stable line. `_READ_ONLY_PATHS` contains only:
  - `/api/Auth/loginKey`
  - `/api/Auth/validate`
  - `/api/Account/search`
  - `/api/Contract/search`
  - `/api/History/retrieveBars`
  - `/api/Position/searchOpen`
  - `/api/Trade/search`
- Order surface: preserved. No `place_order`, `submit_order`, `cancel_order`, `modify_order`, `close_position`, `close_positions`, `flatten`, `buy`, or `sell` method exists on `ProjectXClient`; no order path was added.
- Hub scope: preserved. Capture negotiates the Market Hub and subscribes only to contract quotes and trades. User Hub remains absent and metadata keeps `user_hub: false`.
- Account-state semantics: preserved. Metadata/report keep `equity: null`; no equity is invented.
- Event conflict behavior: a normal identity conflict halts and returns provider failure through `run_listen()`. The nested-marker durability exception is F-2.
- Symbol behavior: empty, inactive, missing, and most multi-match searches reject. Cross-product substring false positives are not fail-closed and are F-1.

`git diff agent/rt-night-2..HEAD -- src/realtime/interfaces.py src/realtime/connectors/projectx.py` is empty. Runtime inspection produced:

```text
LIVE_EXECUTION_ENABLED = False
ProjectXClient.execution_allowed = False
forbidden_order_methods_present = []
_READ_ONLY_PATHS = ['/api/Account/search', '/api/Auth/loginKey', '/api/Auth/validate', '/api/Contract/search', '/api/History/retrieveBars', '/api/Position/searchOpen', '/api/Trade/search']
```

## RT-9 lock

RT-9 remains locked. `LIVE_EXECUTION_ENABLED` is exactly `False`; the experiment adds no execution adapter, no order method, no order endpoint, no User Hub, and no live-order path. Listener metadata explicitly records `rt9: false`, `live_execution_enabled: false`, `mode: "READ_ONLY"`, `user_hub: false`, and `equity: null`.

The requested changes concern correctness and auditability within read-only Market Hub capture and CLI discovery only. They do not require or justify any RT-9 or live-execution capability.

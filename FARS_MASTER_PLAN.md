# FARS Master Plan — Rebaseline 2026-09-02

**Status:** decision record pending human-approved merge  
**Purpose:** replace parallel and overlapping roadmaps with one ordered source
of truth while preserving completed statistical work.

## 1. Decisive project decision

FARS remains one repository and one modular Python system. There will be no
second bot roadmap, no Phase 15, and no FARS 2 rewrite while this plan is
active.

The project has two layers, developed in strict order:

1. **FARS Core 1.2:** the existing statistical and funded-account research
   engine.
2. **FARS Realtime:** the only active feature roadmap after Core review closes.

Legacy Phases 11D–14 stop being implementation milestones. Their valid
requirements are preserved and moved into the RT roadmap below. The old specs
remain historical references; they are not deleted and must not be used as a
second active backlog.

Only one implementation milestone may be active at a time.

## 2. Official status after rebaseline

| Work | Official status | Disposition |
|---|---|---|
| Core Phases 1–10B | Frozen baseline | Preserve compatibility; fixes only |
| Phase 11A | Implemented, review pending | Core Review Gate C1 |
| Phase 11B | Implemented, review pending | Core Review Gate C2 |
| Phase 11C | Implemented, review pending | Core Review Gate C3 |
| Phase 11D Tradovate validation | Superseded as milestone | Requirements move to RT-8 |
| Phase 12 temporal/OOS | Deferred, not cancelled | Moves to RT-8A |
| Phase 13 stress testing | Deferred, not cancelled | Moves to RT-8B |
| Phase 14 read-only reporting | Absorbed | Moves to RT-5 |
| ProjectX code authored by Codex | Candidate, not accepted | Becomes RT-0/RT-1 handoff |
| Automated order execution | Blocked | No implementation before RT-8 passes |

“Deferred” means intentionally scheduled later. It does not mean abandoned.

## 3. One authoritative execution roadmap

### Core Review Gate — must close first

- **C1:** independent review of Phase 11A monetary/account data.
- **C2:** independent review of Phase 11B funded-account rule engine.
- **C3:** independent review of Phase 11C probabilistic paths.
- **C4:** full deterministic tests, statistical acceptance where applicable,
  status/doc reconciliation, and `REVIEW PASSED`.

No new statistical features enter Core during this gate.

### RT-0 — Contracts and canonical events

- Canonical account, contract, OHLCV, quote, trade, order, position, signal,
  risk-decision, order-intent, and execution-result events.
- Decimal monetary semantics, timezone-aware timestamps, schema versions, and
  provider metadata.
- No networking and no orders.

### RT-1 — ProjectX read-only connector

- API-key authentication and in-memory session rotation.
- Exact active-account selection.
- Contract search, historical bars, raw trades, and open positions.
- Credential redaction, rate-limit handling, and fail-closed parsing.
- No order-placement, cancellation, close-position, or order-modification API.

The current local ProjectX implementation is a tested candidate for RT-0/RT-1,
not an approved merge. It becomes a handoff to Hermes after C1–C4 close.

### RT-2 — Realtime bus and normalization

- ProjectX SignalR `market` and `user` hubs.
- `asyncio.Queue` event bus.
- Reconnect and resubscribe behavior.
- Event ordering, duplicate detection, stale-data detection, and heartbeats.
- `TRADING_ALLOWED = FALSE` on disconnect, stale data, or unknown state.

### RT-3 — Recorder and storage

- Append-only raw provider events.
- Canonical normalized events.
- Parquet storage and DuckDB queries.
- Credential-free provenance, hashes, and deterministic ordering.

### RT-4 — Deterministic replay

- Replay recorded sessions through the same event pipeline used by realtime.
- Controllable clock, pause/step/speed, and deterministic seeds.
- Live/replay parity tests and no look-ahead access.

### RT-5 — Strategy and FARS adapter

- Freeze a versioned strategy contract before implementation.
- Translate the mentor/friend strategy document into deterministic signals.
- Keep strategy output limited to `Signal`; it cannot place orders.
- Feed account state and completed outcomes into FARS risk/statistical views.
- Absorb the valid read-only reporting requirements from legacy Phase 14.
- Add a reviewed fill/round-trip aggregator before ProjectX fills enter Phase
  11A statistics; null-P&L half turns must never be relabeled as completed
  trades.

The mentor strategy document is an RT-5 input. It is not a parallel project and
must not be implemented before replay exists.

### RT-6 — Risk gate and provider profiles

- Boundary remains `Strategy -> Signal -> Risk -> OrderIntent -> Execution`.
- Risk has absolute veto authority; `UNKNOWN -> DENY`.
- Sizing, drawdown, daily/session rules, duplicate intents, stale account state,
  connection health, circuit breakers, and kill switch.
- Maintain separate versioned profiles for:
  - MFFU Rapid 25K / Tradovate;
  - the Topstep Labs `$1.5K Challenge`;
  - any later eligible Topstep account type.
- Never apply the Rapid 25K profile to the Topstep Labs Challenge.
- A profile remains disabled until exact provider rules and unresolved
  boundaries are documented from authoritative sources.

### RT-7 — Paper execution and reconciliation

- Paper-only order lifecycle.
- Idempotent client intent IDs.
- Partial fills, rejects, cancellations, brackets, restarts, and reconciliation.
- Persistent state and manual kill switch.
- No Challenge or funded-account orders.

### RT-8 — Validation and acceptance

- **RT-8A:** legacy Phase 12 temporal/OOS and walk-forward validation.
- **RT-8B:** legacy Phase 13 stress testing.
- **RT-8C:** legacy Phase 11D prospective provider/account validation.
- Replay-to-paper parity, disconnect/restart drills, stale-data drills, and rule
  boundary tests.
- Predeclared acceptance thresholds and an untouched final validation set.
- RT-8 must end in an explicit `REVIEW PASSED` before execution can unlock.

### RT-9 — Controlled API-eligible execution

- Disabled by default and enabled only through explicit configuration plus a
  kill switch.
- Initially limited to an API-eligible simulated Challenge/evaluation account.
- Minimum size, bounded daily risk, idempotency, reconciliation, audit log, and
  forced fail-closed behavior.
- ProjectX API execution on a Topstep Live Funded Account is outside this plan
  while Topstep prohibits that route. A rule change requires a new reviewed
  provider-profile version and a new decision record.

## 4. Development ownership

The repository's `AGENTS.md` workflow remains binding:

1. The human approves one narrow task.
2. Hermes implements that task and its tests.
3. Codex independently reviews requirements, code, math/statistics, edge cases,
   and coverage.
4. Hermes applies valid corrections.
5. Codex re-reviews and returns `REVIEW PASSED` or findings.
6. The human explicitly approves commit/merge/push.

Grok or Gemini may perform adversarial review, but they must not edit the same
code concurrently with Hermes. Codex's current ProjectX code is treated as a
candidate/reference because Codex authored it; Hermes must take ownership of
the implementation before the normal review cycle resumes.

## 5. Branch and backlog policy

- One active branch and one milestone at a time.
- Every task names exactly one milestone (`C1`, `RT-2`, and so on).
- No mixed “finish old phase + add realtime + add bot” branches.
- A milestone cannot begin until its predecessor is accepted.
- Specs, tests, implementation, and status documentation change together.
- Never commit `.env`, API keys, tokens, cookies, account IDs in reports, or
  other credentials.

## 6. Immediate next move

All implementation pauses except review fixes.

The next task is **C1: independent Phase 11A review**. After C1, proceed to C2,
C3, and C4. Only then does Hermes receive the RT-0/RT-1 ProjectX candidate.

This ordering closes the statistical foundation before realtime data begins to
depend on it, without discarding the ProjectX work already completed.

## 7. Definition of current success

The immediate goal is not autonomous trading. The current goal is a reviewed,
reproducible pipeline that can:

1. connect without leaking credentials;
2. record and replay the correct account and market data;
3. reproduce a frozen strategy signal;
4. let FARS veto unsafe risk;
5. reconcile paper execution deterministically;
6. pass temporal, stress, prospective, and operational validation;
7. unlock API-eligible simulated execution only after those facts are proven.

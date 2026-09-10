# FARS_REALTIME_SPEC.md

**Status:** Implementation specification  
**Target:** FARS Realtime extension  
**Base:** Existing FARS Core + FARS 1.2  
**Architecture:** Modular monolith, event-driven  
**Initial implementation scope:** RT-0 only

---

# 1. Purpose

FARS Realtime extends the existing FARS system so it can consume market/account data in real time, record what it observed, replay the same session deterministically, evaluate decisions through an independent risk layer, and eventually support paper and live execution.

This is **not FARS 2.0**.

The existing FARS Core MUST NOT be rewritten merely to accommodate realtime data.

The intended long-term pipeline is:

```text
Market / Broker
      ↓
Connector
      ↓
Canonical Events
      ↓
Event Bus
      ├── Recorder → Storage → Replay
      ↓
Realtime Feature Engine
      ↓
Strategy / Model
      ↓
Signal
      ↓
Risk Engine
      ↓
RiskDecision
      ↓
OrderIntent
      ↓
ExecutionAdapter
      ├── PaperExecution
      └── LiveExecution [LOCKED initially]
```

FARS Realtime is infrastructure around the existing statistical/risk system, not a replacement for it.

---

# 2. Core Architectural Rules

The following are mandatory.

1. Realtime processing MUST be event-driven.
2. External provider formats MUST NOT leak beyond their connector.
3. Connectors MUST translate provider data into canonical FARS events.
4. Market events MUST NOT reuse the existing Core `Trade` simply for convenience.
5. Existing Core `Trade.r_result` continues to represent completed analytical trades.
6. Strategy and Risk MUST remain separate.
7. Strategy MUST NOT communicate directly with Execution.
8. Risk MUST have absolute veto authority.
9. Unknown critical state MUST fail closed.
10. Realtime and Replay MUST use the same canonical event contracts.
11. Externally sourced events MUST be traceable.
12. Duplicate events MUST NOT duplicate state.
13. Critical ordering/state ambiguity MUST be detectable.
14. Live execution MUST remain disabled until RT-8 validation passes.
15. Do not introduce Kafka, Kubernetes, microservices, Redis, NATS, or similar infrastructure without demonstrated need.
16. Do not hard-code a specific strategy, prop firm, broker, or market provider into the Core.
17. Preserve compatibility with FARS 1.2 where reasonably possible.

Safety principle:

```text
UNKNOWN CRITICAL STATE → DENY / HALT
```

Never:

```text
UNKNOWN CRITICAL STATE → ALLOW
```

---

# 3. Separation of Worlds

FARS operates across three conceptually distinct layers.

## 3.1 Market World

Examples:

```text
ticks
quotes
bars
market trades
account snapshots
broker events
```

## 3.2 Decision World

Examples:

```text
features
signals
risk decisions
order intents
execution reports
```

## 3.3 Analytical World

Existing FARS Core concepts:

```text
completed trades
R results
metrics
bootstrap
Monte Carlo
probabilistic account analysis
OOS / temporal validation
stress testing
```

Do not collapse these layers into one data model.

---

# 4. Canonical Events

RT-0 MUST define the canonical event model.

At minimum:

```text
MarketTick
Quote
Bar
MarketTrade
AccountSnapshot
Signal
RiskDecision
OrderIntent
ExecutionReport
SystemEvent
```

Exact implementation names MAY differ if repository conventions justify it.

Every external event SHOULD carry, where applicable:

```text
event_id
source
timestamp
sequence
symbol
```

Additional event-specific fields MUST be explicit and validated.

---

# 5. Event Semantics

## 5.1 MarketTick

Represents an observed market update.

Typical fields:

```text
event_id
source
timestamp
sequence
symbol
price
volume
```

## 5.2 Quote

Represents bid/ask state.

Typical fields:

```text
event_id
source
timestamp
sequence
symbol
bid_price
ask_price
bid_size
ask_size
```

## 5.3 Bar

Represents an OHLCV interval.

Typical fields:

```text
event_id
source
timestamp
sequence
symbol
interval
open
high
low
close
volume
```

## 5.4 MarketTrade

Represents an exchange/provider-reported market transaction.

It is NOT the existing analytical FARS `Trade`.

## 5.5 AccountSnapshot

Represents externally observed account state.

Potential fields:

```text
balance
equity
realized_pnl
unrealized_pnl
positions
working_orders
peak_equity
broker_timestamp
last_sync
```

RT-0 does not need full live-account functionality. The contract must merely avoid making future implementation impossible.

## 5.6 Signal

Represents a Strategy proposal.

A signal is NOT an order.

Conceptually:

```text
Signal
symbol = MNQ
action = LONG
```

does not authorize execution.

## 5.7 RiskDecision

Represents an explicit Risk Engine result.

Conceptually:

```text
approved = false
reason = DRAWDOWN_BUFFER_TOO_LOW
```

The absence of a valid approval MUST NOT be interpreted as approval.

## 5.8 OrderIntent

Represents an order proposal that has passed the required risk boundary.

`OrderIntent != ExecutionReport`.

## 5.9 ExecutionReport

Represents the response/result from an execution adapter or broker.

Duplicate reports MUST be safely identifiable.

## 5.10 SystemEvent

Represents operational events such as:

```text
connector disconnected
connector reconnected
stale market data
sequence gap
reconciliation mismatch
circuit breaker triggered
system halted
```

---

# 6. Required Interfaces

RT-0 MUST define small interfaces or protocols for:

```text
MarketDataConnector
EventBus
EventRecorder
Strategy
RiskEngine
ExecutionAdapter
Clock
```

Interfaces SHOULD be minimal.

Do not implement speculative methods merely because they may be useful later.

---

# 7. Mandatory Dependency Direction

The system MUST preserve:

```text
Connector
   ↓
Canonical Events
   ↓
EventBus
   ↓
Strategy
   ↓
Signal
   ↓
RiskEngine
   ↓
RiskDecision
   ↓
OrderIntent
   ↓
ExecutionAdapter
```

Forbidden:

```text
Strategy → Broker
Strategy → LiveExecution directly
Connector provider objects → FARS Core
Risk bypass → Execution
```

---

# 8. Connector Boundary

Provider-specific logic belongs inside adapters/connectors.

Correct:

```text
Tradovate payload
      ↓
TradovateConnector
      ↓
Canonical MarketTick
```

Incorrect:

```text
FARS reads tradovate_response["price"]
```

The architecture MUST allow future providers such as:

```text
Tradovate
TradeSea
Interactive Brokers
historical/replay source
other providers
```

without rewriting downstream FARS logic.

RT-0 MUST NOT implement a complete Tradovate connector.

---

# 9. Event Bus

Initial implementation SHOULD remain lightweight.

Preferred initial direction:

```text
asyncio.Queue
```

behind an interface.

Conceptual API:

```python
await bus.publish(event)
subscribe(...)
```

The precise API MUST follow the repository's actual needs.

Do not introduce distributed infrastructure in RT-0.

---

# 10. Replay Invariant

Realtime and Replay MUST be consumers/producers of the same canonical contracts.

Invariant:

```text
LiveConnector ──┐
                ├──→ EventBus → downstream pipeline
ReplayEngine ───┘
```

Downstream Strategy/Risk logic SHOULD NOT need to know whether an event originated from realtime or replay unless provenance is explicitly inspected.

This requirement is foundational for later deterministic validation.

---

# 11. Idempotency

External systems may send duplicate events.

At minimum the event model MUST make duplicate detection possible using stable identity/provenance such as:

```text
event_id
source
sequence
timestamp
```

Processing the same external state-changing event twice MUST NOT eventually produce duplicated:

```text
positions
fills
PnL
orders
account state
```

RT-0 MUST define/test the contract needed to support this.

Do not overbuild the complete state machine during RT-0.

---

# 12. Event Ordering

External events may arrive:

```text
101
103
102
```

The architecture MUST make it possible to classify at least:

```text
ordered event
duplicate event
late event
out-of-order event
sequence gap
```

RT-0 MUST define explicit behavior at the contract level and test it where practical.

For critical state ambiguity, future runtime policy is:

```text
TRADING_ALLOWED = FALSE
```

Do not silently reorder events in a way that hides provider anomalies unless the behavior is explicitly specified and tested.

---

# 13. Immutability and Time

Canonical events SHOULD be immutable where practical.

Every event MUST use an explicit timestamp policy.

Prefer timezone-aware timestamps.

Provider timestamps and local receipt timestamps MUST NOT be silently treated as identical concepts if both become necessary.

A `Clock` abstraction is required so replay/tests do not depend on wall-clock time.

---

# 14. Risk Boundary

Risk is an independent safety boundary.

Long-term Risk Engine inputs may include:

```text
account state
drawdown
available risk
position size
open positions
pending orders
market-data freshness
connection health
account synchronization
prop-firm constraints
system health
```

Rule:

```text
Strategy proposes.
Risk authorizes or rejects.
Execution executes only authorized intent.
```

RT-0 defines the contract only.

Do not implement the complete Risk Engine v2 during RT-0.

---

# 15. Account State Separation

Do not mutate the existing simulation account model into a live broker model.

Existing analytical/simulation state SHOULD remain intact.

Future concepts may be separated as:

```text
SimulationAccountState
LiveAccountState
```

They may share interfaces where justified, but they represent different problems.

---

# 16. Prop-Firm Rules

Do NOT scatter provider-specific conditionals through Strategy/Core.

Forbidden pattern:

```python
if provider == "MFFU":
    ...
```

Preferred future model:

```text
AccountProfile
├── ProfitTargetRule
├── DrawdownRule
├── DailyLossRule
├── PositionLimitRule
├── ConsistencyRule
└── TradingRestrictionRule
```

RT-0 does not implement all rules.

It only MUST avoid contracts that make this impossible later.

---

# 17. Recorder and Storage Direction

Later phases MUST be able to persist enough information to reconstruct:

```text
what FARS observed
what Strategy produced
what Risk decided
what order was intended
what Execution returned
what account state was observed
```

Expected later storage direction:

```text
Parquet + DuckDB
```

RT-0 MUST only define the recorder boundary/interface needed by future phases.

Do not implement the full storage layer unless strictly necessary for RT-0 tests.

---

# 18. Observability

Runtime code MUST NOT rely solely on `print()`.

Future phases require structured logging and metrics.

Examples:

```text
events_received
events_dropped
events_duplicate
events_out_of_order
market_data_latency
broker_latency
reconnect_count
signals_generated
risk_denials
orders_submitted
orders_rejected
```

RT-0 does not need the complete observability stack.

---

# 19. Operational Safety

Before any live execution can be enabled, the system MUST eventually contain:

```text
idempotency
reconnect/resync
stale-data detection
event ordering checks
circuit breakers
structured logging
latency/error metrics
account reconciliation
kill switch
```

Critical anomaly policy:

```text
SYSTEM_STATE = HALTED
TRADING_ALLOWED = FALSE
```

---

# 20. Paper vs Live Execution

Paper trading and live trading SHOULD share the same execution abstraction.

Target:

```text
ExecutionAdapter
├── PaperExecutionAdapter
└── LiveExecutionAdapter
```

Everything before Execution SHOULD remain substantially identical.

RT-0 MUST define only the interface.

Live execution remains explicitly locked.

---

# 21. Scalability

Initial architecture:

```text
MODULAR MONOLITH
```

A single Python project/process may contain:

```text
Connector
EventBus
Recorder
RealtimeEngine
Strategy
Risk
PaperExecution
```

Scalability should come first from clear contracts, not distributed infrastructure.

Multi-account support is a future extension.

Do not implement it now, but avoid designs that unnecessarily make it impossible.

---

# 22. Development Roadmap

Implementation order is:

```text
RT-0  Contracts & Event Model
RT-1  Market Data Connector
RT-2  Event Bus + Normalization
RT-3  Recorder + Storage
RT-4  Replay Engine
RT-5  Realtime FARS Adapter
RT-6  Risk Engine v2
RT-7  Paper Trading
RT-8  Validation / Acceptance
RT-9  Live Execution
```

Hard dependency:

```text
RT-9 REQUIRES RT-8 PASS
```

Do not skip phases merely because a real funded account or provider access exists.

---

# 23. RT-0 — Contracts & Event Model

## 23.1 Objective

Create executable architectural contracts for FARS Realtime without prematurely implementing provider-specific realtime trading.

RT-0 is primarily:

```text
contracts
interfaces
invariants
tests
```

## 23.2 Required Work

Before implementation:

1. Read `AGENTS.md`.
2. Read `FARS_SPEC.md`.
3. Read `FARS_1_2_SPEC.md`.
4. Inspect the current repository structure and tests.
5. Identify reusable existing types.
6. Do not assume this spec's suggested file paths match the repository exactly.

Then implement the smallest clean RT-0 architecture satisfying this spec.

## 23.3 Expected Components

Suggested location only:

```text
src/
└── realtime/
    ├── events.py
    ├── interfaces.py
    ├── ordering.py        # only if justified
    └── clock.py
```

Tests:

```text
tests/
└── realtime/
```

Follow existing repository package/layout conventions instead if different.

## 23.4 Required Event Contracts

Implement contracts for:

```text
MarketTick
Quote
Bar
MarketTrade
AccountSnapshot
Signal
RiskDecision
OrderIntent
ExecutionReport
SystemEvent
```

Avoid fields that are purely speculative.

If an exact field cannot yet be justified, prefer an extensible but explicit contract over inventing provider-specific assumptions.

## 23.5 Required Interface Contracts

Implement minimal interfaces/protocols for:

```text
MarketDataConnector
EventBus
EventRecorder
Strategy
RiskEngine
ExecutionAdapter
Clock
```

No interface should expose provider-specific payloads downstream.

## 23.6 Required Invariants

Tests MUST demonstrate, where applicable:

1. Every event has a valid timestamp.
2. External events can carry traceable provenance.
3. Required fields reject invalid values.
4. Canonical events serialize and deserialize consistently if serialization is implemented.
5. Events intended to be immutable cannot be mutated.
6. Duplicate identity can be detected.
7. Ordering classification is deterministic.
8. Replay-origin and live-origin events can satisfy the same contract.
9. Strategy interface returns `Signal`, not broker orders.
10. Risk interface produces explicit `RiskDecision`.
11. Execution accepts only the appropriate downstream contract.
12. Critical approval cannot be represented ambiguously as implicit `True`.

## 23.7 Edge Cases

Tests SHOULD cover relevant cases such as:

```text
missing event_id
empty source
naive timestamp
invalid symbol
negative/invalid price where semantically impossible
negative volume
duplicate event_id
same sequence with conflicting identity
sequence gap
late/out-of-order event
invalid enum/action/status
malformed serialization
```

Do not impose arbitrary market restrictions without justification.

For example, do not reject an instrument solely because its symbol is unfamiliar.

## 23.8 Acceptance

RT-0 passes only when:

```text
relevant new tests pass
existing deterministic FARS tests still pass
contracts do not depend on Tradovate-specific types
existing Core behavior is not unnecessarily altered
duplicate/order semantics are explicit
interfaces enforce Strategy → Risk → Execution separation
implementation remains small and reviewable
```

Passing tests alone is not sufficient if the architecture violates the mandatory dependency boundaries.

---

# 24. Explicit RT-0 Non-Goals

RT-0 MUST NOT:

```text
implement complete Tradovate connectivity
implement TradeSea connectivity
place real orders
authenticate to a live broker
implement full paper trading
implement Predictive FARS
implement full Risk Engine v2
rewrite FARS Core
convert ticks into Core Trade.r_result
add dashboard/UI
add Kafka
add Kubernetes
add microservices
add Redis/NATS without necessity
hard-code a funded account provider
hard-code SMC-FVG or SMC-OB logic
commit, merge, or push automatically
```

---

# 25. RT-1 — Market Data Connector

**Implementation status:** implemented on FARS-Hermes (authorized after RT-0). Replay/historical adapter only.

Goal:

Create the first provider adapter that transforms external realtime data into canonical FARS market events.

Acceptance will later require:

```text
connection lifecycle
normalization
reconnect behavior
source provenance
stale-data detection
invalid payload handling
no provider objects leaking downstream
```

The selected first provider MUST be confirmed from actual available API/data access before implementation.

---

# 26. RT-2 — Event Bus + Normalization

**Implementation status:** implemented on FARS-Hermes (authorized after RT-0).

Goal:

Route canonical events reliably through the modular monolith.

Expected direction:

```text
AsyncIOEventBus
```

Required concerns:

```text
bounded queues
backpressure policy
duplicate handling
ordering diagnostics
shutdown semantics
error propagation
```

Do not assume unbounded queues are acceptable.

---

# 27. RT-3 — Recorder + Storage

**Implementation status:** implemented on FARS-Hermes (authorized after RT-0). JSONL journal; Parquet/DuckDB deferred.

Goal:

Persist raw/normalized events and decision-chain events.

Initial preferred storage:

```text
Parquet + DuckDB
```

Must preserve provenance and allow later reconstruction.

---

# 28. RT-4 — Replay Engine

**Implementation status:** implemented on FARS-Hermes (authorized after RT-0).

Goal:

Feed recorded events through the same canonical pipeline used by realtime.

Must support reproducible timing/order behavior.

Replay MUST NOT require Strategy/Risk code changes.

---

# 29. RT-5 — Realtime FARS Adapter

**Implementation status:** implemented on FARS-Hermes (authorized after RT-4).

Goal:

Create the explicit boundary between realtime state/events and the validated analytical components of FARS.

Do not force incomplete realtime data into Core types that require completed trades.

Only invoke Core analyses when the required analytical data exists.

---

# 30. RT-6 — Risk Engine v2

**Future phase.**

Goal:

Create realtime/account-aware risk authorization.

Risk MUST be independently testable and strategy-agnostic.

Unknown critical state MUST deny authorization.

---

# 31. RT-7 — Paper Trading

**Future phase.**

Goal:

Exercise the complete decision pipeline without real capital.

Paper execution SHOULD share the same interface used later by live execution.

Paper trading must model clearly stated assumptions for:

```text
fills
latency
slippage
commissions
partial fills
rejections
```

It MUST NOT present idealized fills as equivalent to live execution.

---

# 32. RT-8 — Validation / Acceptance

**Implementation status:** implemented on FARS-Hermes; deterministic acceptance
PASS (2026-08-30), independent review pending. Live execution remains disabled.

Goal:

Validate the complete realtime/replay/risk/paper pipeline before live execution.

At minimum evaluate:

```text
event correctness
duplicate safety
ordering behavior
replay reproducibility
risk enforcement
account-state consistency
circuit breakers
reconnect/resync
stale-data behavior
paper-trading stability
latency/error behavior
```

RT-8 must end in an explicit acceptance result.

---

# 33. RT-9 — Live Execution

**LOCKED.**

RT-8 deterministic acceptance passes, but RT-9 remains unimplemented because
no confirmed broker contract or explicit live configuration is present.

RT-9 may begin only after:

```text
RT-8 = PASS
```

Live execution requires:

```text
idempotent order handling
broker reconciliation
kill switch
circuit breakers
connection recovery
stale-state blocking
position/order mismatch detection
structured audit trail
explicit configuration enabling live mode
```

Default mode MUST NOT be live trading.

---

# 34. Statistical and FARS Integrity

Realtime development MUST NOT weaken existing statistical guarantees.

Do not:

- treat a backtest as proof of future profitability;
- transform unknown execution costs into zero without explicit labeling;
- introduce look-ahead information into replay/strategy evaluation;
- tune systems on final OOS data;
- convert live observations into completed analytical trades before they truly are complete;
- use FARS probabilistic outputs as deterministic guarantees.

Realtime FARS is a risk-analysis and validation system with a fail-closed
execution-authorization boundary. It does not place live orders; live execution
is locked (`LIVE_EXECUTION_ENABLED = False`).

Declared end goal (NOT yet implemented): FARS will eventually feed an autonomous
execution bot — supplying risk metrics, bootstrap results, funded-account rules,
and risk-engine decisions so the bot can place trades without a human in the loop
at execution time. This goal is explicitly not implemented, not tested, and not
safe to deploy today. See `AUTONOMY_ROADMAP.md`.

---

# 35. Testing Strategy

Use the existing project's testing conventions.

During development:

```text
targeted RT-0 tests
        ↓
relevant integration tests
        ↓
existing deterministic suite
```

Do not repeatedly run expensive statistical acceptance suites after trivial contract edits unless relevant.

Before RT-0 completion, run the broad deterministic suite necessary to demonstrate Core compatibility.

Any pre-existing test failures MUST be distinguished from regressions introduced by RT-0.

---

# 36. Git and Agent Workflow

Normal workflow:

```text
implementation
→ targeted tests
→ fixes
→ broader tests
→ self-review diff
→ independent Codex review
→ valid corrections
→ tests
→ REVIEW PASSED
→ commit / merge / push only when explicitly requested
```

The implementation agent MUST NOT:

```text
commit
merge
push
rebase
rewrite specifications
```

unless explicitly instructed.

---

# 37. Implementation-Agent Instruction

For the first implementation session:

```text
Implement RT-0 only.

Read AGENTS.md, FARS_SPEC.md, FARS_1_2_SPEC.md and this specification.
Inspect the current repository before choosing file paths or abstractions.

Do not implement RT-1 or later phases.
Do not connect to a broker.
Do not create live trading.
Do not rewrite the Core.

Create the smallest tested set of canonical event contracts and interfaces that
enforces the architectural invariants in this specification.

Run targeted tests during development and the appropriate broader deterministic
suite before completion.

At completion report:
1. files changed
2. contracts/interfaces implemented
3. tests run and results
4. compatibility impact
5. unresolved decisions for RT-1
```

---

# 38. Definition of Success

FARS Realtime is not successful merely because:

```text
"it connects to Tradovate"
```

The target is:

> FARS can observe a market through replaceable connectors, translate external data into canonical events, record and replay exactly what it observed, run interchangeable strategy/model components, enforce an independent risk boundary, and eventually execute through replaceable adapters without coupling the FARS Core to one broker or provider.

For RT-0 specifically, success means the contracts and interfaces needed to make that architecture real exist, are tested, are small enough to review, and do not break the existing FARS Core.

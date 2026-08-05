# JenAI Architecture

> Current architecture for JenAI v2.6.0. This document is the source of truth for module
> responsibilities and dependency direction. Historical v0.1 design drafts were removed from
> the current tree and remain available through Git history.

## Product boundary

JenAI is a high-level robot decision and workflow agent:

- The Agent interprets intent and selects a registered Capability.
- A deterministic Workflow owns normal sequencing, retries, cancellation, evidence, completion,
  and return-home behaviour.
- ROS 2 and Nav2 adapters translate Workflow requests into robot interfaces.
- Nav2, robot controllers, and hardware safety own paths, velocity, steering, joints, and
  real-time collision avoidance.

JenAI does not claim that an LLM safely controls motors, that semantic patrol explores unknown
space, or that reaching a dock pose proves physical charging.

## Runtime flow

```text
Operator
  │ natural language / TUI / WebUI / CLI / MCP
  ▼
Interaction adapters
  │ intent routing / permissions / approval / rendering
  ▼
Agent and deterministic fast paths
  │ choose one registered Capability
  ▼
Capability Registry
  ├─ atomic Skills
  └─ deterministic Workflows
       │ policy / approval / optional Twin Gate
       ▼
Navigation Gateway and ROS bridge adapters
       ▼
ROS 2 / Nav2 / robot APIs
       ▼
pose / result / image / health evidence
       ▼
task outcome / audit / durable report
```

Unambiguous read-only requests may bypass the LLM through a deterministic fast path. Open-ended
requests use the LLM-assisted Agent, but model output still crosses the same Capability,
approval, policy, and evidence interfaces.

## Module map

| Module | Interface exposed to callers | Implementation responsibility |
|---|---|---|
| `agent/` | Interpret or run a high-level request | Context building, intent routing, model orchestration, guardrails, sessions, tracing |
| `tools/registry.py` | Resolve registered Skills and Workflows | Capability allowlist and tool registration |
| `workflows/` | Execute a typed long-running Workflow | Pure mission state, legal transitions, coverage planning, bounded retry, cancellation, completion |
| `tools/area_patrol_service.py` | Run semantic area patrol | Load site areas, call navigation and observation adapters, preserve evidence, return home, build report |
| `tools/navigation_gateway.py` | Submit or stop navigation | Single motion dispatch seam shared by Agent, TUI, WebUI, MCP, and daemon |
| `bridge/` | Typed JSON request/result protocol | ROS graph, Nav2 actions, pose, scan, camera, watchdog, cancellation, bounded legacy bring-up |
| `runtime/` | Submit and observe typed Tasks, resolve Approval, STOP, and execute prepared Capability steps | In-memory single-owner Authority for Task lifecycle, lease, epoch, ordered Events, Outcome and Receipt; versioned immutable Executor contracts and test adapter |
| `twin/` | Validate a candidate task | Isolated-domain rehearsal and pass/block/inconclusive verdicts |
| `state/` | Store task lifecycle and evidence | Runs, audit, receipts, reports, history, local data lifecycle |
| `tui/`, `webui/`, `cli/`, `mcp_server/` | Human or external entry points | Rendering and transport only; shared execution modules own behaviour |
| `providers/` | OpenAI-compatible model interface | Ollama, NVIDIA NIM, or other configured providers |
| `config/`, `site_profiles.py`, `site_assets.py` | Validated configuration and site identity | Provider, vehicle, map, location, policy, and deployment configuration |
| `adapters/nxdog.py` | Read one typed NXDog observation snapshot | Experimental HTTP transport, strict payload validation, partial-failure evidence |
| `acceptance/` | Reproducible HIL acceptance run | Isaac Sim/Nav2 preflight, route, cancel, halt, evidence capture, and the ADR 0007 simulation-only differential control arm |

`acceptance/motion_safety.py` is a pure observation-only admission evaluator. The concrete probe interpreter and operation vocabulary are repository-owned; configuration cannot select an executable or motion seam. `IsaacMotionReadinessCollector` is the bounded collection coordinator; `IsaacRosReadOnlyEvidenceSource` is the production decoder over the fixed repository-owned read-only Isaac probe, a create-once collision-geometry export produced inside the active Isaac process, and a closed observation operation enum. The collector captures all inputs concurrently, preserves typed timeout/source failures, and stores before/after RuntimeBinding snapshots so offline validation can reject identity drift, host-clock regression, ROS-clock regression, and overlong capture. Continuous swept clearance is represented by sampled signed clearance minus an explicit translation-and-rotation motion bound; increasing sample density alone is never treated as proof under ADR 0008. It transforms no robot state and exposes no motion seam. Capture
adapters supply one immutable, capture-overlapping and time-bounded Motion Request Binding plus
typed raw Evidence. The admission token binds the exact path and artifact input; a later
authorization must match the current runtime generation and atomically consume its
single-use nonce. Collision coverage is re-derived from a complete raw USD Stage enumeration and
typed effective filter rules rather than caller summaries or attestation. Its offline validator alone derives swept
clearance, geometry attestation, collision-timeline integrity, clearance budget,
and the final PASS/BLOCK decision.

## Important seams

### Capability seam

The Agent may select only registered high-level capabilities. Model output is untrusted until it
passes schema, registry, argument, state, policy, and approval checks.

### Workflow seam

Long-running robot behaviour is exposed through a small Workflow interface. Callers do not manage
individual retries, coverage state, evidence collection, or return-home steps.

`workflows/area_patrol.py` is pure Python and imports no ROS, LLM, TUI, or WebUI implementation.
`tools/area_patrol_service.py` adapts that domain model to navigation, observation, reporting, and
site configuration.

### Robot runtime seam

JenAI currently remains one repository. The conceptual JenAI/JenROS separation is represented by
interfaces rather than a second repository:

```text
JenAI decision side
  Agent / intent / Capability selection
             │
             │ typed workflow and navigation requests
             ▼
Robot runtime side
  Workflow / Navigation Gateway / ROS bridge / Nav2
```

ADR 0006 accepts one Robot Runtime Authority behind an authenticated high-level interface. The
current implementation provides a transport-neutral `InMemoryRuntimeAuthority`. For each accepted
typed Task it is the sole mutable owner of Approval, an optional effectful command lease, safety
epoch, deterministic Workflow Instance progress, public Event sequencing, canonical Task Outcome,
and an immutable in-memory Receipt. Pending Approval does not consume the command lease. Effectful
Tasks are admitted one at a time per robot without a hidden queue; read-only Tasks may run without
acquiring that lease. Approval binds the exact Task, generated steps, Workflow definition version,
epoch, and expiry.

The Authority provides task-scoped cancel separately from robot-wide STOP. STOP advances the
safety epoch, revokes the lease, invalidates pending Approval, and marks affected Tasks stopping
before it invokes the Executor STOP port. Robot-wide STOP dominates overlapping task cancellation.
Effectful admission remains closed through adapter cleanup; timeout, failure, or missing
acknowledgement leaves it blocked for future startup-reconciliation work. Adapter calls are bounded
without waiting for cancellation-resistant coroutines, whose late results are quarantined.
Terminal Outcome assignment is single-shot, so late adapter completion can become diagnostic
progress but cannot replace a prior terminal Outcome. Unverified cleanup produces `UNAVAILABLE`,
and its acknowledgement state and limitations remain in terminal Event and Receipt data. Executor
dispositions, Events, and Evidence remain adapter facts: the Authority alone assigns public
sequence and evaluates the Completion Contract into `TaskOutcome` and Receipt. Snapshots, public
Events, and Receipts are detached recursively immutable projections.

The Executor seam validates a registered `(capability_id, input_schema_version)`, runs an
effect-free preparation handler, and binds its recursively immutable canonical input to the
Authority-supplied `ExecutionContext`. It does not become a second owner of currentness or outcome.
Every production Adapter must also reject stale generation, safety epoch, and fencing token at the
actual platform effect-dispatch seam and bound its provider calls independently. Cancelling an
`asyncio` task is only an in-process scheduling signal, never Evidence that robot work stopped.
Cancel/STOP acknowledgement must come from Adapter/platform Evidence. A quarantined late callback
cannot reopen effectful admission or change terminal truth. The in-memory Executor is a test seam,
not evidence that a future NavigationGateway or physical Adapter satisfies this contract. No
production Adapter may connect to this Authority until the Executor contract adds a linearizable
current-fence/dispatch permit at the actual effect seam and the Adapter proves that it honors the
permit. This slice provides prepared-context binding and fail-closed cleanup truth; it does not
claim complete stale-work prevention at a robot effect seam.

All Authority state in this slice is process-local. A restart loses Tasks, Events, leases, STOP
idempotency records, Outcomes, and Receipts, so this implementation must not claim restart recovery
or durable audit. Durable Event Store, authority-generation takeover, startup reconciliation,
unknown-work handling, and cross-restart idempotency are the next EPIC-0003 slice. HTTP/JSON and SSE
remain later transport work. No interaction adapter has migrated, no product motion path uses this
Authority yet, and current commands still enter through existing adapters before reaching the
shared Navigation Gateway. Migration must deliver one parity-tested vertical slice before the
runtime flow changes. A future public transport must not expose raw ROS topics, services, actions,
`/cmd_vel`, or vendor motion endpoints.

NXDog HTTP observer 刻意位於 motion path 之外：Doctor 只能透過它取得 vendor 可觀察狀態，
不得註冊 Agent motion tool、授權移動或宣稱定位與導航 ready。未來 NXDog motion
integration 必須通過現有 Navigation Gateway，並遵守相同的批准、取消、evidence、
outcome 與 audit contract；TUI／WebUI 不得直接呼叫 vendor motion endpoint。

Split repositories only after this interface is stable and at least two independent runtimes
need separate release cycles.

### Simulation differential-control exception

Every product motion entry point continues to use Navigation Gateway. ADR 0007 authorizes one
acceptance-only exception: `R1_bridge_nav2` may send the same typed, Site-bound canonical goal
through a harness-owned, watchdog-configured `RosBridgeClient` to establish a bridge/Nav2 control
for comparison with `R2_jenai_no_retry` through Navigation Gateway.

This is not a Capability, Runtime command, product CLI path, Task Outcome, physical-robot path, or
general bridge API. It is available only through the dedicated simulation differential CLI after
explicit motion confirmation and clean source, scene, map, Nav2, T0/T1, lifecycle, evidence, and
cleanup gates. Agent, TUI, WebUI, MCP, daemon, Workflow, Runtime, NXDog, and physical integrations
may not import or reuse it. Architecture tests keep its direct bridge call sites on an exact
allowlist.

### Evidence seam

A process exit is not task success. Every motion or inspection result must become a typed task
outcome supported by available evidence:

- Nav2 acceptance and terminal result
- final pose and configured tolerance
- required image or sensor evidence
- coverage and unresolved areas
- cancellation or policy result
- return-home result

Missing evidence produces an unverified, partial, unavailable, blocked, or failed outcome instead
of success.

## Main workflows

### Read-only robot inspection

```text
request → deterministic intent route → pose/scan/Nav2 snapshot → fixed summary → audit
```

No LLM is required and no movement capability is exposed.

### Navigation and docking approach

```text
target → site/location validation → approval → optional Twin Gate
       → Navigation Gateway → Nav2 → final-pose verification → task receipt
```

Docking currently verifies approach pose. Physical charging remains unverified unless a future
robot adapter supplies charging-state evidence.

### Semantic area patrol

```text
mission → load required areas → deterministic coverage order
        → navigate → inspect/capture → update area state
        → bounded retry/defer → completion evaluation
        → return home → durable report
```

This covers registered semantic areas and inspection points. It is not geometric floor coverage,
SLAM exploration, or frontier exploration.

## Dependency rules

- `workflows/` must not import ROS 2, model providers, TUI, WebUI, or CLI modules.
- Agent code must not publish `/cmd_vel` or construct low-level motion commands.
- Product motion enters through Navigation Gateway; only ADR 0007's exact simulation acceptance
  control arm may bypass it, and that exception cannot be registered or imported by product layers.
- UI adapters must not reimplement navigation, approval, patrol, or reporting logic.
- ROS-specific types are converted at the bridge/adapter seam.
- All external calls have bounded timeouts; retries and mission loops are bounded.
- Stop, cancel, and monitoring remain available when the model provider is unavailable.
- Site-bound coordinates require matching map and locations identities.
- Simulation evidence must not be presented as physical safety or charging evidence.

These rules are enforced by architecture tests, type checking, lint, unit/integration tests, HIL
acceptance, and release supply-chain checks.

## Where to read next

- [TECHNICAL_GUIDE](TECHNICAL_GUIDE.md): installation, configuration, runtime details, extension
- [CURRENT_WORKFLOW](workflow/CURRENT_WORKFLOW.md): startup, runtime, approval, ownership, HIL state
  machine, error recovery, and replay policy
- [CODE_TOUR](CODE_TOUR.md): code-reading path by module and execution trace
- [ADR](adr/): accepted architectural decisions
- [ROADMAP](product/ROADMAP.md): future work and remaining evidence gates
- [QUALITY_REVIEW](product/QUALITY_REVIEW_2026-07-25.md): v2.4.0 review and verification results
- [EVIDENCE_LEDGER](validation/EVIDENCE_LEDGER.md): formal experiment evidence and limitations

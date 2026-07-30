# JenAI Architecture

> Current architecture for JenAI v2.5.1. This document is the source of truth for module
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
| `twin/` | Validate a candidate task | Isolated-domain rehearsal and pass/block/inconclusive verdicts |
| `state/` | Store task lifecycle and evidence | Runs, audit, receipts, reports, history, local data lifecycle |
| `tui/`, `webui/`, `cli/`, `mcp_server/` | Human or external entry points | Rendering and transport only; shared execution modules own behaviour |
| `providers/` | OpenAI-compatible model interface | Ollama, NVIDIA NIM, or other configured providers |
| `config/`, `site_profiles.py`, `site_assets.py` | Validated configuration and site identity | Provider, vehicle, map, location, policy, and deployment configuration |
| `adapters/nxdog.py` | Read one typed NXDog observation snapshot | Experimental HTTP transport, strict payload validation, partial-failure evidence |
| `acceptance/` | Reproducible HIL acceptance run | Isaac Sim/Nav2 preflight, route, cancel, halt, and evidence capture |

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

ADR 0006 accepts a future single authenticated high-level HTTP Robot Runtime authority. It is
an architecture direction, not a current production component: no runtime HTTP server is
registered, no interaction adapter has migrated, and current commands still enter through the
existing adapters before reaching the shared Navigation Gateway. A future implementation must
deliver one parity-tested vertical slice before this flow or module map changes. It must not
expose raw ROS topics, services, actions, `/cmd_vel`, or vendor motion endpoints over HTTP.

NXDog HTTP observer 刻意位於 motion path 之外：Doctor 只能透過它取得 vendor 可觀察狀態，
不得註冊 Agent motion tool、授權移動或宣稱定位與導航 ready。未來 NXDog motion
integration 必須通過現有 Navigation Gateway，並遵守相同的批准、取消、evidence、
outcome 與 audit contract；TUI／WebUI 不得直接呼叫 vendor motion endpoint。

Split repositories only after this interface is stable and at least two independent runtimes
need separate release cycles.

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

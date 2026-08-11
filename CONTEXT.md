# JenAI Domain Context

JenAI is a high-level decision agent for unmanned ground vehicles. It interprets
operator intent, selects registered robot capabilities, calls existing ROS 2 or
robot APIs, and verifies the observable result. It does not replace low-level
motion control, localization, collision avoidance, or the robot's safety system.

## User-visible language

All Chinese text shown to an operator uses Taiwan Traditional Chinese. Model
output is treated as untrusted presentation data and normalized at the product
boundary. Program identifiers, ROS 2 names, tool names, paths, numbers, and
units remain unchanged. Simplified Chinese may appear only in hidden input
aliases or regression fixtures used to verify compatibility and conversion.

## Ubiquitous language

### High-level decision agent

The component that turns operator intent into a bounded task, selects an
available capability, supervises its execution, and reports a verified outcome.
It decides *what* the robot should do, while existing controllers decide *how*
the robot moves.

### Mission Draft

A typed but untrusted interpretation of operator intent. It cannot be approved,
compiled, or executed until a deterministic Validator/Binder resolves
registered identities, policy, and defaults.

### Mission Spec

The immutable, validated high-level Task request produced from a Mission Draft.
It states what must be completed without mutable execution progress. The first
product Mission kind is `patrol`.

### Execution Plan

The immutable ordered steps deterministically compiled from one Mission Spec.
Its exact digest is the approval boundary; an approved plan cannot be silently
reordered, extended, or rebound.

### Execution Engine

The deterministic role that owns mutable progress for one approved Execution
Plan. It advances steps, applies bounded policy, handles STOP, and invokes the
Capability Executor. It does not interpret language or control the robot.

### Approval Generation

A session-local, monotonically increasing generation that invalidates Golden
Path Yes／Auto authority after STOP, profile change, unknown runtime state, or
explicit Auto exit. Process restart creates a new Session and generation
domain; it does not require durable generation storage.

_Avoid:_ “Safety Epoch”, which belongs to the future durable Runtime Authority.

### Patrol Mission

The first product Mission. In v1 it compiles registered patrol locations and a
system-added home into a fixed Execution Plan. It is narrower than Semantic
Area Patrol, which adds observations and coverage in a later Epic.

### Capability

A registered action or observation that a robot can perform through a known
interface. A capability includes its maturity, prerequisites, completion
contract, evidence sources, and known limitations.

### Workflow Capability

A long-running, goal-level robot capability that defines a complete deterministic
process rather than one primitive action. A Workflow Capability defines typed
inputs, normal sequencing, bounded retries, cancellation, evidence, completion,
and reporting. The LLM selects it but does not execute its normal steps.

### Workflow Instance

One execution of a Workflow Capability for one accepted Task. It holds mutable
progress, retries, evidence, and terminal state. The Golden Path Execution
Engine is its sole mutable owner; after ADR 0006 migration, the Robot Runtime
Authority contains that engine rather than creating a parallel instance.

_Avoid:_ “application workflow” or “UI workflow”, which can imply a second
execution owner outside the Runtime.

### Semantic Area Patrol

The first Workflow Capability. It covers every required Inspection Area in the
active Site Profile, visits its Inspection Points, preserves observations,
evaluates required-area coverage, and applies the Return Home contract. It is
not random exploration and is not merely an operator-provided waypoint list.

### Inspection Area and Inspection Point

An Inspection Area is a semantic part of a site whose coverage can be required
or optional. An Inspection Point is a registered, map-bound observation pose
that contributes evidence for its area. Reaching a point without obtaining its
required observation does not complete the area.

### Event-driven decision

The rule that normal Workflow steps do not call the LLM. JenAI re-enters the
Agent Path only when a new goal or unresolved high-level event requires semantic
judgment, policy choice, or human escalation. Nav2 feedback, retry counters, and
ordinary progress updates are handled deterministically.

### Robot Runtime Seam

The small typed interface through which a Workflow navigates, inspects, and
returns home. ROS 2, Nav2, Isaac Sim, and future robot-specific SDKs implement
this seam; the Workflow domain does not import them.

### Robot Runtime Authority

The future cross-interface owner of effectful Task admission, Approval
resources, active Workflow Instances, stopping, Task Outcomes, receipts, and
Evidence truth for one robot. Under ADR 0006 it contains the Execution Engine;
it never replaces it with a parallel mutable execution lifecycle.

_Avoid:_ “shared backend” or “vendor API proxy”, which omit the ownership and
safety responsibility.

### Capability Executor

The execution-layer role that dispatches an admitted platform-neutral Workflow
step to navigation, platform-command, or observation Interfaces and returns
typed Events and Evidence. It does not interpret intent, grant Approval, or
decide a Task Outcome.

_Avoid:_ “universal gateway”, which obscures the distinct navigation,
platform-command, and observation responsibilities.

### Command Lease

The exclusive, time-bounded right of one accepted effectful task to command a
robot. Revocation prevents the former holder from starting or continuing work,
including delayed requests.

_Avoid:_ “lock”, which can imply only process-local mutual exclusion.

### Safety Epoch

A monotonically advancing generation that invalidates commands, approvals, and
leases created before a safety event. A client with an older epoch must refresh
authoritative state rather than retry its stale action.

_Avoid:_ “session ID”, which does not express invalidation or ordering.

### Runtime Event

An ordered, immutable fact about a task, approval, stop, availability, or
evidence lifecycle. TUI and WebUI may render different projections, but both
derive them from the same event and state truth.

_Avoid:_ “log message”, which does not imply a stable schema or lifecycle
meaning.

### Evidence Envelope

A source-attributed observation carrying robot and task identity, source and
receive time, freshness, content integrity, transport security, source
assurance, schema version, and known limitations. These dimensions remain
independent; a digest does not prove origin, and an authenticated transport does
not prove that a sensor observation is correct. When the source does not provide
a timestamp, that absence remains explicit.

_Avoid:_ “telemetry blob”, which hides provenance and verification limits.

### Evidence Source Assurance

The stated basis for believing Evidence came from its claimed origin, such as
vendor telemetry, Runtime observation, operator observation, or a derived
result. It is independent of freshness, content integrity, and transport
authentication.

_Avoid:_ “trusted Evidence”, which collapses several different guarantees into
one unsupported claim.

### Robot Capability Card

The authoritative description of a robot's identity and registered
capabilities. The agent may reason about this information, but must not claim a
capability, observation, or successful result that the card and live evidence do
not support.

### Site Profile

The versioned definition of an operating site. It binds a map identity to the
site's locations, routes, semantic Inspection Areas, home and dock approaches,
reference scene, and validation evidence. A profile must be explicitly
activated before its coordinates or Workflow definitions can be used.

### Map Identity

The stable identity of the map used by an active Site Profile. Map identity is
not the map's display name alone; it includes versioned content evidence so that
coordinates from one map cannot silently be reused with another.

### Completion Contract

The task-specific conditions required before JenAI may claim completion. It
defines tolerances, required observations, and acceptable outcomes. Reaching a
navigation goal and confirming that a charger is delivering power are different
contracts.

### Task Outcome

The product-level result of a task:

- `succeeded`: the completion contract was verified.
- `arrived_unverified`: the approach pose was reached, but the final real-world
  effect cannot be observed.
- `partial`: only part of the requested task was completed.
- `endpoint_mismatch`: execution ended outside the required endpoint tolerance.
- `blocked`: a policy, prerequisite, or approval prevented execution.
- `unavailable`: a required capability or dependency was unavailable.
- `failed`: execution or verification failed.
- `cancelled`: the operator or system cancelled the task.

### Evidence

An observation used to evaluate a Completion Contract, such as an AMCL pose,
Nav2 result, laser scan, image, controller state, or charging signal. Evidence
must identify its source and must not be inferred from the requested action.

### Fast Path

A deterministic interpretation route for common, unambiguous intents. It avoids
an LLM call, but uses the same capability contracts, approval boundary,
execution interfaces, and result verification as the Agent Path.

### Agent Path

An LLM-assisted interpretation and planning route for complex or ambiguous
intents. The model may reason and choose among registered capabilities, but it
cannot create facts, coordinates, observations, or successful outcomes. After
selecting a Workflow Capability it yields normal execution to the deterministic
runtime and is re-entered only by a high-level event.

### Ground-truth evaluator

Experiment-only evaluation that compares the operational estimate against
simulation ground truth. Ground truth measures JenAI; it is never fed back into
the operational controller or used to make a failed task appear successful.

### Motion Readiness Gate

An observation-only admission decision evaluated before simulation motion is
authorized. Its immutable Motion Request Binding ties one single-use nonce,
bounded capture-overlapping ROS/host validity window, Site, start, goal,
planner/configuration, scene/map/runtime/filter, boot and simulation epoch to the exact captured path
and artifact input. Authorization is an atomic compare-and-consume operation;
Stop→Play, runtime drift, expiry, or reuse invalidates it. It requires an orientation-specific Swept Footprint Clearance,
attested USD collision geometry and live Nav2 footprint, a reconstructible
Collision Evidence Timeline, and a source-bound Clearance Budget. Missing or
stale Evidence produces `BLOCK`; the Gate never dispatches a goal or velocity.

### Swept Footprint Clearance

The signed separation between the complete, oriented robot footprint swept
along an interpolated planned path and preserved obstacle/unknown costmap
cells. It is not robot-centre clearance and does not treat inflation cost as
physical geometry.

### Clearance Budget

The required minimum separation derived from individually sourced geometry,
localization, controller tracking, map discretization, timing, stopping, and
fixed product-margin terms. Unknown terms remain unavailable and block motion;
they are never silently replaced by zero or a convenient constant.

### Collision Evidence Timeline

Source-attributed collision observations divided into pre-dispatch, motion,
terminal-relative, and post-stop windows. Coverage is derived offline from an independent, complete, content-digested raw
USD Stage enumeration and typed effective rules for every monitored USD prim,
every collision-enabled counterpart/category, and contact-reporting state; neither
caller summaries nor coordinated omissions are trusted. An observed empty window differs from
a missing or stale stream. A vendor Boolean without source time or contact
identity cannot by itself attest that no collision occurred.

### Dock Approach

Navigation to a registered pose near a docking station. In the current Isaac Sim
reference platform this can verify pose arrival, but not final connector
alignment or charging. Its successful product outcome is therefore
`arrived_unverified`.

### Final Alignment

The future close-range docking phase that aligns the robot with a physical
connector or charging interface and verifies the resulting charge state. It is
not part of the current Dock Approach capability.

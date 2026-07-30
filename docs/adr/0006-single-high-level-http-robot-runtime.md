# ADR 0006: One high-level HTTP robot runtime owns robot commands

- Status: Accepted
- Date: 2026-07-30
- Implementation: Accepted direction; production migration has not started

## Context

JenAI currently has several interaction adapters. The TUI, WebUI, MCP server,
daemon, and some CLI commands can each create process-local run state, ROS
bridge clients, busy locks, approvals, or Navigation Gateway instances. A
process-local lock cannot prevent another process from accepting a second goal,
and a stop from one process cannot invalidate an approval held by another.

NXDog demonstrates a useful transport property: a robot implementation can be
hidden behind a small HTTP interface. The useful lesson is not to reproduce
every ROS topic, service, and action as an HTTP endpoint. Doing so would leak
ROS graph, QoS, TF, lifecycle, action feedback, and cancellation complexity to
every caller.

JenAI also needs the approved TUI and WebUI designs to show the same task,
approval, stop, and evidence truth without either interface reimplementing
robot behaviour.

## Decision

JenAI will introduce one authenticated, high-level Robot Runtime authority.
Interaction adapters use an HTTP/JSON command interface and an SSE event
interface. The runtime owns:

- the active robot/domain command lease;
- the safety epoch used to invalidate stale approvals and commands;
- the Approval resource for every accepted effectful Task and the mutable
  Workflow Instance for every accepted Workflow Capability Task;
- deterministic Workflow sequencing, cancellation, bounded retry, and return home;
- Completion Contract evaluation, Evidence, Task Outcome, and immutable Receipt;
- a Capability Executor that keeps the existing Navigation Gateway limited to
  navigation, uses platform-command ports for indicator/posture/charging, and
  uses observation ports for robot state;
- the existing Navigation Gateway and its ROS bridge lifecycle;
- monotonically sequenced runtime events and durable task truth.

The Agent and deterministic Fast Path remain outside this authority only for
intent interpretation and registered Capability selection. Once they submit a
typed high-level Task, they do not retain a second active Workflow, Approval,
lease, Completion Contract, or Task Outcome state.

The interface exposes high-level capabilities and evidence. It does not expose
arbitrary ROS topics, services, actions, `/cmd_vel`, Nav2 lifecycle controls, or
robot-specific vendor paths. HTTP disconnect does not cancel a command.
Commands return an identifier promptly; progress and terminal results are
observed through status and event resources.

Protocol v0 co-locates the authority and its platform Adapter in one deployment
unit. Isaac places both on the DGX Spark. NXDog places both on a robot-side
companion or LAN sidecar that can access the local Foxy domain; remote callers
use the public high-level Runtime interface. That public interface terminates
at the authority and is not reused as an authority-to-Adapter protocol. A
future remote authority/Edge split requires a separate Edge Control Protocol
and ADR covering fencing, takeover, network partition, reconciliation, and
robot-wide stop.

Isaac Sim remains implemented by a ROS 2/Nav2 adapter behind the runtime.
NXDog remains observation-only under ADR 0005 until a separate motion ADR and
physical acceptance evidence exist. The vendor HTTP interface is not exposed
directly to Agent or UI callers.

Isaac Stop/Play/Replay and other simulator administration operations are
separate operator-only controls. They are not registered Agent capabilities.

The first transport binds to loopback and requires a generated access token.
LAN or remote exposure requires an explicitly configured authenticated secure
transport. The existing public WebUI emergency-stop route may forward an
unauthenticated stop request to the local authenticated runtime, but the
runtime transport itself is not anonymously exposed.

Authentication creates a transport-owned principal used for authorization,
audit identity, and idempotency scope. Caller-provided client IDs, source
surface labels, and reason strings are untrusted claims and cannot grant
authority or become a trusted classification. Any transport-bound client
identity must come from the credential or TLS context, never from the payload.
Public STOP callers may request an operator stop; watchdog, policy, and
runtime-shutdown causes are internal facts.

Callers request a bounded execution timeout rather than supply an authoritative
wall-clock deadline. The Runtime clamps it by Capability policy and separately
owns request freshness, Approval expiry, execution, postcondition Evidence,
cleanup/cancel, and STOP budgets. The postcondition window is read-only and
cannot extend effectful execution. Approval waiting does not consume execution
budget; when execution starts, the Runtime publishes the authoritative server
start time and deadline in the TaskStarted event and Task view. Evidence records
content integrity, transport security, and source assurance as independent
dimensions.

Every Runtime start advances its durable authority generation and safety epoch,
invalidates stale Approvals and leases, reconciles non-terminal Tasks and any
observable active robot work, and performs bounded cleanup before accepting an
effectful Task. Until reconciliation finishes the Runtime is read-only,
degraded, or unavailable; observation and STOP remain available.

Migration is incremental. Existing TUI and WebUI appearance and interaction
contracts remain unchanged. A caller is moved only after parity tests prove
that it sends the same typed request through the Capability Executor, keeps
navigation on the existing Navigation Gateway, and preserves Approval,
cancellation, Evidence, and Task Outcome behaviour.

## Consequences

- One stop can invalidate stale approvals across every interaction adapter.
- TUI and WebUI can project the same run and evidence without sharing UI code.
- ROS 2 environment, DDS, TF, Nav2 action, watchdog, and cleanup complexity
  become local to the Isaac adapter.
- Approval, Workflow progress, Completion Contract, and Task Outcome cannot
  diverge between an application process and the Runtime authority.
- Tests can use an in-memory runtime adapter or HTTP fixture without pretending
  that mocks prove live Isaac or physical behaviour.
- The runtime interface and wire schema become versioned public surfaces.
- Runtime availability becomes an explicit product state. A disconnected
  standalone WebUI must become read-only rather than imply that actions work.
- The migration adds a local process and reconnect lifecycle that require
  bounded startup reconciliation, orphan cleanup, authentication, health,
  event replay, fencing continuity, and shutdown tests.

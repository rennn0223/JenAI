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
- the existing Navigation Gateway and its ROS bridge lifecycle;
- active run, command, approval, cancellation, stop, and evidence state;
- monotonically sequenced runtime events and durable task outcomes.

The interface exposes high-level capabilities and evidence. It does not expose
arbitrary ROS topics, services, actions, `/cmd_vel`, Nav2 lifecycle controls, or
robot-specific vendor paths. HTTP disconnect does not cancel a command.
Commands return an identifier promptly; progress and terminal results are
observed through status and event resources.

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

Migration is incremental. Existing TUI and WebUI appearance and interaction
contracts remain unchanged. A caller is moved only after parity tests prove
that it sends the same typed request through the existing Navigation Gateway
and preserves approval, cancellation, evidence, and task-outcome behaviour.

## Consequences

- One stop can invalidate stale approvals across every interaction adapter.
- TUI and WebUI can project the same run and evidence without sharing UI code.
- ROS 2 environment, DDS, TF, Nav2 action, watchdog, and cleanup complexity
  become local to the Isaac adapter.
- Tests can use an in-memory runtime adapter or HTTP fixture without pretending
  that mocks prove live Isaac or physical behaviour.
- The runtime interface and wire schema become versioned public surfaces.
- Runtime availability becomes an explicit product state. A disconnected
  standalone WebUI must become read-only rather than imply that actions work.
- The migration adds a local process and reconnect lifecycle that require
  bounded startup, authentication, health, event replay, and shutdown tests.

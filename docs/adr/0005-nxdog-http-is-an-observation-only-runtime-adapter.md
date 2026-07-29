# ADR 0005: NXDog HTTP begins as an observation-only runtime adapter

- Status: Accepted
- Date: 2026-07-28

## Context

The public `nexuni/nxdog-developer-kit` repository exposes ROS 2 compatibility
interfaces and an example Flask server for the robot-side `nxnav` and platform
driver. The example is explicitly not a production SDK. Its motion endpoints do
not provide the command ownership, authenticated transport, cancellation
acknowledgement, evidence freshness, or timeout cleanup required by JenAI's
physical-robot completion contracts.

JenAI needs evidence from a second robot runtime before the existing Robot
Runtime Seam can be safely generalized. Advertising NXDog navigation before the
vendor contract and physical acceptance evidence exist would turn an
architectural claim into an unsupported product claim.

## Decision

JenAI initially integrates NXDog as an experimental, read-only adapter.

The adapter:

- is enabled explicitly with `JENAI_NXDOG_API_URL`;
- calls only the example server's read-only health, readiness, map, odometry,
  velocity, and charging-state endpoints;
- validates every response into typed evidence and preserves partial failures;
- reports that the HTTP responses have no vendor source timestamp or
  cryptographic map identity;
- is consumed by `JenAI doctor`, not by the Agent, TUI motion commands,
  Navigation Gateway, or deterministic Workflows.

No NXDog motion Capability is registered in this phase. In particular, the
adapter does not call `/navigate`, `/stop`, `/pause`, `/resume`,
`/set_cmd_vel`, `/set_initialpose`, `/map`, `/charging`, or sport-action
endpoints.

A later motion integration must retain the existing Navigation Gateway as the
single application seam and place a hardened, authenticated companion adapter
behind it. That work requires a separate ADR and physical acceptance evidence.

## Consequences

- Existing ROS/Nav2 and Isaac Sim behaviour is unchanged.
- NXDog connectivity and response-shape problems can be diagnosed without
  moving the robot.
- HTTP reachability, `ready_flag`, cached odometry, and charging current are
  observations; none alone proves that navigation is safe or ready.
- The vendor example may remain on plain HTTP for an isolated laboratory pilot,
  but JenAI emits a warning because it provides no transport authentication or
  confidentiality.
- Map name and tile are retained as vendor observations and do not replace the
  Site Profile's content-bound map identity.
- Physical navigation, software stop, auto-charging, posture actions, and
  cross-map routing remain unavailable until their execution and evidence
  contracts are implemented and validated.

# ADR 0007: A simulation differential control arm may bypass Navigation Gateway

- Status: Accepted
- Date: 2026-07-31
- Scope: Isaac Sim acceptance tooling only

## Context

JenAI requires every product motion entry point to use the shared Navigation Gateway. This keeps
Site binding, policy, Approval, bounded recovery, Completion Contract, Evidence, and Task Outcome
semantics out of interaction surfaces and prevents a second production navigation path.

The Product Owner explicitly requested an observation-only experiment that can distinguish the
existing Gateway policy from the underlying bridge and Nav2 execution. The experiment needs two
arms with the same canonical Site-bound goal:

```text
R1_bridge_nav2       canonical goal → harness-owned RosBridgeClient → Nav2
R2_jenai_no_retry    canonical goal → NavigationGateway → Nav2
```

Routing R1 through Navigation Gateway would make the two arms equivalent and could not answer the
diagnostic question. Treating this research control as a general navigation API, however, would
violate the product architecture and create an unsafe precedent.

## Decision

JenAI accepts one narrow exception: the `R1_bridge_nav2` control arm in
`src/jenai/acceptance/nav_differential_runner.py` may call the harness-owned
`RosBridgeClient.nav_send` boundary directly. `_ObservedNavBridge.nav_send` may proxy that exact
boundary to capture the dispatched goal for both arms.

This exception is valid only when all of the following conditions hold:

- execution is in `simulation` deployment mode and uses the dedicated differential CLI;
- the operator supplies the exact motion confirmation required by the harness;
- the executing source is the reviewed clean revision and the absolute Isaac Stage, Site, map,
  locations, Nav2 parameters, ROS domain, and process generation pass identity gates;
- the target is a typed, Site-bound canonical saved location, and the observed outgoing goal is
  equivalent before forwarding;
- the harness owns one watchdog-configured bridge, observes one correlated Nav2 goal lifecycle,
  preserves T0/T1/final evidence, and performs bounded final halt, unwatch, bridge shutdown, and
  durable artifact cleanup;
- R2 continues to use Navigation Gateway with its experimental retry override confined to an
  in-memory configuration copy;
- the result remains simulation diagnostic evidence and never becomes a product Task Outcome or a
  physical-safety claim.

The exception does **not** permit:

- registration as a Capability, Skill, Workflow, Runtime command, or reusable motion API;
- import or invocation by Agent, TUI, WebUI, MCP, daemon, product CLI, Workflow, or Runtime layers;
- direct `/cmd_vel`, arbitrary coordinates, Nav2 or AMCL parameter changes, tolerance changes, or
  endpoint-policy changes;
- use with a physical robot, NXDog, another vendor adapter, or a remote robot Runtime;
- another direct bridge dispatch in a script or product module.

Existing low-level implementation seams remain unchanged: the bridge protocol dispatcher, the
Nav2 adapter behind Navigation Gateway, and the isolated Twin Gate may call the bridge operation
inside their established responsibilities. They do not make this acceptance exception broader.

## Enforcement

Architecture tests keep an exact allowlist of source-level `nav_send` call sites, prohibit product
layers from importing the differential runner, allow only the dedicated differential CLI to import
it, and verify that the differential control is absent from the Robot Capability Card. Any new
call site or importer is a reviewed architecture change, not an implicit extension of this ADR.

The historical E2 ablation reset script contains one frozen direct-dispatch debt predating this
ADR. It is not authorized by this decision and may not expand; it should be migrated or retired in
a separate benchmark-preservation change.

## Consequences

- The R1/R2 experiment can isolate Gateway policy without weakening any product movement path.
- Simulation motion remains explicit, supervised, evidence-bound, and fail closed.
- Interaction surfaces and future Robot Runtime clients still have exactly one product navigation
  seam.
- The exception must be reviewed after the differential baseline is complete or when Isaac motion
  migrates behind Robot Runtime Authority. It must not be copied into NXDog integration.

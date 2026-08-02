# ADR 0008: Motion readiness evidence precedes simulation motion

- Status: Accepted
- Date: 2026-08-03
- Scope: Isaac Sim acceptance and differential experiments

## Context

The ADR 0007 differential harness can make R1 and R2 comparable, but
experimental fairness does not prove that a planned route is safe to execute.
Earlier plan-only observations found small robot-centre clearances, an
unattested relationship between the live Nav2 footprint and Isaac collision
geometry, and a `/twin/collision` Boolean that was not part of the same
timestamped Evidence timeline. Repeating motion under those conditions would
trade collision risk for evidence that cannot distinguish path, geometry,
localization, controller, or collision-reporting defects.

## Decision

Every new Isaac motion smoke test and ADR 0007 Pilot requires an independently
validated Motion Readiness artifact. The observation-only Gate owns no bridge,
Navigation Gateway, goal, velocity, Approval, or Task Outcome. It evaluates four
conjunctive contracts:

1. orientation-specific swept-footprint signed clearance over a conservatively interpolated exact planned path, costmap boundaries, and four separately preserved costmap classes;
2. exact-scene USD collision geometry attestation against the live effective
   Nav2 footprint, without using inflation to compensate for undersizing;
3. a source-timestamped Collision Evidence Timeline whose absent, stale, or
   clock-incompatible state differs from an observed empty window; and
4. a required-clearance budget whose seven positive terms each carry source,
   method, timestamp, configuration, and unit evidence.

All raw Evidence is typed and content-digested. A schema-v4 MotionRequestBinding
binds a single-use nonce, capture-overlapping ROS/host validity whose endpoints
and use-time remain inside the Evidence age bound, Site, start, goal, planner,
product/Nav2 configuration, scene, map, runtime, collision-filter, boot, and epoch
identity to the path and later authorization. A separate admission token binds
the exact PathEvidence digest and immutable artifact input digest. Costmaps preserve complete
RLE grids. USD geometry preserves raw vertices and separate scale under one
row-major affine column-vector, metre-translation, scale-excluded rigid-transform
contract. Collision Evidence preserves the exact robot root, monitored collision-prim
inventory, an independently content-digested raw USD Stage enumeration with query/source
identity, scene binding, count, completeness, and collision-enabled
counterpart/category entries, plus one typed effective filter rule per monitored
prim. Counterpart summaries and filter coverage are both rebuilt against the raw
enumeration, so coordinated omission cannot pass. Offline validation derives full
coverage and contact-reporting enablement; it does not trust a caller coverage
Boolean or an opaque filter digest. Clearance inputs use method-specific typed
non-negative measurements. Evidence is bound to the scene, map, Nav2 parameters,
runtime fingerprint, boot identity, simulation epoch, and bounded capture window.
The public offline validator recomputes geometry, attestation,
timeline, budget, margin, and decision; it does not trust the stored summary.
Artifact paths are create-once.

`PASS` only means that the captured simulation evidence supports requesting a
separate motion authorization whose immutable MotionRequestBinding, exact path,
and artifact input match the validated admission token. Authorization additionally
requires the same current runtime generation, both validity clocks, and an
atomically unconsumed nonce; Stop→Play or any runtime drift invalidates it. It is not motion authorization, a product
Task Outcome, physical safety evidence, or permission to expose a new command
path. Any missing term or Evidence dimension is `BLOCK`.

The first smoke route is selected from plan-only candidates and must be short,
visible, low-turn, and have margin above the derived budget. Dock to
`map_left_down` remains blocked until the complete Gate passes; it is not the
default first smoke route.

## Consequences

- Harness commissioning and motion safety are distinct decisions.
- A centreline or cost value can be reported diagnostically but cannot be
  described as robot-body clearance.
- Current `/twin/collision: std_msgs/msg/Bool` is expected to block until a
  source-timestamped and identity-bearing collision contract exists.
- Unknown controller tracking or stopping bounds block rather than becoming
  zero-valued assumptions.
- Simulation PASS cannot be generalized to NXDog or physical safety.
- Isaac Stop/Play remains an operator commissioning reset, not a Runtime
  recovery contract.

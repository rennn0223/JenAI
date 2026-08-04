# ADR 0008: Versioned geometry attestation precedes simulation motion

- Status: Accepted ADR; proposed amendment dated 2026-08-04
- Original date: 2026-08-03
- Scope: Simulation geometry readiness, motion admission, and acceptance evidence

## Context

ADR 0007 makes the R1/R2 differential experiment comparable, but experimental
fairness does not prove that a route is ready for motion. The original version
of this ADR therefore required every Isaac motion attempt to capture active
Stage geometry, a collision timeline, a full uncertainty budget, and a
tamper-resistant evidence envelope.

Those contracts remain useful for high-assurance research, but requiring them
for ordinary product development and release acceptance coupled every motion
attempt to GUI-local Stage extraction. Product execution, robot geometry
calibration, and acceptance consequently acquired different lifecycles while
being treated as one runtime operation. In particular, USD collision geometry
changes at robot-profile or scene-asset cadence, not at task cadence.

The approved design in
[`OFFLINE_ROBOT_GEOMETRY_CALIBRATION.md`](../design/OFFLINE_ROBOT_GEOMETRY_CALIBRATION.md)
separates these concerns. This amendment records the policy and responsibility
boundaries required before that design may be implemented. It replaces the
original requirement that every Development or Acceptance readiness decision
must recapture active Stage geometry; it does not convert any existing artifact
into a valid geometry attestation or authorize motion.

Until the later Motion Safety admission convergence updates the canonical
glossary and architecture description, their existing `Motion Readiness Gate`
language that requires a per-run active-process Stage export, complete
Collision Evidence Timeline, or full source-bound Clearance Budget denotes the
**Certification Research** profile in this ADR. It is not an additional
Development or Acceptance prerequisite. This ADR controls the amended policy;
the current implementation continues to fail closed until the separately
reviewed implementation and documentation migrations are complete.

## Decision

JenAI separates three flows:

1. **Product Runtime** continues to use the existing
   `NavigationGateway → ROS 2/Nav2 → robot` path. It consumes immutable
   readiness results and does not load a calibration tool, parse USD, or create
   another motion authority.
2. **Offline Calibration** runs at robot-profile and geometry-change cadence in
   a fresh Headless Isaac process. It composes the exact USD dependency closure,
   projects the collision geometry into the configured base frame, compares it
   with the canonical motion-relevant footprint set, and produces a reusable,
   versioned `RobotGeometryAttestation`.
3. **Acceptance** obtains live path, costmap, footprint, localization, runtime,
   result, and STOP/cancel Evidence through ROS 2 and combines it with a
   matching reviewed attestation. It does not recapture USD geometry for each
   task.

These are one-way dependencies: Product Runtime and Acceptance may validate
immutable attestation data, but neither depends on the Headless calibration
implementation. Offline Calibration owns no Navigation Gateway, goal,
`cmd_vel`, initial-pose mutation, Approval, Task Outcome, or runtime recovery.

### Reusable geometry attestation

A `RobotGeometryAttestation` binds, at minimum, the robot-profile identity,
geometry-source kind and deployment applicability, calibration contract and
tool identity, exact geometry source and dependency closure, projected
collision hull, canonical motion-relevant footprint set, containment result,
and content identity. Any bound identity change makes the attestation stale or
mismatched and requires recalibration; normal startup validates the existing
attestation instead of rerunning calibration.

An attestation `PASS` proves only that the attested motion-relevant footprint
conservatively contains the calibrated geometry under the recorded contract.
It does not prove route clearance, localization health, controller tracking,
collision-free execution, task completion, or physical safety.

`geometry_source.kind = isaac_usd` is valid only for a matching `simulation`
vehicle profile. A physical profile must not reference, accept, or derive
confidence from an Isaac USD attestation. Future physical geometry evidence
requires a separately defined provenance and validator, such as reviewed CAD,
manufacturer dimensions, a measured physical envelope, or a
configuration-specific geometry certificate.

### Canonical motion-relevant Nav2 footprint contract

Version 1 uses one conservative strategy:

```text
global costmap effective footprint
  = canonical-equivalent local costmap effective footprint
  = canonical-equivalent attested footprint
```

The contract covers both configured global and local costmap sources, their
geometry mode (`polygon` or `robot_radius`), base frame, padding and padding
algorithm, derived effective geometry, and the presence of any dynamic or
post-validation footprint source. Canonical equivalence is based on normalized
geometry and versioned canonical digests, not raw parameter strings or vertex
ordering. The normative canonicalization contract is defined by the approved
Design Brief and must be preserved by later schema and validator work.

Global/local mode or digest mismatch is `blocked`. An unavailable parameter
interface, indeterminate mode, invalid geometry, enabled dynamic footprint, or
post-validation override is `unavailable` or stale and fails closed. Validating
only one costmap is insufficient. A footprint-generation change after
validation invalidates readiness until the complete set is verified again.

### Motion-admission levels

Evidence requirements are proportional to the claim being made:

| Level | Required evidence and claim boundary |
|---|---|
| **Development** | A deployment-applicable geometry attestation; oriented-footprint conservative path clearance under an explicit route/vehicle policy; localization, TF, costmap, and runtime health; and an available STOP/cancel path. This supports bounded development motion only. |
| **Acceptance** | All Development requirements plus a controlled scene launch manifest for simulation, repeatable fixed-task results, durable artifacts, and cleanup evidence. A missing collision stream must remain an explicit limitation and cannot support `collision_free=true` or an equivalent claim. |
| **Certification Research** | Per-run raw active-Stage geometry, full source-timestamped collision timeline, ground truth, complete uncertainty budget, source/clock attestation, and tamper-resistant reconstruction contracts. Missing required Evidence is `BLOCK`. |

Simulation Evidence at every level remains simulation-only and must not be
generalized to a physical platform. A route or vehicle policy may require
additional Evidence and thereby block a task; the level table is not permission
to weaken a stricter policy.

The first two levels retain four conjunctive motion-admission responsibilities:

1. valid, deployment-applicable geometry attestation for the complete
   motion-relevant footprint set;
2. exact-plan conservative clearance above the independently owned
   route/vehicle threshold;
3. healthy localization, TF, costmap, and runtime identity; and
4. available STOP/cancel behavior under the existing acceptance contract.

Calibration establishes geometry containment only. It does not own or relax
clearance thresholds, runtime health policy, STOP policy, approval, or task
outcome.

## Consequences

- Development and Acceptance no longer require Script Editor, an Isaac
  extension, or per-task active Stage extraction. This ADR does not itself
  remove the existing GUI exporter; migration or deprecation is separate work.
- Headless ROS Bridge/Nav2 parity is not a prerequisite for offline geometry
  calibration or for resuming the existing GUI Product Runtime path.
- The original ADR 0008 raw-geometry, collision-timeline, uncertainty, and
  anti-tamper contracts remain valid as the Certification Research profile and
  for any stricter route policy that explicitly selects them.
- Existing Motion Readiness artifacts remain historical diagnostics. They are
  not automatically upgraded into reusable attestations.
- A geometry attestation cannot authorize motion. Each motion attempt still
  requires live clearance, health, STOP/cancel, policy, and approval checks.
- Physical platforms remain unavailable or blocked under geometry policy until
  an applicable physical provenance contract exists; simulation evidence is
  never substituted.

## Implementation boundary

This amendment decides policy and ownership only. Its PR must not include a
calibration schema implementation, footprint canonicalizer, validator,
Headless extractor, Nova Carter attestation, `doctor` integration, Motion
Safety Gate behavior change, Navigation Gateway change, GUI exporter removal,
Isaac execution, or motion test.

After this ADR is approved, implementation proceeds without reopening the
three-flow architecture:

1. typed calibration schema;
2. pure footprint canonicalization;
3. pure attestation validator;
4. Headless USD geometry extractor;
5. reviewed Nova Carter simulation attestation;
6. `doctor` and startup binding;
7. Motion Safety admission convergence;
8. GUI exporter migration or deprecation.

The first three steps are pure, deterministic Python work and require no Isaac
process. Each implementation PR may refine internal structure, but it must not
move calibration into Product Runtime, make active Stage extraction a normal
task dependency, weaken complete global/local footprint verification, or apply
`isaac_usd` evidence to a physical profile.

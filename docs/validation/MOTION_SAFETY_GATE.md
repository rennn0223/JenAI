# Isaac Motion Safety Gate

This Gate answers one question before any motion request: **does one immutable,
source-bound Evidence set justify asking the operator for motion
authorization?** It never sends a goal, publishes velocity, changes initial
pose, or modifies Nav2/AMCL.

## Four required gates

| Gate | PASS requires | BLOCK examples |
|---|---|---|
| Swept footprint clearance | Exact `x/y/yaw` path, live effective footprint, conservative translation-plus-rotation interpolation bounded by half the smallest costmap cell, signed polygon-to-cell and grid-boundary separation for static lethal, live obstacle and unknown layers; inflation/inscribed cost is diagnostic only | Centre-only distance, between-sample corner sweep, insufficient observed boundary margin, physical-layer overlap, missing/duplicate layer, stale/frame-mismatched grid |
| Geometry attestation | Exact Stage path/SHA, raw collision vertices, separate scale and canonical row-major rigid affine transforms, offline-rebuilt projected base hull fully contained by the live effective Nav2 footprint rebuilt from configured polygon plus padding | Nav2 undersizing, missing prim, unrebuildable mesh/scale/transform, scene/epoch drift |
| Collision timeline | Attested source and transport, exact robot-root/monitored-prim/collision-filter coverage, scene/map/Nav2/runtime binding, ROS/host time alignment, boot/epoch binding, bounded named windows and at least one fresh raw presence observation per window; collision state is re-derived from the exact raw Boolean field | Missing/stale stream or sample, unbounded window, unaligned/ambiguous Boolean, empty unproven window, positive collision event |
| Clearance policy | Seven positive, source-bound terms and `measured - required >= 0` | Missing controller/stopping bound, zero term, arbitrary threshold |

The canonical USD transform is a flattened row-major 4x4 rigid affine matrix
applied to a column vector. Translation occupies indices 3, 7, and 11 and is
already expressed in metres. Local vertex coordinates are converted by
meters_per_unit and the separately recorded positive scale before the matrix is
applied; the matrix must not contain scale, shear, or reflection. Its last row
must be [0, 0, 0, 1].

MotionRequestBinding is immutable and content-digested. It records a unique
single-use authorization nonce; the Site, exact start and goal poses; planner,
product, and Nav2 configuration identities; scene, map, runtime, collision-filter,
boot, and simulation-epoch identities; plus bounded ROS-time and host-monotonic
validity windows. Both window endpoints and the authorization instant must stay
within the captured Evidence `max_evidence_age_ns`, and both clock windows must
contain their corresponding capture instant; a short future-shifted window cannot
replay old Evidence. Path Evidence carries the same request digest and Nav2 identity,
and its first/last poses must equal the bound start/goal. A separate admission
token binds the exact MotionRequestBinding, PathEvidence content digest, and
immutable artifact input digest.

The only public admission interface is
MotionAuthorizationNonceStore.consume_if_authorized(): a successful result
atomically consumes the nonce while checking a valid PASS artifact, an exactly
equal request, an exactly matching current RuntimeBinding, and current ROS and
host times inside both validity windows. The non-consuming predicate is private
to that transaction. The store is a process-local reference contract; a durable
Runtime must execute the same predicate and nonce consumption in one
authoritative event-store transaction. Stop→Play, runtime/scene/map change,
expiry, path change, and second consumption all fail closed.

Collision coverage is derived offline from typed raw effective-filter Evidence,
not from an opaque digest or caller Boolean. An independent typed raw USD Stage query preserves source/query identity, scene
binding, source time, count, canonical digest, completeness attestation, and every
collision-enabled counterpart prim/category entry. The human-readable counterpart
summaries are rebuilt from that enumeration and must match it exactly. For every monitored USD
collision prim, each effective filter rule must exactly cover those independent
scene inventories and enable contact reporting.
The canonical rule digest must equal the RuntimeBinding filter identity. A
positive event also requires both participants plus contact point, normal,
penetration, and impulse, with at least one participant in the monitored inventory.

The required clearance is:

```text
geometry attestation uncertainty
+ localization uncertainty
+ controller tracking bound
+ map discretization bound
+ latency distance
+ stopping distance
+ fixed product margin
```

No term may be filled with zero merely because it is unavailable. Each term
resolves to a method-specific typed, content-digested source bound directly to
runtime fingerprint, boot identity, simulation epoch, timestamp, configuration,
and a recognized derivation method. Bounds and speed magnitudes are finite and
non-negative; minimum deceleration is positive. Latency distance and stopping
distance are rebuilt from their speed-magnitude/latency or
speed-magnitude/deceleration inputs; the validator does not trust the stored term
summary.

## Artifact and CLI

Capture adapters provide a schema-v4 Evidence JSON with `result: null`. The
assembler derives the result and reserves a new output path:

```bash
uv run python scripts/isaac_motion_readiness.py assemble \
  --evidence captured-evidence.json \
  --output motion-readiness.json
```

An independent process must then rebuild the verdict:

```bash
uv run python scripts/isaac_motion_readiness.py validate \
  --artifact motion-readiness.json
```

Only `valid=true`, `failures=[]`, and `decision=PASS` permits a later request
for exact-commit motion authorization. That request must exactly equal the
artifact's immutable MotionRequestBinding and admission token. A wrong goal,
changed path, expired window, Stop→Play epoch, runtime drift, or consumed nonce
fails closed. `valid=true` with `decision=BLOCK`
means the artifact is internally trustworthy and correctly prevents motion. The CLI returns exit `0` only for valid `PASS`, exit `3` for valid `BLOCK`, and exit `2` for an invalid artifact.

Every costmap layer preserves a complete, content-digested RLE grid as raw Evidence. Hazard-cell witnesses are reconstructed from that grid; deleting or replacing a summarized cell cannot create a valid artifact. The final assembled artifact must carry a non-empty input digest.

## Current baseline decision

Dock to `map_left_down` is **BLOCKED**. Historical centre-point observations
around `0.228 m` static and `0.126 m` live clearance are not swept body
clearance; their raw artifacts remain historical diagnostics. The current
collision source lacks the source time, collision participants, and contact
data required by this contract, and controller tracking/stopping bounds are not
yet established. The Gate must report those facts instead of choosing a
convenient `0.20 m` threshold.

No first smoke route is currently recommended: `NO_SAFE_ROUTE` remains the
only honest result until one plan-only candidate has a complete PASS artifact.

## No-motion validation matrix

The repository contains four sanitized, synthetic/representative artifacts:

- `candidate-route-block.json`: candidate route blocked by unavailable live
  safety Evidence;
- `known-narrow-route-block.json`: oriented footprint overlaps a lethal cell;
- `collision-unavailable-block.json`: all geometry is safe but collision
  Evidence is unavailable;
- `footprint-mismatch-block.json`: USD collision hull exceeds Nav2 footprint.

These fixtures prove reconstruction and fail-closed behavior. They are not a
live Isaac PASS. A future no-motion live capture may replace the candidate
fixture only after code review and must preserve all blocked artifacts.

## Motion sequence after a real PASS

1. One short, high-clearance smoke route, with separate exact authorization.
2. One non-statistical R1/R2 Pilot.
3. Five predeclared paired runs.

At every stage, the first failed gate stops the sequence. No route is repeated
until a captured artifact identifies whether the cause is code, environment,
operation, or unavailable Evidence.

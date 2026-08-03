# Isaac Motion Safety Gate

This Gate answers one question before any motion request: **does one immutable,
source-bound Evidence set justify asking the operator for motion
authorization?** It never sends a goal, publishes velocity, changes initial
pose, or modifies Nav2/AMCL.

## Four required gates

| Gate | PASS requires | BLOCK examples |
|---|---|---|
| Swept footprint clearance | Exact `x/y/yaw` path, live effective footprint, signed polygon-to-cell samples plus an explicit continuous-segment motion bound; each segment subtracts translation + footprint-radius × yaw-change + numeric uncertainty from the sampled clearance and grid-boundary separation for static lethal, live obstacle and unknown layers; inflation/inscribed cost is diagnostic only | Centre-only distance, between-sample corner sweep, insufficient observed boundary margin, physical-layer overlap, missing/duplicate layer, stale/frame-mismatched grid |
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

IsaacMotionReadinessCollector performs bounded concurrent read-only collection through the platform observation port, rechecks the runtime generation after capture, and emits typed missing/unavailable Evidence as a trustworthy BLOCK. It never exposes an effectful robot operation. Runtime drift is preserved in collection_failures and produces BLOCK.

The production source is `IsaacRosReadOnlyEvidenceSource`. It accepts only the nine operations listed below through `RepositoryIsaacReadOnlyTransport`. The transport always invokes the checked-in `scripts/isaac_motion_readiness_probe.py`; callers cannot select an executable or add operations. The probe requires a clean reviewed Git revision, runs Python in isolated mode, pins imports to the reviewed repository `src`, uses `/usr/bin/git` for attestation, removes caller `PATH`/`PYTHONPATH`, freezes one bounded config snapshot for the entire collection, records entrypoint/config/environment identity, uses no shell, runs in a private process group, bounds output and time, and reaps the complete process group on every exit. Every response is validated into the exact typed Evidence model. A typed missing/stale source produces a complete reconstructible BLOCK artifact. A timeout or source exception produces a separate content-digested collection BLOCK artifact that preserves failure type and completed-operation digests without persisting private exception text; both `assemble` and `validate` preserve and verify that terminal BLOCK.

```text
runtime_binding (before/after)
motion_request
planned_path
effective_nav_footprint
usd_collision_geometry
costmap_layers
collision_timeline
clearance_budget
clearance_sources
```

No operation vocabulary contains robot actuation. The checked-in probe, wrapper, adapter, Stage exporter, and CLI are all covered by the observation-only dependency guard, which also rejects publisher creation and known motion seams. The external probe performs only `/clock`/topic observations, plan-only `ComputePathToPose`, create-once Stage-Evidence loading, and configured safety-Evidence reads. It cannot inspect an active GUI Stage from its fresh subprocess. Collision geometry is therefore exported separately, from inside the already-running Isaac process, by the checked-in `scripts/isaac_motion_readiness_stage_export.py`; the external probe then verifies the export digest, runtime/epoch/scene/Nav2 binding, and freshness before accepting it. This PR does not claim live no-motion acceptance until that two-step capture is executed.

The whole probe process and collector budgets are strictly larger than the sum of bounded ComputePathToPose acceptance/result/cancel/reconciliation phase budgets, so hard process cleanup cannot pre-empt the normal cancellation window. Collision topic discovery and message type alone do not attest the publisher; until publisher GID/node identity and offered QoS are independently bound to the Isaac collision producer, source assurance remains `unknown` and the Gate blocks.

The current repository collector is intentionally unable to produce a motion PASS from configuration claims alone. Runtime boot/epoch/fingerprint, map/Nav2/filter identities, all seven clearance sources, and four distinct costmap-layer semantics remain explicit observation limitations or `UNAVAILABLE` unless a live attested observer supplies them. A complete capture with any of those limitations is a valid, reconstructible `BLOCK`, never a false `PASS`. The combined Nav2 costmap cannot be relabelled four times as static/live/inflation/unknown evidence: each layer records a distinct source topic and a semantic attestation, and `unavailable` semantics block admission.

Capture adapters provide a schema-v5 Evidence JSON with `result: null`. Prepare the repository-owned ROS-compatible probe environment once from the clean source checkout; it is not selected by config:

```bash
uv venv --python /usr/bin/python3.12 .venv-ros
uv pip install --python .venv-ros/bin/python "pydantic==2.13.4"
source /opt/ros/jazzy/setup.bash
source /home/nvidia/IsaacSim-ros_workspaces/jazzy_ws/install/setup.bash
```

The collector records the resolved interpreter digest and injects only ROS dependency roots under `/opt/ros` and `/home/nvidia/IsaacSim-ros_workspaces`; it never inherits arbitrary `PYTHONPATH`. Before external capture, freeze the exact reviewed source closure and Stage bootstrap identity from the immutable Git revision:

```bash
uv run python scripts/isaac_motion_readiness.py prepare-stage-export \
  --config /absolute/path/to/motion-readiness-pre-export.json \
  --source-bundle /absolute/create-once/path/reviewed-source.zip
```

The preparation result prints the source-bundle path/SHA-256 and Stage-entrypoint path/SHA-256. The pre-export config sets `usd.stage_export_sha256` to `null`; external capture will reject that unbound value. In the already-running Isaac Script Editor, load the exact bootstrap bytes, verify the printed entrypoint digest, and invoke them with the verified source bundle. This avoids importing any module from the mutable worktree:

```python
import hashlib
from pathlib import Path

entrypoint = Path("/absolute/reviewed/scripts/isaac_motion_readiness_stage_export.py")
source = entrypoint.read_bytes()
assert hashlib.sha256(source).hexdigest() == "<stage_entrypoint_sha256>"
namespace = {"__name__": "reviewed_stage_bootstrap", "__file__": str(entrypoint)}
exec(compile(source, str(entrypoint), "exec"), namespace)
namespace["main"]([
    "--source-bundle", "/absolute/create-once/path/reviewed-source.zip",
    "--config", "/absolute/path/to/motion-readiness-pre-export.json",
    "--output", "/absolute/create-once/path/stage-evidence.json",
])
```

The bootstrap requires the repository containing its exact reviewed entrypoint to be clean, enumerates every tracked `src/jenai/**/*.py` at that Git HEAD, compares every ZIP member byte-for-byte with the Git object, validates the embedded commit manifest, and then holds an `O_NOFOLLOW` read descriptor while importing the Stage exporter. The exporter refuses a non-Play timeline, unreviewed/dirty source, scene digest mismatch, unsupported Mesh collision approximation, incomplete collision inventory, disabled colliders, or a reused output path. It explicitly traverses USD instance proxies and prints the create-once Stage Evidence SHA-256. Create the final capture config by copying the pre-export config and setting `usd.stage_export_sha256` to that printed digest; do not mutate a config already owned by a collector instance. Reusing, replacing, symlinking, or rebinding the export fails closed. A standalone host Python process cannot access the GUI process Stage. The external operator entry point then writes raw Evidence directly in assembler-ready form:

```bash
uv run python scripts/isaac_motion_readiness.py capture \
  --config /absolute/path/to/motion-readiness.json \
  --output captured-evidence.json
```

The local configuration is data, not executable code. It must be an absolute regular non-symlink JSON file and provides the reviewed RuntimeBinding, exact start/goal and planner, map frame, both local/global live Nav2 footprint parameter services, exact USD scene/root and create-once Stage export, four costmap topics, collision topic/type/effective subscription QoS/filter inventory, and the seven typed clearance sources/budget. The caller cannot select an executable. The repository-owned probe path is `.venv-ros/bin/python`; it must be a Python 3.12-compatible environment with the sourced ROS 2 Jazzy installation and JenAI's Pydantic dependency because the development venv may use a newer Python ABI that cannot load `rclpy`. The resolved interpreter is opened once as a regular executable, its digest is recorded, and the subprocess executes that exact descriptor rather than reopening the pathname. The config contains no secret and cannot add a ROS action, publisher, service mutation, or arbitrary command. Configuration values are never treated as proof of live identity, tracking, stopping, localization, latency, or filter state; missing live attestation remains BLOCK.

The assembler derives the result and reserves a new output path:

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

For every adjacent interpolated path segment, the artifact preserves the sampled minimum, the explicit unobserved-motion error bound, the conservative minimum, segment index, start/end poses, and the obstacle/boundary witness. PASS/BLOCK uses only the conservative minimum. Increasing sample density never removes the error-bound subtraction. The offline validator re-derives these fields.

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

The repository contains four sanitized, synthetic/representative v5 artifacts. They exercise the evaluator and collector-compatible schema; no live no-motion Isaac acceptance has been executed:

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

# JenAI Product and Code Quality Review — 2026-07-25

## Verdict

The current revision is a credible release candidate for an Isaac Sim-first
research product. It passes the repository's production type, test, coverage,
website, and packaging gates. The new semantic patrol path has a clear
workflow-first architecture and is no longer a sequence of LLM-selected
primitive navigation calls.

This review does **not** assign an unconditional “10/10.” Software quality is
defined by explicit gates and remaining evidence gaps, not by a permanent
score. The release candidate meets the gates listed below; the unresolved risks
remain visible.

## Product boundary

JenAI owns:

- natural-language mission interpretation;
- selection of a registered high-level Workflow Capability;
- approval, policy, audit, progress, and honest outcome reporting;
- re-entry into LLM reasoning for unresolved high-level events.

The deterministic workflow owns:

- loading required semantic patrol areas from the active Site Profile;
- bounded navigation retries;
- evidence capture;
- observation-coverage accounting;
- cancellation and return-home behavior;
- typed terminal results.

ROS 2 and Nav2 own path planning, local control, recovery behavior, obstacle
avoidance, and vehicle motion. The LLM does not publish low-level velocity,
steering, or joint commands.

## Architecture findings and changes

### Deep workflow module

`jenai.workflows.area_patrol.AreaPatrolWorkflow` is the core module. Its public
runtime seam contains only `navigate`, `inspect`, and `return_home`. The module
does not import ROS 2, Nav2, Isaac Sim, an LLM provider, TUI, WebUI, or CLI code.

Two real consumers validate the seam:

1. `AgentAreaPatrolRuntime`, which adapts Site Profile, NavigationGateway/Nav2,
   camera evidence, reporting, and live TUI progress.
2. In-memory fake runtimes used by domain and failure-path tests.

An architecture test rejects future dependency leaks from workflow domain code
into agent, provider, ROS, bridge, TUI, WebUI, or tool adapters.

### One model decision per normal mission

The natural-language route uses three independent intent signals (patrol,
coverage, and site) before narrowing the Agent to the semantic-area workflow.
Ordered waypoint requests remain on the general path. The dedicated Agent has
one registered workflow tool; after approval, ordinary navigation, inspection,
retry, coverage, and return-home steps do not invoke the model again.

### Coverage and review are independent

The prior report model conflated “observed” with “semantically cleared.” The
current contract treats an area as observation-covered only when every required
inspection point was reached and produced evidence. Evidence that requires
review still counts as observed and is listed separately in
`review_required_areas`. Missing evidence never counts as coverage.

### Honest vision output

The inspection prompt now asks only for concrete, visible, actionable deviations
and requires an empty anomaly list for a normal scene. Deterministic
normalization removes non-anomalies such as “safe,” “orderly,” “no people,” or
“simulated appearance,” while retaining concrete deviations such as a spill.
The historical HIL artifact is not rewritten.

### Long-task observability

The workflow emits bounded progress updates for navigation target, remaining
distance, elapsed time, recovery count, inspection/evidence capture, and
return-home. Observer failure is isolated and cannot fail the mission.

### TUI trust boundary

Model and tool text is untrusted. Final output escapes Rich markup before
rendering, preventing bracketed model output from crashing the TUI after an
approval. Trusted application panels retain intentional markup.

## Verification

| Gate | Result |
|---|---|
| Ruff lint | PASS |
| Ruff formatting | PASS, 228 files |
| Production strict mypy | PASS, 125 source files |
| Pytest | PASS, 1,048 tests |
| Overall branch coverage | PASS, 78% (gate 76%) |
| Safety-chain branch coverage | PASS, 94% (gate 90%) |
| Workflow domain branch coverage | 98% |
| Workflow Agent adapter branch coverage | 93% |
| Documentation website tests | PASS, 2 rendered-page tests |
| Documentation website production build | PASS |
| Python sdist and wheel build | PASS |
| DOCX integrity | PASS |
| Thesis PDF layout check | PASS, A4, 76 pages |

The full test collection is intentionally not included in strict mypy. The
repository's formal strict-mypy scope is production code (`src/jenai`). Applying
strict mypy to the entire historical pytest suite currently exposes 1,421
fixture/mock typing issues. Hiding those with broad `Any` or ignores would not
improve runtime confidence; test-suite typing is a separate debt-reduction
project.

## Live Isaac Sim/Nav2 evidence

The supervised natural-language TUI run
`run_eb258df6c980445582c6dc0c7e79de8d` selected one workflow tool and required
one approval. The deterministic workflow:

- visited all four required semantic inspection areas;
- preserved four camera evidence images;
- completed the third point after five Nav2 recoveries;
- returned to dock with 0.037 m position error and 0.149 rad yaw error under
  the 0.05 m / 0.15 rad arrival contract;
- completed in 1,152.420 seconds, including navigation, recovery, four local VLM
  calls, evidence capture, and return-home.

The historical report returned `requires_human_review` and incorrectly displayed
25% coverage. That result exposed the coverage/clearance defect described above.
After the fix, a one-shot reanalysis of the same final image returned no
anomalies in 33.64 seconds. This is not a second full workflow run and is not a
VLM accuracy benchmark.

## Remaining release risks

1. **No post-fix full live rerun.** Navigation itself was exercised end to end,
   while the final coverage/report and TUI fixes are regression-tested and the
   VLM filter was rechecked on one preserved frame. A second full live patrol
   remains the strongest final acceptance check.
2. **ROS sidecar coverage.** `ros_bridge.py` and
   `ros_bounded_publisher.py` require a ROS runtime and show 0% in pure Python
   CI. Their protocol/state siblings are highly covered and live HIL exercises
   the deployed path, but a self-hosted ROS coverage job would close the gap.
3. **Overall coverage is 78%, not 85%.** The safety chain is 94% and the new
   workflow path is above 90%; broad legacy CLI/UI and hardware sidecars reduce
   the total. The gate should be ratcheted upward with meaningful behavior
   tests, not exclusions.
4. **Semantic coverage is not geometric exploration.** The workflow covers
   preconfigured semantic areas and inspection points. Unknown-map frontier or
   boustrophedon coverage is future work.
5. **Docking is pose verification only in Isaac Sim.** Physical final alignment
   and charging-current feedback are not validated.
6. **Cross-platform portability is architectural.** A quadruped adapter has not
   yet provided physical evidence for the same runtime seam.
7. **Persistent event-driven re-entry is incomplete.** Normal workflow execution
   is deterministic, but durable mission handles and automatic structured
   re-entry into LLM reasoning for unresolved events remain future work.
8. **Fresh-machine adoption evidence remains partial.** The build and isolated
   wheel lifecycle are automated; a non-maintainer should still complete the
   public installation guide on a clean machine.

## Release decision

The code is suitable for a new minor release after:

1. review of the complete diff;
2. confirmation that local thesis files remain ignored;
3. version and release-note update;
4. pull-request CI on Python 3.12, 3.13, and 3.14;
5. optional second full Isaac Sim patrol if the simulator is reset and available.

The release must not claim physical docking, charging, unknown-map coverage,
cross-robot physical generalization, or statistical VLM reliability.

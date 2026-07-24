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

Model and tool text is untrusted. `OutputPanel` now builds Textual `Content`
directly instead of interpolating result data into Rich markup. This closes a
parser edge case found by the full HIL result, where a nested list plus summary
still crashed despite ordinary escaping. A regression uses the minimum failing
structured patrol result, and a live `/report task` replay rendered the complete
6,377-character receipt without terminating the TUI.

### Transient navigation readiness

A temporary failure to read the latched occupancy grid is now
`unavailable`/retryable, not a permanent site-policy block. The semantic patrol
workflow applies its existing bounded retry policy. A true site/map identity
mismatch remains fail-closed and is never retried as if it were transient.

## Verification

| Gate | Result |
|---|---|
| Ruff lint | PASS |
| Ruff formatting | PASS, 231 files |
| Production strict mypy | PASS, 127 source files |
| Pytest | PASS, 1,066 tests |
| Overall branch coverage | PASS, 79% (gate 76%) |
| Safety-chain branch coverage | PASS, 94% (gate 90%) |
| Workflow domain branch coverage | 97% |
| Workflow service adapter branch coverage | 93% |
| Documentation website tests | PASS, 3 rendered-page tests |
| Documentation website production build | PASS |
| Python sdist and wheel build | PASS |
| Isolated wheel entry points | PASS, `JenAI version` and `jenai --help` |
| Locked runtime dependency audit | PASS, no known vulnerabilities |
| DOCX integrity | PASS |
| Thesis PDF layout check | PASS, v22 DOCX integrity verified; A4 PDF, 76 pages; chapter 6 and section 6.1–6.3 TOC pages aligned with body |

The full test collection is intentionally not included in strict mypy. The
repository's formal strict-mypy scope is production code (`src/jenai`). Applying
strict mypy to the entire historical pytest suite currently exposes 1,421
fixture/mock typing issues. Hiding those with broad `Any` or ignores would not
improve runtime confidence; test-suite typing is a separate debt-reduction
project.

## Live Isaac Sim/Nav2 evidence

Both current-revision runs used the same natural-language instruction:
`巡檢目前場域的所有必要區域，每個區域保存影像證據，完成後回到 dock。`
The model selected exactly one `area_patrol_workflow_tool`, and the operator
approved exactly once. Ordinary navigation, inspection, retry, coverage
accounting, and return-home did not require further model decisions.

### Defect-discovery run

Run `run_3f195d0654744b328d4d1eaa90194563` honestly ended
`PARTIAL_SUCCESS`:

- 3/4 required areas produced image evidence (75% observation coverage);
- `map-right-down` was not sent because `/map` was transiently unavailable at
  the site-verification instant;
- return-home still succeeded at 0.034 m position error and 0.149 rad yaw error;
- the structured report was saved before the TUI final-result view raised a
  Rich `MarkupError`.

This run produced two actionable defects rather than being discarded as a
failed demo: temporary readiness was classified too harshly, and untrusted
structured output could still reach the markup parser.

### Post-fix full run

Run `run_595fe9c1430e471889549f8ee08f08c4` then completed:

- 4/4 required semantic areas and 4/4 required inspection points;
- four preserved PNG evidence images;
- 100% required observation coverage, with no unresolved or review-required
  area in this scenario;
- successful return to dock at 0.036 m position error and 0.150 rad yaw error;
- 795.197 seconds end to end, including local-model workflow selection, one
  approval, Nav2, four local VLM observations, evidence persistence, and return;
- a post-run `JenAI doctor` result of PASS for map, AMCL, scan, Nav2, live odom,
  site identity, configured domain isolation, twin graph/contact sensor, and
  the local Ollama provider.

The HIL process still used the pre-`Content` final renderer and therefore
exposed the same display exception after the report had safely persisted. The
final renderer was subsequently verified two ways: the exact minimum failing
result is a regression test, and a live TUI `/report task` replay loaded the
full persisted result and remained operational. The workflow result and the UI
acceptance are thus evidenced separately instead of hiding the crash.

## Remaining release risks

1. **ROS sidecar coverage.** `ros_bridge.py` and
   `ros_bounded_publisher.py` require a ROS runtime and show 0% in pure Python
   CI. Their protocol/state siblings are highly covered and live HIL exercises
   the deployed path, but a self-hosted ROS coverage job would close the gap.
2. **Overall coverage is 79%, not 85%.** The safety chain is 94% and the new
   workflow path is above 90%; broad legacy CLI/UI and hardware sidecars reduce
   the total. The gate should be ratcheted upward with meaningful behavior
   tests, not exclusions.
3. **Semantic coverage is not geometric exploration.** The workflow covers
   preconfigured semantic areas and inspection points. Unknown-map frontier or
   boustrophedon coverage is future work.
4. **Docking is pose verification only in Isaac Sim.** Physical final alignment
   and charging-current feedback are not validated.
5. **Cross-platform portability is architectural.** A quadruped adapter has not
   yet provided physical evidence for the same runtime seam.
6. **Persistent event-driven re-entry is incomplete.** Normal workflow execution
   is deterministic, but durable mission handles and automatic structured
   re-entry into LLM reasoning for unresolved events remain future work.
7. **Fresh-machine adoption evidence remains partial.** The build and isolated
   wheel lifecycle are automated; a non-maintainer should still complete the
   public installation guide on a clean machine.
8. **The successful live scenario is not a statistical benchmark.** One
   current-revision full patrol proves the integrated path, not VLM precision,
   navigation reliability across environments, or an incident-free operating
   duration.

## Release decision

The code is suitable for a new minor release after:

1. review of the complete diff;
2. confirmation that local thesis files remain ignored;
3. version and release-note update;
4. pull-request CI on Python 3.12, 3.13, and 3.14.

The release must not claim physical docking, charging, unknown-map coverage,
cross-robot physical generalization, or statistical VLM reliability.

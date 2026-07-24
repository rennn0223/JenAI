# ADR 0004: LLM selects deterministic robot workflows

- Status: Accepted
- Date: 2026-07-24

## Context

Calling an LLM after every navigation or inspection step creates avoidable
latency, nondeterminism, provider dependency, and ambiguous completion. A
user-specified waypoint sequence is also not equivalent to an autonomous
coverage mission: it does not prove that all required areas were inspected.

JenAI is a high-level decision agent. It must decide what goal-level capability
fits the operator's intent without taking over Nav2 path planning or low-level
robot control.

## Decision

The LLM selects one registered Workflow Capability for a goal-level task. The
selected workflow owns its normal execution sequence, bounded retries,
cancellation, coverage state, evidence collection, completion evaluation, and
return-home contract.

The first workflow is Semantic Area Patrol. It loads versioned areas and
inspection locations from the active Site Profile, calls Nav2 through the single
navigation gateway, preserves image evidence, and returns a typed coverage
report. It does not call the LLM between normal steps.

The Agent may be re-entered only for a high-level event that the workflow cannot
resolve deterministically, such as an ambiguous anomaly, conflicting evidence,
a permanently blocked required area, or a policy decision requiring a person.

Slash commands, TUI, WebUI, natural language, and future ROS interfaces are
callers of the same Workflow Capability. They must not contain independent
copies of the workflow.

JenAI and the deterministic robot runtime remain in one repository until their
typed contract is stable. Dependency boundaries are established before any
physical repository split.

## Consequences

- One LLM tool choice can start a complete, bounded patrol mission.
- Provider latency does not occur at every normal robot step.
- The workflow remains executable and testable without an LLM.
- Random known-location exploration, ordered waypoint patrol, and semantic area
  coverage are different capabilities with different completion contracts.
- Site onboarding must define semantic areas, inspection locations, and home.
- New robot backends implement the small workflow runtime seam instead of
  reimplementing coverage logic.
- Long-running mission handles and event-driven Agent re-entry remain future
  extensions of the same contract, not reasons to put normal sequencing back
  into the LLM loop.

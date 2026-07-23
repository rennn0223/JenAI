# JenAI Domain Context

JenAI is a high-level decision agent for unmanned ground vehicles. It interprets
operator intent, selects registered robot capabilities, calls existing ROS 2 or
robot APIs, and verifies the observable result. It does not replace low-level
motion control, localization, collision avoidance, or the robot's safety system.

## Ubiquitous language

### High-level decision agent

The component that turns operator intent into a bounded task, selects an
available capability, supervises its execution, and reports a verified outcome.
It decides *what* the robot should do, while existing controllers decide *how*
the robot moves.

### Capability

A registered action or observation that a robot can perform through a known
interface. A capability includes its maturity, prerequisites, completion
contract, evidence sources, and known limitations.

### Robot Capability Card

The authoritative description of a robot's identity and registered
capabilities. The agent may reason about this information, but must not claim a
capability, observation, or successful result that the card and live evidence do
not support.

### Site Profile

The versioned definition of an operating site. It binds a map identity to the
site's locations, routes, dock approaches, reference scene, and validation
evidence. A profile must be explicitly activated before its coordinates can be
used.

### Map Identity

The stable identity of the map used by an active Site Profile. Map identity is
not the map's display name alone; it includes versioned content evidence so that
coordinates from one map cannot silently be reused with another.

### Completion Contract

The task-specific conditions required before JenAI may claim completion. It
defines tolerances, required observations, and acceptable outcomes. Reaching a
navigation goal and confirming that a charger is delivering power are different
contracts.

### Task Outcome

The product-level result of a task:

- `succeeded`: the completion contract was verified.
- `arrived_unverified`: the approach pose was reached, but the final real-world
  effect cannot be observed.
- `partial`: only part of the requested task was completed.
- `endpoint_mismatch`: execution ended outside the required endpoint tolerance.
- `blocked`: a policy, prerequisite, or approval prevented execution.
- `unavailable`: a required capability or dependency was unavailable.
- `failed`: execution or verification failed.
- `cancelled`: the operator or system cancelled the task.

### Evidence

An observation used to evaluate a Completion Contract, such as an AMCL pose,
Nav2 result, laser scan, image, controller state, or charging signal. Evidence
must identify its source and must not be inferred from the requested action.

### Fast Path

A deterministic interpretation route for common, unambiguous intents. It avoids
an LLM call, but uses the same capability contracts, approval boundary,
execution interfaces, and result verification as the Agent Path.

### Agent Path

An LLM-assisted interpretation and planning route for complex or ambiguous
intents. The model may reason and choose among registered capabilities, but it
cannot create facts, coordinates, observations, or successful outcomes.

### Ground-truth evaluator

Experiment-only evaluation that compares the operational estimate against
simulation ground truth. Ground truth measures JenAI; it is never fed back into
the operational controller or used to make a failed task appear successful.

### Dock Approach

Navigation to a registered pose near a docking station. In the current Isaac Sim
reference platform this can verify pose arrival, but not final connector
alignment or charging. Its successful product outcome is therefore
`arrived_unverified`.

### Final Alignment

The future close-range docking phase that aligns the robot with a physical
connector or charging interface and verifies the resulting charge state. It is
not part of the current Dock Approach capability.

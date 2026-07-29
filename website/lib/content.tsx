import type { ReactNode } from "react";

import { Callout, CodeBlock, StatusPill, Steps } from "@/components/doc-elements";

export type DocPage = {
  title: string;
  description: string;
  eyebrow: string;
  body: ReactNode;
};

const startNav2 = `
cd /home/nvidia/JenAI
./scripts/isaac_nav2.sh restart
`;

const startJenAI = `
cd /home/nvidia/JenAI
./scripts/jenai doctor
./scripts/jenai
`;

export const docPages: Record<string, DocPage> = {
  overview: {
    title: "What is JenAI?",
    description:
      "A high-level decision agent that turns human intent into bounded, observable robot tasks.",
    eyebrow: "Get started",
    body: (
      <>
        <p>
          JenAI gives robots a high-level decision layer. An operator describes the goal in
          natural language; the Agent chooses a registered Workflow, while that deterministic
          Workflow owns coverage, bounded retries, evidence collection, completion checks, and
          the call to existing ROS 2 or robot APIs.
        </p>
        <Callout title="The product boundary" tone="success">
          JenAI decides <em>what</em> the robot should do. Nav2, motor controllers,
          localization, collision avoidance, and hardware safety still decide <em>how</em> it
          moves.
        </Callout>
        <h2>Reference platform</h2>
        <div className="spec-grid">
          <div><span>Compute</span><strong>NVIDIA DGX Spark</strong></div>
          <div><span>Simulation</span><strong>Isaac Sim 5.1</strong></div>
          <div><span>Robotics</span><strong>ROS 2 Jazzy + Nav2</strong></div>
          <div><span>Reference robot</span><strong>Nova Carter UGV</strong></div>
          <div><span>Local model</span><strong>Ollama · Qwen</strong></div>
          <div><span>Interface</span><strong>Terminal-first TUI</strong></div>
        </div>
        <h2>One decision, then a reliable workflow</h2>
        <p>
          Common unambiguous requests may use a deterministic fast path. Complex requests use
          the LLM-assisted Agent to choose a bounded Workflow. Normal navigation and inspection
          steps then run without repeatedly asking the model. Only an unresolved high-level
          event returns to the Agent. Every path shares approvals, capability contracts,
          execution gateways, evidence, and honest task outcomes.
        </p>
        <div className="flow-row">
          <div>Natural language</div><span>→</span><div>Workflow selection</div><span>→</span>
          <div>Deterministic workflow</div><span>→</span><div>ROS 2 / Nav2</div><span>→</span>
          <div>Evidence contract</div>
        </div>
      </>
    ),
  },
  "isaac-sim-quickstart": {
    title: "Isaac Sim QuickStart",
    description:
      "Use the fixed Nova Carter warehouse workflow to run one honest, verified JenAI task.",
    eyebrow: "Get started · 15–30 minutes after installation",
    body: (
      <>
        <Callout title="Before you start" tone="warning">
          This workflow controls the live Isaac Sim robot. Keep the simulator visible. Press
          Stop if the scene is not in its known start state; press Play only when ready.
        </Callout>
        <h2>1. Start the reference scene</h2>
        <Steps>
          <li>Open Isaac Sim with the ROS 2 Jazzy environment sourced.</li>
          <li>Load the ROS 2 Navigation → Nova Carter warehouse example.</li>
          <li>Confirm the ROS 2 bridge is enabled, then press <strong>Play</strong>.</li>
          <li>Keep the robot near the recorded start/dock area for the first run.</li>
        </Steps>
        <h2>2. Start Nav2</h2>
        <CodeBlock label="Terminal A">{startNav2}</CodeBlock>
        <p>
          RViz should show the map, scan, costmaps, and robot pose. If localization is visibly
          wrong, use <strong>2D Pose Estimate</strong> before continuing.
        </p>
        <h2>3. Start JenAI and run the health check</h2>
        <CodeBlock label="Terminal B">{startJenAI}</CodeBlock>
        <p>
          Continue when the map, localization, laser, NavigateToPose, and velocity subscriber
          checks pass. A provider warning is acceptable only when you plan to use deterministic
          slash commands; natural-language Agent tasks need a working model provider.
        </p>
        <h2>4. Bind the saved locations to this map</h2>
        <CodeBlock label="Terminal B">{`
JenAI site status
JenAI site map-identity
JenAI site init ~/.config/jenai/site-profile.toml \
  --site-id isaac-warehouse \
  --name "Isaac Sim Warehouse" \
  --scene "Isaac Sim 5.1.0 Warehouse"
# Review home, dock, patrol-area grouping, and required flags before activation.
JenAI site activate ~/.config/jenai/site-profile.toml
JenAI site validate
`}</CodeBlock>
        <p>
          If a valid Site Profile is already active, only run <code>site status</code> and
          <code>site validate</code>. Activation recomputes the locations-file identity; the
          imported file cannot mark itself trusted.
        </p>
        <h2>5. Inspect without moving</h2>
        <CodeBlock label="JenAI TUI">{`
幫我檢查現在機器人的位置、雷射掃描與 Nav2 狀態，不要移動機器人。
`}</CodeBlock>
        <p>
          The result should include a map-frame pose, a bounded LaserScan summary, Nav2
          readiness, and an explicit statement that no movement command was sent.
        </p>
        <h2>6. Verify saved locations</h2>
        <CodeBlock label="JenAI TUI">{`
/loc list
`}</CodeBlock>
        <p>
          The reference configuration contains four map corners and a Dock approach. Saved
          locations are coordinates, not universal place names; use them only with the matching
          Site Profile.
        </p>
        <h2>7. Run one navigation task</h2>
        <CodeBlock label="JenAI TUI">{`
請導航到 map_left_up，到達後檢查位置並告訴我誤差。
`}</CodeBlock>
        <p>
          Review the approval card and choose Yes only when the target is correct. JenAI should
          show progress immediately, call Nav2, then independently compare the terminal pose
          with the configured 5 cm / 0.15 rad reference limits.
        </p>
        <h2>8. Run the autonomous area workflow</h2>
        <CodeBlock label="JenAI TUI · natural language">{`
巡檢目前 Site Profile 中的所有必要區域，每個觀察點保存照片；
完成後回到 home，若證據不足或發現異常就誠實標記需要人工確認。
`}</CodeBlock>
        <p>
          The Agent selects <code>area_patrol_workflow_tool</code> once. The Workflow—not the
          LLM—tracks required coverage, orders areas, calls Nav2, bounds retries, captures image
          evidence, returns home, and writes the durable report. No dedicated slash command is
          required.
        </p>
        <h2>9. Read the outcome, not just “Done”</h2>
        <ul>
          <li><StatusPill tone="good">succeeded</StatusPill> completion evidence passed.</li>
          <li><StatusPill tone="warn">partial_success</StatusPill> only part of the required work was verified.</li>
          <li><StatusPill tone="warn">requires_human_review</StatusPill> evidence or an anomaly needs review.</li>
          <li><StatusPill tone="bad">endpoint_mismatch</StatusPill> Nav2 ended outside JenAI limits.</li>
          <li><StatusPill tone="warn">arrived_unverified</StatusPill> approach reached; physical effect unobserved.</li>
          <li><StatusPill tone="neutral">blocked</StatusPill> a prerequisite or policy prevented motion.</li>
        </ul>
        <Callout title="Stop is always available">
          Press Esc or run <code>/stop</code>. Stopping and monitoring do not depend on the LLM.
        </Callout>
      </>
    ),
  },
  "decision-boundary": {
    title: "Decision boundary",
    description: "Separate high-level intent from low-level motion and safety control.",
    eyebrow: "Core concepts",
    body: (
      <>
        <p>
          JenAI is an orchestrator, not a replacement controller. It selects a registered
          high-level capability such as semantic-area patrol, state inspection, navigation, or
          dock approach. A Workflow owns normal task sequencing and may call smaller Skills;
          the selected robot API owns the lower-level implementation.
        </p>
        <div className="boundary-stack">
          <div><strong>Operator intent</strong><span>Natural language or slash shortcut</span></div>
          <div><strong>JenAI decision layer</strong><span>Interpret the goal and select a registered Workflow</span></div>
          <div><strong>Workflow layer</strong><span>Execute, monitor, retry, collect evidence, and evaluate completion</span></div>
          <div><strong>Robot APIs</strong><span>Nav2, ROS 2 topics/actions, perception interfaces</span></div>
          <div><strong>Control & safety</strong><span>Localization, planners, controllers, watchdog, hardware limits</span></div>
        </div>
        <h2>Non-negotiable rules</h2>
        <ul>
          <li>The model cannot invent a capability, coordinate, observation, or successful result.</li>
          <li>The LLM is not polled for each normal navigation or inspection step.</li>
          <li>Motion uses the single Navigation Gateway and the same approval boundary.</li>
          <li>LLM failure cannot disable stop, cancel, or monitoring.</li>
          <li>Simulation ground truth evaluates JenAI; it never corrects JenAI’s operational answer.</li>
        </ul>
      </>
    ),
  },
  "capability-card": {
    title: "Robot Capability Card",
    description: "The robot’s honest, machine-readable self introduction.",
    eyebrow: "Core concepts",
    body: (
      <>
        <p>
          The Capability Card is the authoritative answer to “who are you and what can you do?”
          It records identity, platform type, deployment mode, capabilities, maturity,
          completion evidence, and limitations.
        </p>
        <CodeBlock label="Conceptual example">{`
Robot: JenAI Ackermann UGV
Capability: dock_approach
Maturity: implemented_unvalidated
Evidence: Nav2 result + terminal pose
Success outcome: arrived_unverified
Limitation: charging state is unavailable
`}</CodeBlock>
        <h2>Platform inheritance</h2>
        <p>
          The reference Ackermann and differential UGV profiles register navigation workflows.
          A quadruped does not inherit those claims merely because it also uses ROS 2. It must
          explicitly register and validate its own motion capabilities.
        </p>
        <Callout title="LLM freedom with factual limits" tone="success">
          The model may reason creatively about the operator’s goal, but the Capability Card
          defines which actions and claims exist.
        </Callout>
      </>
    ),
  },
  "task-outcomes": {
    title: "Task outcomes",
    description: "A completed process is not automatically a successful robot task.",
    eyebrow: "Core concepts",
    body: (
      <>
        <p>
          Run lifecycle answers “did the process finish?” Task outcome answers “was the
          requested effect verified?” JenAI stores both, so reports never infer success from a
          normal process exit.
        </p>
        <div className="outcome-table">
          <div><code>succeeded</code><span>The completion contract passed.</span></div>
          <div><code>arrived_unverified</code><span>Approach reached; final effect cannot be observed.</span></div>
          <div><code>partial</code><span>Only part of the requested work completed.</span></div>
          <div><code>endpoint_mismatch</code><span>Terminal pose exceeded the required tolerance.</span></div>
          <div><code>blocked</code><span>Policy, approval, site, or prerequisite prevented execution.</span></div>
          <div><code>unavailable</code><span>A required capability or dependency was absent.</span></div>
          <div><code>failed</code><span>Execution or verification failed.</span></div>
          <div><code>cancelled</code><span>The operator or system cancelled the task.</span></div>
        </div>
      </>
    ),
  },
  "site-profiles": {
    title: "Site Profiles",
    description: "Keep map-frame assets attached to the environment where they are valid.",
    eyebrow: "Core concepts",
    body: (
      <>
        <p>
          Locations, routes, dock approaches, and image baselines are meaningful only on the
          map where they were recorded. An active Site Profile binds them to a SHA-256 identity
          of the complete ROS OccupancyGrid and its geometry.
        </p>
        <CodeBlock label="Create an import document">{`
JenAI site map-identity
JenAI site init ~/.config/jenai/site-profile.toml \
  --site-id isaac-warehouse \
  --name "Isaac Sim Warehouse" \
  --scene "Isaac Sim 5.1.0 Warehouse"
`}</CodeBlock>
        <CodeBlock label="Site Profile excerpt">{`
[site]
site_id = "isaac-warehouse"
display_name = "Isaac Sim Warehouse"
version = "1"
map_sha256 = "<copy the live SHA-256 from site map-identity>"
map_frame = "map"
reference_scene = "Isaac Sim 5.1.0 Warehouse"
locations_path = "locations.toml"
validated_routes = ["map_left_up", "map_right_up", "map_left_down", "map_right_down", "dock"]
home_location = "dock"
dock_location = "dock"
validation_evidence = ["docs/validation/ISAAC_HIL_ACCEPTANCE.md"]

[[site.patrol_areas]]
area_id = "upper_left"
display_name = "Upper-left inspection area"
inspection_locations = ["map_left_up"]
required = true
optional_inspection_locations = ["map_right_up"]
`}</CodeBlock>
        <h2>Activate and verify</h2>
        <CodeBlock label="Terminal">{`
JenAI site activate ~/.config/jenai/site-profile.toml
JenAI site validate
`}</CodeBlock>
        <p>
          Activation recomputes <code>locations_sha256</code> and validates every referenced
          route, home, dock, required inspection location, and optional inspection location.
          Required and optional lists may not overlap. Imported <code>active</code>,
          <code>validated</code>, or locations-hash self-claims are never trusted.
        </p>
        <Callout title="Mismatch behavior" tone="warning">
          If ROS publishes a different map, JenAI blocks navigation before the goal reaches
          Nav2. Validate and explicitly activate a new profile instead of reusing old
          coordinates.
        </Callout>
      </>
    ),
  },
  "inspect-robot": {
    title: "Inspect robot state",
    description: "Collect deterministic live evidence without commanding motion.",
    eyebrow: "Workflows",
    body: (
      <>
        <CodeBlock label="Natural language">{`
幫我檢查目前位置、雷射掃描與 Nav2 狀態，不要移動機器人。
`}</CodeBlock>
        <p>
          JenAI reads the current map pose, a bounded scan summary, and Nav2 prerequisites. The
          final summary is generated from recorded tool values, not rewritten as model facts.
        </p>
        <h2>Expected evidence</h2>
        <ul>
          <li>Pose source and map-frame x, y, yaw.</li>
          <li>Laser angle, range, sample, and finite-return summary.</li>
          <li>Map, localization, scan, NavigateToPose, and velocity subscriber readiness.</li>
          <li>An explicit “no movement command sent” statement.</li>
        </ul>
      </>
    ),
  },
  navigate: {
    title: "Navigate to a location",
    description: "Resolve a registered place, approve motion, and verify the endpoint.",
    eyebrow: "Workflows",
    body: (
      <>
        <CodeBlock label="Save the current pose">{`
/loc add here Inspection Point
`}</CodeBlock>
        <CodeBlock label="Natural-language task">{`
請帶我到 Inspection Point，到達後核對位置與朝向。
`}</CodeBlock>
        <p>
          JenAI resolves the place from the active location file, previews the action, waits
          for approval, checks the active Site Profile, calls Nav2, and verifies terminal
          position and yaw.
        </p>
        <Callout title="Precision contract">
          The Isaac reference profile uses 0.05 m position and 0.15 rad yaw limits. Nav2’s own
          goal checker must use compatible tolerances; JenAI’s check is an independent
          verifier, not a substitute controller.
        </Callout>
      </>
    ),
  },
  explore: {
    title: "Explore known locations",
    description: "Bounded, low-repeat navigation over saved places—not frontier SLAM.",
    eyebrow: "Workflows",
    body: (
      <>
        <CodeBlock label="Natural language">{`
在已儲存地點之間巡邏三分鐘，最多兩個目標，連續失敗兩次就停止。
`}</CodeBlock>
        <CodeBlock label="Reproducible shortcut">{`
/explore 3m goals=2 failures=2 seed=5
`}</CodeBlock>
        <p>
          The selector favors least-visited eligible locations. Every goal still passes through
          the normal Navigation Gateway, approvals, active-site check, Nav2 execution, and
          endpoint verification.
        </p>
        <Callout title="What this is not" tone="warning">
          This workflow does not discover unknown free space and does not build a SLAM map.
        </Callout>
      </>
    ),
  },
  "area-patrol": {
    title: "Semantic area patrol",
    description: "Cover every required Site Profile area with bounded execution and evidence.",
    eyebrow: "Workflows",
    body: (
      <>
        <p>
          Explicit waypoint patrol follows a route chosen by the user. Known-location
          exploration samples registered places with a time or goal limit. Semantic area patrol
          instead owns a completion contract: every required configured area must be inspected,
          unresolved work must be reported, and the robot returns to the configured home.
        </p>
        <div className="outcome-table">
          <div><code>Waypoint patrol</code><span>User chooses the ordered places.</span></div>
          <div><code>Random exploration</code><span>System samples eligible saved locations within a bound.</span></div>
          <div><code>Area patrol</code><span>Workflow covers all required semantic areas and proves completion.</span></div>
        </div>
        <h2>Prerequisites</h2>
        <ul>
          <li>An active, validated Site Profile with <code>patrol_areas</code> and <code>home_location</code>.</li>
          <li>Every inspection location exists in the profile-bound locations file.</li>
          <li>Nav2, localization, and the configured camera topic are available.</li>
        </ul>
        <h2>Ask in natural language</h2>
        <CodeBlock label="JenAI TUI">{`
巡檢目前場域的所有必要區域，每個觀察點保存照片；
完成後回到 home，證據不足或有異常時要求人工確認。
`}</CodeBlock>
        <p>
          The Agent selects the registered <code>area_patrol_workflow_tool</code> once. It does
          not choose every waypoint in a token-by-token loop. The durable Workflow can continue
          normal steps even when the model is not consulted again.
        </p>
        <h2>What the Workflow owns</h2>
        <Steps>
          <li>Load required and optional areas from the active Site Profile.</li>
          <li>Create a deterministic coverage order from registered inspection locations.</li>
          <li>Navigate through the single approval and Navigation Gateway boundary.</li>
          <li>Capture a distinct image for every inspection attempt.</li>
          <li>Apply bounded retries and preserve blocked or unresolved areas.</li>
          <li>Return to the configured home when the coverage attempt finishes.</li>
          <li>Write an immutable report whose status follows the completion evidence.</li>
        </Steps>
        <h2>Honest final states</h2>
        <div className="outcome-table">
          <div><code>succeeded</code><span>All required areas, evidence, and return-home contract passed.</span></div>
          <div><code>partial_success</code><span>Some requested work completed, but the full contract did not.</span></div>
          <div><code>requires_human_review</code><span>An anomaly or missing evidence needs a person.</span></div>
          <div><code>failed</code><span>The workflow could not complete its required work.</span></div>
          <div><code>aborted</code><span>The operator or system stopped the workflow.</span></div>
        </div>
        <h2>Reports and images</h2>
        <p>
          JSON reports are stored under <code>~/.config/jenai/reports/area-patrol-*.json</code>.
          Captures use distinct <code>evidence-*.png</code> files in the same managed directory,
          so export, retention, and hardening rules cover both the decision trace and its images.
        </p>
        <Callout title="No invented inspection" tone="warning">
          A saved image is evidence that a frame was captured—not proof that the scene is normal.
          Until change detection or a reviewed perception contract exists, JenAI records the
          observation and reports uncertainty instead of inventing “no anomaly.”
        </Callout>
      </>
    ),
  },

  "dock-approach": {
    title: "Dock approach",
    description: "Reach the saved approach pose without claiming an unobserved charge state.",
    eyebrow: "Workflows",
    body: (
      <>
        <CodeBlock label="JenAI TUI">{`
/dock
`}</CodeBlock>
        <p>
          The current reference capability navigates to a registered Dock approach pose and
          verifies terminal pose. Isaac Sim does not expose charging engagement for this
          setup, so the honest outcome is <code>arrived_unverified</code>.
        </p>
        <Callout title="Not yet implemented" tone="warning">
          Close-range connector alignment, charging-current evidence, and retry/recovery are
          future capabilities. “Arrived at Dock” must never be rewritten as “charging.”
        </Callout>
      </>
    ),
  },
  commands: {
    title: "Command reference",
    description: "Natural language is primary; slash commands make common actions reproducible.",
    eyebrow: "Reference",
    body: (
      <>
        <div className="command-grid">
          <div><code>/doctor</code><span>Check ROS 2, Nav2, provider, site, and storage readiness.</span></div>
          <div><code>JenAI site status</code><span>Show the active profile and trust state.</span></div>
          <div><code>JenAI site map-identity</code><span>Read the live occupancy-map SHA-256 and geometry.</span></div>
          <div><code>JenAI site activate FILE</code><span>Validate, fingerprint, and explicitly activate one profile.</span></div>
          <div><code>JenAI site init [FILE]</code><span>Create an inactive, reviewable draft from the live map and saved locations.</span></div>
          <div><code>JenAI site validate</code><span>Recheck the active map and all bound assets.</span></div>
          <div><code>/loc list</code><span>List registered locations without aliases noise.</span></div>
          <div><code>/route …</code><span>Preview and execute a named Nav2 route.</span></div>
          <div><code>/explore …</code><span>Run a bounded known-location exploration.</span></div>
          <div><code>/dock</code><span>Navigate to the Dock approach contract.</span></div>
          <div><code>/stop</code><span>Cancel motion and send zero velocity without approval.</span></div>
          <div><code>/report task</code><span>Inspect durable outcome receipts and evidence.</span></div>
          <div><code>/help</code><span>Open the current in-product command catalog.</span></div>
        </div>
        <p>
          Use natural language when the goal requires interpretation. Use slash commands when
          teaching, debugging, repeating an experiment, or avoiding unnecessary model latency.
          Semantic area patrol intentionally has no dedicated slash command: the Agent selects
          the same registered Workflow that other interfaces can call, so task logic never lives
          in the TUI parser.
        </p>
      </>
    ),
  },
  configuration: {
    title: "Configuration",
    description: "Keep provider, vehicle, safety, precision, and site facts explicit.",
    eyebrow: "Reference",
    body: (
      <>
        <CodeBlock label="Reference vehicle excerpt">{`
route_adapter = "nav2"

[vehicle]
type = "diff"
domain_id = 20
cmd_vel_topic = "/cmd_vel"
camera_topic = "/front_stereo_camera/left/image_raw"
max_linear = 0.8
max_angular = 1.0
arrival_position_tolerance_m = 0.05
arrival_yaw_tolerance_rad = 0.15
nav_timeout_s = 240.0

[twin]
enabled = true
domain_id = 0
`}</CodeBlock>
        <p>
          The physical-vehicle domain is documented as 20 while the Isaac twin uses 0. JenAI’s
          current command graph still follows the process ROS_DOMAIN_ID; keep launch
          environments explicit.
        </p>
        <h2>Configuration ownership</h2>
        <ul>
          <li><code>~/.config/jenai/config.toml</code> — non-secret product settings.</li>
          <li><code>~/.config/jenai/.env</code> — provider credentials, mode 0600.</li>
          <li><code>~/.config/jenai/locations.toml</code> — site-specific named poses.</li>
          <li><code>~/.config/jenai/site-profile.toml</code> — import document for explicit site activation.</li>
        </ul>
      </>
    ),
  },
  acceptance: {
    title: "Validation & acceptance",
    description: "Release claims require repeatable evidence, not one successful demo.",
    eyebrow: "Reference",
    body: (
      <>
        <h2>Release gate</h2>
        <ul>
          <li>Unit, integration, lint, type, install, and documentation checks pass.</li>
          <li>Fixed-scenario navigation runs meet declared endpoint tolerances.</li>
          <li>No false success when evidence is missing or contradictory.</li>
          <li>Natural-language safety cases preserve approval, stop, and capability boundaries.</li>
          <li>Every release-gate artifact records JenAI version, model, Site Profile, and evidence source.</li>
        </ul>
        <h2>Operational vs experimental truth</h2>
        <p>
          AMCL/Nav2 feedback decides the task outcome. Isaac ground truth may measure error in
          an experiment, but it cannot be fed back to make the operational task look better.
        </p>
      </>
    ),
  },
  troubleshooting: {
    title: "Troubleshooting",
    description: "Start with deterministic prerequisites before blaming the Agent.",
    eyebrow: "Reference",
    body: (
      <>
        <div className="trouble-list">
          <section><h2>Map or localization warning</h2><p>Confirm Isaac is playing, `/map` is latched, AMCL is active, and the initial pose is credible.</p></section>
          <section><h2>Nav2 exists but the robot does not move</h2><p>Verify the controller subscribes to the real odometry topic and `/cmd_vel` reaches the vehicle graph.</p></section>
          <section><h2>Goal succeeds “near” the point</h2><p>Align the Nav2 goal checker and DWB controller tolerances, restart Nav2 fully, then rely on JenAI’s endpoint verifier.</p></section>
          <section><h2>Laser has frequency but poor coverage</h2><p>Enable RTX LiDAR Publish Full Scan and validate finite-return coverage, not only topic Hz.</p></section>
          <section><h2>Natural language is slow</h2><p>Check local-model load time, prompt history size, tool waits, and provider health. Common unambiguous intents can use the deterministic fast path.</p></section>
          <section><h2>Site map mismatch</h2><p>Do not bypass the guard. Activate the correct map or validate a new Site Profile and re-record its locations.</p></section>
        </div>
        <Callout title="When in doubt">
          Run <code>JenAI doctor</code>, capture the exact failing check, and keep the simulator
          stopped until map, localization, scan, and controller prerequisites agree.
        </Callout>
      </>
    ),
  },
  "future-work": {
    title: "Future work",
    description: "Planned product layers are recorded without being presented as current features.",
    eyebrow: "Reference",
    body: (
      <>
        <ul className="future-list">
          <li><strong>Final docking</strong><span>Close-range alignment and verified charge engagement.</span></li>
          <li><strong>Quadruped motion integration</strong><span>Read-only NXDog observation exists; motion still requires explicit vendor contracts, capability registration, and physical validation.</span></li>
          <li><strong>New-site onboarding</strong><span>Guided map capture, profile validation, and explicit activation.</span></li>
          <li><strong>Visual change detection</strong><span>Compare managed baseline/current images and add VLM-assisted review with factual provenance.</span></li>
          <li><strong>Agent latency</strong><span>Smaller prompts, model routing, caching, and broader deterministic intent coverage.</span></li>
          <li><strong>User study</strong><span>A five-person exploratory usability pilot; not a statistical efficacy claim.</span></li>
        </ul>
      </>
    ),
  },
};

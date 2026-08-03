"""Architecture rules enforced as tests.

The rules live in docs/ARCHITECTURE.md; these tests make violating them
a red CI instead of a code-review hope:

1. The reflex/safety layer must work with the LLM stack dead — so its modules
   may not even import it.
2. Everything above the vehicle profile must stay vehicle-agnostic — no
   vehicle words outside the profile itself.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

from jenai.adapters.nxdog import NXDOG_READ_ONLY_ENDPOINTS
from jenai.agent.specialists import build_supervisor_agent
from jenai.capabilities import build_robot_capability_card
from jenai.config.store import build_minimal_config
from jenai.schemas.models import TaskOutcome

SRC = Path(__file__).resolve().parents[2] / "src" / "jenai"
ROOT = SRC.parents[1]

# Rule 1: modules that must never depend on an LLM (or the network stack it
# implies). The daemon *runner* wires in PerceptionLoop (decision-layer input),
# so the enforced set is the reflex core: engine, bridge, safety, twin gate.
_REFLEX_MODULES = [
    "bridge/client.py",
    "bridge/ros_bridge.py",
    "bridge/_avoidance.py",
    "bridge/_navigation_state.py",
    "bridge/_safety_order.py",
    "bridge/_watchdog.py",
    "daemon/engine.py",
    "tools/safety.py",
    "twin/gate.py",
]
_LLM_IMPORT_PREFIXES = ("openai", "litellm", "agents", "jenai.providers", "jenai.agent")
_WORKFLOW_FORBIDDEN_IMPORT_PREFIXES = (
    "agents",
    "openai",
    "rclpy",
    "jenai.adapters",
    "jenai.agent",
    "jenai.bridge",
    "jenai.providers",
    "jenai.tools",
    "jenai.tui",
    "jenai.webui",
)

# Rule 2: layers above the vehicle profile must not name a vehicle. The
# profile itself (config/models.py) and the bridge-side message-family clamp
# (ros2_core.py mentions Ackermann message shapes) are the only exemptions.
_VEHICLE_WORDS = re.compile(r"ackermann|quadruped|leatherback|go2|unitree", re.IGNORECASE)
_VEHICLE_AGNOSTIC_DIRS = ["agent", "tui", "webui", "mcp_server", "daemon", "state", "twin"]
_VEHICLE_AGNOSTIC_FILES = [
    "tools/skills.py",
    "tools/mission_core.py",
    "tools/route_core.py",
    "tools/perception.py",
]
_VENDOR_NEUTRAL_COMMAND_DIRS = (
    "agent",
    "tui",
    "webui",
    "mcp_server",
    "cli",
    "daemon",
    "tools",
    "workflows",
)
_VENDOR_NEUTRAL_COMMAND_FILES = ("capabilities.py",)
_NXDOG_VENDOR_IMPORT_PREFIXES = ("nxnav_msgs", "nxdog_interfaces")
_NXDOG_VENDOR_COUPLING_LITERALS = frozenset(
    {
        "/navigate",
        "/pause",
        "/resume",
        "/set_cmd_vel",
        "/cmd_vel_low",
        "/cmd_vel_mid",
        "/cmd_vel_high",
        "/nxnav/avoidance_enabled",
        "/initialpose",
        "/nxnav/compute_prm_path",
        "/nxnav/switch_map",
        "/nxnav/set_map",
        "/set_initialpose",
        "/charging",
        "/auto_charging_stop",
        "/set_sport_action",
        "/nxnav/navigate_to_pose",
        "/nxdog/sport",
        "/nxdog/cmd_vui",
        "/nxdog/auto_charging_cmd",
        "/nxdog/auto_charging_result",
        "NxNavClient",
        "NxDogPlatformClient",
        "nxnav_msgs",
        "nxdog_interfaces",
        "JENAI_NXDOG_API_URL",
        ":5088",
    }
)

_DIRECT_NAV_SEND_SOURCE_ALLOWLIST = (
    ("acceptance/nav_differential_runner.py", "_ObservedNavBridge.nav_send"),
    ("acceptance/nav_differential_runner.py", "_record_live_preflight"),
    ("acceptance/nav_differential_runner.py", "_run_r1"),
    ("bridge/_protocol.py", "<module>"),
    ("tools/nav_live.py", "_dispatch_navigation"),
    ("twin/gate.py", "TwinGate._execute_twin_goal"),
)
_NAV_DIFFERENTIAL_RUNNER_MODULE = "jenai.acceptance.nav_differential_runner"
_NAV_DIFFERENTIAL_RUNNER_IMPORTER_ALLOWLIST = frozenset(
    {
        "scripts/isaac_nav_differential.py",
    }
)
_MOTION_SAFETY_MODULE_FAMILY = "jenai.acceptance.motion_safety"
_MOTION_SAFETY_IMPORTER_ALLOWLIST = frozenset(
    {
        "scripts/isaac_motion_readiness.py",
        "scripts/isaac_motion_readiness_probe.py",
        "scripts/isaac_motion_readiness_stage_export.py",
        "src/jenai/acceptance/motion_safety_cli.py",
        "src/jenai/acceptance/motion_safety_capture.py",
        "src/jenai/acceptance/motion_safety_isaac.py",
        "src/jenai/acceptance/motion_safety_probe.py",
        "src/jenai/acceptance/motion_safety_stage_export.py",
    }
)
_MOTION_SAFETY_FORBIDDEN_SEAMS = (
    "nav_send",
    "cmd_vel",
    "NavigationGateway",
    "RosBridgeClient",
    "create_publisher",
)

# `/stop` is deliberately not a vendor literal here: it is JenAI's approved,
# provider-free high-level safety command in the TUI, WebUI, and CLI. The known
# NXDog HTTP adapter remains read-only, excludes `/stop`, and is Doctor-scoped
# by `test_nxdog_adapter_stays_observation_only_and_doctor_scoped`.


def _imports_of(path: Path, *, package_root: Path = SRC) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module_parts = node.module.split(".") if node.module else []
            if node.level:
                relative = path.resolve().relative_to(package_root.resolve())
                package_parts = [package_root.name, *relative.parent.parts]
                parents_to_drop = node.level - 1
                if parents_to_drop >= len(package_parts):
                    raise AssertionError(f"invalid relative import in {path}: level={node.level}")
                if parents_to_drop:
                    package_parts = package_parts[:-parents_to_drop]
                import_base = ".".join((*package_parts, *module_parts))
            else:
                import_base = node.module or ""
            if import_base:
                found.append(import_base)
            found.extend(
                f"{import_base}.{alias.name}" if import_base else alias.name
                for alias in node.names
                if alias.name != "*"
            )
    return found


def _direct_importers_of_differential_runner(
    *, source_root: Path = SRC, repository_root: Path = ROOT
) -> set[str]:
    """Return every production source file that directly imports the ADR 0007 runner."""

    candidates = [
        *source_root.rglob("*.py"),
        *(repository_root / "scripts").glob("*.py"),
    ]
    importers: set[str] = set()
    for path in candidates:
        imported_names = _imports_of(path, package_root=source_root)
        if any(
            name == _NAV_DIFFERENTIAL_RUNNER_MODULE
            or name.startswith(f"{_NAV_DIFFERENTIAL_RUNNER_MODULE}.")
            for name in imported_names
        ):
            importers.add(path.relative_to(repository_root).as_posix())
    return importers


def _direct_importers_of_motion_safety_family(
    *, source_root: Path = SRC, repository_root: Path = ROOT
) -> set[str]:
    """Return production files that directly import any ADR 0008 module."""

    candidates = [
        *source_root.rglob("*.py"),
        *(repository_root / "scripts").glob("*.py"),
    ]
    importers: set[str] = set()
    for path in candidates:
        imported_names = _imports_of(path, package_root=source_root)
        if any(
            name == _MOTION_SAFETY_MODULE_FAMILY
            or name.startswith(
                (
                    f"{_MOTION_SAFETY_MODULE_FAMILY}_",
                    f"{_MOTION_SAFETY_MODULE_FAMILY}.",
                )
            )
            for name in imported_names
        ):
            importers.add(path.relative_to(repository_root).as_posix())
    return importers


def _motion_safety_observation_violations(
    paths: tuple[Path, ...],
) -> set[tuple[str, str]]:
    """Return forbidden motion seams present in observation-only entrypoints."""

    return {
        (path.as_posix(), forbidden)
        for path in paths
        for forbidden in _MOTION_SAFETY_FORBIDDEN_SEAMS
        if forbidden in path.read_text(encoding="utf-8")
    }


def _string_literals_of(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


class _ScopedAttributeCallVisitor(ast.NodeVisitor):
    """Collect one attribute call together with its lexical class/function scope."""

    def __init__(self, attribute: str) -> None:
        self._attribute = attribute
        self._scope: list[str] = []
        self.calls: list[tuple[str, int]] = []

    def _visit_scope(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scope(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scope(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr == self._attribute:
            self.calls.append((".".join(self._scope) or "<module>", node.lineno))
        self.generic_visit(node)


def _scoped_attribute_calls(path: Path, attribute: str) -> list[tuple[str, int]]:
    visitor = _ScopedAttributeCallVisitor(attribute)
    visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
    return visitor.calls


def _is_nxdog_vendor_coupling_literal(value: str) -> bool:
    for literal in _NXDOG_VENDOR_COUPLING_LITERALS:
        if not literal.startswith("/") and literal in value:
            return True
        if value == literal or value.endswith(literal):
            return True
        if f"{literal}?" in value or f"{literal}/" in value:
            return True
    return False


def test_reflex_layer_never_imports_the_llm_stack() -> None:
    violations: list[str] = []
    for rel in _REFLEX_MODULES:
        path = SRC / rel
        assert path.is_file(), f"iron-rule module moved? {rel}"
        violations.extend(
            f"{rel} imports {name}"
            for name in _imports_of(path)
            if name.startswith(_LLM_IMPORT_PREFIXES)
        )
    assert not violations, (
        "Reflex/safety layer must survive a dead LLM — remove these imports:\n"
        + "\n".join(violations)
    )


def test_import_scanner_keeps_qualified_from_import_names(tmp_path: Path) -> None:
    fixture = tmp_path / "qualified_import.py"
    fixture.write_text("from jenai.adapters import nxdog\n", encoding="utf-8")

    assert "jenai.adapters.nxdog" in _imports_of(fixture)


def test_import_scanner_resolves_relative_import_names(tmp_path: Path) -> None:
    package_root = tmp_path / "src" / "jenai"
    parent_fixture = package_root / "tui" / "parent_import.py"
    parent_fixture.parent.mkdir(parents=True)
    parent_fixture.write_text("from ..adapters import nxdog\n", encoding="utf-8")
    local_fixture = package_root / "adapters" / "local_import.py"
    local_fixture.parent.mkdir(parents=True)
    local_fixture.write_text("from . import nxdog\n", encoding="utf-8")

    assert "jenai.adapters.nxdog" in _imports_of(parent_fixture, package_root=package_root)
    assert "jenai.adapters.nxdog" in _imports_of(local_fixture, package_root=package_root)


def test_nxdog_adapter_stays_observation_only_and_doctor_scoped() -> None:
    """NXDog cannot silently become an Agent, UI, MCP, or Workflow motion seam."""

    expected = (
        "/nav_health",
        "/get_ready_flag",
        "/current_map",
        "/odom",
        "/velocity",
        "/is_charging",
    )
    assert NXDOG_READ_ONLY_ENDPOINTS == expected
    assert not set(expected).intersection(
        {
            "/navigate",
            "/stop",
            "/pause",
            "/resume",
            "/set_cmd_vel",
            "/set_initialpose",
            "/map",
            "/charging",
            "/auto_charging_stop",
            "/set_sport_action",
        }
    )

    allowed_importers = {"doctor/nxdog.py"}
    actual_importers: set[str] = set()
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(SRC).as_posix()
        if relative == "adapters/nxdog.py":
            continue
        if "jenai.adapters.nxdog" in _imports_of(path):
            actual_importers.add(relative)
    assert actual_importers == allowed_importers


def test_command_layers_cannot_embed_nxdog_vendor_interfaces() -> None:
    """Command layers submit Capabilities and never bind vendor operations."""

    violations: list[str] = []
    files = [SRC / relative for relative in _VENDOR_NEUTRAL_COMMAND_FILES]
    for directory in _VENDOR_NEUTRAL_COMMAND_DIRS:
        files.extend((SRC / directory).rglob("*.py"))
    for path in files:
        relative = path.relative_to(SRC).as_posix()
        violations.extend(
            f"{relative}: imports {name}"
            for name in _imports_of(path)
            if name.startswith(_NXDOG_VENDOR_IMPORT_PREFIXES)
        )
        violations.extend(
            f"{relative}:{lineno}: embeds {literal!r}"
            for lineno, literal in _string_literals_of(path)
            if _is_nxdog_vendor_coupling_literal(literal)
        )
    assert not violations, (
        "NXDog vendor interfaces must stay behind the future Robot Runtime "
        "and NavigationGateway; expose typed platform-neutral "
        "Capabilities instead:\n" + "\n".join(violations)
    )


def test_nxdog_runtime_docs_preserve_the_internal_adapter_seam() -> None:
    protocol = (
        ROOT / "docs" / "integrations" / "nxdog" / "ROBOT_RUNTIME_PROTOCOL_DRAFT.md"
    ).read_text(encoding="utf-8")
    internal_section = protocol.split("## Capability Executor and internal ports", maxsplit=1)[
        1
    ].split("## Startup reconciliation", maxsplit=1)[0]

    for operation in ("snapshot(", "prepare(", "execute(", "stop("):
        assert operation in internal_section


def test_nxdog_physical_plan_uses_every_authoritative_task_outcome() -> None:
    plan = (ROOT / "docs" / "integrations" / "nxdog" / "PHYSICAL_ACCEPTANCE_PLAN.md").read_text(
        encoding="utf-8"
    )
    missing = sorted(outcome.value for outcome in TaskOutcome if f"`{outcome.value}`" not in plan)

    assert not missing, f"NXDog physical plan omits TaskOutcome values: {missing}"


def test_nxdog_assessment_does_not_preassign_the_next_release_version() -> None:
    assessment = (ROOT / "docs" / "integrations" / "nxdog" / "REPOSITORY_ASSESSMENT.md").read_text(
        encoding="utf-8"
    )

    assert "Published release baseline（completed）" in assessment
    assert "Planned release baseline" not in assessment
    assert "目前提議版本" not in assessment
    assert "下一個 release 版本在功能" in assessment


def test_workflow_domain_is_independent_of_llm_ros_and_user_interfaces() -> None:
    """Workflow logic stays executable through its seam without infrastructure."""

    violations: list[str] = []
    workflow_files = sorted((SRC / "workflows").glob("*.py"))
    assert workflow_files, "workflow domain package disappeared"
    for path in workflow_files:
        relative = path.relative_to(SRC).as_posix()
        violations.extend(
            f"{relative} imports {name}"
            for name in _imports_of(path)
            if name.startswith(_WORKFLOW_FORBIDDEN_IMPORT_PREFIXES)
        )
    assert not violations, (
        "Workflow domain must not depend on LLM, ROS, tools, or UI adapters:\n"
        + "\n".join(violations)
    )


def test_area_patrol_service_is_independent_of_agent_and_ui_adapters() -> None:
    """Every UI and the LLM adapter must share one deterministic patrol service."""

    service = SRC / "tools" / "area_patrol_service.py"
    assert service.is_file(), "shared area-patrol service disappeared"
    forbidden = ("agents", "jenai.agent", "jenai.tui", "jenai.webui")
    violations = [name for name in _imports_of(service) if name.startswith(forbidden)]
    assert not violations, (
        "The shared patrol service must not depend on Agent SDK or UI adapters: "
        + ", ".join(violations)
    )

    agent_adapter_imports = _imports_of(SRC / "tools" / "area_patrol_agent_tools.py")
    assert "jenai.tools.area_patrol_service" in agent_adapter_imports
    assert not any(name.startswith("jenai.webui") for name in agent_adapter_imports)


def test_layers_above_vehicle_profile_stay_vehicle_agnostic() -> None:
    files = [SRC / rel for rel in _VEHICLE_AGNOSTIC_FILES]
    for directory in _VEHICLE_AGNOSTIC_DIRS:
        files.extend((SRC / directory).rglob("*.py"))
    violations: list[str] = []
    for path in files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _VEHICLE_WORDS.search(line):
                violations.append(f"{path.relative_to(SRC)}:{lineno}: {line.strip()}")
    assert not violations, (
        "Vehicle words above the vehicle profile — move the difference into "
        "config [vehicle]:\n" + "\n".join(violations)
    )


def test_navigation_surfaces_cannot_bypass_the_gateway() -> None:
    allowed = {"tools/navigation_gateway.py"}
    violations: list[str] = []
    for path in SRC.rglob("*.py"):
        rel = str(path.relative_to(SRC))
        if rel in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "route_execute":
                violations.append(f"{rel}:{node.lineno} calls route_execute directly")
            if isinstance(node, ast.ImportFrom) and node.module == "jenai.adapters.route_adapter":
                violations.append(f"{rel}:{node.lineno} imports the route adapter directly")
            if isinstance(node, ast.Name) and node.id == "get_route_adapter":
                violations.append(f"{rel}:{node.lineno} resolves the route adapter directly")
            if isinstance(node, ast.Name) and node.id == "navigate_with_fallback":
                violations.append(f"{rel}:{node.lineno} bypasses NavigationGateway")
    assert not violations, "Navigation must go through NavigationGateway:\n" + "\n".join(violations)


def test_direct_nav_send_is_confined_to_reviewed_low_level_seams() -> None:
    """ADR 0007 permits one simulation control arm, not a second product path."""

    actual: list[tuple[str, str]] = []
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(SRC).as_posix()
        actual.extend(
            (relative, scope) for scope, _lineno in _scoped_attribute_calls(path, "nav_send")
        )

    assert sorted(actual) == sorted(_DIRECT_NAV_SEND_SOURCE_ALLOWLIST), (
        "Direct bridge nav_send calls changed. Product motion must use NavigationGateway; "
        "only the low-level Nav2 adapter, isolated Twin, and ADR 0007 differential "
        "instrumentation are allowed:\n"
        + "\n".join(f"{path}:{scope}" for path, scope in sorted(actual))
    )


def test_differential_runner_importer_scan_covers_every_production_layer(
    tmp_path: Path,
) -> None:
    """The exact allowlist scanner must not omit acceptance, adapters, bridge, or scripts."""

    fixture_sources = {
        "src/jenai/acceptance/facade.py": (
            "from .nav_differential_runner import capture_navigation_differential\n"
        ),
        "src/jenai/adapters/backdoor.py": (
            "import jenai.acceptance.nav_differential_runner as runner\n"
        ),
        "src/jenai/bridge/backdoor.py": ("from jenai.acceptance import nav_differential_runner\n"),
        "scripts/backdoor.py": (
            "from jenai.acceptance.nav_differential_runner import DifferentialMode\n"
        ),
        "tests/unit/test_backdoor.py": (
            "from jenai.acceptance.nav_differential_runner import DifferentialMode\n"
        ),
    }
    for relative, source in fixture_sources.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    importers = _direct_importers_of_differential_runner(
        source_root=tmp_path / "src" / "jenai",
        repository_root=tmp_path,
    )

    assert importers == {
        "scripts/backdoor.py",
        "src/jenai/acceptance/facade.py",
        "src/jenai/adapters/backdoor.py",
        "src/jenai/bridge/backdoor.py",
    }


def test_only_the_dedicated_differential_cli_may_import_the_harness_runner() -> None:
    importers = _direct_importers_of_differential_runner()

    assert importers == _NAV_DIFFERENTIAL_RUNNER_IMPORTER_ALLOWLIST, (
        "ADR 0007 is an acceptance-only simulation exception. Direct runner importers changed:\n"
        + "\n".join(sorted(importers))
    )


def test_scripts_cannot_expand_direct_nav_send_beyond_frozen_e2_debt() -> None:
    """ADR 0007 does not legalise direct Nav2 dispatch from general scripts."""

    actual: list[tuple[str, str]] = []
    for path in (ROOT / "scripts").glob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        actual.extend(
            (relative, scope) for scope, _lineno in _scoped_attribute_calls(path, "nav_send")
        )

    # This pre-existing research reset is frozen debt, outside ADR 0007. Any
    # change or new script call must be reviewed and migrated separately.
    assert actual == [("scripts/e2_ablation.py", "_go_home_once")]


def test_differential_control_is_not_a_registered_robot_capability() -> None:
    config = build_minimal_config(
        provider_name="architecture-differential",
        provider="openai",
        default_model="model",
        api_key_env="",
    )
    card = build_robot_capability_card(config)
    capability_ids = {capability.capability_id for capability in card.capabilities}
    interface_names = {capability.interface_name for capability in card.capabilities}

    assert "R1_bridge_nav2" not in capability_ids
    assert "isaac_nav_differential" not in capability_ids
    assert all("differential" not in interface_name for interface_name in interface_names)


def test_motion_safety_gate_is_observation_only_and_not_imported_by_product_layers() -> None:
    importers = _direct_importers_of_motion_safety_family()

    assert importers == _MOTION_SAFETY_IMPORTER_ALLOWLIST
    observation_entrypoints = (
        SRC / "acceptance" / "motion_safety.py",
        SRC / "acceptance" / "motion_safety_capture.py",
        SRC / "acceptance" / "motion_safety_isaac.py",
        SRC / "acceptance" / "motion_safety_probe.py",
        SRC / "acceptance" / "motion_safety_stage_export.py",
        SRC / "acceptance" / "motion_safety_cli.py",
        ROOT / "scripts" / "isaac_motion_readiness.py",
        ROOT / "scripts" / "isaac_motion_readiness_probe.py",
        ROOT / "scripts" / "isaac_motion_readiness_stage_export.py",
    )
    assert not _motion_safety_observation_violations(observation_entrypoints)
    evaluator_source = observation_entrypoints[0].read_text(encoding="utf-8")
    assert "def motion_authorization_matches(" not in evaluator_source
    assert "def _motion_authorization_matches(" in evaluator_source


def test_motion_safety_import_guard_rejects_capture_facade_bypass(tmp_path: Path) -> None:
    fixture_sources = {
        "src/jenai/tools/backdoor.py": (
            "from jenai.acceptance.motion_safety_capture import capture_motion_readiness_evidence\n"
        ),
        "src/jenai/acceptance/motion_safety_capture.py": (
            "from jenai.acceptance.motion_safety import RuntimeBinding\n"
        ),
        "scripts/isaac_motion_readiness.py": (
            "from jenai.acceptance.motion_safety_cli import main\n"
        ),
    }
    for relative, source in fixture_sources.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    importers = _direct_importers_of_motion_safety_family(
        source_root=tmp_path / "src" / "jenai",
        repository_root=tmp_path,
    )

    assert "src/jenai/tools/backdoor.py" in importers
    assert importers != _MOTION_SAFETY_IMPORTER_ALLOWLIST


def test_motion_safety_observation_guard_rejects_cli_gateway_bypass(
    tmp_path: Path,
) -> None:
    cli = tmp_path / "motion_safety_cli.py"
    cli.write_text(
        "from jenai.tools.navigation_gateway import NavigationGateway\n",
        encoding="utf-8",
    )

    assert _motion_safety_observation_violations((cli,)) == {(cli.as_posix(), "NavigationGateway")}


# Functions over this teaching-code ceiling are prohibited. Keeping this map
# empty makes any future exception an explicit, reviewable source change.
_OVERSIZED_FUNCTION_BUDGETS: dict[tuple[str, str], int] = {}


def test_function_size_debt_can_only_shrink() -> None:
    found_oversized: dict[tuple[str, str], int] = {}
    violations: list[str] = []
    for path in SRC.rglob("*.py"):
        relative = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.end_lineno is None:
                continue
            lines = node.end_lineno - node.lineno + 1
            if lines <= 120:
                continue
            key = (relative, node.name)
            found_oversized[key] = lines
            budget = _OVERSIZED_FUNCTION_BUDGETS.get(key)
            if budget is None:
                violations.append(f"{relative}:{node.lineno} {node.name} grew to {lines} lines")
            elif lines > budget:
                violations.append(
                    f"{relative}:{node.lineno} {node.name}: {lines} > budget {budget}"
                )

    stale = set(_OVERSIZED_FUNCTION_BUDGETS).difference(found_oversized)
    assert not stale, f"Remove refactored functions from the size-debt allowlist: {sorted(stale)}"
    assert not violations, "Split oversized functions before merging:\n" + "\n".join(violations)


def test_production_code_uses_runtime_guards_not_assertions() -> None:
    """`python -O` must not erase production validation or safety branches."""
    violations: list[str] = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        violations.extend(
            f"{path.relative_to(SRC)}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Assert)
        )
    assert not violations, (
        "Replace production assert with an explicit runtime guard:\n" + "\n".join(violations)
    )


def test_litellm_gateway_remains_server_side_not_a_client_dependency() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        dependencies = tomllib.load(handle)["project"]["dependencies"]

    # JenAI calls the gateway through its OpenAI-compatible HTTP endpoint;
    # installing LiteLLM on the robot duplicates the server and its large tree.
    assert not any(dependency.lower().startswith("litellm") for dependency in dependencies)


def test_autonomous_agent_never_receives_raw_actuation_tools() -> None:
    """Low-level diagnostics may exist, but cannot enter the LLM tool graph."""

    config = build_minimal_config(
        provider_name="architecture",
        provider="openai",
        default_model="model",
        api_key_env="",
    )
    supervisor = build_supervisor_agent(config)
    forbidden = {
        "ros_drive_execute_tool",
        "ros_drive_verified_tool",
        "ros_pub_execute_tool",
        "ros_pub_validate_tool",
    }
    violations: dict[str, list[str]] = {}
    for agent in [supervisor, *supervisor.handoffs]:
        exposed = sorted(forbidden & {tool.name for tool in agent.tools})
        if exposed:
            violations[agent.name] = exposed
    assert not violations, f"Autonomous Agent exposes low-level actuation tools: {violations}"

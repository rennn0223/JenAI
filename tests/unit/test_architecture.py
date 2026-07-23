"""Architecture iron rules as tests (V1_GATE A5).

The rules live in docs/product/PROJECT_DIRECTION.md; these tests make violating them
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


def _imports_of(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


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
    allowed = {"tools/navigation_gateway.py", "tools/route_core.py"}
    violations: list[str] = []
    for path in SRC.rglob("*.py"):
        rel = str(path.relative_to(SRC))
        if rel in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "route_execute":
                violations.append(f"{rel}:{node.lineno} calls route_execute directly")
            if isinstance(node, ast.Name) and node.id == "navigate_with_fallback":
                violations.append(f"{rel}:{node.lineno} bypasses NavigationGateway")
    assert not violations, "Navigation must go through NavigationGateway:\n" + "\n".join(violations)


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

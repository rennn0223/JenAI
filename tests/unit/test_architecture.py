"""Architecture iron rules as tests (V1_GATE A5).

The rules live in docs/PROJECT_DIRECTION.md; these tests make violating them
a red CI instead of a code-review hope:

1. The reflex/safety layer must work with the LLM stack dead — so its modules
   may not even import it.
2. Everything above the vehicle profile must stay vehicle-agnostic — no
   vehicle words outside the profile itself.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "jenai"

# Rule 1: modules that must never depend on an LLM (or the network stack it
# implies). The daemon *runner* wires in PerceptionLoop (decision-layer input),
# so the enforced set is the reflex core: engine, bridge, safety, twin gate.
_REFLEX_MODULES = [
    "bridge/client.py",
    "bridge/ros_bridge.py",
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
        for name in _imports_of(path):
            if name.startswith(_LLM_IMPORT_PREFIXES):
                violations.append(f"{rel} imports {name}")
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
    allowed = {"tools/nav_live.py", "tools/navigation_gateway.py", "tools/route_core.py"}
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

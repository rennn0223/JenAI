"""File-defined skills: drop a TOML in <config dir>/skills/ → a new slash command.

The flexibility layer users asked for — extend JenAI without touching code:

    # ~/.config/jenai/skills/inspect.toml
    name = "inspect"                      # slash name (default: file stem)
    description = "巡檢主走廊"
    steps = "大廳, drive 左轉, 機械系館"    # exactly /mission syntax

`/inspect` then appears in the palette and runs those steps as a mission —
which means every safety property is inherited for free: one approval card up
front, navigation through NavigationGateway (Twin Gate, avoidance,
clamps), honest per-step reporting. A skill file can never introduce a new
kind of actuation; it can only compose the already-gated primitives.

Loading is tolerant: a malformed file becomes a warning shown by `/skills`,
never a TUI crash.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")

# Built-in command words a skill may not shadow — a skill named "stop" hijacking
# the emergency stop would be a safety hole, not a feature.
RESERVED_NAMES = {
    "help",
    "status",
    "stop",
    "clear",
    "quit",
    "exit",
    "doctor",
    "providers",
    "models",
    "model",
    "provider",
    "permissions",
    "config",
    "plan",
    "run",
    "why",
    "review",
    "abort",
    "ros",
    "route",
    "drive",
    "loc",
    "mission",
    "patrol",
    "explore",
    "dock",
    "report",
    "vision",
    "perception",
    "shell",
    "skills",
    "scaffold",
}


@dataclass(frozen=True)
class UserSkill:
    name: str  # slash command without the leading /
    description: str
    steps: str  # /mission syntax
    source: Path


def skills_dir(config_path: Path) -> Path:
    return config_path.parent / "skills"


def load_user_skills(config_path: Path) -> tuple[dict[str, UserSkill], list[str]]:
    """Load every skills/*.toml → ({name: skill}, warnings). Never raises."""
    directory = skills_dir(config_path)
    skills: dict[str, UserSkill] = {}
    warnings: list[str] = []
    if not directory.is_dir():
        return skills, warnings
    for path in sorted(directory.glob("*.toml")):
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            warnings.append(f"{path.name}: not valid TOML ({exc})")
            continue
        name = str(raw.get("name") or path.stem).strip().lower()
        steps = str(raw.get("steps") or "").strip()
        if not _NAME.match(name):
            warnings.append(f"{path.name}: invalid skill name '{name}' (need [a-z][a-z0-9_-]*)")
            continue
        if name in RESERVED_NAMES:
            warnings.append(f"{path.name}: '{name}' shadows a built-in command — skipped")
            continue
        if not steps:
            warnings.append(f"{path.name}: missing 'steps' (use /mission syntax)")
            continue
        if name in skills:
            warnings.append(f"{path.name}: duplicate skill '{name}' — keeping the first")
            continue
        skills[name] = UserSkill(
            name=name,
            description=str(raw.get("description") or "").strip() or f"user skill from {path.name}",
            steps=steps,
            source=path,
        )
    return skills, warnings

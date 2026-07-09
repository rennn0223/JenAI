"""Visual building blocks of the JenAI TUI.

Widgets, colors, and text-mark helpers only — no command handling, no app
state. Everything here renders; nothing here decides.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static

from jenai.schemas import DoctorCheckItem, DoctorResult, DoctorStatus


class SlashCommand(NamedTuple):
    name: str
    description: str
    template: str = ""

    @property
    def completion(self) -> str:
        return self.template or self.name



ACCENT = "#d97757"
ACCENT_DARK = "#c15f3c"
MUTED = "#9c9689"
GREEN = "#7d9b6a"
ERROR = "#cb6250"
BLUE = "#d97757"



class WelcomePanel(Container):
    """Orange hero card shown at the top of the transcript."""

    def __init__(
        self,
        *,
        version: str,
        provider_name: str,
        provider_kind: str,
        model_name: str,
        config_path: Path,
        doctor_result: DoctorResult | None,
        locations_count: int | None = None,
        skills_count: int | None = None,
    ) -> None:
        super().__init__(id="welcome")
        self.version = version
        self.provider_name = provider_name
        self.provider_kind = provider_kind
        self.model_name = model_name
        self.config_path = config_path
        self.doctor_result = doctor_result
        self.locations_count = locations_count
        self.skills_count = skills_count

    def compose(self) -> ComposeResult:
        # Single, centred column so it never crushes on a narrow (mobile) terminal.
        # The mascot is decorative and is hidden below a width threshold (see the
        # app's on_resize -> `narrow` class) rather than being squished.
        self.border_title = f"JenAI v{self.version}"
        yield Static(pixel_mark(), id="pixel-mark")
        yield Static("Robot workflow console", classes="heading")
        yield Static("Plan, inspect, and drive robot tasks from one terminal.", classes="meta")
        yield Static(self._provider_meta(), id="welcome-provider-meta", classes="meta")
        yield Static(self._workspace_meta(), id="welcome-workspace-meta", classes="meta")
        yield Static(self._doctor_summary(), id="welcome-doctor-status", classes="meta")

    def update_doctor_result(self, doctor_result: DoctorResult | None) -> None:
        self.doctor_result = doctor_result
        self.query_one("#welcome-doctor-status", Static).update(self._doctor_summary())

    def _workspace_meta(self) -> str:
        """One line of live workspace facts — what this robot already knows."""
        parts: list[str] = []
        if self.locations_count is not None:
            parts.append(f"{self.locations_count} locations")
        if self.skills_count:
            parts.append(f"{self.skills_count} skills")
        if not parts:
            return ""
        return "[#9c9689]" + " · ".join(parts) + "[/]"

    def update_model(
        self,
        model_name: str,
        *,
        provider_name: str | None = None,
        provider_kind: str | None = None,
    ) -> None:
        self.model_name = model_name
        if provider_name is not None:
            self.provider_name = provider_name
        if provider_kind is not None:
            self.provider_kind = provider_kind
        self.query_one("#welcome-provider-meta", Static).update(self._provider_meta())

    def _provider_meta(self) -> str:
        return (
            f"{self.model_name} · {self.provider_kind}\n"
            f"{self.provider_name} · {self.config_path.parent}"
        )

    def _doctor_summary(self) -> Text:
        if self.doctor_result is None:
            return Text("Not checked", style=MUTED)

        text = Text()
        status = DoctorStatus(self.doctor_result.overall)
        text.append(status.value, style=f"bold {status_color(status)}")

        fails = sum(item.status == DoctorStatus.FAIL for item in self.doctor_result.items)
        warns = sum(item.status == DoctorStatus.WARN for item in self.doctor_result.items)
        text.append(f" · {fails} fail · {warns} warn", style=MUTED)
        return text


# Claude Code-style markers: a filled bullet for each transcript entry and an
# elbow connector for the indented result/detail lines beneath it.
BULLET = "⏺"
ELBOW = "⎿"

_MARKER_COLOR = {
    "command": BLUE,
    "success": GREEN,
    "warn": ACCENT,
    "error": ERROR,
    "muted": MUTED,
    "assistant": ACCENT,
}

# Variants whose multi-line bodies render airy (blank line between logical
# lines). A property of the variant, not the callsite, so every future place
# that mounts an assistant reply gets the same rhythm automatically.
_SPACED_VARIANTS = {"assistant"}


def _bullet_markup(variant: str, body: str) -> str:
    color = _MARKER_COLOR.get(variant, ACCENT)
    return f"[{color}]{BULLET}[/] {body}"


def _spaced_body(body: str) -> str:
    """Open up a multi-line reply: blank line between logical lines, and align
    continuation lines under the bullet's text so it reads as one airy block."""
    lines = body.split("\n")
    if len(lines) == 1:
        return body
    spaced: list[str] = [lines[0]]
    for line in lines[1:]:
        spaced.append("")  # blank line widens the vertical rhythm
        spaced.append(f"  {line}" if line else line)
    return "\n".join(spaced)


def _detail_markup(lines: list[str]) -> str:
    """Render detail lines under a bullet as Claude Code elbow-indented text."""
    out: list[str] = []
    for i, line in enumerate(lines):
        prefix = f"  [{MUTED}]{ELBOW}[/] " if i == 0 else "     "
        out.append(f"{prefix}[{MUTED}]{line}[/]")
    return "\n".join(out)


def _spaced_detail(lines: list[str]) -> list[str]:
    """Exactly one blank line between logical lines, collapsing whatever
    spacing the model chose — prose replies get a FIXED airy rhythm
    regardless of how the answer was formatted."""
    logical = [line for line in lines if line.strip()]
    out: list[str] = []
    for line in logical:
        if out:
            out.append("")
        out.append(line)
    return out


class PromptPill(Static):
    """Echo of the user's submitted line, shown as a muted `>` prompt."""

    def __init__(self, text: str) -> None:
        super().__init__(f"[{MUTED}]>[/] [#d9d3c7]{text}[/]", classes="prompt-line")


class TimelineItem(Static):
    """A single Claude Code-style bullet line (⏺ marker + body markup).

    Multi-line bodies of _SPACED_VARIANTS render with blank lines between
    logical lines, so assistant replies breathe instead of packing tight.
    """

    def __init__(self, variant: str, body: str) -> None:
        self.variant = variant
        super().__init__(self._render_body(body), classes="bullet-line")
        self.body = body

    def _render_body(self, body: str) -> str:
        # Not named `_render`: that would shadow textual.Widget's internal hook.
        rendered = _spaced_body(body) if self.variant in _SPACED_VARIANTS else body
        return _bullet_markup(self.variant, rendered)

    def set_body(self, body: str) -> None:
        """Replace the body in place — this is how a streaming reply grows."""
        self.body = body
        self.update(self._render_body(body))


class OutputPanel(Static):
    """A bullet with a title line and elbow-indented body lines (no box).

    ``spaced=True`` gives the body the assistant-reply rhythm (one blank
    line between logical lines, pre-existing blanks collapsed) — use it for
    prose answers; tables and listings stay compact by default.
    """

    def __init__(
        self, title: str, body: str, *, variant: str = "assistant", spaced: bool = False
    ) -> None:
        body_lines = body.split("\n") if body else []
        if spaced:
            body_lines = _spaced_detail(body_lines)
        detail = _detail_markup(body_lines) if body_lines else ""
        markup = _bullet_markup(variant, f"[bold #f2ede1]{title}[/]")
        if detail:
            markup = f"{markup}\n{detail}"
        super().__init__(markup, classes="bullet-line")
        self.title = title
        self.body = body


class CommandPalette(Static):
    # Rows shown at once; the window scrolls to follow the selection so every
    # matching command is reachable without a hard cap.
    WINDOW = 12

    def update_matches(
        self,
        matches: list[SlashCommand],
        selected_index: int,
    ) -> None:
        if not matches:
            self.update("[#9c9689]No matching commands[/]")
            return

        total = len(matches)
        # Centre the window on the selection, then clamp so it never runs past
        # either end of the list (keeps the selected row visible while scrolling).
        if total <= self.WINDOW:
            start = 0
        else:
            start = min(max(selected_index - self.WINDOW // 2, 0), total - self.WINDOW)
        end = min(start + self.WINDOW, total)

        text = Text()
        text.append(f"Commands  ({selected_index + 1}/{total})\n", style=f"bold {ACCENT}")
        if start > 0:
            text.append(f"  ↑ {start} more\n", style=MUTED)
        for index in range(start, end):
            command = matches[index]
            selected = index == selected_index
            arrow_style = GREEN if selected else MUTED
            line_style = "bold #f2ede1" if selected else "#d9d3c7"
            text.append("❯ " if selected else "  ", style=arrow_style)
            text.append(command.name.ljust(16), style=line_style)
            text.append(command.description, style=MUTED)
            text.append("\n")
        if end < total:
            text.append(f"  ↓ {total - end} more", style=MUTED)
        text.rstrip()
        self.update(text)


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def _short_cwd() -> str:
    """Home-relative, abbreviated cwd for the status line (e.g. ~/JenAI)."""
    cwd = Path.cwd()
    try:
        return "~/" + str(cwd.relative_to(Path.home()))
    except ValueError:
        return str(cwd)



# Extra torso columns beyond the original sketch — THE one number to bump
# when the dachshund needs to be longer. Rear parts (tail, back legs, torso
# start) shift left together; head/front stay put. Mind the welcome panel's
# narrow-layout threshold in app.py when growing this.
EXTRA_LENGTH = 4


def pixel_mark(frame: int = 0, *, running: bool = False) -> Text:
    """The dachshund mascot, one animation frame at a time.

    frame cycles the idle animation (tail wag, an occasional blink);
    ``running=True`` switches to a two-pose gallop — the mascot doubles as a
    task-status indicator. Frame geometry is stable (same bounding box every
    pose) so the welcome panel never jitters.
    """
    colors = {
        "body": "#d98c69",
        "belly": "#e8a987",
        "dark": "#ad6248",
        "black": "#34241d",
        "white": "#fdf5ef",
        "cheek": "#e89a9a",
        "collar": "#5fb1c0",
        "tag": "#f0c84e",
    }
    tail_up = frame % 2 == 0
    blink = (not running) and frame % 8 == 6  # a blink every ~5s at idle pace
    stride = frame % 2 if running else None
    ext = EXTRA_LENGTH  # rear-half leftward shift (longer dog)

    cells: dict[tuple[int, int], str] = {}

    def fill(x0: int, y0: int, x1: int, y1: int, color: str) -> None:
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                cells[(x, y)] = color

    def put(x: int, y: int, color: str) -> None:
        cells[(x, y)] = color

    def delete(x: int, y: int) -> None:
        cells.pop((x, y), None)

    fill(9, 2, 11, 9, colors["dark"])
    put(10, 10, colors["dark"])
    fill(11, 1, 18, 7, colors["body"])
    delete(11, 1)
    delete(18, 1)
    fill(16, 5, 20, 7, colors["body"])
    delete(20, 7)
    put(20, 5, colors["black"])
    put(20, 6, colors["black"])
    put(19, 6, colors["black"])
    put(18, 7, colors["black"])
    # Eye: open (pupil + highlight) or a closed lid line mid-blink.
    if blink:
        fill(14, 3, 15, 3, colors["body"])
        fill(14, 4, 15, 4, colors["black"])
    else:
        fill(14, 3, 15, 4, colors["black"])
        put(15, 3, colors["white"])
    put(17, 6, colors["cheek"])
    fill(-1 - ext, 7, 13, 10, colors["body"])
    delete(-1 - ext, 7)
    fill(0 - ext, 10, 12, 10, colors["belly"])
    # Tail: wag between an up-curl and a down-sweep.
    if tail_up:
        put(-2 - ext, 6, colors["body"])
        put(-3 - ext, 5, colors["body"])
        put(-3 - ext, 4, colors["body"])
        put(-2 - ext, 4, colors["body"])
    else:
        put(-2 - ext, 8, colors["body"])
        put(-3 - ext, 9, colors["body"])
        put(-3 - ext, 10, colors["body"])
        put(-2 - ext, 10, colors["body"])
    # Legs: standing, or a two-pose gallop (pairs spread ↔ tucked).
    if stride is None:
        fill(0 - ext, 11, 1 - ext, 13, colors["body"])
        fill(3 - ext, 11, 4 - ext, 13, colors["body"])
        fill(10, 11, 11, 13, colors["body"])
        fill(13, 11, 14, 13, colors["body"])
    elif stride == 0:  # spread: back pair kicks back, front pair reaches out
        fill(-1 - ext, 11, 0 - ext, 13, colors["body"])
        fill(4 - ext, 11, 5 - ext, 13, colors["body"])
        fill(9, 11, 10, 13, colors["body"])
        fill(14, 11, 15, 13, colors["body"])
    else:  # tucked under the body
        fill(1 - ext, 11, 2 - ext, 13, colors["body"])
        fill(2 - ext, 11, 3 - ext, 13, colors["body"])
        fill(11, 11, 12, 13, colors["body"])
        fill(12, 11, 13, 13, colors["body"])
    # Collar/tag is drawn last: the body fills above cover this region.
    fill(11, 7, 12, 9, colors["collar"])
    put(12, 10, colors["tag"])
    # Pin the bounding box so every frame renders the same size (no jitter):
    # x (−3−ext)..20 and y 1..13 are the extremes across all poses.
    cells.setdefault((-3 - ext, 1), None)
    cells.setdefault((20, 13), None)

    min_x = min(x for x, _ in cells)
    max_x = max(x for x, _ in cells)
    min_y = min(y for _, y in cells)
    max_y = max(y for _, y in cells)

    text = Text()
    for y in range(min_y, max_y + 1, 2):
        for x in range(min_x, max_x + 1):
            top = cells.get((x, y))
            bottom = cells.get((x, y + 1))
            if top and bottom:
                text.append("█" if top == bottom else "▀", style=f"{top} on {bottom}")
            elif top:
                text.append("▀", style=top)
            elif bottom:
                text.append("▄", style=bottom)
            else:
                text.append(" ")
        if y + 1 < max_y:
            text.append("\n")
    return text


def status_color(status: DoctorStatus | str) -> str:
    try:
        status = DoctorStatus(status)
    except ValueError:
        return MUTED
    return {
        DoctorStatus.PASS: GREEN,
        DoctorStatus.WARN: ACCENT,
        DoctorStatus.FAIL: ERROR,
    }.get(status, MUTED)


def format_doctor_item(item: DoctorCheckItem) -> str:
    fix = f"\n[#9c9689]  fix:[/] {item.fix_suggestion}" if item.fix_suggestion else ""
    return (
        f"[bold {status_color(item.status)}]{item.status}[/] "
        f"{item.section}.{item.check_name}: {item.message}{fix}"
    )

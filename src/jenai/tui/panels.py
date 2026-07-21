"""Visual building blocks of the JenAI TUI.

Widgets, colors, and text-mark helpers only — no command handling, no app
state. Everything here renders; nothing here decides.
"""

from __future__ import annotations

from base64 import b64decode
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import NamedTuple

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.markup import escape
from textual.widgets import Static

from jenai.schemas import DoctorCheckItem, DoctorStatus


class SlashCommand(NamedTuple):
    name: str
    description: str
    template: str = ""

    @property
    def completion(self) -> str:
        return self.template or self.name


ACCENT = "#e8683f"
ACCENT_DARK = "#e8683f"
MUTED = "#8f897f"
GREEN = "#8fbf6f"
ERROR = "#d85f52"


class WelcomePanel(Container):
    """Responsive Claude Code-style welcome panel."""

    def __init__(
        self,
        *,
        version: str,
        provider_name: str,
        provider_kind: str,
        model_name: str,
        config_path: Path,
    ) -> None:
        super().__init__(id="welcome")
        self.version = version
        self.provider_name = provider_name
        self.provider_kind = provider_kind
        self.model_name = model_name
        self.config_path = config_path
        self._recent_activity: list[str] = []

    def compose(self) -> ComposeResult:
        self.border_title = f"JenAI v{self.version}"
        with Horizontal(id="welcome-content"):
            with Vertical(id="welcome-left"):
                yield Static("Welcome back!", id="welcome-greeting", classes="heading")
                yield Static(pixel_mark(), id="pixel-mark")
                yield Static(self._provider_meta(), id="welcome-provider-meta", classes="meta")
            with Vertical(id="welcome-right"):
                yield Static("Tips for getting started", classes="welcome-section-title")
                yield Static(
                    "Run [bold #f2ede4]/doctor[/] to check ROS 2 and provider readiness\n"
                    "Run [bold #f2ede4]/run <task>[/] to plan and execute a robot task\n"
                    "Use [bold #f2ede4]/help[/] to learn commands and shortcuts",
                    id="welcome-quick-start",
                )
                yield Static("Recent activity", classes="welcome-section-title recent-title")
                yield Static("No activity in this session yet", id="welcome-recent", classes="meta")

    def record_activity(self, value: str) -> None:
        """Show the two most recent session inputs without echoing shell text."""
        label = value.strip()
        if not label:
            return
        if label.startswith("!"):
            label = "! shell command"
        elif len(label) > 60:
            label = label[:57] + "…"
        label = escape(label)
        if not self._recent_activity or self._recent_activity[0] != label:
            self._recent_activity.insert(0, label)
            del self._recent_activity[2:]
        self.query_one("#welcome-recent", Static).update(
            "\n".join(f"[#7a756c]now[/]  {item}" for item in self._recent_activity)
        )

    def clear_activity(self) -> None:
        self._recent_activity.clear()
        self.query_one("#welcome-recent", Static).update("No activity in this session yet")

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
            f"{self.model_name} · {self.provider_kind} · {self.provider_name}\n"
            f"{self.config_path.parent}"
        )


# Claude Code-style markers: a filled bullet for each transcript entry and an
# elbow connector for the indented result/detail lines beneath it.
BULLET = "●"
ELBOW = "⎿"

_MARKER_COLOR = {
    "command": ACCENT,
    "success": GREEN,
    "warn": ACCENT,
    "error": ERROR,
    "muted": MUTED,
    "assistant": ACCENT,
}


def _bullet_markup(variant: str, body: str) -> str:
    color = _MARKER_COLOR.get(variant, ACCENT)
    return f"[{color}]{BULLET}[/] {body}"


def _detail_markup(lines: list[str]) -> str:
    """Render detail lines under a bullet as Claude Code elbow-indented text."""
    out: list[str] = []
    for i, line in enumerate(lines):
        prefix = f"  [{MUTED}]{ELBOW}[/] " if i == 0 else "     "
        out.append(f"{prefix}[{MUTED}]{line}[/]")
    return "\n".join(out)


def _normalized_detail(lines: list[str]) -> list[str]:
    """Keep normal line spacing and collapse repeated paragraph gaps."""
    out: list[str] = []
    for line in lines:
        if line.strip():
            out.append(line)
        elif out and out[-1] != "":
            out.append("")
    while out and out[-1] == "":
        out.pop()
    return out


class PromptPill(Static):
    """Echo of the user's submitted line, shown as a Claude-style prompt."""

    def __init__(self, text: str) -> None:
        # User text goes into Textual markup: unescaped, a pasted "[/]" would
        # raise MarkupError inside the compositor and crash the whole app.
        super().__init__(f"[bold #f2ede1]❯[/] [#f2ede1]{escape(text)}[/]", classes="prompt-line")


class TimelineItem(Static):
    """A single Claude Code-style bullet line (● marker + body markup)."""

    def __init__(self, variant: str, body: str) -> None:
        self.variant = variant
        super().__init__(self._render_body(body), classes="bullet-line")
        self.body = body

    def _render_body(self, body: str) -> str:
        # Not named `_render`: that would shadow textual.Widget's internal hook.
        return _bullet_markup(self.variant, body)

    def set_body(self, body: str) -> None:
        """Replace the body in place — this is how a streaming reply grows."""
        self.body = body
        self.update(self._render_body(body))


class OutputPanel(Static):
    """A bullet with a title line and elbow-indented body lines (no box).

    ``spaced=True`` normalizes repeated paragraph gaps while retaining normal
    one-row line spacing. Tables and listings stay untouched by default.
    """

    def __init__(
        self, title: str, body: str, *, variant: str = "assistant", spaced: bool = False
    ) -> None:
        body_lines = body.split("\n") if body else []
        if spaced:
            body_lines = _normalized_detail(body_lines)
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

    def update_hint(self, command: SlashCommand) -> None:
        """Dim, non-interactive argument-format hint shown while typing args.

        Completion inserts only the command name; the format lives HERE as a
        hint — never in the composer, where it would have to be deleted.
        """
        args = command.template.removeprefix(command.name).strip()
        text = Text()
        text.append("格式  ", style=f"bold {ACCENT}")
        text.append(command.name, style="bold #f2ede1")
        if args:
            text.append(f"  {args}", style=MUTED)
        if command.description:
            text.append(f"\n  {command.description}", style=MUTED)
        self.update(text)

    def update_matches(
        self,
        matches: list[SlashCommand],
        selected_index: int,
    ) -> None:
        if not matches:
            self.update("[#9c9689]No matching commands[/]")
            return

        total = len(matches)
        # Keep the composer and status line on-screen in short terminals.  The
        # normal 12-row window is unchanged at 26+ rows; smaller viewports show
        # a scrollable slice that still follows the selected command.
        window = min(self.WINDOW, max(1, self.screen.size.height - 13))
        # Centre the window on the selection, then clamp so it never runs past
        # either end of the list (keeps the selected row visible while scrolling).
        if total <= window:
            start = 0
        else:
            start = min(max(selected_index - window // 2, 0), total - window)
        end = min(start + window, total)

        # One visual row per command keeps the selected item and composer
        # reachable in compact terminals; long descriptions end in an ellipsis.
        text = Text(no_wrap=True, overflow="ellipsis")
        text.append(f"Commands  ({selected_index + 1}/{total})\n", style=f"bold {ACCENT}")
        # Keep every command label visually separate from its description,
        # including long entries such as ``/perception start``.
        name_width = max(18, max(len(command.name) for command in matches) + 2)
        if start > 0:
            text.append(f"  ↑ {start} more\n", style=MUTED)
        for index in range(start, end):
            command = matches[index]
            selected = index == selected_index
            arrow_style = GREEN if selected else MUTED
            line_style = "bold #f2ede1" if selected else "#d9d3c7"
            text.append("❯ " if selected else "  ", style=arrow_style)
            text.append(command.name.ljust(name_width), style=line_style)
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


# Compact, original dachshund mascot designed for the terminal welcome panel.
# The dark coat and cyan status collar echo the robot-dog direction without
# turning the friendly mascot into a mechanical character.  A terminal cell
# is roughly twice as tall as it is wide, so two square pixels are packed into
# each half-block character below.  This 34×18 grid was sampled from candidate
# C's source artwork and occupies only 34×9 terminal cells.
_DESIGNED_DOG = (
    "                                  ",
    "     KDBBBBBK                     ",
    "    KBBBDDDDBK                    ",
    "    KBBBDDDDDBK               K   ",
    "    DDTTTDKDDDB              KB   ",
    " DKBDDDWKDDDDDBK             KBK  ",
    " BDBBBBDDDDDDDDK             DD   ",
    " DTTTTTTDKDDDDD             KBK   ",
    "  DBBTTTDKDDDDKKK          DBDK   ",
    "       KCKKDDKDDBBBBBBBBBBBDDK    ",
    "       BCDDDKDDDDDDDDDDDDDDDK     ",
    "       DCDDDDDDDDDDDDDDDDDDDD     ",
    "       BTBDDDDDDDDDDDDDDDDDDD     ",
    "       KTTDDDDDDDDDDDDDDDDDDDK    ",
    "        KDKDDDKBBBBBBDDDKKDDBD    ",
    "       KTDKBTBKKDDDDK  KBDKBTD    ",
    "       DBKBTTK         DBKBTTK    ",
    "                           K      ",
)

_DESIGNED_DOG_COLORS = {
    "K": "#1f110a",  # outline / eye
    "B": "#513d32",  # chocolate coat highlight
    "D": "#3c2c26",  # coat / floppy ear
    "T": "#ba773e",  # muzzle / chest / paws
    "C": "#6ff8f9",  # robot status collar
    "W": "#f2ede4",  # bright eye against the dark coat
}


@lru_cache(maxsize=1)
def terminal_mascot() -> Text:
    """Return Claude Design's full-size ANSI mascot without resampling it."""

    encoded = (
        files("jenai.tui.assets").joinpath("mascot-terminal.b64").read_text(encoding="ascii")
    )
    ansi = b64decode(encoded).decode("utf-8").rstrip("\n")
    return Text.from_ansi(ansi)


def pixel_mark(frame: int = 0, *, running: bool = False) -> Text:
    """Render the compact robot-dog dachshund with a tiny terminal animation."""

    width, height = max(map(len, _DESIGNED_DOG)), len(_DESIGNED_DOG)
    cells: dict[tuple[int, int], str | None] = {}
    for y, row in enumerate(_DESIGNED_DOG):
        for x, token in enumerate(row.ljust(width)):
            cells[(x, y)] = _DESIGNED_DOG_COLORS.get(token)

    # The sprite faces left. Its tail occupies the far-right pixels; alternate
    # the tip without changing the 34×18 bounding box.
    if frame % 2:
        cells[(30, 3)] = None
        cells[(30, 2)] = _DESIGNED_DOG_COLORS["K"]

    # Preserve the old status animation: blink occasionally while idle and
    # lift alternating paws while a task is running.
    if not running and frame % 8 == 6:
        cells[(7, 5)] = _DESIGNED_DOG_COLORS["D"]
    if running:
        lift = ((10, 16), (29, 16)) if frame % 2 else ((13, 15), (32, 15))
        for point in lift:
            cells[point] = None

    text = Text()
    for y in range(0, height, 2):
        for x in range(width):
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
        if y + 2 < height:
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

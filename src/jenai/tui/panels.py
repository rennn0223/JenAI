"""Visual building blocks of the JenAI TUI.

Widgets, colors, and text-mark helpers only — no command handling, no app
state. Everything here renders; nothing here decides.
"""

from __future__ import annotations

from base64 import b64decode
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.content import Content
from textual.markup import escape
from textual.widgets import Static

from jenai.schemas import DoctorCheckItem, DoctorStatus
from jenai.tui.command_palette import SlashCommand

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
                yield Static("歡迎回來！", id="welcome-greeting", classes="heading")
                yield Static(pixel_mark(), id="pixel-mark")
                yield Static(self._provider_meta(), id="welcome-provider-meta", classes="meta")
            with Vertical(id="welcome-right"):
                yield Static("快速開始", classes="welcome-section-title")
                yield Static(
                    "直接輸入任務，例如「檢查機器人狀態」\n"
                    "執行 [bold #f2ede4]/doctor[/]，確認 ROS 2 與模型服務就緒\n"
                    "輸入 [bold #f2ede4]/help[/]，查看指令與快捷鍵",
                    id="welcome-quick-start",
                )
                yield Static("本次操作", classes="welcome-section-title recent-title")
                yield Static("這個 session 尚無操作紀錄", id="welcome-recent", classes="meta")

    def record_activity(self, value: str) -> None:
        """Show the two most recent session inputs without echoing shell text."""
        label = value.strip()
        if not label:
            return
        if label.startswith("!"):
            label = "! shell 指令"
        elif len(label) > 60:
            label = label[:57] + "…"
        label = escape(label)
        if not self._recent_activity or self._recent_activity[0] != label:
            self._recent_activity.insert(0, label)
            del self._recent_activity[2:]
        self.query_one("#welcome-recent", Static).update(
            "\n".join(f"[#7a756c]剛剛[/]  {item}" for item in self._recent_activity)
        )

    def clear_activity(self) -> None:
        self._recent_activity.clear()
        self.query_one("#welcome-recent", Static).update("這個 session 尚無操作紀錄")

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


def _detail_markup(lines: list[str], *, trusted_markup: bool = False) -> str:
    """Render detail lines under a bullet as Claude Code elbow-indented text."""
    out: list[str] = []
    for i, line in enumerate(lines):
        prefix = f"  [{MUTED}]{ELBOW}[/] " if i == 0 else "     "
        body = line if trusted_markup else escape(line)
        out.append(f"{prefix}[{MUTED}]{body}[/]")
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
        self,
        title: str,
        body: str,
        *,
        variant: str = "assistant",
        spaced: bool = False,
        body_markup: bool = False,
    ) -> None:
        body_lines = body.split("\n") if body else []
        if spaced:
            body_lines = _normalized_detail(body_lines)
        color = _MARKER_COLOR.get(variant, ACCENT)
        visual = Content.styled(BULLET, color)
        visual = visual.append_text(" ")
        visual = visual.append_text(title, "bold #f2ede1")
        for index, line in enumerate(body_lines):
            visual = visual.append_text("\n")
            visual = visual.append_text(f"  {ELBOW} " if index == 0 else "     ", MUTED)
            if body_markup:
                visual = visual.append(Content.from_markup(line))
            else:
                visual = visual.append_text(line, MUTED)
        super().__init__(visual, classes="bullet-line")
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
            self.update("[#9c9689]找不到相符指令[/]")
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
        text.append(f"指令  ({selected_index + 1}/{total})\n", style=f"bold {ACCENT}")
        # Keep every command label visually separate from its description,
        # including long entries such as ``/perception start``.
        name_width = max(18, max(len(command.name) for command in matches) + 2)
        if start > 0:
            text.append(f"  ↑ 還有 {start} 個\n", style=MUTED)
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
            text.append(f"  ↓ 還有 {total - end} 個", style=MUTED)
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


# A terminal cell is roughly twice as tall as it is wide, so this 24×18
# chocolate long-haired dachshund is packed into 24×9 half-block cells.
# Periods are transparent pixels and keep every frame in a stable bounding box.
_DESIGNED_DOG = (
    "........................",
    ".....OOOOOO.............",
    "...OOttttttO............",
    "..OtwwTTTEEEO...........",
    "..OTWNTTEEEEO...........",
    "..OTNNTTEEEEEO.......O..",
    "BBwwTTTTEEEEEO......OtO.",
    "BBwwwwTTEEEEEO......OTO.",
    ".OOOwwTTEEEEEO.....OtTO.",
    "...OOTTTEEEEEO.....OTTO.",
    ".....OCCCEEEEEO....OTTO.",
    "......OwwwEEEEtOOOOtTTO.",
    "......OwwwTEETTttttTTTO.",
    ".......OwwTTTTTTTTTTTTO.",
    ".......OTTTTOOOOOTTTTO..",
    "........OwwO.....OTTO...",
    "........OwwO.....OTTO...",
    ".......OwwwO....OwwwO...",
)

_DESIGNED_DOG_COLORS = {
    "O": "#2e1c12",  # outline
    "t": "#8a5a38",  # lit coat
    "T": "#6b4229",  # chocolate coat
    "E": "#54331e",  # floppy ear
    "w": "#e8cda2",  # cream markings
    "N": "#14100d",  # eye
    "W": "#ffffff",  # eye highlight
    "B": "#8a5a4a",  # liver nose
    "C": "#4a8fb5",  # collar
}


@lru_cache(maxsize=1)
def terminal_mascot() -> Text:
    """Return Claude Design's full-size ANSI mascot without resampling it."""

    encoded = files("jenai.tui.assets").joinpath("mascot-terminal.b64").read_text(encoding="ascii")
    ansi = b64decode(encoded).decode("utf-8").rstrip("\n")
    return Text.from_ansi(ansi)


def pixel_mark(frame: int = 0, *, running: bool = False) -> Text:
    """Render the compact robot-dog dachshund with a tiny terminal animation."""

    cells, width, height = _animated_dog_cells(frame, running=running)
    return _render_half_block_sprite(cells, width=width, height=height)


def _animated_dog_cells(
    frame: int, *, running: bool
) -> tuple[dict[tuple[int, int], str | None], int, int]:
    """Build one animation frame while keeping the source sprite immutable."""

    width, height = max(map(len, _DESIGNED_DOG)), len(_DESIGNED_DOG)
    cells: dict[tuple[int, int], str | None] = {}
    for y, row in enumerate(_DESIGNED_DOG):
        for x, token in enumerate(row.ljust(width)):
            cells[(x, y)] = _DESIGNED_DOG_COLORS.get(token)

    # The sprite faces left. Move its far-right tail tip inside the fixed box.
    if frame % 2:
        cells[(21, 5)] = None
        cells[(22, 4)] = _DESIGNED_DOG_COLORS["O"]

    # Blink while idle; alternate the feet while a task is running.
    if not running and frame % 8 == 6:
        for point in ((4, 4), (5, 4), (4, 5), (5, 5)):
            cells[point] = _DESIGNED_DOG_COLORS["O"]
    if running:
        foot = range(7, 12) if frame % 2 else range(16, 21)
        for x in foot:
            cells[(x, 17)] = None
    return cells, width, height


def _render_half_block_sprite(
    cells: dict[tuple[int, int], str | None], *, width: int, height: int
) -> Text:
    """Pack two vertical source pixels into each terminal half-block cell."""

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
    fix = f"\n[#9c9689]  fix:[/] {escape(item.fix_suggestion)}" if item.fix_suggestion else ""
    return (
        f"[bold {status_color(item.status)}]{item.status}[/] "
        f"{escape(item.section)}.{escape(item.check_name)}: {escape(item.message)}{fix}"
    )

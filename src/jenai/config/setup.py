"""First-run setup wizard (ASCII banner, provider presets, per-field examples)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from jenai.adapters.locations import ensure_locations_file
from jenai.config.store import build_minimal_config, default_env_file_path, save_config
from jenai.secure_files import atomic_write_text

_BANNER_LINES = (
    "     ██╗███████╗███╗   ██╗ █████╗ ██╗",
    "     ██║██╔════╝████╗  ██║██╔══██╗██║",
    "     ██║█████╗  ██╔██╗ ██║███████║██║",
    "██   ██║██╔══╝  ██║╚██╗██║██╔══██║██║",
    "╚█████╔╝███████╗██║ ╚████║██║  ██║██║",
    " ╚════╝ ╚══════╝╚═╝  ╚═══╝╚═╝  ╚═╝╚═╝",
)
# Warm orange sweep (light→deep), one shade per banner row — the wizard wears
# the same Claude-orange theme as the TUI (see ACCENT in tui/panels.py).
_BANNER_COLORS = ("#f2b28c", "#eda680", "#e69468", "#d97757", "#cb6a49", "#c15f3c")

# TUI palette twins (kept literal: config/ must not import the tui/ layer).
_ACCENT = "#d97757"  # tui.panels.ACCENT
_GREEN = "#7d9b6a"  # tui.panels.GREEN


@dataclass(frozen=True)
class ProviderPreset:
    """One selectable provider recipe: every field doubles as the prompt default."""

    key: str
    title: str
    provider: str
    base_url: str
    api_key_env: str
    model_example: str
    hint: str


PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        key="local",
        title="Local — Ollama",
        provider="ollama",
        base_url="http://localhost:11434/v1",
        api_key_env="",
        model_example="qwen3.6:35b",
        hint="斷網可用；DGX Spark 實測過。先跑 `ollama pull qwen3.6:35b`",
    ),
    ProviderPreset(
        key="nvidia-cloud",
        title="Cloud — NVIDIA NIM",
        provider="nvidia",
        base_url="https://integrate.api.nvidia.com/v1",
        api_key_env="NVIDIA_API_KEY",
        model_example="meta/llama-3.3-70b-instruct",
        hint="金鑰：https://build.nvidia.com（nvapi- 開頭）",
    ),
    ProviderPreset(
        key="openai",
        title="Cloud — OpenAI",
        provider="openai",
        base_url="",
        api_key_env="OPENAI_API_KEY",
        model_example="gpt-4.1-mini",
        hint="金鑰：https://platform.openai.com（sk- 開頭）",
    ),
    ProviderPreset(
        key="custom",
        title="Custom — 任何 OpenAI 相容端點",
        provider="openai",
        base_url="http://localhost:8000/v1",
        api_key_env="",
        model_example="my-model",
        hint="vLLM、LM Studio、llama.cpp server 都是這類",
    ),
)


def _print_banner(console: Console) -> None:
    console.print()
    for line, color in zip(_BANNER_LINES, _BANNER_COLORS, strict=True):
        console.print(f"[bold {color}]{line}[/bold {color}]", highlight=False)
    console.print("[dim]Terminal-first AI agent for ROS 2 robots[/dim]\n", highlight=False)


def _prompt(
    console: Console,
    label: str,
    *,
    default: str,
    example: str = "",
    validator: Callable[[str], bool] | None = None,
    error: str = "輸入值無效。",
    hide_input: bool = False,
) -> str:
    """Prompt until one field is valid, without discarding earlier answers."""
    hint = f"（例：{example}）" if example and example != default else ""
    while True:
        value = str(
            typer.prompt(
                f"  {label}{hint}",
                default=default,
                show_default=bool(default),
                hide_input=hide_input,
            )
        ).strip()
        if validator is None or validator(value):
            return value
        console.print(f"  [red]{error}[/red]", highlight=False)


def _not_blank(value: str) -> bool:
    return bool(value)


def _http_url_or_blank(value: str) -> bool:
    if not value:
        return True
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _secure_api_key_input(
    value: str, preset: ProviderPreset, config_path: Path
) -> tuple[str, Path | None]:
    """Accept an env name, or safely relocate an accidentally pasted key."""
    stripped = value.strip()
    if not stripped or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", stripped):
        return stripped, None

    env_name = preset.api_key_env or "JENAI_API_KEY"
    env_path = default_env_file_path(config_path)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []

    def _assignment_name(line: str) -> str:
        candidate = line.strip()
        if candidate.startswith("export "):
            candidate = candidate[len("export ") :].lstrip()
        return candidate.partition("=")[0].strip()

    lines = [line for line in existing if _assignment_name(line) != env_name]
    lines.append(f"{env_name}={stripped}")
    atomic_write_text(env_path, "\n".join(lines) + "\n")
    return env_name, env_path


def _choose_provider(console: Console) -> ProviderPreset:
    console.print(
        Panel(
            "第一次使用，先接上一個模型供應商。之後隨時可用 [bold]/provider[/bold] 切換、"
            "[bold]JenAI config[/bold] 檢視。",
            title="Setup 1/3 — 選供應商",
            border_style=_ACCENT,
        )
    )
    for index, preset in enumerate(PRESETS, start=1):
        console.print(
            f"  [bold #d97757]{index}[/bold #d97757]. [bold]{preset.title}[/bold]"
            f"  [dim]{preset.hint}[/dim]",
            highlight=False,
        )
    while True:
        raw = typer.prompt("  選擇", default="1").strip()
        if raw in {str(index) for index in range(1, len(PRESETS) + 1)}:
            return PRESETS[int(raw) - 1]
        console.print(f"  [red]請輸入 1–{len(PRESETS)}[/red]")


def run_setup_wizard(config_path: Path) -> Path:
    console = Console()
    _print_banner(console)
    preset = _choose_provider(console)

    console.print(
        Panel(
            "留白直接 Enter 使用預設值；每欄都附範例。",
            title="Setup 2/3 — 連線細節",
            border_style=_ACCENT,
        )
    )
    provider_name = _prompt(
        console,
        "Profile 名稱",
        default=preset.key,
        example="local",
        validator=_not_blank,
        error="Profile 名稱不可留白。",
    )
    default_model = _prompt(
        console,
        "預設模型",
        default=preset.model_example,
        example=preset.model_example,
        validator=_not_blank,
        error="預設模型不可留白。",
    )
    base_url = _prompt(
        console,
        "Base URL（供應商官方端點可留白）",
        default=preset.base_url,
        example="http://localhost:11434/v1",
        validator=_http_url_or_blank,
        error="Base URL 必須是完整的 http:// 或 https:// 網址。",
    )
    api_key_env = _prompt(
        console,
        "API 金鑰環境變數名稱（貼入金鑰會安全搬到 .env；本地模型留白）",
        default=preset.api_key_env,
        example="NVIDIA_API_KEY",
        hide_input=True,
    )
    api_key_env, saved_credential_path = _secure_api_key_input(api_key_env, preset, config_path)

    console.print(
        Panel(
            "地點檔保存 `/loc add here` 建立的導航點。",
            title="Setup 3/3 — 地點檔",
            border_style=_ACCENT,
        )
    )
    locations_path = _prompt(
        console,
        "Locations 檔路徑",
        default="locations.toml",
        example="locations.toml",
        validator=_not_blank,
        error="Locations 檔路徑不可留白。",
    )

    config = build_minimal_config(
        provider_name=provider_name,
        provider=preset.provider,
        default_model=default_model,
        base_url=base_url,
        api_key_env=api_key_env,
        locations_path=locations_path,
    )
    written = save_config(config, config_path)

    resolved_locations_path = config.resolved_locations_path(written)
    if resolved_locations_path is not None:
        ensure_locations_file(resolved_locations_path)

    summary = Table(show_header=False, box=None, padding=(0, 1))
    summary.add_row("[dim]Provider[/dim]", f"{provider_name} ({preset.provider})")
    summary.add_row("[dim]Model[/dim]", default_model)
    summary.add_row("[dim]Base URL[/dim]", base_url or "（供應商預設）")
    summary.add_row("[dim]API key env[/dim]", api_key_env or "（不需要）")
    summary.add_row("[dim]Config[/dim]", str(written))
    summary.add_row("[dim]Locations[/dim]", str(resolved_locations_path or "（未設定）"))
    console.print(Panel(summary, title="✓ 設定完成", border_style=_GREEN))
    if resolved_locations_path is not None:
        console.print(
            f"  [dim]Locations 檔：[/dim][bold]{resolved_locations_path}[/bold]",
            highlight=False,
            soft_wrap=True,
        )
    if saved_credential_path is not None:
        console.print(
            f"  [green]金鑰已安全寫入：[/green] [bold]{saved_credential_path}[/bold] "
            f"（[bold]{api_key_env}[/bold]，權限 0600）",
            highlight=False,
        )
    elif api_key_env:
        env_path = default_env_file_path(config_path)
        console.print(
            f"  [yellow]記得放入金鑰：[/yellow]在 [bold]{env_path}[/bold] 加入 "
            f"[bold]{api_key_env}=你的金鑰[/bold]",
            highlight=False,
        )
    console.print(
        "  下一步：\n"
        "  1. [bold]JenAI doctor[/bold] — 確認模型、ROS 2 與必要檔案\n"
        "  2. [bold]JenAI[/bold] — 進入 TUI，先從 /status 開始\n",
        highlight=False,
    )

    return written

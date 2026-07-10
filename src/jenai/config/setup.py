"""First-run setup wizard (ASCII banner, provider presets, per-field examples)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from jenai.adapters.locations import ensure_locations_file
from jenai.config.store import build_minimal_config, save_config

_BANNER_LINES = (
    "     ██╗███████╗███╗   ██╗ █████╗ ██╗",
    "     ██║██╔════╝████╗  ██║██╔══██╗██║",
    "     ██║█████╗  ██╔██╗ ██║███████║██║",
    "██   ██║██╔══╝  ██║╚██╗██║██╔══██║██║",
    "╚█████╔╝███████╗██║ ╚████║██║  ██║██║",
    " ╚════╝ ╚══════╝╚═╝  ╚═══╝╚═╝  ╚═╝╚═╝",
)
# Cyan→magenta sweep, one shade per banner row.
_BANNER_COLORS = (
    "cyan1", "deep_sky_blue1", "dodger_blue1", "slate_blue1", "medium_purple1", "magenta"
)


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
        model_example="qwen3:8b",
        hint="斷網可用;Jetson 實測過。先跑 `ollama pull qwen3:8b`",
    ),
    ProviderPreset(
        key="nvidia-cloud",
        title="Cloud — NVIDIA NIM",
        provider="nvidia",
        base_url="https://integrate.api.nvidia.com/v1",
        api_key_env="NVIDIA_API_KEY",
        model_example="meta/llama-3.3-70b-instruct",
        hint="金鑰:https://build.nvidia.com(nvapi- 開頭)",
    ),
    ProviderPreset(
        key="openai",
        title="Cloud — OpenAI",
        provider="openai",
        base_url="",
        api_key_env="OPENAI_API_KEY",
        model_example="gpt-4.1-mini",
        hint="金鑰:https://platform.openai.com(sk- 開頭)",
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
    console.print("[dim]Terminal-first AI agent for ROS2 robots[/dim]\n", highlight=False)


def _prompt(label: str, *, default: str, example: str = "") -> str:
    # typer.prompt renders its own [default]; the example rides in the label so
    # every field shows "怎麼填" without extra lines.
    hint = f" (例:{example})" if example and example != default else ""
    return typer.prompt(f"  {label}{hint}", default=default, show_default=bool(default))


def _secure_api_key_input(
    value: str, preset: ProviderPreset, config_path: Path
) -> tuple[str, Path | None]:
    """Accept an env name, or safely relocate an accidentally pasted key."""
    stripped = value.strip()
    if not stripped or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", stripped):
        return stripped, None

    env_name = preset.api_key_env or "JENAI_API_KEY"
    env_path = config_path.parent / ".env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []

    def _assignment_name(line: str) -> str:
        candidate = line.strip()
        if candidate.startswith("export "):
            candidate = candidate[len("export ") :].lstrip()
        return candidate.partition("=")[0].strip()

    lines = [line for line in existing if _assignment_name(line) != env_name]
    lines.append(f"{env_name}={stripped}")
    temporary = env_path.with_name(f".{env_path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        os.replace(temporary, env_path)
        os.chmod(env_path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return env_name, env_path


def run_setup_wizard(config_path: Path) -> Path:
    console = Console()
    _print_banner(console)
    console.print(
        Panel(
            "第一次使用,先接上一個模型供應商。之後隨時可用 [bold]/provider[/bold] 切換、"
            "[bold]JenAI config[/bold] 檢視。",
            title="Setup 1/3 — 選供應商",
            border_style="cyan",
        )
    )
    for idx, preset in enumerate(PRESETS, start=1):
        console.print(
            f"  [bold cyan]{idx}[/bold cyan]. [bold]{preset.title}[/bold]"
            f"  [dim]{preset.hint}[/dim]",
            highlight=False,
        )
    while True:
        raw = typer.prompt("  選擇", default="1")
        if raw.strip() in {str(i) for i in range(1, len(PRESETS) + 1)}:
            preset = PRESETS[int(raw.strip()) - 1]
            break
        console.print(f"  [red]請輸入 1–{len(PRESETS)}[/red]")

    console.print(
        Panel(
            "留白直接 Enter 用預設值;每欄都附範例。",
            title="Setup 2/3 — 連線細節",
            border_style="cyan",
        )
    )
    provider_name = _prompt("Profile 名稱", default=preset.key, example="local")
    default_model = _prompt("預設模型", default=preset.model_example, example=preset.model_example)
    base_url = _prompt(
        "Base URL(供應商官方端點可留白)", default=preset.base_url, example="http://localhost:11434/v1"
    )
    api_key_env = _prompt(
        "API 金鑰環境變數名稱(貼入金鑰會安全搬到 .env;本地模型留白)",
        default=preset.api_key_env,
        example="NVIDIA_API_KEY",
    )
    api_key_env, saved_credential_path = _secure_api_key_input(
        api_key_env, preset, config_path
    )

    console.print(
        Panel(
            "地點檔存 `/loc add here` 建的導航點。",
            title="Setup 3/3 — 地點檔",
            border_style="cyan",
        )
    )
    locations_path = _prompt("Locations 檔路徑", default="locations.toml", example="locations.toml")

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
    summary.add_row("[dim]Base URL[/dim]", base_url or "(供應商預設)")
    summary.add_row("[dim]API key env[/dim]", api_key_env or "(不需要)")
    summary.add_row("[dim]Config[/dim]", str(written))
    console.print(Panel(summary, title="✓ 設定完成", border_style="green"))
    if saved_credential_path is not None:
        console.print(
            f"  [green]金鑰已安全寫入:[/green] [bold]{saved_credential_path}[/bold] "
            f"([bold]{api_key_env}[/bold],權限 0600)",
            highlight=False,
        )
    elif api_key_env:
        console.print(
            f"  [yellow]記得放金鑰:[/yellow]在 [bold]~/.config/jenai/.env[/bold] 加一行 "
            f"[bold]{api_key_env}=你的金鑰[/bold]",
            highlight=False,
        )
    console.print(
        "  下一步:[bold]JenAI doctor[/bold] 健檢 → [bold]JenAI[/bold] 進 TUI\n", highlight=False
    )

    return written

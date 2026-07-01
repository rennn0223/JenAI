from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from jenai import __version__
from jenai.adapters.locations import (
    LocationNotFoundError,
    LocationsFileError,
    ensure_locations_file,
    find_location,
    load_locations,
)
from jenai.config import ConfigError, default_config_path, load_config
from jenai.config.models import AppConfig
from jenai.config.setup import run_setup_wizard
from jenai.doctor import run_doctor
from jenai.schemas import DoctorResult, DoctorStatus, Location
from jenai.tools.route_core import route_execute, route_preview
from jenai.tui import run_tui, status_color

app = typer.Typer(
    name="JenAI",
    help="JenAI terminal-first AI agent interface for ROS2 robot workflows.",
    no_args_is_help=False,
)
loc_app = typer.Typer(help="Manage locations.")
app.add_typer(loc_app, name="loc")
console = Console()


ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", help="Path to JenAI config file."),
]


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    config: ConfigOption = None,
    debug: Annotated[bool, typer.Option("--debug", help="Show debug details.")] = False,
) -> None:
    if ctx.invoked_subcommand is not None:
        return

    config_path = config or default_config_path()
    try:
        loaded = load_config(config_path)
    except ConfigError:
        console.print("[bold]No complete JenAI config found.[/bold]")
        written = run_setup_wizard(config_path)
        console.print(f"Config written to {written}")
        raise typer.Exit(0) from None

    if not loaded.is_complete():
        console.print("[yellow]JenAI config is incomplete. Starting setup wizard.[/yellow]")
        written = run_setup_wizard(config_path)
        console.print(f"Config written to {written}")
        raise typer.Exit(0)

    doctor_result = run_doctor(config_path)
    blocking = [
        item
        for item in doctor_result.items
        if item.status == DoctorStatus.FAIL and item.section in {"config", "provider"}
    ]
    if blocking:
        console.print("[red]Startup checks failed. Run JenAI doctor for details.[/red]")
        if debug:
            _print_doctor_result(doctor_result)
        raise typer.Exit(1)

    try:
        run_tui(loaded, config_path=config_path, doctor_result=doctor_result)
    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"[red]JenAI TUI exited unexpectedly: {exc}[/red]")
        if debug:
            console.print_exception()
        raise typer.Exit(1) from exc


@app.command()
def doctor(
    config: ConfigOption = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    result = run_doctor(config)
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        _print_doctor_result(result)


@app.command()
def config(config: ConfigOption = None) -> None:
    config_path = config or default_config_path()
    try:
        loaded = load_config(config_path)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(json.dumps(loaded.model_dump(mode="json"), ensure_ascii=False, indent=2))


@app.command()
def providers(config: ConfigOption = None) -> None:
    config_path = config or default_config_path()
    try:
        loaded = load_config(config_path)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    table = Table(title="JenAI providers")
    table.add_column("Active")
    table.add_column("Name")
    table.add_column("Provider")
    table.add_column("Base URL")
    table.add_column("API Key Env")
    for name, profile in loaded.provider_profiles.items():
        table.add_row(
            "*" if name == loaded.active_provider else "",
            profile.name,
            profile.provider,
            profile.base_url or "",
            profile.api_key_env or "",
        )
    console.print(table)


@app.command()
def models(config: ConfigOption = None) -> None:
    config_path = config or default_config_path()
    try:
        loaded = load_config(config_path)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    if loaded.model_bindings is None:
        console.print("[yellow]No model bindings configured.[/yellow]")
        raise typer.Exit(1)

    table = Table(title="JenAI model bindings")
    table.add_column("Binding")
    table.add_column("Model")
    for name, value in loaded.model_bindings.model_dump().items():
        table.add_row(name, str(value))
    console.print(table)


@app.command()
def route(text: str, config: ConfigOption = None) -> None:
    config_path = config or default_config_path()
    try:
        loaded = load_config(config_path)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    locations = _load_locations_for_cli(loaded, config_path)
    output = asyncio.run(route_preview(loaded, locations, text))
    console.print(output.route_preview)
    if not output.outgoing_action:
        raise typer.Exit(1)

    if not typer.confirm("Send this route?"):
        console.print("[yellow]Cancelled.[/yellow]")
        raise typer.Exit(0)

    result = asyncio.run(route_execute(loaded, output.outgoing_action))
    console.print(f"[green]{result.execution_status}[/green]")


@app.command()
def web(
    config: ConfigOption = None,
    host: Annotated[str, typer.Option("--host", help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Bind port.")] = 8760,
) -> None:
    config_path = config or default_config_path()
    try:
        loaded = load_config(config_path)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    from jenai.webui import serve

    console.print(f"[green]JenAI WebUI serving at http://{host}:{port}[/green] (Ctrl-C to stop)")
    serve(loaded, config_path, host=host, port=port)


@app.command("version")
def version_command() -> None:
    console.print(f"JenAI {__version__}")


@loc_app.command("list")
def loc_list(config: ConfigOption = None) -> None:
    config_path = config or default_config_path()
    try:
        loaded = load_config(config_path)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    locations = _load_locations_for_cli(loaded, config_path)
    if not locations:
        console.print("[yellow]No locations configured.[/yellow]")
        raise typer.Exit(1)

    table = Table(title="JenAI locations")
    table.add_column("Name")
    table.add_column("Aliases")
    table.add_column("Tags")
    for location in locations:
        table.add_row(location.name, ", ".join(location.aliases), ", ".join(location.tags))
    console.print(table)


@loc_app.command("show")
def loc_show(name: str, config: ConfigOption = None) -> None:
    config_path = config or default_config_path()
    try:
        loaded = load_config(config_path)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    locations = _load_locations_for_cli(loaded, config_path)
    try:
        location = find_location(locations, name)
    except LocationNotFoundError as exc:
        if exc.candidates:
            names = ", ".join(loc.name for loc in exc.candidates)
            console.print(f"[yellow]Location '{name}' not found. Did you mean: {names}?[/yellow]")
        else:
            console.print(f"[yellow]Location '{name}' not found.[/yellow]")
        raise typer.Exit(1) from exc

    console.print(json.dumps(location.model_dump(mode="json"), ensure_ascii=False, indent=2))


def _load_locations_for_cli(loaded: AppConfig, config_path: Path) -> list[Location]:
    locations_path = loaded.resolved_locations_path(config_path)
    if locations_path is None:
        return []
    try:
        ensure_locations_file(locations_path)
        return load_locations(locations_path)
    except LocationsFileError:
        return []


def _print_doctor_result(result: DoctorResult) -> None:
    table = Table(title=f"JenAI doctor: {result.overall}")
    table.add_column("Section")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Message")
    table.add_column("Fix")

    for item in result.items:
        style = status_color(item.status)
        table.add_row(
            item.section,
            item.check_name,
            f"[{style}]{item.status}[/{style}]",
            item.message,
            item.fix_suggestion or "",
        )
    console.print(table)

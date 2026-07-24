"""Operator commands for importing, activating, and checking Site Profiles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from jenai.adapters.locations import LocationsFileError, find_dock, load_locations
from jenai.bridge import BridgeError
from jenai.config import (
    AppConfig,
    ConfigError,
    default_config_path,
    load_config,
    save_config,
)
from jenai.doctor.site import check_site, read_live_map_identity
from jenai.schemas import DoctorStatus
from jenai.site_profiles import (
    SiteProfileDocumentError,
    activate_site_profile,
    build_site_profile_draft,
    deactivate_site_profile,
    load_site_profile_document,
    revalidate_active_site_profile,
    write_site_profile_draft,
)

site_app = typer.Typer(help="Manage the validated operating-site binding.")
console = Console()
err_console = Console(stderr=True)

ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", help="Path to JenAI config file."),
]


def _load(path: Path) -> AppConfig:
    try:
        return load_config(path)
    except ConfigError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@site_app.command("init")
def initialize(
    profile: Annotated[
        Path | None,
        typer.Argument(help="Draft path; defaults beside the JenAI config."),
    ] = None,
    config: ConfigOption = None,
    site_id: Annotated[
        str,
        typer.Option("--site-id", help="Stable identifier for this operating site."),
    ] = "site-draft",
    display_name: Annotated[
        str,
        typer.Option("--name", help="Human-readable site name."),
    ] = "Site draft",
    reference_scene: Annotated[
        str | None,
        typer.Option("--scene", help="Optional simulator scene or physical-site reference."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Replace an existing draft; never activates it."),
    ] = False,
) -> None:
    """Create a reviewable, inactive draft from the live map and saved locations."""

    config_path = config or default_config_path()
    loaded = _load(config_path)
    locations_path = loaded.resolved_locations_path(config_path)
    locations_reference = (
        loaded.site.locations_path if loaded.site.active else loaded.locations_path
    )
    if locations_path is None or locations_reference is None:
        err_console.print(
            "[red]No locations file is configured. Run onboarding and save locations first.[/red]"
        )
        raise typer.Exit(1)

    try:
        locations = load_locations(locations_path)
        observed = read_live_map_identity()
        dock = find_dock(locations)
        draft = build_site_profile_draft(
            site_id=site_id,
            display_name=display_name,
            map_sha256=observed.digest,
            map_frame=observed.frame_id,
            locations_path=locations_reference,
            locations=locations,
            dock_location=dock.name if dock is not None else None,
            reference_scene=reference_scene,
        )
        destination = profile or config_path.parent / "site-profile.toml"
        written = write_site_profile_draft(draft, destination, overwrite=force)
    except (BridgeError, LocationsFileError, OSError, SiteProfileDocumentError) as exc:
        err_console.print(f"[red]Could not create Site Profile draft: {exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"[green]Created inactive Site Profile draft: {written}[/green]")
    console.print(
        f"[dim]Map {observed.digest[:12]} · {len(locations)} route(s) · "
        f"{len(draft.patrol_areas)} generated patrol area(s).[/dim]"
    )
    if dock is None:
        console.print(
            "[yellow]No Dock-tagged location was found; set home_location and "
            "dock_location manually if required.[/yellow]"
        )
    console.print("[yellow]Review area grouping and required flags before activation.[/yellow]")
    console.print(f"[dim]Next: JenAI site activate {written}[/dim]")


@site_app.command("status")
def status(
    config: ConfigOption = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Show the configured site without probing ROS."""

    config_path = config or default_config_path()
    site = _load(config_path).site
    payload = site.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    table = Table(title="JenAI Site Profile")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Active", str(site.active))
    table.add_row("Validated", str(site.validated))
    table.add_row("Site", f"{site.display_name} ({site.site_id})")
    table.add_row("Version", site.version)
    table.add_row("Map", site.map_sha256[:12] if site.map_sha256 else "not bound")
    table.add_row(
        "Locations",
        site.locations_sha256[:12] if site.locations_sha256 else "not bound",
    )
    table.add_row("Home", site.home_location or "not configured")
    table.add_row("Dock", site.dock_location or "not configured")
    table.add_row("Patrol areas", str(len(site.patrol_areas)))
    console.print(table)


@site_app.command("map-identity")
def map_identity(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Read the current ROS occupancy-map fingerprint without changing config."""

    try:
        observed = read_live_map_identity()
    except BridgeError as exc:
        err_console.print(f"[red]Could not read the live ROS map: {exc}[/red]")
        raise typer.Exit(1) from exc

    payload = {
        "algorithm": observed.algorithm,
        "digest": observed.digest,
        "frame_id": observed.frame_id,
        "width": observed.width,
        "height": observed.height,
        "resolution": observed.resolution,
        "origin_x": observed.origin_x,
        "origin_y": observed.origin_y,
        "origin_yaw": observed.origin_yaw,
        "source": observed.source,
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    table = Table(title="Live ROS Map Identity")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("SHA-256", observed.digest)
    table.add_row("Frame", observed.frame_id)
    table.add_row("Size", f"{observed.width} × {observed.height}")
    table.add_row("Resolution", f"{observed.resolution:g} m/cell")
    table.add_row(
        "Origin", f"({observed.origin_x:g}, {observed.origin_y:g}, {observed.origin_yaw:g})"
    )
    table.add_row("Source", observed.source)
    console.print(table)


@site_app.command("activate")
def activate(
    profile: Annotated[Path, typer.Argument(help="Strict [site] TOML document.")],
    config: ConfigOption = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Replace an already active Site Profile."),
    ] = False,
) -> None:
    """Validate an imported profile and atomically activate it."""

    config_path = config or default_config_path()
    loaded = _load(config_path)
    if loaded.site.active and not yes:
        if not typer.confirm(f"Replace active Site Profile '{loaded.site.display_name}'?"):
            console.print("[yellow]Site Profile activation cancelled.[/yellow]")
            raise typer.Exit(0)
    try:
        imported = load_site_profile_document(profile)
        activated = activate_site_profile(loaded, config_path, imported)
        save_config(activated, config_path)
    except (OSError, SiteProfileDocumentError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(
        f"[green]Activated Site Profile '{activated.site.display_name}' "
        f"({activated.site.site_id}) with "
        f"{len(activated.site.patrol_areas)} patrol area(s).[/green]"
    )
    console.print(
        "[dim]Run 'JenAI site validate' with the validated map and Nav2 stack active.[/dim]"
    )


@site_app.command("validate")
def validate(
    config: ConfigOption = None,
    repair: Annotated[
        bool,
        typer.Option(
            "--repair",
            help="Explicitly bind a legacy profile to the current locations file.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Confirm legacy profile revalidation."),
    ] = False,
) -> None:
    """Verify local assets and the live ROS map identity."""

    config_path = config or default_config_path()
    loaded = _load(config_path)
    if not loaded.site.active:
        err_console.print("[red]No active Site Profile to validate.[/red]")
        raise typer.Exit(1)

    if repair:
        if not yes and not typer.confirm(
            "Revalidate this profile against the live map and current locations file?"
        ):
            console.print("[yellow]Site Profile revalidation cancelled.[/yellow]")
            raise typer.Exit(0)
        try:
            observed = read_live_map_identity()
            loaded = revalidate_active_site_profile(
                loaded,
                config_path,
                observed_map_sha256=observed.digest,
                observed_map_frame=observed.frame_id,
            )
            save_config(loaded, config_path)
        except (BridgeError, OSError, SiteProfileDocumentError) as exc:
            err_console.print(f"[red]Site Profile revalidation failed: {exc}[/red]")
            raise typer.Exit(1) from exc
        console.print(
            f"[green]Revalidated Site Profile '{loaded.site.display_name}' against "
            "the live map and current locations.[/green]"
        )

    items = check_site(loaded, config_path)
    if not items:
        err_console.print("[red]Site validation produced no checks.[/red]")
        raise typer.Exit(1)
    failed = False
    for item in items:
        status = DoctorStatus(item.status)
        color = {
            DoctorStatus.PASS: "green",
            DoctorStatus.WARN: "yellow",
            DoctorStatus.FAIL: "red",
        }[status]
        console.print(f"[{color}]{status.value}[/{color}] {item.check_name}: {item.message}")
        if item.fix_suggestion:
            console.print(f"  [dim]fix: {item.fix_suggestion}[/dim]")
        failed = failed or status is DoctorStatus.FAIL
    if failed:
        raise typer.Exit(1)


@site_app.command("deactivate")
def deactivate(
    config: ConfigOption = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the deactivation confirmation."),
    ] = False,
) -> None:
    """Disable navigation trust while preserving all profile metadata."""

    config_path = config or default_config_path()
    loaded = _load(config_path)
    if not loaded.site.active:
        console.print("[yellow]Site Profile is already inactive.[/yellow]")
        return
    if not yes and not typer.confirm(f"Deactivate Site Profile '{loaded.site.display_name}'?"):
        console.print("[yellow]Site Profile deactivation cancelled.[/yellow]")
        raise typer.Exit(0)
    try:
        save_config(deactivate_site_profile(loaded), config_path)
    except OSError as exc:
        err_console.print(f"[red]Could not save config: {exc}[/red]")
        raise typer.Exit(1) from exc
    console.print("[green]Site Profile deactivated; navigation now fails closed.[/green]")

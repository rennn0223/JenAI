"""Safe CLI for inspecting, exporting and deleting JenAI local data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from jenai.config import default_config_path
from jenai.state.data_hardening import apply_hardening, build_hardening_plan
from jenai.state.data_lifecycle import (
    data_status,
    export_data,
    find_prune_candidates,
    prune_data,
    purge_data,
    purge_targets,
    resolve_data_paths,
)

data_app = typer.Typer(
    help="Inspect, harden, export, retain, or purge private local JenAI data.",
    no_args_is_help=False,
)
console = Console()

ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", help="Path to JenAI config file."),
]


@data_app.callback(invoke_without_command=True)
def data_main(ctx: typer.Context) -> None:
    """Show safe data controls without requiring application setup."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@data_app.command("status")
def status_command(
    config: ConfigOption = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """Show the location, size and mode of each operational data category."""
    paths = resolve_data_paths(config or default_config_path())
    rows = data_status(paths)
    if json_output:
        typer.echo(
            json.dumps(
                [
                    {
                        "category": row.category,
                        "path": str(row.path),
                        "exists": row.exists,
                        "files": row.files,
                        "bytes": row.bytes,
                        "mode": row.mode,
                        "insecure": row.insecure,
                        "refused": row.refused,
                        "permissions_ok": row.permissions_ok,
                    }
                    for row in rows
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    table = Table(title="JenAI local data (read-only status)")
    table.add_column("Category")
    table.add_column("Files", justify="right")
    table.add_column("Bytes", justify="right")
    table.add_column("Mode")
    table.add_column("Permissions")
    table.add_column("Path", overflow="fold")
    for row in rows:
        table.add_row(
            row.category,
            str(row.files),
            str(row.bytes),
            row.mode,
            ("ok" if row.permissions_ok else f"insecure={row.insecure}, refused={row.refused}"),
            str(row.path),
        )
    console.print(table)
    console.print(
        "[dim]Config, credentials, and config backups are excluded from routine export "
        "and default purge.[/dim]"
    )


@data_app.command("harden")
def harden_command(
    config: ConfigOption = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show safe chmod operations; change nothing."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Apply the displayed hardening plan."),
    ] = False,
) -> None:
    """Migrate legacy operational data to 0700 directories and 0600 files."""
    paths = resolve_data_paths(config or default_config_path())
    plan = build_hardening_plan(paths)
    table = Table(title="Legacy data hardening plan")
    table.add_column("Category")
    table.add_column("Kind")
    table.add_column("Mode")
    table.add_column("Path", overflow="fold")
    for candidate in plan.candidates:
        table.add_row(
            candidate.category,
            candidate.kind,
            f"{candidate.current_mode:04o} -> {candidate.target_mode:04o}",
            str(candidate.path),
        )
    console.print(table)
    if plan.refusals:
        refused = Table(title="Refused unsafe paths")
        refused.add_column("Category")
        refused.add_column("Reason")
        refused.add_column("Path", overflow="fold")
        for item in plan.refusals:
            refused.add_row(item.category, item.reason, str(item.path))
        console.print(refused)
    if dry_run:
        console.print("[yellow]Dry run: no permissions were changed.[/yellow]")
        return
    if not plan.candidates:
        if plan.refusals:
            console.print(
                "[red]No safe changes applied; refused paths require manual review.[/red]"
            )
            raise typer.Exit(1)
        console.print("[green]Operational data permissions already satisfy policy.[/green]")
        return
    if not yes and not typer.confirm("Apply exactly these safe chmod operations?"):
        console.print("[yellow]Hardening cancelled; no permissions were changed.[/yellow]")
        return
    result = apply_hardening(plan)
    console.print(
        f"[green]Hardened {result.hardened} path(s); "
        f"skipped {result.skipped} changed/unsafe path(s).[/green]"
    )
    if plan.refusals or result.skipped:
        raise typer.Exit(1)


@data_app.command("export")
def export_command(
    destination: Annotated[
        Path,
        typer.Argument(help="Destination .tar.gz archive."),
    ],
    config: ConfigOption = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Atomically replace an existing archive."),
    ] = False,
) -> None:
    """Export operational data; config, backups, .env and secret values are excluded."""
    destination = destination.expanduser()
    if destination.exists() and not force:
        console.print(f"[red]Destination exists: {destination}. Use --force to replace it.[/red]")
        raise typer.Exit(1)
    paths = resolve_data_paths(config or default_config_path())
    try:
        output, files = export_data(paths, destination)
    except (OSError, ValueError) as exc:
        console.print(f"[red]Export failed; no existing archive was changed: {exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]Exported {files} data file(s) to {output} (mode 0600).[/green]")
    console.print(
        "[dim]Config and credential files were not included; secrets were redacted.[/dim]"
    )


@data_app.command("purge")
def purge_command(
    config: ConfigOption = None,
    include_locations: Annotated[
        bool,
        typer.Option(
            "--include-locations",
            help="Also delete saved navigation locations (excluded by default).",
        ),
    ] = False,
    include_config: Annotated[
        bool,
        typer.Option(
            "--include-config",
            help="Also delete config.toml (separate explicit opt-in).",
        ),
    ] = False,
    include_credentials: Annotated[
        bool,
        typer.Option(
            "--include-credentials",
            help="Also delete the .env credential file (separate explicit opt-in).",
        ),
    ] = False,
    include_config_backups: Annotated[
        bool,
        typer.Option(
            "--include-config-backups",
            help="Also delete timestamped config.toml.bak-* files.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show the exact deletion plan; change nothing."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Confirm the displayed deletion plan."),
    ] = False,
) -> None:
    """Delete local state only after an exact plan and explicit confirmation."""
    paths = resolve_data_paths(config or default_config_path())
    targets = purge_targets(
        paths,
        include_locations=include_locations,
        include_config=include_config,
        include_credentials=include_credentials,
        include_config_backups=include_config_backups,
    )
    _print_targets("Purge plan", targets)
    if not include_config:
        console.print("[dim]Preserving config (use --include-config to opt in).[/dim]")
    if not include_credentials:
        console.print("[dim]Preserving credentials (use --include-credentials to opt in).[/dim]")
    if not include_locations:
        console.print("[dim]Preserving locations (use --include-locations to opt in).[/dim]")
    if not include_config_backups:
        console.print(
            "[dim]Preserving config backups (use --include-config-backups to opt in).[/dim]"
        )
    if dry_run:
        console.print("[yellow]Dry run: nothing was deleted.[/yellow]")
        return

    if not yes and not typer.confirm("Permanently delete exactly the paths above?"):
        console.print("[yellow]Purge cancelled; nothing was deleted.[/yellow]")
        return
    protected = tuple(
        path
        for enabled, path in (
            (include_locations, paths.locations),
            (include_config, paths.config),
            (include_credentials, paths.credentials),
            *(
                (include_config_backups, backup)
                for backup in paths.config_backups
            ),
        )
        if not enabled
    )
    removed = purge_data(targets, protected_paths=protected)
    console.print(f"[green]Purged {len(removed)} existing target(s).[/green]")


@data_app.command("prune")
def prune_command(
    config: ConfigOption = None,
    older_than_days: Annotated[
        int,
        typer.Option(
            "--older-than-days",
            min=1,
            help="Delete old session/pending/report files and trace/audit rows.",
        ),
    ] = 30,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show stale data; change nothing."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Confirm deletion without an interactive prompt."),
    ] = False,
) -> None:
    """Apply a configurable age-based retention policy to generated history."""
    paths = resolve_data_paths(config or default_config_path())
    candidates = find_prune_candidates(paths, older_than_days=older_than_days)
    if not candidates:
        console.print(f"[green]No data older than {older_than_days} day(s).[/green]")
        return
    table = Table(title=f"Retention plan (> {older_than_days} days)")
    table.add_column("Category")
    table.add_column("Records", justify="right")
    table.add_column("Path", overflow="fold")
    for candidate in candidates:
        records = str(candidate.stale_records) if candidate.stale_records else "file"
        table.add_row(candidate.category, records, str(candidate.path))
    console.print(table)
    if dry_run:
        console.print("[yellow]Dry run: nothing was deleted.[/yellow]")
        return
    if not yes and not typer.confirm("Apply this retention plan?"):
        console.print("[yellow]Prune cancelled; nothing was deleted.[/yellow]")
        return
    files, records = prune_data(candidates, older_than_days=older_than_days)
    console.print(f"[green]Pruned {files} file(s) and {records} trace record(s).[/green]")


def _print_targets(title: str, targets: list[tuple[str, Path]]) -> None:
    table = Table(title=title)
    table.add_column("Category")
    table.add_column("Path", overflow="fold")
    table.add_column("Exists")
    for category, path in targets:
        table.add_row(category, str(path), "yes" if path.exists() else "no")
    console.print(table)

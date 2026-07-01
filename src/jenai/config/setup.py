from __future__ import annotations

from pathlib import Path

import typer

from jenai.adapters.locations import ensure_locations_file
from jenai.config.store import build_minimal_config, save_config


def run_setup_wizard(config_path: Path) -> Path:
    typer.echo("JenAI setup wizard")
    typer.echo("Configure a minimal provider profile. You can edit it later with JenAI config.")

    provider_name = typer.prompt("Provider profile name", default="default")
    provider = typer.prompt("Provider type", default="openai")
    default_model = typer.prompt("Default model", default="gpt-4.1-mini")
    base_url = typer.prompt("Base URL (blank for provider default)", default="", show_default=False)
    api_key_env = typer.prompt("API key environment variable", default="OPENAI_API_KEY")
    locations_path = typer.prompt("Locations file path", default="locations.toml")

    config = build_minimal_config(
        provider_name=provider_name,
        provider=provider,
        default_model=default_model,
        base_url=base_url,
        api_key_env=api_key_env,
        locations_path=locations_path,
    )
    written = save_config(config, config_path)

    resolved_locations_path = config.resolved_locations_path(written)
    if resolved_locations_path is not None:
        ensure_locations_file(resolved_locations_path)

    return written


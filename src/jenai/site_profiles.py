"""Import and activate versioned Site Profiles without trusting self-attestation."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import tomli_w
from pydantic import ValidationError

from jenai.config.models import AppConfig, PatrolAreaProfile, SiteProfile
from jenai.schemas import Location
from jenai.secure_files import atomic_write_text
from jenai.site_assets import (
    SiteAssetError,
    fingerprint_locations_file,
    validate_site_assets,
)


class SiteProfileDocumentError(ValueError):
    """A Site Profile document is malformed or cannot be activated safely."""


def load_site_profile_document(path: Path) -> SiteProfile:
    """Load one strict [site] TOML document.

    active, validated, and locations_sha256 are accepted for round-tripping
        but are never trusted by activate_site_profile.
    """

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SiteProfileDocumentError(f"Site Profile not found: {path}") from exc
    except OSError as exc:
        raise SiteProfileDocumentError(f"Could not read Site Profile: {exc}") from exc
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise SiteProfileDocumentError(f"Site Profile is not valid UTF-8 TOML: {exc}") from exc

    if set(raw) != {"site"} or not isinstance(raw["site"], dict):
        raise SiteProfileDocumentError(
            "Site Profile document must contain exactly one [site] table."
        )
    try:
        return SiteProfile.model_validate(raw["site"])
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_input=False)
        )
        raise SiteProfileDocumentError(f"Invalid Site Profile: {details}") from exc


def _unique_area_id(name: str, index: int, used: set[str]) -> str:
    """Return a stable TOML-safe area id without guessing site semantics."""

    base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or f"area-{index}"
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def build_site_profile_draft(
    *,
    site_id: str,
    display_name: str,
    map_sha256: str,
    map_frame: str,
    locations_path: str,
    locations: list[Location],
    dock_location: str | None = None,
    reference_scene: str | None = None,
) -> SiteProfile:
    """Build an untrusted draft from observed assets.

    Every non-dock saved location becomes a one-point required patrol area.
    This is intentionally a reviewable bootstrap, not an activated semantic
    claim: operators may group, rename, or mark areas optional before running
    ``site activate``.
    """

    if not locations:
        raise SiteProfileDocumentError(
            "A Site Profile draft requires at least one registered location."
        )
    names = [location.name for location in locations]
    if dock_location is not None and dock_location not in names:
        raise SiteProfileDocumentError(f"Draft dock location '{dock_location}' is not registered.")

    used_ids: set[str] = set()
    areas = [
        PatrolAreaProfile(
            area_id=_unique_area_id(location.name, index, used_ids),
            display_name=location.name,
            inspection_locations=[location.name],
            required=True,
        )
        for index, location in enumerate(locations, start=1)
        if location.name != dock_location
    ]
    return SiteProfile(
        site_id=site_id,
        display_name=display_name,
        version="1",
        map_sha256=map_sha256,
        map_frame=map_frame,
        reference_scene=reference_scene,
        locations_path=locations_path,
        validated_routes=names,
        home_location=dock_location,
        dock_location=dock_location,
        patrol_areas=areas,
    )


def write_site_profile_draft(
    profile: SiteProfile,
    path: Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Write a private import document without serializing trust fields."""

    destination = path.expanduser()
    if not overwrite and (destination.exists() or destination.is_symlink()):
        raise SiteProfileDocumentError(
            f"Site Profile draft already exists: {destination}. Use --force to replace it."
        )
    payload = profile.model_dump(
        mode="python",
        exclude_none=True,
        exclude={"active", "validated", "locations_sha256"},
    )
    return atomic_write_text(destination, tomli_w.dumps({"site": payload}))


def activate_site_profile(
    config: AppConfig,
    config_path: Path,
    imported: SiteProfile,
) -> AppConfig:
    """Return a validated config with imported activated.

    The imported document cannot grant trust to itself. JenAI recomputes the
    locations digest, validates every route/home/dock/area reference, and only
    then sets validated and active.
    """

    if imported.map_sha256 is None:
        raise SiteProfileDocumentError("Site Profile activation requires a validated map_sha256.")
    locations_reference = imported.locations_path or config.locations_path
    if locations_reference is None:
        raise SiteProfileDocumentError("Site Profile activation requires a locations_path.")
    locations_path = Path(locations_reference).expanduser()
    if not locations_path.is_absolute():
        locations_path = config_path.parent / locations_path
    try:
        locations_digest = fingerprint_locations_file(locations_path)
    except OSError as exc:
        raise SiteProfileDocumentError(
            f"Could not fingerprint Site Profile locations: {exc}"
        ) from exc

    site_data = imported.model_dump(mode="python")
    site_data.update(
        {
            "active": True,
            "validated": True,
            "locations_path": locations_reference,
            "locations_sha256": locations_digest,
        }
    )
    config_data = config.model_dump(mode="python")
    config_data["site"] = site_data
    try:
        candidate = AppConfig.model_validate(config_data)
        validate_site_assets(candidate, config_path)
    except (ValidationError, SiteAssetError) as exc:
        raise SiteProfileDocumentError(f"Site Profile activation failed: {exc}") from exc
    return candidate


def deactivate_site_profile(config: AppConfig) -> AppConfig:
    """Return a config that preserves the profile but disables navigation trust."""

    config_data = config.model_dump(mode="python")
    site_data = config.site.model_dump(mode="python")
    site_data["active"] = False
    config_data["site"] = site_data
    return AppConfig.model_validate(config_data)


def revalidate_active_site_profile(
    config: AppConfig,
    config_path: Path,
    *,
    observed_map_sha256: str,
    observed_map_frame: str,
) -> AppConfig:
    """Re-establish trust for a selected legacy profile.

    This is deliberately explicit and fail-closed: the live map must match
    the profile's previously recorded identity. The function only adds the
    missing locations fingerprint; it never changes the expected map.
    """

    site = config.site
    if not site.active:
        raise SiteProfileDocumentError("No active Site Profile to revalidate.")
    if site.map_sha256 is None:
        raise SiteProfileDocumentError(
            "The active Site Profile has no expected map identity; create and activate a "
            "new profile instead."
        )
    if observed_map_frame != site.map_frame:
        raise SiteProfileDocumentError(
            f"Live map frame '{observed_map_frame}' does not match expected '{site.map_frame}'."
        )
    if observed_map_sha256 != site.map_sha256:
        raise SiteProfileDocumentError(
            "Live map identity does not match the active Site Profile; activate the correct "
            "profile instead of repairing this one."
        )

    locations_path = config.resolved_locations_path(config_path)
    if locations_path is None:
        raise SiteProfileDocumentError("The active Site Profile has no locations file.")
    try:
        locations_digest = fingerprint_locations_file(locations_path)
    except OSError as exc:
        raise SiteProfileDocumentError(
            f"Could not fingerprint Site Profile locations: {exc}"
        ) from exc

    config_data = config.model_dump(mode="python")
    site_data = site.model_dump(mode="python")
    site_data.update(
        {
            "validated": True,
            "locations_sha256": locations_digest,
        }
    )
    config_data["site"] = site_data
    candidate = AppConfig.model_validate(config_data)
    try:
        validate_site_assets(candidate, config_path)
    except SiteAssetError as exc:
        raise SiteProfileDocumentError(f"Site Profile revalidation failed: {exc}") from exc
    return candidate

"""Validated Site Profile assets used by every navigation surface."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import ValidationError

from jenai.adapters.locations import (
    LocationsFileError,
    LocationsSnapshot,
    load_locations_snapshot,
)
from jenai.config.models import AppConfig
from jenai.schemas import Location
from jenai.workflows.area_patrol import InspectionPoint, PatrolArea


def fingerprint_locations_file(path: Path) -> str:
    """Return the SHA-256 identity of the exact saved locations document."""

    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


class SiteAssetError(ValueError):
    """The active Site Profile cannot authorize the requested coordinates."""


def _location_matches_reference(location: Location, reference: str) -> bool:
    normalized = reference.strip().casefold()
    return normalized in {location.id.casefold(), location.name.casefold()}


def resolve_site_location(locations: list[Location], reference: str) -> Location:
    """Resolve an exact Site Profile location id or name."""

    for location in locations:
        if _location_matches_reference(location, reference):
            return location
    raise SiteAssetError(f"Site Profile references unknown location '{reference}'.")


def _validated_location(
    locations: list[Location],
    validated_routes: list[str],
    reference: str,
    *,
    owner: str,
) -> Location:
    location = resolve_site_location(locations, reference)
    if not any(_location_matches_reference(location, candidate) for candidate in validated_routes):
        raise SiteAssetError(f"{owner} location '{reference}' is not listed in validated_routes.")
    return location


def _find_bound_location(locations: list[Location], raw_goal: dict[str, object]) -> Location:
    raw_id = str(raw_goal.get("id", "")).strip()
    raw_name = str(raw_goal.get("name", "")).strip().casefold()
    for location in locations:
        if raw_id and location.id == raw_id:
            return location
        if raw_name and location.name.casefold() == raw_name:
            return location
    raise SiteAssetError("Navigation goal is not registered in the active Site Profile.")


def _same_pose(requested: Location, saved: Location) -> bool:
    return requested.frame_id == saved.frame_id and requested.pose.model_dump(
        mode="python"
    ) == saved.pose.model_dump(mode="python")


class _SiteAssetValidator:
    """Own the content-identity and semantic-reference policy for one site."""

    def __init__(
        self,
        config: AppConfig,
        config_path: Path,
        *,
        locations_snapshot: LocationsSnapshot | None = None,
    ) -> None:
        self._config = config
        self._config_path = config_path
        self._site = config.site
        self._locations_snapshot = locations_snapshot

    def validate(self) -> list[Location]:
        self._require_execution_ready()
        locations, observed_digest = self._load_locations()
        self._require_matching_identity(observed_digest)
        self._validate_route_references(locations)
        self._validate_dock_reference(locations)
        self._validate_mission_references(locations)
        return locations

    def _require_execution_ready(self) -> None:
        if not self._site.execution_ready:
            raise SiteAssetError(
                "The active Site Profile is not execution-ready; run "
                "'JenAI site validate --repair' before navigation."
            )

    def _load_locations(self) -> tuple[list[Location], str]:
        locations_path = self._config.resolved_locations_path(self._config_path)
        if locations_path is None:
            raise SiteAssetError("The active Site Profile has no locations file.")
        try:
            snapshot = self._locations_snapshot or load_locations_snapshot(locations_path)
            if snapshot.path.resolve() != locations_path.resolve():
                raise SiteAssetError("The reviewed snapshot belongs to a different locations path.")
            return list(snapshot.locations), snapshot.sha256
        except (OSError, LocationsFileError) as exc:
            raise SiteAssetError(
                f"Could not load the active Site Profile locations: {exc}"
            ) from exc

    def _require_matching_identity(self, observed_digest: str) -> None:
        if observed_digest != self._site.locations_sha256:
            raise SiteAssetError("Locations identity mismatch for the active Site Profile.")

    def _validate_route_references(self, locations: list[Location]) -> None:
        for reference in self._site.validated_routes:
            if not any(_location_matches_reference(item, reference) for item in locations):
                raise SiteAssetError(f"validated_routes references unknown location '{reference}'.")

    def _validate_dock_reference(self, locations: list[Location]) -> None:
        reference = self._site.dock_location
        if reference is not None and not any(
            _location_matches_reference(item, reference) for item in locations
        ):
            raise SiteAssetError(f"dock_location references unknown location '{reference}'.")

    def _validate_mission_references(self, locations: list[Location]) -> None:
        if self._site.home_location is not None:
            _validated_location(
                locations,
                self._site.validated_routes,
                self._site.home_location,
                owner="home_location",
            )
        for area in self._site.patrol_areas:
            references = (
                *area.inspection_locations,
                *area.optional_inspection_locations,
            )
            for reference in references:
                _validated_location(
                    locations,
                    self._site.validated_routes,
                    reference,
                    owner=f"patrol area '{area.area_id}'",
                )


def validate_site_assets(
    config: AppConfig,
    config_path: Path,
    *,
    locations_snapshot: LocationsSnapshot | None = None,
) -> list[Location]:
    """Load and validate every location reference bound by the active profile."""

    return _SiteAssetValidator(
        config,
        config_path,
        locations_snapshot=locations_snapshot,
    ).validate()


def load_site_patrol_areas(
    config: AppConfig,
    config_path: Path,
    target: str = "all",
) -> tuple[PatrolArea, ...]:
    """Resolve one semantic coverage target from the active Site Profile."""

    locations = validate_site_assets(config, config_path)
    normalized_target = target.strip().casefold()
    site = config.site
    all_targets = {"", "all", site.site_id.casefold(), site.display_name.casefold()}
    selected = (
        list(site.patrol_areas)
        if normalized_target in all_targets
        else [
            area
            for area in site.patrol_areas
            if normalized_target in {area.area_id.casefold(), area.display_name.casefold()}
        ]
    )
    if not selected:
        raise SiteAssetError(f"Site Profile has no patrol area matching '{target or 'all'}'.")

    return tuple(
        PatrolArea(
            area_id=area.area_id,
            display_name=area.display_name,
            required=area.required,
            inspection_points=tuple(
                InspectionPoint(
                    location=resolve_site_location(locations, reference).name,
                    required=required,
                )
                for references, required in (
                    (area.inspection_locations, True),
                    (area.optional_inspection_locations, False),
                )
                for reference in references
            ),
        )
        for area in selected
    )


def bind_navigation_action(
    config: AppConfig,
    config_path: Path,
    outgoing_action: dict[str, object],
    *,
    locations_snapshot: LocationsSnapshot | None = None,
) -> dict[str, object]:
    """Authorize and canonicalize one goal against the active Site Profile."""

    site = config.site
    locations = validate_site_assets(
        config,
        config_path,
        locations_snapshot=locations_snapshot,
    )

    raw_goal = outgoing_action.get("goal")
    if not isinstance(raw_goal, dict):
        raise SiteAssetError("Navigation action has no structured goal.")
    try:
        requested = Location.model_validate(raw_goal)
    except ValidationError as exc:
        raise SiteAssetError(f"Navigation goal is malformed: {exc}") from exc
    saved = _find_bound_location(locations, raw_goal)
    if not _same_pose(requested, saved):
        raise SiteAssetError(
            f"Navigation goal '{saved.name}' does not match its validated saved pose."
        )
    if not any(_location_matches_reference(saved, ref) for ref in site.validated_routes):
        raise SiteAssetError(f"Navigation goal '{saved.name}' is not listed in validated_routes.")

    configured_dock = site.dock_location
    is_dock = configured_dock is not None and _location_matches_reference(saved, configured_dock)
    requested_capability = outgoing_action.get("capability_id")
    if requested_capability == "dock_approach" and not is_dock:
        raise SiteAssetError("Dock Approach target does not match the active site's dock_location.")

    bound = dict(outgoing_action)
    bound["goal"] = saved.model_dump(mode="json")
    if is_dock:
        bound["capability_id"] = "dock_approach"
    return bound

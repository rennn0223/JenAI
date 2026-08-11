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
from jenai.workflows.patrol_mission import (
    BoundLocation,
    MissionBindingError,
    MissionDraft,
    NavigateMissionDraft,
    NavigateMissionPolicy,
    NavigateMissionSpec,
    PatrolMissionPolicy,
    PatrolMissionSpec,
    build_navigation_mission_spec,
    build_patrol_mission_spec,
)


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
        if not locations:
            raise SiteAssetError("The active Site Profile has no registered locations.")
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
        for reference in self._site.default_patrol:
            _validated_location(
                locations,
                self._site.validated_routes,
                reference,
                owner="default_patrol",
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


def bind_patrol_mission(
    config: AppConfig,
    config_path: Path,
    draft: MissionDraft,
    *,
    mission_id: str,
    policy: PatrolMissionPolicy | None = None,
) -> PatrolMissionSpec:
    """Bind a patrol request to one freshly loaded, content-identified Site snapshot."""

    detached_config = AppConfig.model_validate(config.model_dump(mode="json"))
    detached_draft = MissionDraft.model_validate(draft.model_dump(mode="json"))
    locations = validate_site_assets(detached_config, config_path)
    site = detached_config.site
    if site.home_location is None or site.dock_location is None:
        raise SiteAssetError("The active Site Profile has no reviewed Dock/home location.")
    locations_sha256 = site.locations_sha256
    if locations_sha256 is None:
        raise SiteAssetError("The active Site Profile has no locations identity.")

    home = _validated_location(
        locations,
        site.validated_routes,
        site.home_location,
        owner="home_location",
    )
    dock = _validated_location(
        locations,
        site.validated_routes,
        site.dock_location,
        owner="dock_location",
    )
    if home.id != dock.id:
        raise SiteAssetError("home_location and dock_location must identify the same location.")

    default_references = tuple(site.default_patrol)
    if not default_references:
        raise MissionBindingError("The active Site Profile has no default patrol.")
    default_locations = tuple(
        _validated_location(
            locations,
            site.validated_routes,
            reference,
            owner="default_patrol",
        )
        for reference in default_references
    )
    default_location_ids = tuple(location.id for location in default_locations)
    if (
        len(default_location_ids) != 3
        or len(set(default_location_ids)) != 3
        or home.id in default_location_ids
    ):
        raise MissionBindingError(
            "The v1 default patrol must contain exactly three distinct non-Dock locations."
        )

    references = detached_draft.ordered_location_references or default_references
    ordered: list[BoundLocation] = []
    for reference in references:
        location = _validated_location(
            locations,
            site.validated_routes,
            reference,
            owner="patrol mission",
        )
        if location.id == home.id:
            raise MissionBindingError("Dock is system-added and cannot be an operator waypoint.")
        ordered.append(BoundLocation(location_id=location.id, location_name=location.name))

    if detached_draft.ordered_location_references is not None:
        ordered_location_ids = tuple(location.location_id for location in ordered)
        if (
            len(ordered_location_ids) != 3
            or len(set(ordered_location_ids)) != 3
            or set(ordered_location_ids) != set(default_location_ids)
        ):
            raise MissionBindingError(
                "The v1 explicit patrol order must be a permutation of the reviewed "
                "three default locations."
            )

    return build_patrol_mission_spec(
        mission_id=mission_id,
        site=site,
        vehicle=detached_config.vehicle,
        locations_sha256=locations_sha256,
        ordered_locations=tuple(ordered),
        home_location=BoundLocation(location_id=home.id, location_name=home.name),
        policy=policy,
    )


def bind_navigation_mission(
    config: AppConfig,
    config_path: Path,
    draft: NavigateMissionDraft,
    *,
    mission_id: str,
    policy: NavigateMissionPolicy | None = None,
) -> NavigateMissionSpec:
    """Bind one untrusted location reference to a fresh reviewed Site snapshot."""

    detached_config = AppConfig.model_validate(config.model_dump(mode="json"))
    detached_draft = NavigateMissionDraft.model_validate(draft.model_dump(mode="json"))
    if detached_draft.decision != "navigate" or detached_draft.location_reference is None:
        raise MissionBindingError("This NavigateMissionDraft does not authorize navigation.")

    locations = validate_site_assets(detached_config, config_path)
    normalized = detached_draft.location_reference.strip().casefold()
    matches = [
        location
        for location in locations
        if normalized in {location.id.casefold(), location.name.casefold()}
    ]
    if not matches:
        raise SiteAssetError(
            f"Site Profile references unknown location '{detached_draft.location_reference}'."
        )
    if len(matches) != 1:
        raise SiteAssetError(
            f"Site Profile location reference '{detached_draft.location_reference}' is ambiguous."
        )
    target = _validated_location(
        locations,
        detached_config.site.validated_routes,
        matches[0].id,
        owner="navigation mission",
    )
    locations_sha256 = detached_config.site.locations_sha256
    if locations_sha256 is None:
        raise SiteAssetError("The active Site Profile has no locations identity.")

    return build_navigation_mission_spec(
        mission_id=mission_id,
        site=detached_config.site,
        vehicle=detached_config.vehicle,
        locations_sha256=locations_sha256,
        target_location=BoundLocation(
            location_id=target.id,
            location_name=target.name,
        ),
        policy=policy,
    )


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

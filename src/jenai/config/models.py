"""AppConfig + profiles: VehicleProfile, TwinProfile, MapDatum, AvoidanceProfile."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jenai.schemas import ModelBindings


class ProviderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    provider: str
    base_url: str | None = None
    api_key_env: str | None = None

    @field_validator("name", "provider")
    @classmethod
    def required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped

    @field_validator("api_key_env")
    @classmethod
    def api_key_env_is_a_variable_name(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        stripped = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", stripped):
            raise ValueError("api_key_env must be an environment variable name, not an API key")
        return stripped


class VehicleProfile(BaseModel):
    """What JenAI must know about the vehicle it commands.

    The single place vehicle differences are allowed to live: everything above
    the bridge (skills, safety, guardrails) reads these fields instead of
    hardcoding topics or limits, so switching Ackermann car ⇄ quadruped is a
    config edit, not a code change.
    """

    model_config = ConfigDict(extra="forbid")

    # Literal so a typo ("ackerman") fails at config load, not months later
    # when the first type-aware consumer appears.
    type: Literal["ackermann", "diff", "quadruped"] = "ackermann"
    robot_id: str = "reference-ackermann"
    display_name: str = "JenAI Ackermann UGV"
    description: str = "Simulation-first unmanned ground vehicle controlled through JenAI."
    capabilities: list[str] | None = None
    limitations: list[str] = Field(default_factory=list)
    # Physical deployment graph. None preserves the historical behavior: the
    # process's ambient ROS_DOMAIN_ID is treated as the vehicle domain. This
    # documents isolation only; command routing still follows the environment
    # JenAI was launched in.
    domain_id: int | None = Field(default=None, ge=0, le=232)
    cmd_vel_topic: str = "/cmd_vel"
    cmd_vel_stamped: bool = False  # publish TwistStamped instead of Twist
    camera_topic: str = "/camera/image_raw"  # default for /vision camera & MCP camera_look
    # Hard velocity clamp applied at execution time, regardless of what the
    # model or user asked. Defaults match the historical built-in limits.
    max_linear: float = Field(default=1.0, gt=0, allow_inf_nan=False)  # m/s
    max_angular: float = Field(default=2.0, gt=0, allow_inf_nan=False)  # rad/s
    # Nav2's action status only means "inside Nav2's configured goal checker".
    # JenAI independently verifies the terminal pose against these limits
    # before reporting success.  The conservative defaults preserve existing
    # physical-robot behavior; simulation profiles can use 0.05 m / 0.15 rad.
    arrival_position_tolerance_m: float = Field(default=0.25, gt=0, allow_inf_nan=False)
    arrival_yaw_tolerance_rad: float = Field(default=0.25, gt=0, le=math.pi, allow_inf_nan=False)
    # The deprecated odom direct-drive fallback must stop when localization
    # feedback freezes after its first sample; a global route timeout is far too
    # slow for that failure mode.
    odom_timeout_s: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    # Fail-closed AMCL discontinuity guard for Nav2 goals. A displacement over
    # this threshold between two samples no more than `pose_jump_window_s`
    # apart cancels navigation and pulses zero velocity. Five metres in two
    # seconds is deliberately above this profile's normal 1 m/s motion while
    # still catching simulator/localization resets measured in tens of metres.
    pose_jump_threshold_m: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    pose_jump_window_s: float = Field(default=2.0, gt=0, allow_inf_nan=False)

    @field_validator("robot_id", "display_name", "description")
    @classmethod
    def identity_text_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("robot identity text must not be blank")
        return stripped


class AvoidanceProfile(BaseModel):
    """Reactive local obstacle avoidance for the Nav2-less odom driver.

    A depth camera (sensor_msgs/Image, 32FC1 metres) is turned into a
    pseudo-laserscan (range per angular sector across the horizontal FOV).
    When the forward corridor is blocked inside slow_distance, the odom
    driver STOPS, plans a two-waypoint detour around the sighted obstacle
    (position remembered from the scan — a down-pitched camera cannot keep a
    low obstacle in view while rounding it), drives the detour, then reseeks
    the goal; if it ends up pinned it reports "blocked" instead of grinding.
    This is a reflex-layer behavior — it runs in the bridge with NO LLM. Off by
    default; irrelevant to the Nav2 adapter (Nav2 does its own avoidance). If
    the depth topic becomes stale (wrong name / camera off), the driver stops
    and reports sensor_unavailable instead of continuing blind.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    depth_topic: str = "/depth"
    stop_distance: float = Field(default=0.6, gt=0, allow_inf_nan=False)
    slow_distance: float = Field(default=2.0, gt=0, allow_inf_nan=False)
    hfov_deg: float = Field(default=90.0, gt=0, le=180, allow_inf_nan=False)
    sectors: int = Field(default=15, ge=3, le=360)
    band_lo: float = Field(default=0.45, ge=0, le=1, allow_inf_nan=False)
    band_hi: float = Field(default=0.60, ge=0, le=1, allow_inf_nan=False)
    min_valid: float = Field(default=0.1, ge=0, allow_inf_nan=False)
    # Ground filtering for a down-pitched camera: returns at/beyond this
    # distance are the floor, not obstacles (0 = off). Measure with the scene:
    # the uniform ring the empty ground reads in your band IS the reference.
    floor_ref: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    floor_tol: float = Field(default=0.2, ge=0, allow_inf_nan=False)
    # Per-pixel floor reference: .npy saved by the bridge's `avoid_snapshot`
    # op, captured while the view is EMPTY. Each pixel reading closer than its
    # reference (minus floor_tol) is an obstacle — one mechanism for the floor
    # ring, the vehicle's own body in frame, and obstacles shorter than the
    # camera (which no fixed band + scalar floor_ref can see in time).
    # Supersedes floor_ref where the file is valid; flat ground only.
    floor_snapshot: str = ""
    # Stop-and-go detour parameters (the avoidance mechanism itself): on a
    # blocked corridor the driver STOPS, plans two waypoints around the
    # sighted obstacle, drives them from memory, then reseeks the goal.
    detour_clearance: float = Field(default=0.5, gt=0, allow_inf_nan=False)
    detour_beyond: float = Field(default=1.2, gt=0, allow_inf_nan=False)
    max_replans: int = Field(default=4, ge=0, le=100)
    depth_timeout_s: float = Field(default=1.0, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def distances_and_band_are_ordered(self) -> AvoidanceProfile:
        if self.stop_distance >= self.slow_distance:
            raise ValueError("stop_distance must be less than slow_distance")
        if self.band_lo >= self.band_hi:
            raise ValueError("band_lo must be less than band_hi")
        return self

    def as_params(self) -> dict[str, Any]:
        return self.model_dump()


class MapDatum(BaseModel):
    """GPS anchor of the Nav2 map frame.

    lat/lon of the map origin plus the bearing of map +x (degrees CCW from
    east), so campus lat/lon can be converted into map-frame metres for
    `/loc add gps`. Unset (None) means GPS locations are honestly refused.
    """

    model_config = ConfigDict(extra="forbid")

    lat: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    lon: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    yaw_deg: float = Field(default=0.0, allow_inf_nan=False)

    @property
    def configured(self) -> bool:
        return self.lat is not None and self.lon is not None


class SiteProfile(BaseModel):
    """Versioned operating-site binding for saved map-frame assets."""

    model_config = ConfigDict(extra="forbid")

    site_id: str = "unbound"
    display_name: str = "Unbound site"
    version: str = "0"
    active: bool = False
    validated: bool = False
    map_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    map_frame: str = "map"
    reference_scene: str | None = None
    locations_path: str | None = None
    validated_routes: list[str] = Field(default_factory=list)
    dock_location: str | None = None
    validation_evidence: list[str] = Field(default_factory=list)

    @field_validator("site_id", "display_name", "version", "map_frame")
    @classmethod
    def required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("site identity text must not be blank")
        return stripped

    @field_validator("map_sha256")
    @classmethod
    def normalize_map_digest(cls, value: str | None) -> str | None:
        return value.lower() if value is not None else None

    @field_validator("reference_scene", "locations_path", "dock_location")
    @classmethod
    def optional_asset_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("validated_routes", "validation_evidence")
    @classmethod
    def asset_reference_list(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            stripped = value.strip()
            if not stripped:
                raise ValueError("site asset references must not be blank")
            if stripped not in normalized:
                normalized.append(stripped)
        return normalized

    @model_validator(mode="after")
    def active_site_is_verified(self) -> SiteProfile:
        if self.active and (not self.validated or self.map_sha256 is None):
            raise ValueError("an active site must be validated and include map_sha256")
        return self


class ForbiddenZone(BaseModel):
    """Axis-aligned rectangle in the map frame the twin trajectory must not enter."""

    model_config = ConfigDict(extra="forbid")

    name: str = "zone"
    x_min: float = Field(allow_inf_nan=False)
    y_min: float = Field(allow_inf_nan=False)
    x_max: float = Field(allow_inf_nan=False)
    y_max: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def bounds_are_ordered(self) -> ForbiddenZone:
        if self.x_min > self.x_max or self.y_min > self.y_max:
            raise ValueError("forbidden-zone minimums must not exceed maximums")
        return self

    def contains(self, x: float, y: float) -> bool:
        return self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max


class TwinProfile(BaseModel):
    """Digital-twin gate (Twin-Gated Execution): rehearse a navigation goal in
    the Isaac Sim twin scene before the real robot moves.

    Off by default and fully optional — with `enabled = false` no twin bridge
    is ever spawned and navigation behaves exactly as before. The twin runs on
    its own ROS_DOMAIN_ID so its Nav2 stack can never cross-talk with the
    real robot's graph.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    domain_id: int = Field(default=42, ge=0, le=232)
    nav_timeout_s: float = Field(default=180.0, gt=0, allow_inf_nan=False)
    goal_tolerance_m: float = Field(default=0.5, gt=0, allow_inf_nan=False)
    collision_topic: str = "/twin/collision"  # G1: std_msgs/Bool from a contact sensor
    # A safety gate cannot claim success without observing its collision
    # criterion. Research scenes that genuinely have no contact sensor may opt
    # out explicitly, and their reports will continue to show G1 as skipped.
    require_collision_evidence: bool = True
    pose_sample_s: float = Field(default=0.5, gt=0, allow_inf_nan=False)
    forbidden_zones: list[ForbiddenZone] = Field(default_factory=list)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "0.1.0"
    active_provider: str | None = None
    provider_profiles: dict[str, ProviderProfile] = Field(default_factory=dict)
    model_bindings: ModelBindings | None = None
    locations_path: str | None = None
    route_adapter: str = "stub"
    # Existing JenAI deployments are simulation-first. Physical deployments
    # must opt in explicitly; that mode turns configuration warnings such as a
    # shared target/Twin ROS domain into hard execution blocks.
    deployment_mode: Literal["simulation", "physical"] = "simulation"
    ros2_ws: str | None = None  # workspace root for `JenAI scaffold` (default ~/ros2_ws)
    vehicle: VehicleProfile = Field(default_factory=VehicleProfile)
    twin: TwinProfile = Field(default_factory=TwinProfile)
    site: SiteProfile = Field(default_factory=SiteProfile)
    map_datum: MapDatum = Field(default_factory=MapDatum)
    avoidance: AvoidanceProfile = Field(default_factory=AvoidanceProfile)
    created_by_setup: bool = False

    def is_complete(self) -> bool:
        return (
            self.active_provider is not None
            and self.active_provider in self.provider_profiles
            and self.model_bindings is not None
        )

    @model_validator(mode="after")
    def active_site_binds_locations(self) -> AppConfig:
        """Migrate the legacy global path into the active versioned Site Profile."""
        if not self.site.active:
            return self
        site_path = self.site.locations_path
        legacy_path = self.locations_path
        if site_path is None:
            if legacy_path is None:
                raise ValueError("an active site must bind a locations_path")
            self.site.locations_path = legacy_path
        elif (
            legacy_path is not None
            and Path(site_path).expanduser() != Path(legacy_path).expanduser()
        ):
            raise ValueError(
                "active site locations_path conflicts with the legacy global locations_path"
            )
        return self

    def active_profile(self) -> ProviderProfile | None:
        if self.active_provider is None:
            return None
        return self.provider_profiles.get(self.active_provider)

    def resolved_locations_path(self, config_path: Path) -> Path | None:
        configured = self.site.locations_path if self.site.active else self.locations_path
        if configured is None:
            return None
        path = Path(configured).expanduser()
        if path.is_absolute():
            return path
        return config_path.parent / path

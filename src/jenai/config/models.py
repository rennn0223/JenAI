from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    cmd_vel_topic: str = "/cmd_vel"
    cmd_vel_stamped: bool = False  # publish TwistStamped instead of Twist
    camera_topic: str = "/camera/image_raw"  # default for /vision camera & MCP camera_look
    # Hard velocity clamp applied at execution time, regardless of what the
    # model or user asked. Defaults match the historical built-in limits.
    max_linear: float = 1.0  # m/s
    max_angular: float = 2.0  # rad/s


class MapDatum(BaseModel):
    """GPS anchor of the Nav2 map frame.

    lat/lon of the map origin plus the bearing of map +x (degrees CCW from
    east), so campus lat/lon can be converted into map-frame metres for
    `/loc add gps`. Unset (None) means GPS locations are honestly refused.
    """

    model_config = ConfigDict(extra="forbid")

    lat: float | None = None
    lon: float | None = None
    yaw_deg: float = 0.0

    @property
    def configured(self) -> bool:
        return self.lat is not None and self.lon is not None


class ForbiddenZone(BaseModel):
    """Axis-aligned rectangle in the map frame the twin trajectory must not enter."""

    model_config = ConfigDict(extra="forbid")

    name: str = "zone"
    x_min: float
    y_min: float
    x_max: float
    y_max: float

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
    domain_id: int = 42  # twin's isolated ROS graph; real robot keeps the env default
    nav_timeout_s: float = 180.0  # G2: twin rehearsal must finish within this
    goal_tolerance_m: float = 0.5  # G4: max endpoint deviation from the goal
    collision_topic: str = "/twin/collision"  # G1: std_msgs/Bool from a contact sensor
    pose_sample_s: float = 0.5  # G3: twin trajectory sampling period
    forbidden_zones: list[ForbiddenZone] = Field(default_factory=list)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "0.1.0"
    active_provider: str | None = None
    provider_profiles: dict[str, ProviderProfile] = Field(default_factory=dict)
    model_bindings: ModelBindings | None = None
    locations_path: str | None = None
    route_adapter: str = "stub"
    vehicle: VehicleProfile = Field(default_factory=VehicleProfile)
    twin: TwinProfile = Field(default_factory=TwinProfile)
    map_datum: MapDatum = Field(default_factory=MapDatum)
    created_by_setup: bool = False

    def is_complete(self) -> bool:
        return (
            self.active_provider is not None
            and self.active_provider in self.provider_profiles
            and self.model_bindings is not None
        )

    def active_profile(self) -> ProviderProfile | None:
        if self.active_provider is None:
            return None
        return self.provider_profiles.get(self.active_provider)

    def resolved_locations_path(self, config_path: Path) -> Path | None:
        if self.locations_path is None:
            return None
        path = Path(self.locations_path).expanduser()
        if path.is_absolute():
            return path
        return config_path.parent / path


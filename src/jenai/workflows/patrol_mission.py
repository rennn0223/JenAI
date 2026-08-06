"""Immutable patrol mission binding and deterministic plan compilation."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jenai.config.models import SiteProfile, VehicleProfile

PATROL_COMPILER_VERSION: Literal["patrol-compiler-v1"] = "patrol-compiler-v1"
PATROL_COMPLETION_CONTRACT_VERSION: Literal["nav2-terminal+fresh-map-pose-v1"] = (
    "nav2-terminal+fresh-map-pose-v1"
)


class MissionBindingError(ValueError):
    """An untrusted mission request cannot be bound to reviewed assets."""


class MissionModel(BaseModel):
    """Strict frozen base for mission data crossing a trust boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _required_text(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("text must not be blank")
    return stripped


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _render_tolerance_m(value: float) -> str:
    """Render a lossless float while keeping ordinary values at two decimals."""

    rendered = repr(value)
    mantissa, exponent_marker, exponent = rendered.partition("e")
    whole, decimal_marker, fraction = mantissa.partition(".")
    if not decimal_marker:
        mantissa = f"{whole}.00"
    elif len(fraction) < 2:
        mantissa = f"{whole}.{fraction.ljust(2, '0')}"
    return f"{mantissa}{exponent_marker}{exponent}"


class MissionDraft(MissionModel):
    """Typed but untrusted patrol intent produced from operator language."""

    kind: Literal["patrol"] = "patrol"
    ordered_location_references: tuple[str, ...] | None = None

    @field_validator("ordered_location_references", mode="before")
    @classmethod
    def normalize_references(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, (list, tuple)):
            raise ValueError("ordered locations must be a list")
        references: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("ordered location references must be strings")
            references.append(_required_text(item))
        if not references:
            raise ValueError("an explicit patrol order must not be empty")
        return tuple(references)


class PatrolMissionPolicy(MissionModel):
    """Reviewed v1 policy; it cannot be supplied by the language model."""

    retry_count: int = Field(default=1, ge=0, le=1)
    waypoint_failure: Literal["skip_and_continue"] = "skip_and_continue"
    system_failure: Literal["abort"] = "abort"
    position_tolerance_m: float = Field(
        default=0.15,
        gt=0,
        le=0.15,
        allow_inf_nan=False,
    )
    require_yaw: Literal[False] = False
    capture_photo: Literal[False] = False

    @field_validator("position_tolerance_m")
    @classmethod
    def finite_tolerance(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("position tolerance must be finite")
        return value


class BoundLocation(MissionModel):
    """Detached registered-location identity without mutable coordinates."""

    location_id: str
    location_name: str

    _normalize_id = field_validator("location_id")(_required_text)
    _normalize_name = field_validator("location_name")(_required_text)


class PatrolMissionSpec(MissionModel):
    """Trusted immutable patrol mission bound to exact reviewed profiles."""

    mission_id: str
    kind: Literal["patrol"] = "patrol"
    site_id: str
    site_version: str
    site_profile_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    robot_id: str
    vehicle_profile_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    locations_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ordered_locations: tuple[BoundLocation, ...]
    home_location: BoundLocation
    policy: PatrolMissionPolicy

    _normalize_profile_identity = field_validator(
        "site_id",
        "site_version",
        "robot_id",
    )(_required_text)
    _normalize_mission_id = field_validator("mission_id")(_required_text)

    @model_validator(mode="after")
    def validate_route(self) -> PatrolMissionSpec:
        location_ids = tuple(location.location_id for location in self.ordered_locations)
        if len(location_ids) != 3 or len(set(location_ids)) != 3:
            raise ValueError("a v1 patrol mission requires exactly three distinct waypoints")
        if self.home_location.location_id in location_ids:
            raise ValueError("the system home location cannot be an operator waypoint")
        return self

    def semantic_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"mission_id", "mission_digest"},
        )

    @property
    def mission_digest(self) -> str:
        """Digest semantic mission content while excluding run identity."""

        return _canonical_digest(self.semantic_payload())


MissionSpec = PatrolMissionSpec


class NavigateStep(MissionModel):
    kind: Literal["navigate"] = "navigate"
    location_id: str
    location_name: str
    position_tolerance_m: float = Field(gt=0, allow_inf_nan=False)
    require_yaw: Literal[False] = False

    _normalize_location_identity = field_validator("location_id", "location_name")(_required_text)


class ReturnHomeStep(MissionModel):
    kind: Literal["return_home"] = "return_home"
    location_id: str
    location_name: str
    position_tolerance_m: float = Field(gt=0, allow_inf_nan=False)
    require_yaw: Literal[False] = False

    _normalize_location_identity = field_validator("location_id", "location_name")(_required_text)


ExecutionStep = Annotated[NavigateStep | ReturnHomeStep, Field(discriminator="kind")]


class ExecutionPlan(MissionModel):
    """Exact ordered plan displayed for approval and consumed later by execution."""

    mission: PatrolMissionSpec
    compiler_version: Literal["patrol-compiler-v1"] = PATROL_COMPILER_VERSION
    completion_contract_version: Literal["nav2-terminal+fresh-map-pose-v1"] = (
        PATROL_COMPLETION_CONTRACT_VERSION
    )
    steps: tuple[ExecutionStep, ...]

    @model_validator(mode="after")
    def steps_match_mission(self) -> ExecutionPlan:
        expected: tuple[tuple[str, str, str], ...] = (
            *(
                ("navigate", location.location_id, location.location_name)
                for location in self.mission.ordered_locations
            ),
            (
                "return_home",
                self.mission.home_location.location_id,
                self.mission.home_location.location_name,
            ),
        )
        actual = tuple((step.kind, step.location_id, step.location_name) for step in self.steps)
        if actual != expected:
            raise ValueError("execution steps do not match the bound mission order")
        expected_tolerance = self.mission.policy.position_tolerance_m
        if any(
            step.position_tolerance_m != expected_tolerance
            or step.require_yaw != self.mission.policy.require_yaw
            for step in self.steps
        ):
            raise ValueError("execution step completion policy differs from the mission")
        return self

    @property
    def plan_digest(self) -> str:
        """Bind the exact executable sequence, profiles, policy, and compiler."""

        return _canonical_digest(
            {
                "compiler_version": self.compiler_version,
                "completion_contract_version": self.completion_contract_version,
                "mission": self.mission.semantic_payload(),
                "steps": [step.model_dump(mode="json") for step in self.steps],
            }
        )


def build_patrol_mission_spec(
    *,
    mission_id: str,
    site: SiteProfile,
    vehicle: VehicleProfile,
    locations_sha256: str,
    ordered_locations: tuple[BoundLocation, ...],
    home_location: BoundLocation,
    policy: PatrolMissionPolicy | None = None,
) -> PatrolMissionSpec:
    """Build a spec from assets already validated by the application boundary."""

    detached_site = SiteProfile.model_validate(site.model_dump(mode="json"))
    detached_vehicle = VehicleProfile.model_validate(vehicle.model_dump(mode="json"))
    if not detached_site.execution_ready:
        raise MissionBindingError("The active Site Profile is not execution-ready")
    if detached_site.locations_sha256 != locations_sha256:
        raise MissionBindingError("Locations identity does not match the active Site Profile")
    return PatrolMissionSpec(
        mission_id=mission_id,
        site_id=detached_site.site_id,
        site_version=detached_site.version,
        site_profile_digest=_canonical_digest(detached_site.model_dump(mode="json")),
        robot_id=detached_vehicle.robot_id,
        vehicle_profile_digest=_canonical_digest(detached_vehicle.model_dump(mode="json")),
        locations_sha256=locations_sha256,
        ordered_locations=ordered_locations,
        home_location=home_location,
        policy=policy or PatrolMissionPolicy(),
    )


def compile_patrol_mission(spec: PatrolMissionSpec) -> ExecutionPlan:
    """Compile the exact bound waypoint order and append the reviewed Dock."""

    detached = PatrolMissionSpec.model_validate(spec.model_dump(mode="json"))
    tolerance = detached.policy.position_tolerance_m
    steps: tuple[ExecutionStep, ...] = (
        *(
            NavigateStep(
                location_id=location.location_id,
                location_name=location.location_name,
                position_tolerance_m=tolerance,
                require_yaw=detached.policy.require_yaw,
            )
            for location in detached.ordered_locations
        ),
        ReturnHomeStep(
            location_id=detached.home_location.location_id,
            location_name=detached.home_location.location_name,
            position_tolerance_m=tolerance,
            require_yaw=detached.policy.require_yaw,
        ),
    )
    return ExecutionPlan(mission=detached, steps=steps)


def render_plan_preview(plan: ExecutionPlan) -> str:
    """Render the exact approved order without coordinates or invented prose."""

    policy = plan.mission.policy
    lines = [
        "計畫",
        "",
        f"Site: {plan.mission.site_id}",
        f"Robot: {plan.mission.robot_id}",
        "",
    ]
    for index, step in enumerate(plan.steps, start=1):
        action = "前往" if isinstance(step, NavigateStep) else "返回"
        lines.append(f"{index}. {action} {step.location_name}")
    retry_summary = "重試一次，仍失敗則略過" if policy.retry_count == 1 else "不重試，失敗則略過"
    lines.extend(
        (
            "",
            f"抵達容差：≤ {_render_tolerance_m(policy.position_tolerance_m)} m",
            "朝向要求：無",
            f"航點失敗：{retry_summary}",
            "系統級導航故障：中止剩餘步驟",
            "拍照：否",
        )
    )
    return "\n".join(lines)

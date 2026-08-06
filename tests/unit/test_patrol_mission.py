from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from jenai.adapters.locations import save_locations
from jenai.config.models import AppConfig, SiteProfile, VehicleProfile
from jenai.schemas import Location, Pose2D
from jenai.site_assets import SiteAssetError, bind_patrol_mission, fingerprint_locations_file
from jenai.workflows.patrol_mission import (
    ExecutionPlan,
    MissionBindingError,
    MissionDraft,
    NavigateStep,
    PatrolMissionPolicy,
    PatrolMissionSpec,
    ReturnHomeStep,
    compile_patrol_mission,
    render_plan_preview,
)

_LOCATION_DIGEST = "b" * 64


def _location(location_id: str, name: str) -> Location:
    return Location(
        id=location_id,
        name=name,
        frame_id="map",
        pose=Pose2D(x=0.0, y=0.0, yaw=0.0),
    )


def _site(*, default_patrol: list[str]) -> SiteProfile:
    return SiteProfile(
        site_id="warehouse",
        display_name="Warehouse",
        version="7",
        active=True,
        validated=True,
        map_sha256="a" * 64,
        locations_sha256=_LOCATION_DIGEST,
        locations_path="locations.toml",
        validated_routes=["A", "B", "C", "D", "Dock"],
        default_patrol=default_patrol,
        home_location="Dock",
        dock_location="Dock",
    )


def _locations() -> tuple[Location, ...]:
    return (
        _location("loc-a", "A"),
        _location("loc-b", "B"),
        _location("loc-c", "C"),
        _location("loc-d", "D"),
        _location("loc-dock", "Dock"),
    )


def _bind(
    tmp_path: Path,
    draft: MissionDraft,
    *,
    mission_id: str = "mission-1",
    site: SiteProfile | None = None,
    vehicle: VehicleProfile | None = None,
    locations: tuple[Location, ...] | None = None,
    policy: PatrolMissionPolicy | None = None,
) -> PatrolMissionSpec:
    bound_locations = _locations() if locations is None else locations
    locations_path = tmp_path / "locations.toml"
    save_locations(list(bound_locations), locations_path)
    site_data = (site or _site(default_patrol=["A", "B", "C"])).model_dump(mode="python")
    site_data.update(
        {
            "locations_path": "locations.toml",
            "locations_sha256": fingerprint_locations_file(locations_path),
        }
    )
    config = AppConfig(
        locations_path="locations.toml",
        vehicle=vehicle or VehicleProfile(robot_id="robot-1"),
        site=SiteProfile.model_validate(site_data),
    )
    return bind_patrol_mission(
        config,
        tmp_path / "config.toml",
        draft,
        mission_id=mission_id,
        policy=policy,
    )


def test_default_patrol_binds_and_compiles_site_order_with_system_home(
    tmp_path: Path,
) -> None:
    spec = _bind(tmp_path, MissionDraft(kind="patrol"))
    plan = compile_patrol_mission(spec)

    assert [type(step) for step in plan.steps] == [
        NavigateStep,
        NavigateStep,
        NavigateStep,
        ReturnHomeStep,
    ]
    assert [step.location_name for step in plan.steps] == ["A", "B", "C", "Dock"]
    assert render_plan_preview(plan) == (
        "計畫\n"
        "\n"
        "Site: warehouse\n"
        "Robot: robot-1\n"
        "\n"
        "1. 前往 A\n"
        "2. 前往 B\n"
        "3. 前往 C\n"
        "4. 返回 Dock\n"
        "\n"
        "抵達容差：≤ 0.15 m\n"
        "朝向要求：無\n"
        "航點失敗：重試一次，仍失敗則略過\n"
        "系統級導航故障：中止剩餘步驟\n"
        "拍照：否"
    )


def test_explicit_operator_order_is_preserved_and_dock_is_system_added(
    tmp_path: Path,
) -> None:
    spec = _bind(
        tmp_path,
        MissionDraft(ordered_location_references=["C", "A", "B"]),
    )
    plan = compile_patrol_mission(spec)

    assert [step.location_name for step in plan.steps] == ["C", "A", "B", "Dock"]
    assert isinstance(plan.steps[-1], ReturnHomeStep)


@pytest.mark.parametrize(
    "references",
    (
        ["C", "A"],
        ["A", "B", "D"],
        ["A", "A", "B"],
        ["A", "B", "C", "A"],
    ),
)
def test_explicit_patrol_must_be_a_permutation_of_the_reviewed_three_locations(
    tmp_path: Path,
    references: list[str],
) -> None:
    with pytest.raises(MissionBindingError, match="permutation"):
        _bind(
            tmp_path,
            MissionDraft(ordered_location_references=references),
        )


@pytest.mark.parametrize(
    "default_patrol",
    (
        ["A", "B"],
        ["A", "B", "C", "D"],
        ["A", "A", "B"],
        ["A", "B", "Dock"],
    ),
)
def test_default_patrol_must_be_exactly_three_distinct_non_dock_locations(
    tmp_path: Path,
    default_patrol: list[str],
) -> None:
    with pytest.raises(MissionBindingError, match="three distinct non-Dock"):
        _bind(
            tmp_path,
            MissionDraft(),
            site=_site(default_patrol=default_patrol),
        )


def test_unknown_location_missing_catalog_and_missing_default_fail_closed(
    tmp_path: Path,
) -> None:
    with pytest.raises(SiteAssetError, match="unknown location"):
        _bind(
            tmp_path,
            MissionDraft(ordered_location_references=["Unknown"]),
        )
    with pytest.raises(MissionBindingError, match="no default patrol"):
        _bind(
            tmp_path,
            MissionDraft(),
            site=_site(default_patrol=[]),
        )
    with pytest.raises(SiteAssetError, match="no registered locations"):
        _bind(tmp_path, MissionDraft(), locations=())


def test_operator_cannot_supply_dock_as_a_patrol_waypoint(tmp_path: Path) -> None:
    with pytest.raises(MissionBindingError, match="system-added"):
        _bind(
            tmp_path,
            MissionDraft(ordered_location_references=["A", "Dock"]),
        )


def test_language_model_draft_cannot_supply_trusted_execution_fields() -> None:
    forbidden_fields = (
        {"coordinates": {"x": 1.0, "y": 2.0}},
        {"position_tolerance_m": 0.8},
        {"home_location": "A"},
        {"retry_count": 9},
        {"failure_policy": "ignore_everything"},
    )
    for forbidden in forbidden_fields:
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            MissionDraft.model_validate({"kind": "patrol", **forbidden})
    with pytest.raises(ValidationError, match="must be strings"):
        MissionDraft.model_validate({"kind": "patrol", "ordered_location_references": ["A", 2]})


def test_v1_policy_rejects_position_tolerance_above_fifteen_centimetres() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 0.15"):
        PatrolMissionPolicy(position_tolerance_m=0.150001)

    assert PatrolMissionPolicy(position_tolerance_m=0.1).position_tolerance_m == 0.1


def test_mission_and_plan_digests_exclude_run_identity(tmp_path: Path) -> None:
    first = _bind(tmp_path, MissionDraft(), mission_id="run-1")
    second = _bind(tmp_path, MissionDraft(), mission_id="run-2")

    assert first.mission_id != second.mission_id
    assert first.mission_digest == second.mission_digest
    assert compile_patrol_mission(first).plan_digest == compile_patrol_mission(second).plan_digest


def test_order_changes_mission_and_plan_digests(tmp_path: Path) -> None:
    first = _bind(
        tmp_path,
        MissionDraft(ordered_location_references=["A", "B", "C"]),
    )
    second = _bind(
        tmp_path,
        MissionDraft(ordered_location_references=["C", "B", "A"]),
    )

    assert first.mission_digest != second.mission_digest
    assert compile_patrol_mission(first).plan_digest != compile_patrol_mission(second).plan_digest


def test_policy_profile_and_completion_contract_are_digest_bound(tmp_path: Path) -> None:
    baseline = _bind(tmp_path, MissionDraft())
    changed_policy = _bind(
        tmp_path,
        MissionDraft(),
        policy=PatrolMissionPolicy(retry_count=0),
    )
    changed_vehicle = _bind(
        tmp_path,
        MissionDraft(),
        vehicle=VehicleProfile(robot_id="robot-2"),
    )

    assert baseline.mission_digest != changed_policy.mission_digest
    assert (
        compile_patrol_mission(baseline).plan_digest
        != compile_patrol_mission(changed_policy).plan_digest
    )
    assert baseline.mission_digest != changed_vehicle.mission_digest

    plan = compile_patrol_mission(baseline)
    future_contract = plan.model_copy(
        update={"completion_contract_version": "nav2-terminal+fresh-map-pose-v2"}
    )
    assert plan.plan_digest != future_contract.plan_digest


def test_policy_change_is_visible_in_plan_digest_and_preview(tmp_path: Path) -> None:
    baseline = compile_patrol_mission(_bind(tmp_path, MissionDraft()))
    stricter = compile_patrol_mission(
        _bind(
            tmp_path,
            MissionDraft(),
            policy=PatrolMissionPolicy(
                retry_count=0,
                position_tolerance_m=0.1,
            ),
        )
    )

    assert baseline.plan_digest != stricter.plan_digest
    assert "抵達容差：≤ 0.15 m" in render_plan_preview(baseline)
    assert "航點失敗：重試一次，仍失敗則略過" in render_plan_preview(baseline)
    assert "抵達容差：≤ 0.10 m" in render_plan_preview(stricter)
    assert "航點失敗：不重試，失敗則略過" in render_plan_preview(stricter)


def test_distinct_tolerances_cannot_collapse_to_the_same_approval_preview(
    tmp_path: Path,
) -> None:
    first = compile_patrol_mission(
        _bind(
            tmp_path,
            MissionDraft(),
            policy=PatrolMissionPolicy(position_tolerance_m=0.101),
        )
    )
    second = compile_patrol_mission(
        _bind(
            tmp_path,
            MissionDraft(),
            policy=PatrolMissionPolicy(position_tolerance_m=0.104),
        )
    )

    first_preview = render_plan_preview(first)
    second_preview = render_plan_preview(second)

    assert first.plan_digest != second.plan_digest
    assert first_preview != second_preview
    assert "抵達容差：≤ 0.101 m" in first_preview
    assert "抵達容差：≤ 0.104 m" in second_preview


@pytest.mark.parametrize(
    "ordered_locations",
    (
        [{"location_id": "loc-a", "location_name": "A"}],
        [
            {"location_id": "loc-a", "location_name": "A"},
            {"location_id": "loc-a", "location_name": "A"},
            {"location_id": "loc-b", "location_name": "B"},
        ],
    ),
)
def test_trusted_mission_requires_exactly_three_distinct_waypoints(
    tmp_path: Path,
    ordered_locations: list[dict[str, str]],
) -> None:
    payload = _bind(tmp_path, MissionDraft()).model_dump(mode="json")
    payload["ordered_locations"] = ordered_locations

    with pytest.raises(ValidationError, match="exactly three distinct waypoints"):
        PatrolMissionSpec.model_validate(payload)


@pytest.mark.parametrize("field", ("site_id", "site_version", "robot_id"))
def test_trusted_mission_rejects_blank_profile_identity(
    tmp_path: Path,
    field: str,
) -> None:
    payload = _bind(tmp_path, MissionDraft()).model_dump(mode="json")
    payload[field] = "  "

    with pytest.raises(ValidationError, match="must not be blank"):
        PatrolMissionSpec.model_validate(payload)


@pytest.mark.parametrize("field", ("site_profile_digest", "vehicle_profile_digest"))
def test_trusted_mission_rejects_invalid_profile_digest(
    tmp_path: Path,
    field: str,
) -> None:
    payload = _bind(tmp_path, MissionDraft()).model_dump(mode="json")
    payload[field] = "not-a-digest"

    with pytest.raises(ValidationError, match="String should match pattern"):
        PatrolMissionSpec.model_validate(payload)


@pytest.mark.parametrize("step_type", (NavigateStep, ReturnHomeStep))
@pytest.mark.parametrize("field", ("location_id", "location_name"))
def test_execution_step_rejects_blank_location_identity(
    step_type: type[NavigateStep] | type[ReturnHomeStep],
    field: str,
) -> None:
    payload: dict[str, object] = {
        "location_id": "loc-a",
        "location_name": "A",
        "position_tolerance_m": 0.15,
    }
    payload[field] = "  "

    with pytest.raises(ValidationError, match="must not be blank"):
        step_type.model_validate(payload)


def test_models_are_detached_and_recursively_immutable(tmp_path: Path) -> None:
    requested = ["A", "B", "C"]
    draft = MissionDraft(ordered_location_references=requested)
    requested[0] = "C"

    source_locations = _locations()
    spec = _bind(tmp_path, draft, locations=source_locations)
    plan = compile_patrol_mission(spec)
    source_locations[0].name = "MUTATED"

    assert spec.ordered_locations[0].location_name == "A"
    assert plan.steps[0].location_name == "A"
    assert isinstance(spec.ordered_locations, tuple)
    assert isinstance(plan.steps, tuple)
    with pytest.raises(ValidationError, match="Instance is frozen"):
        spec.mission_id = "mutated"
    with pytest.raises(ValidationError, match="Instance is frozen"):
        plan.steps[0].location_name = "mutated"


def test_plan_constructor_rejects_steps_that_do_not_match_bound_mission(
    tmp_path: Path,
) -> None:
    spec = _bind(tmp_path, MissionDraft())
    wrong_steps = (
        NavigateStep(
            location_id="loc-c",
            location_name="C",
            position_tolerance_m=0.15,
        ),
        ReturnHomeStep(
            location_id="loc-dock",
            location_name="Dock",
            position_tolerance_m=0.15,
        ),
    )

    with pytest.raises(ValidationError, match="do not match"):
        ExecutionPlan(mission=spec, steps=wrong_steps)


def test_all_steps_use_reviewed_completion_policy(tmp_path: Path) -> None:
    plan = compile_patrol_mission(_bind(tmp_path, MissionDraft()))

    assert {step.position_tolerance_m for step in plan.steps} == {0.15}
    assert {step.require_yaw for step in plan.steps} == {False}


def test_binder_reloads_locations_and_rejects_a_claimed_digest_mismatch(
    tmp_path: Path,
) -> None:
    locations_path = tmp_path / "locations.toml"
    save_locations(list(_locations()), locations_path)
    config = AppConfig(
        locations_path="locations.toml",
        site=_site(default_patrol=["A", "B", "C"]),
        vehicle=VehicleProfile(robot_id="robot-1"),
    )

    with pytest.raises(SiteAssetError, match="Locations identity mismatch"):
        bind_patrol_mission(
            config,
            tmp_path / "config.toml",
            MissionDraft(),
            mission_id="mission-1",
        )

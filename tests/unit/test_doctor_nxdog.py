from __future__ import annotations

from datetime import UTC, datetime

from jenai.adapters.nxdog import (
    NXDOG_API_URL_ENV,
    NXDogEndpointFailure,
    NXDogFailureKind,
    NXDogObservation,
    NXDogPoseObservation,
    NXDogVelocityObservation,
)
from jenai.doctor.nxdog import check_nxdog


class _Observer:
    def __init__(self, observation: NXDogObservation, *, uses_https: bool = False) -> None:
        self.observation = observation
        self.uses_https = uses_https

    def observe(self) -> NXDogObservation:
        return self.observation


def _snapshot(
    *,
    nav_alive: bool = True,
    client_ready: bool = True,
    current_map: str | None = "lab",
    charging: bool = False,
    failures: tuple[NXDogEndpointFailure, ...] = (),
) -> NXDogObservation:
    return NXDogObservation(
        captured_at=datetime.now(UTC),
        base_url="http://dog.local:5088",
        nav_alive=nav_alive,
        client_ready=client_ready,
        current_map=current_map,
        pose=NXDogPoseObservation(
            x=1.0,
            y=2.0,
            yaw=0.5,
            map_name="lab",
            map_tile="lab_p0_p0",
        ),
        velocity=NXDogVelocityObservation(vx=0.0, vy=0.0, wz=0.0),
        charging=charging,
        failures=failures,
    )


def test_doctor_skips_nxdog_when_the_adapter_is_not_explicitly_configured() -> None:
    assert check_nxdog(environ={}) == []


def test_doctor_projects_a_valid_snapshot_without_claiming_motion_readiness() -> None:
    items = check_nxdog(observer=_Observer(_snapshot()))  # type: ignore[arg-type]
    by_name = {item.check_name: item for item in items}

    assert by_name["transport_security"].status == "warn"
    assert by_name["nav_heartbeat"].status == "pass"
    assert by_name["client_initialized"].status == "pass"
    assert "does not prove" in by_name["client_initialized"].message
    assert by_name["current_map"].status == "pass"
    assert "name only" in by_name["current_map"].message
    assert by_name["pose_observation"].status == "pass"
    assert "timestamp unavailable" in by_name["pose_observation"].message
    assert by_name["velocity_observation"].status == "pass"
    assert by_name["charging_observation"].status == "pass"
    assert by_name["evidence_contract"].status == "warn"
    assert "do not prove navigation readiness" in by_name["evidence_contract"].message


def test_doctor_treats_false_as_an_observation_not_a_transport_failure() -> None:
    items = check_nxdog(
        observer=_Observer(  # type: ignore[arg-type]
            _snapshot(nav_alive=False, client_ready=False, current_map=None, charging=False)
        )
    )
    by_name = {item.check_name: item for item in items}

    assert by_name["nav_heartbeat"].status == "warn"
    assert by_name["client_initialized"].status == "warn"
    assert by_name["current_map"].status == "warn"
    assert by_name["charging_observation"].status == "pass"
    assert "no charging current" in by_name["charging_observation"].message


def test_doctor_fails_the_specific_endpoint_without_erasing_other_checks() -> None:
    failure = NXDogEndpointFailure(
        endpoint="/velocity",
        kind=NXDogFailureKind.INVALID_PAYLOAD,
        message="/velocity.level must contain [vx, vy, wz]",
    )
    observation = _snapshot(failures=(failure,)).model_copy(update={"velocity": None})

    items = check_nxdog(observer=_Observer(observation))  # type: ignore[arg-type]
    by_name = {item.check_name: item for item in items}

    assert by_name["velocity_observation"].status == "fail"
    assert by_name["velocity_observation"].fix_suggestion
    assert by_name["pose_observation"].status == "pass"


def test_doctor_rejects_an_ambiguous_configured_base_url_without_network_io() -> None:
    items = check_nxdog(environ={NXDOG_API_URL_ENV: "http://dog.local:5088/api"})

    assert len(items) == 1
    assert items[0].check_name == "configuration"
    assert items[0].status == "fail"
    assert items[0].fix_suggestion


def test_run_doctor_only_probes_nxdog_on_the_full_navigation_path(
    monkeypatch,
    tmp_path,
) -> None:
    from jenai.doctor import checks

    called: list[bool] = []
    monkeypatch.setattr(checks, "check_nxdog", lambda: called.append(True) or [])

    checks.run_doctor(tmp_path / "missing.toml", include_nav=False)
    assert called == []

    checks.run_doctor(tmp_path / "missing.toml", include_nav=True)
    assert called == [True]

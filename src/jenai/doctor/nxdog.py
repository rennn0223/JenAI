"""Doctor projection for the experimental read-only NXDog adapter."""

from __future__ import annotations

import os
from collections.abc import Mapping

from jenai.adapters.nxdog import (
    NXDOG_API_URL_ENV,
    NXDogConfigurationError,
    NXDogEndpointFailure,
    NXDogFailureKind,
    NXDogObservation,
    NXDogObserver,
)
from jenai.schemas import DoctorCheckItem, DoctorStatus


def check_nxdog(
    *,
    environ: Mapping[str, str] | None = None,
    observer: NXDogObserver | None = None,
) -> list[DoctorCheckItem]:
    """Return no checks unless the NXDog adapter was explicitly configured."""

    values = os.environ if environ is None else environ
    configured_url = values.get(NXDOG_API_URL_ENV)
    if observer is None and (configured_url is None or not configured_url.strip()):
        return []

    try:
        active_observer = observer or NXDogObserver.from_environment(values)
    except NXDogConfigurationError as exc:
        return [
            DoctorCheckItem(
                section="nxdog",
                check_name="configuration",
                status=DoctorStatus.FAIL,
                message=str(exc),
                fix_suggestion=(
                    f"Set {NXDOG_API_URL_ENV} to the root of the isolated NXDog "
                    "example backend, for example http://192.168.123.18:5088."
                ),
            )
        ]

    snapshot = active_observer.observe()
    return [
        _transport_security_item(active_observer),
        *_snapshot_items(snapshot),
        DoctorCheckItem(
            section="nxdog",
            check_name="evidence_contract",
            status=DoctorStatus.WARN,
            message=(
                "NXDog HTTP observations have no vendor source timestamp, covariance, "
                "or cryptographic map identity; they do not prove navigation readiness "
                "or physical completion."
            ),
            fix_suggestion=(
                "Use this adapter for observation only. Obtain a versioned, timestamped "
                "vendor contract before enabling physical motion."
            ),
        ),
    ]


def _transport_security_item(observer: NXDogObserver) -> DoctorCheckItem:
    if observer.uses_https:
        message = (
            "NXDog observation uses HTTPS, but the vendor example defines no application "
            "authentication."
        )
    else:
        message = (
            "NXDog observation uses unauthenticated plain HTTP; any reachable host may be "
            "able to call the vendor example's motion endpoints."
        )
    return DoctorCheckItem(
        section="nxdog",
        check_name="transport_security",
        status=DoctorStatus.WARN,
        message=message,
        fix_suggestion=(
            "Keep TCP 5088 on an isolated robot network and allow only the JenAI host. "
            "Do not expose it through public Wi-Fi, port forwarding, or the internet."
        ),
    )


def _snapshot_items(snapshot: NXDogObservation) -> list[DoctorCheckItem]:
    failures = {item.endpoint: item for item in snapshot.failures}
    items = [
        _bool_item(
            failures.get("/nav_health"),
            check_name="nav_heartbeat",
            value=snapshot.nav_alive,
            pass_message="NXDog nxnav heartbeat is currently observable.",
            false_message="NXDog nxnav heartbeat is not currently observable.",
            false_fix="Start nxnav and confirm the example backend shares its ROS 2 domain.",
        ),
        _bool_item(
            failures.get("/get_ready_flag"),
            check_name="client_initialized",
            value=snapshot.client_ready,
            pass_message=(
                "NXDog example client reports initialized; this does not prove localization "
                "or action-server readiness."
            ),
            false_message="NXDog example client is not initialized.",
            false_fix="Restart the example backend after nxnav and its interfaces are available.",
        ),
        _map_item(snapshot, failures.get("/current_map")),
        _pose_item(snapshot, failures.get("/odom")),
        _velocity_item(snapshot, failures.get("/velocity")),
        _bool_item(
            failures.get("/is_charging"),
            check_name="charging_observation",
            value=snapshot.charging,
            pass_message="NXDog reports charging current is present.",
            false_message="NXDog reports no charging current.",
            false_fix=None,
            false_status=DoctorStatus.PASS,
        ),
    ]
    if (
        "/current_map" not in failures
        and "/odom" not in failures
        and snapshot.current_map is not None
        and snapshot.pose is not None
        and snapshot.pose.map_name is not None
        and snapshot.current_map != snapshot.pose.map_name
    ):
        items.append(
            DoctorCheckItem(
                section="nxdog",
                check_name="map_consistency",
                status=DoctorStatus.WARN,
                message="NXDog map observations are inconsistent and untimestamped.",
                fix_suggestion="Confirm the active NXDog map before relying on pose observations.",
            )
        )
    return items


def _failure_item(check_name: str, failure: NXDogEndpointFailure) -> DoctorCheckItem:
    if failure.kind == NXDogFailureKind.TRANSPORT:
        fix = (
            "Check the isolated NXDog network path and example backend. "
            "Do not enable motion while read-only evidence is invalid."
        )
    elif failure.kind == NXDogFailureKind.HTTP_STATUS:
        fix = "Check the NXDog backend service and response version before enabling motion."
    elif failure.kind == NXDogFailureKind.REDIRECT_REJECTED:
        fix = "Remove the redirect and expose the documented read-only endpoint directly."
    elif failure.kind == NXDogFailureKind.INVALID_PAYLOAD:
        fix = "Check the NXDog response schema and vendor API version before enabling motion."
    else:
        fix = "Inspect the JenAI NXDog adapter error before enabling motion."
    return DoctorCheckItem(
        section="nxdog",
        check_name=check_name,
        status=DoctorStatus.FAIL,
        message=f"[{failure.kind.value}] {failure.endpoint} observation failed: {failure.message}",
        fix_suggestion=fix,
    )


def _bool_item(
    failure: NXDogEndpointFailure | None,
    *,
    check_name: str,
    value: bool | None,
    pass_message: str,
    false_message: str,
    false_fix: str | None,
    false_status: DoctorStatus = DoctorStatus.WARN,
) -> DoctorCheckItem:
    if failure is not None:
        return _failure_item(check_name, failure)
    if value is True:
        return DoctorCheckItem(
            section="nxdog",
            check_name=check_name,
            status=DoctorStatus.PASS,
            message=pass_message,
        )
    return DoctorCheckItem(
        section="nxdog",
        check_name=check_name,
        status=false_status,
        message=false_message,
        fix_suggestion=false_fix,
    )


def _map_item(
    snapshot: NXDogObservation,
    failure: NXDogEndpointFailure | None,
) -> DoctorCheckItem:
    if failure is not None:
        return _failure_item("current_map", failure)
    if snapshot.current_map is None:
        return DoctorCheckItem(
            section="nxdog",
            check_name="current_map",
            status=DoctorStatus.WARN,
            message="NXDog reports no current map group name.",
            fix_suggestion="Load the intended NXDog map and wait for /nxnav/current_map.",
        )
    return DoctorCheckItem(
        section="nxdog",
        check_name="current_map",
        status=DoctorStatus.PASS,
        message=f"NXDog reports map group '{snapshot.current_map}' (name only, not a digest).",
    )


def _pose_item(
    snapshot: NXDogObservation,
    failure: NXDogEndpointFailure | None,
) -> DoctorCheckItem:
    if failure is not None:
        return _failure_item("pose_observation", failure)
    if snapshot.pose is None:
        return _missing_evidence_item("pose_observation", "/odom")
    pose = snapshot.pose
    return DoctorCheckItem(
        section="nxdog",
        check_name="pose_observation",
        status=DoctorStatus.PASS,
        message=(
            f"NXDog reports x={pose.x:.3f}, y={pose.y:.3f}, yaw={pose.yaw:.3f}, "
            f"tile={pose.map_tile or 'unknown'} (vendor timestamp unavailable)."
        ),
    )


def _velocity_item(
    snapshot: NXDogObservation,
    failure: NXDogEndpointFailure | None,
) -> DoctorCheckItem:
    if failure is not None:
        return _failure_item("velocity_observation", failure)
    if snapshot.velocity is None:
        return _missing_evidence_item("velocity_observation", "/velocity")
    velocity = snapshot.velocity
    return DoctorCheckItem(
        section="nxdog",
        check_name="velocity_observation",
        status=DoctorStatus.PASS,
        message=(
            f"NXDog reports vx={velocity.vx:.3f}, vy={velocity.vy:.3f}, "
            f"wz={velocity.wz:.3f} (vendor timestamp unavailable)."
        ),
    )


def _missing_evidence_item(check_name: str, endpoint: str) -> DoctorCheckItem:
    return _failure_item(
        check_name,
        NXDogEndpointFailure(
            endpoint=endpoint,
            kind=NXDogFailureKind.INVALID_PAYLOAD,
            message="valid evidence was not preserved",
        ),
    )

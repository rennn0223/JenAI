"""Provider-free emergency-stop primitives shared by every interface."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from jenai.bridge import HaltEvidence, RosBridgeClient
from jenai.config.models import AppConfig


class NavigationCancelStatus(StrEnum):
    """Evidence state for navigation cancellation during an emergency stop."""

    NOT_ACTIVE = "not_active"
    ACKNOWLEDGED = "acknowledged"
    UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True, slots=True)
class HaltReceipt:
    """Evidence returned only after the bridge confirms zero-velocity delivery."""

    navigation_cancel_status: NavigationCancelStatus
    zero_velocity_delivered: bool
    message: str

    @property
    def navigation_goal_canceled(self) -> bool:
        """Compatibility view for callers that need positive acknowledgement."""
        return self.navigation_cancel_status is NavigationCancelStatus.ACKNOWLEDGED


def _cancel_status(evidence: HaltEvidence) -> NavigationCancelStatus:
    if evidence.navigation_cancel_acknowledged:
        return NavigationCancelStatus.ACKNOWLEDGED
    if evidence.navigation_cancel_requested:
        return NavigationCancelStatus.UNCONFIRMED
    return NavigationCancelStatus.NOT_ACTIVE


async def halt_robot_with_receipt(config: AppConfig, bridge: RosBridgeClient) -> HaltReceipt:
    """Cancel Nav2 and deliver zero velocity, returning typed bridge evidence.

    ``RosBridgeClient.halt_with_evidence`` raises unless the sidecar confirms
    the stop pulse. A receipt therefore never manufactures delivery success.
    """

    vehicle = config.vehicle
    evidence = await bridge.halt_with_evidence(
        cmd_vel_topic=vehicle.cmd_vel_topic, stamped=vehicle.cmd_vel_stamped
    )
    cancel_status = _cancel_status(evidence)
    if cancel_status is NavigationCancelStatus.ACKNOWLEDGED:
        message = "Robot halted (navigation goal canceled, zero velocity sent)."
    elif cancel_status is NavigationCancelStatus.UNCONFIRMED:
        message = "Zero velocity was delivered, but navigation cancellation was not acknowledged."
    else:
        message = "Robot halted (no active navigation goal, zero velocity sent)."
    return HaltReceipt(
        navigation_cancel_status=cancel_status,
        zero_velocity_delivered=evidence.zero_velocity_delivered,
        message=message,
    )


async def arm_watchdog(config: AppConfig, bridge: RosBridgeClient, timeout_s: float = 6.0) -> None:
    """Arm the bridge-side dead-client watchdog with this vehicle's settings."""

    vehicle = config.vehicle
    await bridge.configure_safety(
        watchdog_s=timeout_s,
        cmd_vel_topic=vehicle.cmd_vel_topic,
        stamped=vehicle.cmd_vel_stamped,
        pose_jump_threshold_m=vehicle.pose_jump_threshold_m,
        pose_jump_window_s=vehicle.pose_jump_window_s,
    )

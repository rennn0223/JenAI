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
    """Evidence returned after the bridge publishes the zero-velocity command.

    ``zero_velocity_delivered`` is retained as a compatibility field. It means
    that the sidecar completed its publish calls; it does not prove DDS
    reception, controller application, or observed vehicle motion.
    """

    navigation_cancel_status: NavigationCancelStatus
    zero_velocity_delivered: bool
    message: str
    motion_stop_observed: bool | None = None

    @property
    def zero_velocity_command_published(self) -> bool:
        """Canonical name for the legacy publish-completion evidence."""
        return self.zero_velocity_delivered

    @property
    def navigation_goal_canceled(self) -> bool:
        """Compatibility view for callers that need positive acknowledgement."""
        return self.navigation_cancel_status is NavigationCancelStatus.ACKNOWLEDGED


def halt_receipt_evidence(receipt: HaltReceipt) -> dict[str, bool | str | None]:
    """Serialize stop evidence without overstating publish completion."""

    command_published = bool(
        getattr(receipt, "zero_velocity_command_published", receipt.zero_velocity_delivered)
    )
    return {
        "navigation_cancel_status": receipt.navigation_cancel_status.value,
        "navigation_goal_canceled": receipt.navigation_goal_canceled,
        "zero_velocity_command_published": command_published,
        # Compatibility key retained for existing receipt and audit consumers.
        "zero_velocity_delivered": receipt.zero_velocity_delivered,
        "motion_stop_observed": getattr(receipt, "motion_stop_observed", None),
        "message": receipt.message,
    }


def _cancel_status(evidence: HaltEvidence) -> NavigationCancelStatus:
    if evidence.navigation_cancel_acknowledged:
        return NavigationCancelStatus.ACKNOWLEDGED
    if evidence.navigation_cancel_requested:
        return NavigationCancelStatus.UNCONFIRMED
    return NavigationCancelStatus.NOT_ACTIVE


async def halt_robot_with_receipt(config: AppConfig, bridge: RosBridgeClient) -> HaltReceipt:
    """Cancel Nav2 and deliver zero velocity, returning typed bridge evidence.

    ``RosBridgeClient.halt_with_evidence`` raises unless the sidecar confirms
    that its publish calls completed. A receipt never upgrades that evidence
    into DDS reception, controller application, or observed motion stop.
    """

    vehicle = config.vehicle
    evidence = await bridge.halt_with_evidence(
        cmd_vel_topic=vehicle.cmd_vel_topic, stamped=vehicle.cmd_vel_stamped
    )
    cancel_status = _cancel_status(evidence)
    if cancel_status is NavigationCancelStatus.ACKNOWLEDGED:
        message = (
            "Zero-velocity command published; navigation cancellation acknowledged. "
            "Motion stop was not independently observed."
        )
    elif cancel_status is NavigationCancelStatus.UNCONFIRMED:
        message = (
            "Zero-velocity command published, but navigation cancellation was not acknowledged. "
            "Motion stop was not independently observed."
        )
    else:
        message = (
            "Zero-velocity command published; no active navigation goal was reported. "
            "Motion stop was not independently observed."
        )
    return HaltReceipt(
        navigation_cancel_status=cancel_status,
        zero_velocity_delivered=evidence.zero_velocity_command_published,
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

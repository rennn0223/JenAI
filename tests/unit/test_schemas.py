from __future__ import annotations

import pytest
from pydantic import ValidationError

from jenai.schemas import (
    ApprovalRequest,
    EffectScope,
    ErrorType,
    JenAIError,
    ModelBindings,
    PlanStep,
    Pose2D,
    RiskLevel,
    RunRecord,
    RunStatus,
)


def test_model_bindings_reject_blank_values() -> None:
    with pytest.raises(ValidationError):
        ModelBindings(chat="", plan="planner", vision="vision", route="route", default="default")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_pose_rejects_non_finite_coordinates(value: float) -> None:
    with pytest.raises(ValidationError):
        Pose2D(x=value, y=0.0, yaw=0.0)


def test_run_record_defaults_match_state_machine() -> None:
    bindings = ModelBindings(
        chat="chat-model",
        plan="plan-model",
        vision="vision-model",
        route="route-model",
        default="default-model",
    )
    _ = bindings
    run = RunRecord(session_id="session_1", user_input="/plan test")

    assert run.status == RunStatus.IDLE
    assert run.plan_steps == []
    assert run.tool_calls == []
    assert run.run_id.startswith("run_")


def test_approval_request_defaults_to_pending() -> None:
    approval = ApprovalRequest(
        run_id="run_1",
        tool_call_id="tool_1",
        title="Publish ROS2 message",
        summary="Publish payload to /cmd_vel",
        raw_action='ros2 topic pub /cmd_vel geometry_msgs/msg/Twist "{...}"',
        risk_level=RiskLevel.P1,
        effect_scope=EffectScope.SIM_CONTROL,
        justification="Required to execute the requested route.",
    )

    assert approval.status == "pending"
    assert approval.approval_id.startswith("approval_")


def test_error_type_is_restricted() -> None:
    err = JenAIError(error_type=ErrorType.CONFIG_ERROR, message="Missing config")

    assert err.error_type == "config_error"


def test_plan_step_defaults() -> None:
    step = PlanStep(title="Inspect topics", description="List ROS topics", reason="Need context")

    assert step.status == "pending"
    assert step.requires_approval is False
    assert step.step_id.startswith("step_")
